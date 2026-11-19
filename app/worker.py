import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    JudgingAttempt, Submission, ProblemVersion, TestSuiteVersion,
    TestCase, TestCaseResult
)
from app.runner import (
    BaseRunner, get_runner, TestCaseInput
)

logger = logging.getLogger("shodh.worker")
logging.basicConfig(level=logging.INFO, format="%(message)s")

def log_structured(event: str, **kwargs):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **kwargs
    }
    # Security: Ensure code or hidden test data is never leaked in logs
    if "code" in payload:
        payload["code"] = "[REDACTED]"
    if "hidden_data" in payload:
        payload["hidden_data"] = "[REDACTED]"
    logger.info(json.dumps(payload))


async def claim_next_attempt(db: AsyncSession, worker_id: str) -> Optional[JudgingAttempt]:
    """
    Atomically claims the next pending or expired attempt using SELECT ... FOR UPDATE SKIP LOCKED.
    Fences concurrent workers with a new UUID lease_token and bounded retry count.
    """
    now = datetime.now(timezone.utc)
    
    # Check if dialect supports SKIP LOCKED (e.g. Postgres)
    dialect_name = db.bind.dialect.name if db.bind else "postgresql"
    
    stmt = (
        select(JudgingAttempt)
        .where(
            (JudgingAttempt.status == "QUEUED") |
            ((JudgingAttempt.status == "RUNNING") & (JudgingAttempt.lease_expires_at < now))
        )
        .order_by(JudgingAttempt.created_at.asc())
        .limit(1)
    )

    if dialect_name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt:
        return None

    # Check bounded retries
    if attempt.retry_count >= settings.MAX_JUDGE_RETRIES:
        # Mark attempt permanently failed due to repeated crash/timeout
        attempt.status = "FAILED"
        attempt.verdict = "INFRASTRUCTURE_ERROR"
        attempt.error_message = f"Exceeded maximum retries ({settings.MAX_JUDGE_RETRIES}) without completion"
        attempt.completed_at = now

        # Also update parent submission status to prevent permanently stuck job
        sub_res = await db.execute(select(Submission).where(Submission.id == attempt.submission_id))
        sub = sub_res.scalar_one_or_none()
        if sub and sub.status in ("QUEUED", "RUNNING"):
            sub.status = "FAILED"
            sub.verdict = "INFRASTRUCTURE_ERROR"

        await db.commit()
        log_structured(
            "attempt_max_retries_exceeded",
            attempt_id=attempt.id,
            submission_id=attempt.submission_id,
            retry_count=attempt.retry_count,
            worker_id=worker_id
        )
        return None

    # Assign lease and fencing token
    new_lease_token = str(uuid.uuid4())
    lease_expires = now + timedelta(seconds=settings.LEASE_TIMEOUT_SECONDS)

    attempt.status = "RUNNING"
    attempt.lease_token = new_lease_token
    attempt.lease_expires_at = lease_expires
    attempt.retry_count += 1
    attempt.worker_id = worker_id

    await db.commit()
    await db.refresh(attempt)

    log_structured(
        "attempt_claimed",
        attempt_id=attempt.id,
        submission_id=attempt.submission_id,
        attempt_number=attempt.attempt_number,
        lease_token=new_lease_token,
        retry_count=attempt.retry_count,
        worker_id=worker_id
    )

    return attempt


