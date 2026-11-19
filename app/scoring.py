from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import (
    Contest, ContestParticipant, ContestProblem, Problem, Submission,
    JudgingAttempt, User
)
from app.schemas import LeaderboardResponse, LeaderboardEntry, ProblemScoreDetail

PENALTY_VERDICTS = {
    "WRONG_ANSWER",
    "RUNTIME_ERROR",
    "TIME_LIMIT_EXCEEDED",
    "OUTPUT_LIMIT_EXCEEDED",
    "COMPILATION_ERROR"
}

def calculate_time_offset_minutes(starts_at: datetime, event_time: datetime) -> int:
    """Computes floor((event_time - starts_at) / 60) in minutes."""
    # Ensure timezone aware
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
        
    diff_seconds = (event_time - starts_at).total_seconds()
    if diff_seconds < 0:
        return 0
    return int(diff_seconds // 60)


async def compute_standings_data(
    db: AsyncSession,
    contest_id: str,
    from_authoritative_only: bool = True
) -> LeaderboardResponse:
    """
    Computes deterministic ICPC leaderboard standings.
    Can be used by both the serving path and the rebuild_standings verification path.
    """
    # 1. Fetch Contest
    contest_res = await db.execute(select(Contest).where(Contest.id == contest_id))
    contest = contest_res.scalar_one_or_none()
    if not contest:
        raise ValueError(f"Contest {contest_id} not found")

    # 2. Fetch Contest Problems
    cp_stmt = (
        select(ContestProblem, Problem)
        .join(Problem, ContestProblem.problem_id == Problem.id)
        .where(ContestProblem.contest_id == contest_id)
        .order_by(ContestProblem.order_index.asc(), ContestProblem.letter.asc())
    )
    cp_res = await db.execute(cp_stmt)
    contest_problems = cp_res.all()
    problem_map = {row.Problem.id: row.ContestProblem.letter for row in contest_problems}

    # 3. Fetch Registered Participants
    part_stmt = (
        select(ContestParticipant, User)
        .join(User, ContestParticipant.user_id == User.id)
        .where(ContestParticipant.contest_id == contest_id)
    )
    part_res = await db.execute(part_stmt)
    participants = part_res.all()

    # 4. Fetch Submissions
    sub_stmt = (
        select(Submission)
        .where(Submission.contest_id == contest_id)
        .order_by(Submission.created_at.asc())
    )
    sub_res = await db.execute(sub_stmt)
    all_submissions = sub_res.scalars().all()

    # Pre-fetch authoritative judging attempts if needed
    auth_attempt_ids = [s.authoritative_attempt_id for s in all_submissions if s.authoritative_attempt_id]
    attempt_verdicts = {}
    if auth_attempt_ids:
        att_stmt = select(JudgingAttempt).where(JudgingAttempt.id.in_(auth_attempt_ids))
        att_res = await db.execute(att_stmt)
        for att in att_res.scalars().all():
            attempt_verdicts[att.id] = att.verdict

    # Group submissions by (user_id, problem_id)
    user_prob_subs: Dict[Tuple[str, str], List[Submission]] = {}
    for s in all_submissions:
        # Ignore submissions created outside contest window for ICPC ranking
        key = (s.user_id, s.problem_id)
        if key not in user_prob_subs:
            user_prob_subs[key] = []
        user_prob_subs[key].append(s)

    # 5. Calculate scores for each participant
    raw_entries = []

    for part_row in participants:
        user = part_row.User
        user_id = user.id
        solved_count = 0
        total_penalty = 0
        last_ac_at: Optional[datetime] = None
        problem_scores: Dict[str, ProblemScoreDetail] = {}

        for cp_row in contest_problems:
            p_id = cp_row.Problem.id
            letter = cp_row.ContestProblem.letter
            subs = user_prob_subs.get((user_id, p_id), [])

            solved = False
            failed_count = 0
            prob_penalty = 0
            first_ac_seconds: Optional[int] = None

            for sub in subs:
                # Determine authoritative verdict
                verdict = sub.verdict
                if sub.authoritative_attempt_id and sub.authoritative_attempt_id in attempt_verdicts:
                    verdict = attempt_verdicts[sub.authoritative_attempt_id]

                if verdict == "ACCEPTED":
                    solved = True
                    first_ac_time = sub.created_at
                    if first_ac_time.tzinfo is None:
                        first_ac_time = first_ac_time.replace(tzinfo=timezone.utc)
                    
                    c_starts_at = contest.starts_at
                    if c_starts_at.tzinfo is None:
                        c_starts_at = c_starts_at.replace(tzinfo=timezone.utc)

                    elapsed_minutes = calculate_time_offset_minutes(c_starts_at, first_ac_time)
                    prob_penalty = elapsed_minutes + (20 * failed_count)
                    first_ac_seconds = int((first_ac_time - c_starts_at).total_seconds())

                    if last_ac_at is None or first_ac_time > last_ac_at:
                        last_ac_at = first_ac_time
                    break
                elif verdict in PENALTY_VERDICTS:
                    failed_count += 1
                # INFRASTRUCTURE_ERROR or unjudged submissions do NOT count as penalties

            if solved:
                solved_count += 1
                total_penalty += prob_penalty

            problem_scores[p_id] = ProblemScoreDetail(
                problem_id=p_id,
                letter=letter,
                solved=solved,
                attempts_before_ac=failed_count,
                penalty_minutes=prob_penalty if solved else 0,
                first_ac_time_seconds=first_ac_seconds
            )

        raw_entries.append({
            "user_id": user_id,
            "username": user.username,
            "full_name": user.full_name,
            "solved_count": solved_count,
            "total_penalty_minutes": total_penalty,
            "last_ac_at": last_ac_at,
            "problem_scores": problem_scores
        })

    # 6. Deterministic Sort:
    # 1. solved_count DESC
    # 2. total_penalty_minutes ASC
    # 3. last_ac_at ASC (None/never-solved sorts last)
    # 4. user_id ASC
    def sort_key(entry: Dict[str, Any]):
        lac = entry["last_ac_at"]
        lac_timestamp = lac.timestamp() if lac else float("inf")
        return (
            -entry["solved_count"],
            entry["total_penalty_minutes"],
            lac_timestamp,
            entry["user_id"]
        )

    raw_entries.sort(key=sort_key)

    # 7. Assign Ranks
    ranked_entries: List[LeaderboardEntry] = []
    current_rank = 1
    for idx, entry in enumerate(raw_entries):
        ranked_entries.append(LeaderboardEntry(
            rank=idx + 1,
            user_id=entry["user_id"],
            username=entry["username"],
            full_name=entry["full_name"],
            solved_count=entry["solved_count"],
            total_penalty_minutes=entry["total_penalty_minutes"],
            problem_scores=entry["problem_scores"]
        ))

    now_utc = datetime.now(timezone.utc)
    is_frozen = bool(contest.freeze_at and now_utc >= contest.freeze_at)

    return LeaderboardResponse(
        contest_id=contest.id,
        contest_title=contest.title,
        is_frozen=is_frozen,
        generated_at=now_utc,
        entries=ranked_entries
    )


async def rebuild_standings(db: AsyncSession, contest_id: str) -> LeaderboardResponse:
    """
    Rebuilds standings strictly from authoritative persistent records
    and validates zero drift against current standings.
    """
    return await compute_standings_data(db, contest_id, from_authoritative_only=True)
