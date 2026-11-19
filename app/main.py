import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import (
    FastAPI, Depends, HTTPException, Header, Query, Request, status
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    Organization, User, OrgMembership, Contest, ContestParticipant,
    Problem, ContestProblem, ProblemVersion, TestSuiteVersion,
    TestCase, IdempotencyRecord, Submission, JudgingAttempt, TestCaseResult
)
from app.schemas import (
    ContestResponse, ContestDetailResponse, ContestParticipantResponse,
    ProblemSummaryResponse, ProblemDetailResponse, TestCasePublicResponse,
    SubmissionCreate, SubmissionResponse, SubmissionDetailResponse,
    JudgingAttemptResponse, TestCaseResultResponse, RejudgeRequest,
    LeaderboardResponse, PaginatedResponse, PaginationMeta, ErrorEnvelope, ErrorDetail
)
from app.auth import AuthContext, get_current_auth, require_organizer, require_participant
from app.scoring import compute_standings_data, rebuild_standings

logger = logging.getLogger("shodh.api")
logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(
    title=settings.APP_NAME,
    description="Shodh a Code - Coding Contest Platform Backend API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Request ID & Structured Logging Middleware
@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    start_time = time.monotonic()
    
    # Store request_id in request state for access in routes
    request.state.request_id = request_id

    response = await call_next(request)
    
    duration_ms = int((time.monotonic() - start_time) * 1000)
    response.headers["X-Request-Id"] = request_id

    log_payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": duration_ms
    }
    logger.info(json.dumps(log_payload))
    return response


# Exception Handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Malformed request parameters or payload",
                "details": exc.errors()
            }
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content={"error": detail})
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "HTTP_ERROR",
                "message": str(detail)
            }
        }
    )