async def process_attempt(
    db: AsyncSession,
    attempt: JudgingAttempt,
    runner: Optional[BaseRunner] = None
) -> bool:
    """
    Executes an attempt against the configured runner and safely commits verdict using CAS fencing.
    """
    start_monotonic = datetime.now(timezone.utc)
    attempt_id = attempt.id
    lease_token = attempt.lease_token
    submission_id = attempt.submission_id
    worker_id = attempt.worker_id or settings.WORKER_ID

    active_runner = runner or get_runner()

    # 1. Fetch submission details and code
    sub_res = await db.execute(select(Submission).where(Submission.id == submission_id))
    submission = sub_res.scalar_one_or_none()
    if not submission:
        log_structured("submission_missing_error", attempt_id=attempt_id, submission_id=submission_id)
        return False

    # 2. Fetch Problem Version Limits
    pv_res = await db.execute(
        select(ProblemVersion).where(ProblemVersion.id == attempt.problem_version_id)
    )
    pv = pv_res.scalar_one_or_none()
    time_limit_ms = pv.time_limit_ms if pv else settings.DEFAULT_TIME_LIMIT_MS
    memory_limit_mb = pv.memory_limit_mb if pv else settings.DEFAULT_MEMORY_LIMIT_MB
    output_limit_bytes = pv.output_limit_bytes if pv else settings.DEFAULT_OUTPUT_LIMIT_BYTES

    # 3. Fetch Test Cases for the version
    tc_stmt = (
        select(TestCase)
        .where(TestCase.suite_version_id == attempt.test_suite_version_id)
        .order_by(TestCase.ordinal.asc())
    )
    tc_res = await db.execute(tc_stmt)
    test_cases_db = tc_res.scalars().all()

    tc_inputs = [
        TestCaseInput(
            ordinal=tc.ordinal,
            input_data=tc.input_data,
            expected_output=tc.expected_output,
            is_hidden=tc.is_hidden
        )
        for tc in test_cases_db
    ]

    # Map for saving results later
    tc_db_map = {tc.ordinal: tc.id for tc in test_cases_db}

    # 4. Execute submission in Sandbox Runner
    try:
        runner_result = active_runner.run_submission(
            code=submission.code,
            language=submission.language,
            test_cases=tc_inputs,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            output_limit_bytes=output_limit_bytes
        )
    except Exception as ex:
        log_structured(
            "runner_execution_exception",
            attempt_id=attempt_id,
            error=str(ex),
            worker_id=worker_id
        )
        # Transient worker failure: lease will expire or can be retried
        return False

    now_utc = datetime.now(timezone.utc)
    duration_ms = int((now_utc - start_monotonic).total_seconds() * 1000)

    # 5. CAS (Compare-And-Swap) Update on JudgingAttempt
    # Ensures a slow or replaced worker cannot overwrite a renewed or reassigned attempt
    cas_stmt = (
        update(JudgingAttempt)
        .where(
            JudgingAttempt.id == attempt_id,
            JudgingAttempt.lease_token == lease_token,
            JudgingAttempt.status == "RUNNING"
        )
        .values(
            status="COMPLETED" if runner_result.verdict != "INFRASTRUCTURE_ERROR" else "FAILED",
            verdict=runner_result.verdict,
            execution_time_ms=runner_result.execution_time_ms,
            memory_used_kb=runner_result.memory_used_kb,
            error_message=runner_result.error_message,
            completed_at=now_utc
        )
    )
    res = await db.execute(cas_stmt)
    if res.rowcount == 0:
        log_structured(
            "cas_fencing_aborted_stale_worker",
            attempt_id=attempt_id,
            lease_token=lease_token,
            worker_id=worker_id,
            reason="Lease expired, renewed, or reassigned to another worker"
        )
        await db.rollback()
        return False

    # 6. Insert Test Case Results
    for tc_out in runner_result.test_results:
        tc_id = tc_db_map.get(tc_out.ordinal)
        if tc_id:
            tc_result_record = TestCaseResult(
                attempt_id=attempt_id,
                test_case_id=tc_id,
                ordinal=tc_out.ordinal,
                passed=tc_out.passed,
                verdict=tc_out.verdict,
                execution_time_ms=tc_out.execution_time_ms,
                actual_output=tc_out.actual_output,
                is_hidden=tc_out.is_hidden
            )
            db.add(tc_result_record)

    # 7. Update Submission Authoritative Attempt & Verdict
    # Invariant: Only update if attempt_number >= current_authoritative_attempt_number
    # and verdict is NOT INFRASTRUCTURE_ERROR
    sub_curr_stmt = select(Submission).where(Submission.id == submission_id)
    sub_curr_res = await db.execute(sub_curr_stmt)
    fresh_sub = sub_curr_res.scalar_one_or_none()

    if fresh_sub and runner_result.verdict != "INFRASTRUCTURE_ERROR":
        # Check current authoritative attempt number if exists
        current_auth_num = 0
        if fresh_sub.authoritative_attempt_id:
            auth_stmt = select(JudgingAttempt.attempt_number).where(
                JudgingAttempt.id == fresh_sub.authoritative_attempt_id
            )
            auth_res = await db.execute(auth_stmt)
            c_num = auth_res.scalar_one_or_none()
            if c_num is not None:
                current_auth_num = c_num

        if attempt.attempt_number >= current_auth_num:
            fresh_sub.authoritative_attempt_id = attempt_id
            fresh_sub.verdict = runner_result.verdict
            fresh_sub.status = "COMPLETED"

    await db.commit()

    log_structured(
        "attempt_completed",
        attempt_id=attempt_id,
        submission_id=submission_id,
        attempt_number=attempt.attempt_number,
        verdict=runner_result.verdict,
        duration_ms=duration_ms,
        execution_mode=runner_result.execution_mode,
        worker_id=worker_id
    )

    return True


async def claim_and_process_one(
    worker_id: Optional[str] = None,
    runner: Optional[BaseRunner] = None
) -> bool:
    """Claims and executes a single attempt from the queue (convenient for loops and test harnesses)."""
    wid = worker_id or settings.WORKER_ID
    async with AsyncSessionLocal() as db:
        attempt = await claim_next_attempt(db, wid)
        if not attempt:
            return False
        return await process_attempt(db, attempt, runner=runner)


async def run_worker_loop(worker_id: Optional[str] = None):
    """Continuous polling loop for judge worker."""
    wid = worker_id or settings.WORKER_ID
    log_structured("worker_started", worker_id=wid, runner_mode=settings.RUNNER_MODE)

    while True:
        try:
            processed = await claim_and_process_one(worker_id=wid)
            if not processed:
                await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            log_structured("worker_stopping", worker_id=wid)
            break
        except Exception as e:
            log_structured("worker_uncaught_error", worker_id=wid, error=str(e))
            await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    import sys
    wid = sys.argv[1] if len(sys.argv) > 1 else settings.WORKER_ID
    asyncio.run(run_worker_loop(wid))
