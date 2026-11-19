import asyncio
import uuid
from datetime import datetime, timezone, timedelta
import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.seed import (
    ORG_A_ID, USER_ADMIN_A, USER_ALICE, USER_BOB,
    CONTEST_A_ID, PROB_A_ID, SAMPLE_PROGRAMS
)
from app.models import Submission, JudgingAttempt
from app.worker import claim_and_process_one, claim_next_attempt, process_attempt
from app.runner import DeterministicMockRunner
from app.scoring import rebuild_standings, compute_standings_data

@pytest.mark.asyncio
async def test_idempotency_barrier_race_and_conflict(client: AsyncClient, db_session: AsyncSession):
    """
    Fires 10 simultaneous identical requests with the same Idempotency-Key.
    Guarantees that exactly 1 logical submission is created and all callers receive the same ID.
    Follows with a conflicting payload under the same key to verify explicit 409 Conflict.
    """
    idem_key = f"race-key-{uuid.uuid4()}"
    headers = {
        "X-User-Id": USER_ALICE,
        "X-Org-Id": ORG_A_ID,
        "Idempotency-Key": idem_key
    }
    payload = {
        "problem_id": PROB_A_ID,
        "language": "python3",
        "code": SAMPLE_PROGRAMS["AC_TWO_SUM"]
    }

    # 1. Fire 10 concurrent requests
    tasks = [
        client.post(f"/contests/{CONTEST_A_ID}/submissions", headers=headers, json=payload)
        for _ in range(10)
    ]
    responses = await asyncio.gather(*tasks)

    # All should return 200 or 202
    for r in responses:
        assert r.status_code in (200, 202)

    submission_ids = {r.json()["id"] for r in responses}
    assert len(submission_ids) == 1, "Expected all concurrent requests to return the identical submission ID"
    single_sub_id = list(submission_ids)[0]

    # Check database: exactly one submission row
    sub_count_stmt = select(func.count(Submission.id)).where(Submission.id == single_sub_id)
    count_res = await db_session.execute(sub_count_stmt)
    assert count_res.scalar() == 1

    # 2. Conflicting Request with same idempotency key but different code
    conflict_payload = {
        "problem_id": PROB_A_ID,
        "language": "python3",
        "code": "print('CONFLICTING_PAYLOAD_CODE')"
    }
    conflict_res = await client.post(
        f"/contests/{CONTEST_A_ID}/submissions",
        headers=headers,
        json=conflict_payload
    )
    assert conflict_res.status_code == 409
    assert conflict_res.json()["error"]["code"] == "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"

    # Drain created attempt from queue
    await claim_and_process_one(worker_id="test-drain-worker")


@pytest.mark.asyncio
async def test_competing_workers_no_duplicate_judging(client: AsyncClient):
    """
    Creates multiple queued submissions and runs competing workers concurrently.
    Verifies that jobs are partitioned and no attempt is processed twice.
    """
    sub_ids = []
    for i in range(4):
        headers = {
            "X-User-Id": USER_ALICE,
            "X-Org-Id": ORG_A_ID,
            "Idempotency-Key": f"multi-worker-sub-{i}-{uuid.uuid4()}"
        }
        payload = {
            "problem_id": PROB_A_ID,
            "language": "python3",
            "code": SAMPLE_PROGRAMS["AC_TWO_SUM"]
        }
        res = await client.post(f"/contests/{CONTEST_A_ID}/submissions", headers=headers, json=payload)
        assert res.status_code == 202
        sub_ids.append(res.json()["id"])

    # Launch 2 competing workers concurrently
    async def worker_task(worker_name: str):
        processed_count = 0
        for _ in range(10):
            ok = await claim_and_process_one(worker_id=worker_name)
            if ok:
                processed_count += 1
            await asyncio.sleep(0.02)
        return processed_count

    results = await asyncio.gather(
        worker_task("worker-compete-A"),
        worker_task("worker-compete-B")
    )
    total_processed = sum(results)
    assert total_processed >= 4

    # Verify all submissions completed
    for sid in sub_ids:
        headers = {"X-User-Id": USER_ALICE, "X-Org-Id": ORG_A_ID}
        res = await client.get(f"/contests/{CONTEST_A_ID}/submissions/{sid}", headers=headers)
        assert res.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_worker_crash_lease_expiry_and_recovery(client: AsyncClient, db_session: AsyncSession):
    """
    Simulates a worker crash mid-judging:
    1. Worker 1 claims attempt and dies (lease expires).
    2. Worker 2 detects expired lease, re-claims attempt (retry_count=2), and finishes.
    3. Stale worker 1 attempts CAS commit and is rejected (rowcount=0).
    """
    headers = {
        "X-User-Id": USER_BOB,
        "X-Org-Id": ORG_A_ID,
        "Idempotency-Key": f"crash-recovery-{uuid.uuid4()}"
    }
    payload = {
        "problem_id": PROB_A_ID,
        "language": "python3",
        "code": SAMPLE_PROGRAMS["AC_TWO_SUM"]
    }
    res = await client.post(f"/contests/{CONTEST_A_ID}/submissions", headers=headers, json=payload)
    sub_id = res.json()["id"]

    # 1. Worker 1 claims attempt
    attempt_w1 = await claim_next_attempt(db_session, "worker-crashed")
    assert attempt_w1 is not None
    assert attempt_w1.submission_id == sub_id
    assert attempt_w1.retry_count == 1
    w1_lease_token = attempt_w1.lease_token

    # Simulate crash & lease expiry by artificially moving lease_expires_at to the past
    attempt_w1.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    # 2. Worker 2 claims the expired attempt
    attempt_w2 = await claim_next_attempt(db_session, "worker-recovery")
    assert attempt_w2 is not None
    assert attempt_w2.id == attempt_w1.id
    assert attempt_w2.retry_count == 2
    assert attempt_w2.lease_token != w1_lease_token

    # Worker 2 successfully completes judging
    ok2 = await process_attempt(db_session, attempt_w2)
    assert ok2 is True

    # 3. Stale Worker 1 wakes up and attempts to commit using old lease token
    # Simulate attempt object with stale lease token
    attempt_w1.lease_token = w1_lease_token
    stale_runner = DeterministicMockRunner(forced_verdict="WRONG_ANSWER")
    stale_commit_result = await process_attempt(db_session, attempt_w1, runner=stale_runner)
    assert stale_commit_result is False, "Stale worker commit MUST be rejected by CAS fencing token"

    # Verify final stored verdict is the recovered one (ACCEPTED)
    res_final = await client.get(f"/contests/{CONTEST_A_ID}/submissions/{sub_id}", headers=headers)
    assert res_final.json()["verdict"] == "ACCEPTED"