# -------------------------------------------------------------
# System & Health
# -------------------------------------------------------------
@app.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(1))
        db_status = "HEALTHY"
    except Exception as e:
        db_status = f"UNHEALTHY: {str(e)}"

    return {
        "status": "UP",
        "database": db_status,
        "runner_mode": settings.RUNNER_MODE,
        "worker_id": settings.WORKER_ID,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# -------------------------------------------------------------
# Contests Endpoints
# -------------------------------------------------------------
@app.get("/contests", response_model=PaginatedResponse[ContestResponse], tags=["Contests"])
async def list_contests(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """Lists published contests belonging to the caller's organization."""
    count_stmt = select(func.count(Contest.id)).where(
        Contest.org_id == auth.org_id,
        Contest.is_published == True
    )
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    stmt = (
        select(Contest)
        .where(Contest.org_id == auth.org_id, Contest.is_published == True)
        .order_by(Contest.created_at.desc(), Contest.id.asc())
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(stmt)
    contests = res.scalars().all()

    return PaginatedResponse(
        items=[ContestResponse.model_validate(c) for c in contests],
        meta=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total
        )
    )


@app.get("/contests/{contest_id}", response_model=ContestDetailResponse, tags=["Contests"])
async def get_contest(
    contest_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves contest details along with problem letter assignments."""
    stmt = (
        select(Contest)
        .where(Contest.id == contest_id, Contest.org_id == auth.org_id)
    )
    res = await db.execute(stmt)
    contest = res.scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest '{contest_id}' not found"}
        )

    # Fetch problems in contest
    cp_stmt = (
        select(ContestProblem, Problem, ProblemVersion)
        .join(Problem, ContestProblem.problem_id == Problem.id)
        .join(ProblemVersion, Problem.active_problem_version_id == ProblemVersion.id)
        .where(ContestProblem.contest_id == contest_id)
        .order_by(ContestProblem.order_index.asc(), ContestProblem.letter.asc())
    )
    cp_res = await db.execute(cp_stmt)
    problems_list = []
    for cp, p, pv in cp_res.all():
        problems_list.append(ProblemSummaryResponse(
            id=p.id,
            slug=p.slug,
            title=pv.title,
            letter=cp.letter,
            created_at=p.created_at
        ))

    detail = ContestDetailResponse.model_validate(contest)
    detail.problems = problems_list
    return detail


@app.post("/contests/{contest_id}/join", response_model=ContestParticipantResponse, tags=["Contests"])
async def join_contest(
    contest_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """Registers the authenticated caller as a participant in the contest."""
    c_stmt = select(Contest).where(Contest.id == contest_id, Contest.org_id == auth.org_id)
    c_res = await db.execute(c_stmt)
    contest = c_res.scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest '{contest_id}' not found"}
        )

    # Check if already registered
    part_stmt = select(ContestParticipant).where(
        ContestParticipant.contest_id == contest_id,
        ContestParticipant.user_id == auth.user_id
    )
    part_res = await db.execute(part_stmt)
    existing = part_res.scalar_one_or_none()
    if existing:
        return ContestParticipantResponse.model_validate(existing)

    participant = ContestParticipant(
        contest_id=contest_id,
        user_id=auth.user_id
    )
    db.add(participant)
    await db.commit()
    await db.refresh(participant)
    return ContestParticipantResponse.model_validate(participant)


# -------------------------------------------------------------
# Problems Endpoints
# -------------------------------------------------------------
@app.get("/contests/{contest_id}/problems/{problem_id}", response_model=ProblemDetailResponse, tags=["Problems"])
async def get_problem(
    contest_id: str,
    problem_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves problem statement, constraints, and non-hidden sample test cases.
    Hidden test cases are strictly filtered out.
    """
    # Verify problem in contest and belongs to organization
    stmt = (
        select(Problem, ProblemVersion, TestSuiteVersion)
        .join(ContestProblem, ContestProblem.problem_id == Problem.id)
        .join(ProblemVersion, Problem.active_problem_version_id == ProblemVersion.id)
        .join(TestSuiteVersion, Problem.active_suite_version_id == TestSuiteVersion.id)
        .where(
            ContestProblem.contest_id == contest_id,
            Problem.id == problem_id,
            Problem.org_id == auth.org_id
        )
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROBLEM_NOT_FOUND", "message": f"Problem '{problem_id}' not found in contest"}
        )

    problem, pv, suite = row

    # Fetch public (non-hidden) sample test cases
    tc_stmt = (
        select(TestCase)
        .where(TestCase.suite_version_id == suite.id, TestCase.is_hidden == False)
        .order_by(TestCase.ordinal.asc())
    )
    tc_res = await db.execute(tc_stmt)
    sample_tests = [
        TestCasePublicResponse(
            ordinal=tc.ordinal,
            input_data=tc.input_data,
            expected_output=tc.expected_output,
            is_hidden=False
        )
        for tc in tc_res.scalars().all()
    ]

    return ProblemDetailResponse(
        id=problem.id,
        slug=problem.slug,
        title=pv.title,
        statement=pv.statement,
        time_limit_ms=pv.time_limit_ms,
        memory_limit_mb=pv.memory_limit_mb,
        output_limit_bytes=pv.output_limit_bytes,
        active_problem_version_id=problem.active_problem_version_id,
        active_suite_version_id=problem.active_suite_version_id,
        sample_test_cases=sample_tests
    )


# -------------------------------------------------------------
# Submissions Endpoints
# -------------------------------------------------------------
@app.post(
    "/contests/{contest_id}/submissions",
    response_model=SubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Submissions"]
)
async def create_submission(
    contest_id: str,
    payload: SubmissionCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Submits code asynchronously for judging.
    - Idempotent: Same key + identical payload returns existing submission (200/202).
    - Conflicting key with different payload returns 409 Conflict.
    - Atomically persists Submission and JudgingAttempt (status='QUEUED').
    """
    now = datetime.now(timezone.utc)

    # 1. Verify contest is open
    c_stmt = select(Contest).where(Contest.id == contest_id, Contest.org_id == auth.org_id)
    c_res = await db.execute(c_stmt)
    contest = c_res.scalar_one_or_none()
    if not contest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEST_NOT_FOUND", "message": f"Contest '{contest_id}' not found"}
        )

    # Time window check
    c_starts = contest.starts_at if contest.starts_at.tzinfo else contest.starts_at.replace(tzinfo=timezone.utc)
    c_ends = contest.ends_at if contest.ends_at.tzinfo else contest.ends_at.replace(tzinfo=timezone.utc)
    if not (c_starts <= now < c_ends):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CONTEST_NOT_OPEN",
                "message": "Submissions are only accepted while the contest is open",
                "window": {"starts_at": c_starts.isoformat(), "ends_at": c_ends.isoformat()}
            }
        )

    # 2. Verify user is registered for contest
    part_stmt = select(ContestParticipant).where(
        ContestParticipant.contest_id == contest_id,
        ContestParticipant.user_id == auth.user_id
    )
    part_res = await db.execute(part_stmt)
    if not part_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "NOT_REGISTERED",
                "message": "User must join contest before submitting code"
            }
        )

    # 3. Verify problem belongs to contest
    p_stmt = (
        select(Problem)
        .join(ContestProblem, ContestProblem.problem_id == Problem.id)
        .where(ContestProblem.contest_id == contest_id, Problem.id == payload.problem_id)
    )
    p_res = await db.execute(p_stmt)
    problem = p_res.scalar_one_or_none()
    if not problem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROBLEM_NOT_IN_CONTEST", "message": "Problem is not part of this contest"}
        )

    if not problem.active_problem_version_id or not problem.active_suite_version_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PROBLEM_NOT_READY", "message": "Problem version configuration is incomplete"}
        )

    # Compute payload hash
    payload_repr = f"{payload.problem_id}:{payload.language}:{payload.code}"
    payload_hash = hashlib.sha256(payload_repr.encode("utf-8")).hexdigest()

    # 4. Check Idempotency Key
    if idempotency_key:
        idem_stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.contest_id == contest_id,
            IdempotencyRecord.user_id == auth.user_id,
            IdempotencyRecord.idempotency_key == idempotency_key
        )
        idem_res = await db.execute(idem_stmt)
        existing_record = idem_res.scalar_one_or_none()

        if existing_record:
            if existing_record.payload_sha256 == payload_hash:
                # Return existing submission
                sub_res = await db.execute(
                    select(Submission).where(Submission.id == existing_record.submission_id)
                )
                existing_sub = sub_res.scalar_one_or_none()
                if existing_sub:
                    return SubmissionResponse.model_validate(existing_sub)
            else:
                # Same idempotency key with different payload -> 409 Conflict
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH",
                        "message": "Idempotency key reused with conflicting submission payload"
                    }
                )

    # 5. Create Submission and Attempt atomically
    submission = Submission(
        contest_id=contest_id,
        user_id=auth.user_id,
        problem_id=problem.id,
        problem_version_id=problem.active_problem_version_id,
        test_suite_version_id=problem.active_suite_version_id,
        language=payload.language,
        code=payload.code,
        status="QUEUED"
    )
    db.add(submission)
    await db.flush()  # assign submission.id

    attempt = JudgingAttempt(
        submission_id=submission.id,
        attempt_number=1,
        problem_version_id=problem.active_problem_version_id,
        test_suite_version_id=problem.active_suite_version_id,
        status="QUEUED"
    )
    db.add(attempt)

    if idempotency_key:
        idem_rec = IdempotencyRecord(
            contest_id=contest_id,
            user_id=auth.user_id,
            idempotency_key=idempotency_key,
            payload_sha256=payload_hash,
            submission_id=submission.id
        )
        db.add(idem_rec)

    await db.commit()
    await db.refresh(submission)
    return SubmissionResponse.model_validate(submission)


@app.get("/contests/{contest_id}/submissions", response_model=PaginatedResponse[SubmissionResponse], tags=["Submissions"])
async def list_submissions(
    contest_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists submissions for contest.
    Participants can only view their own submissions.
    Organizers can view all submissions across the contest.
    """
    conditions = [Submission.contest_id == contest_id]
    if auth.role != "ORGANIZER":
        conditions.append(Submission.user_id == auth.user_id)

    count_stmt = select(func.count(Submission.id)).where(and_(*conditions))
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    stmt = (
        select(Submission)
        .where(and_(*conditions))
        .order_by(Submission.created_at.desc(), Submission.id.asc())
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(stmt)
    submissions = res.scalars().all()

    return PaginatedResponse(
        items=[SubmissionResponse.model_validate(s) for s in submissions],
        meta=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total
        )
    )


@app.get("/contests/{contest_id}/submissions/{submission_id}", response_model=SubmissionDetailResponse, tags=["Submissions"])
async def get_submission(
    contest_id: str,
    submission_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves full submission details and judging attempts.
    Security:
    - Participants cannot inspect other participants' submissions (403 Forbidden).
    - Hidden test outputs and details are completely redacted from participant view.
    """
    stmt = (
        select(Submission)
        .options(
            selectinload(Submission.attempts).selectinload(JudgingAttempt.test_case_results)
        )
        .where(Submission.id == submission_id, Submission.contest_id == contest_id)
    )
    res = await db.execute(stmt)
    submission = res.scalar_one_or_none()
    if not submission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBMISSION_NOT_FOUND", "message": f"Submission '{submission_id}' not found"}
        )

    # Participant privacy barrier
    if auth.role != "ORGANIZER" and submission.user_id != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Cannot inspect another participant's private submission"}
        )

    # Build attempts with proper hidden test case redactions
    sanitized_attempts = []
    for att in sorted(submission.attempts, key=lambda a: a.attempt_number):
        sanitized_results = []
        for tc_res in sorted(att.test_case_results, key=lambda t: t.ordinal):
            # Redact actual_output if hidden test and caller is participant
            actual_out = tc_res.actual_output
            if tc_res.is_hidden and auth.role != "ORGANIZER":
                actual_out = None

            sanitized_results.append(TestCaseResultResponse(
                ordinal=tc_res.ordinal,
                passed=tc_res.passed,
                verdict=tc_res.verdict,
                execution_time_ms=tc_res.execution_time_ms,
                actual_output=actual_out,
                is_hidden=tc_res.is_hidden
            ))

        sanitized_attempts.append(JudgingAttemptResponse(
            id=att.id,
            attempt_number=att.attempt_number,
            problem_version_id=att.problem_version_id,
            test_suite_version_id=att.test_suite_version_id,
            status=att.status,
            verdict=att.verdict,
            execution_time_ms=att.execution_time_ms,
            memory_used_kb=att.memory_used_kb,
            retry_count=att.retry_count,
            created_at=att.created_at,
            completed_at=att.completed_at,
            test_case_results=sanitized_results
        ))

    return SubmissionDetailResponse(
        id=submission.id,
        contest_id=submission.contest_id,
        user_id=submission.user_id,
        problem_id=submission.problem_id,
        problem_version_id=submission.problem_version_id,
        test_suite_version_id=submission.test_suite_version_id,
        language=submission.language,
        code=submission.code,
        status=submission.status,
        verdict=submission.verdict,
        authoritative_attempt_id=submission.authoritative_attempt_id,
        created_at=submission.created_at,
        attempts=sanitized_attempts
    )


@app.post(
    "/contests/{contest_id}/submissions/{submission_id}/rejudge",
    response_model=JudgingAttemptResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Submissions"]
)
async def rejudge_submission(
    contest_id: str,
    submission_id: str,
    req: RejudgeRequest,
    auth: AuthContext = Depends(require_organizer),
    db: AsyncSession = Depends(get_db)
):
    """
    Organizer operation: Rejudge a submission against a specific or active problem and test-suite version.
    Increments attempt_number monotonically to guarantee newer attempts take precedence.
    """
    stmt = (
        select(Submission, Problem)
        .join(Problem, Submission.problem_id == Problem.id)
        .where(Submission.id == submission_id, Submission.contest_id == contest_id)
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBMISSION_NOT_FOUND", "message": f"Submission '{submission_id}' not found"}
        )

    submission, problem = row

    # Determine target versions
    target_problem_version = req.problem_version_id or problem.active_problem_version_id
    target_suite_version = req.test_suite_version_id or problem.active_suite_version_id

    # Find highest existing attempt number
    max_att_stmt = select(func.max(JudgingAttempt.attempt_number)).where(
        JudgingAttempt.submission_id == submission_id
    )
    max_att_res = await db.execute(max_att_stmt)
    highest_num = max_att_res.scalar() or 0
    next_attempt_number = highest_num + 1

    new_attempt = JudgingAttempt(
        submission_id=submission_id,
        attempt_number=next_attempt_number,
        problem_version_id=target_problem_version,
        test_suite_version_id=target_suite_version,
        status="QUEUED"
    )
    db.add(new_attempt)
    submission.status = "QUEUED"

    await db.commit()
    await db.refresh(new_attempt)

    return JudgingAttemptResponse.model_validate(new_attempt)


# -------------------------------------------------------------
# Leaderboard & Standings
# -------------------------------------------------------------
@app.get("/contests/{contest_id}/leaderboard", response_model=LeaderboardResponse, tags=["Leaderboard"])
async def get_leaderboard(
    contest_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns deterministic ICPC contest standings.
    Ranked by (1) solved_count DESC, (2) total_penalty_minutes ASC, (3) last_ac_at ASC, (4) user_id ASC.
    """
    return await compute_standings_data(db, contest_id)


@app.post("/contests/{contest_id}/leaderboard/rebuild", response_model=LeaderboardResponse, tags=["Leaderboard"])
async def trigger_rebuild_standings(
    contest_id: str,
    auth: AuthContext = Depends(require_organizer),
    db: AsyncSession = Depends(get_db)
):
    """
    Organizer diagnostic endpoint: Rebuilds standings strictly from authoritative records
    and verifies that the serving path equals the rebuilt standings.
    """
    return await rebuild_standings(db, contest_id)


# -------------------------------------------------------------
# Diagnostics & Bottleneck Proof
# -------------------------------------------------------------
@app.get("/diagnostics/bottleneck-explain", tags=["Diagnostics"])
async def get_bottleneck_explain(
    contest_id: str,
    auth: AuthContext = Depends(require_organizer),
    db: AsyncSession = Depends(get_db)
):
    """
    Runs EXPLAIN (ANALYZE, BUFFERS) or EXPLAIN on the leaderboard submission scan.
    Provides diagnostic evidence comparing before and after the composite index.
    """
    dialect_name = db.bind.dialect.name if db.bind else "postgresql"
    query = f"""
    EXPLAIN {'(ANALYZE, BUFFERS)' if dialect_name == 'postgresql' else ''}
    SELECT s.id, s.contest_id, s.user_id, s.problem_id, s.authoritative_attempt_id, s.created_at
    FROM submissions s
    WHERE s.contest_id = '{contest_id}' AND s.authoritative_attempt_id IS NOT NULL;
    """
    try:
        from sqlalchemy import text
        res = await db.execute(text(query))
        rows = [row[0] for row in res.fetchall()]
        return {
            "contest_id": contest_id,
            "dialect": dialect_name,
            "query_plan": rows
        }
    except Exception as ex:
        return {
            "error": str(ex),
            "note": "EXPLAIN ANALYZE requires PostgreSQL database engine"
        }