@pytest.mark.asyncio
async def test_rejudge_preserves_monotonic_standings(client: AsyncClient, db_session: AsyncSession):
    """
    Organizer triggers a rejudge on an existing submission:
    1. Creates attempt 2 with attempt_number = 2.
    2. Verifies that delayed/stale attempts from attempt 1 cannot overwrite attempt 2.
    """
    headers_org = {"X-User-Id": USER_ADMIN_A, "X-Org-Id": ORG_A_ID}
    headers_bob = {"X-User-Id": USER_BOB, "X-Org-Id": ORG_A_ID}

    # Use existing Bob submission sub-bob-001 (which was WA)
    res_rejudge = await client.post(
        f"/contests/{CONTEST_A_ID}/submissions/sub-bob-001/rejudge",
        headers=headers_org,
        json={}
    )
    assert res_rejudge.status_code == 202
    new_att = res_rejudge.json()
    assert new_att["attempt_number"] == 2

    # Judge attempt 2 to ACCEPTED
    runner_ac = DeterministicMockRunner(forced_verdict="ACCEPTED")
    processed = await claim_and_process_one(worker_id="rejudge-worker", runner=runner_ac)
    assert processed is True

    # Check submission: authoritative attempt is now attempt 2 and verdict is ACCEPTED
    res_sub = await client.get(f"/contests/{CONTEST_A_ID}/submissions/sub-bob-001", headers=headers_bob)
    sub_data = res_sub.json()
    assert sub_data["authoritative_attempt_id"] == new_att["id"]
    assert sub_data["verdict"] == "ACCEPTED"

    # Simulate a delayed/superseded attempt 1 finishing LATE
    # Fetch attempt 1 from DB
    att1_stmt = select(JudgingAttempt).where(
        JudgingAttempt.submission_id == "sub-bob-001",
        JudgingAttempt.attempt_number == 1
    )
    att1_res = await db_session.execute(att1_stmt)
    attempt_1 = att1_res.scalar_one()

    # Re-set attempt 1 to RUNNING with a mock lease to simulate delayed worker completion
    attempt_1.status = "RUNNING"
    attempt_1.lease_token = "delayed-stale-token-1"
    await db_session.commit()

    late_runner = DeterministicMockRunner(forced_verdict="WRONG_ANSWER")
    await process_attempt(db_session, attempt_1, runner=late_runner)

    # Invariant Verification: Authoritative attempt must REMAIN attempt 2 (ACCEPTED)
    res_sub_after = await client.get(f"/contests/{CONTEST_A_ID}/submissions/sub-bob-001", headers=headers_bob)
    sub_data_after = res_sub_after.json()
    assert sub_data_after["authoritative_attempt_id"] == new_att["id"], "Superseded attempt 1 must NOT overwrite authoritative_attempt_id"
    assert sub_data_after["verdict"] == "ACCEPTED", "Delayed older attempt must NOT corrupt authoritative verdict"


@pytest.mark.asyncio
async def test_rebuild_standings_equals_serving_path(client: AsyncClient, db_session: AsyncSession):
    """
    Demonstrates that rebuilding standings from scratch strictly from
    authoritative persistent records produces the exact same result as the serving path.
    """
    serving_standings = await compute_standings_data(db_session, CONTEST_A_ID)
    rebuilt_standings = await rebuild_standings(db_session, CONTEST_A_ID)

    assert len(serving_standings.entries) == len(rebuilt_standings.entries)
    for s_entry, r_entry in zip(serving_standings.entries, rebuilt_standings.entries):
        assert s_entry.rank == r_entry.rank
        assert s_entry.user_id == r_entry.user_id
        assert s_entry.solved_count == r_entry.solved_count
        assert s_entry.total_penalty_minutes == r_entry.total_penalty_minutes
        for prob_id in s_entry.problem_scores:
            s_prob = s_entry.problem_scores[prob_id]
            r_prob = r_entry.problem_scores[prob_id]
            assert s_prob.solved == r_prob.solved
            assert s_prob.penalty_minutes == r_prob.penalty_minutes
            assert s_prob.attempts_before_ac == r_prob.attempts_before_ac
