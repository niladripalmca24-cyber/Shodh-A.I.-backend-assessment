import pytest
from httpx import AsyncClient
from app.seed import (
    ORG_A_ID, ORG_B_ID, USER_ADMIN_A, USER_ALICE, USER_BOB,
    USER_CHARLIE, USER_DAVID, CONTEST_A_ID, CONTEST_B_ID,
    PROB_A_ID, PROB_B_ID, SAMPLE_PROGRAMS
)
from app.worker import claim_and_process_one

@pytest.mark.asyncio
async def test_contest_listing_and_detail(client: AsyncClient):
    headers = {"X-User-Id": USER_ALICE, "X-Org-Id": ORG_A_ID}
    response = await client.get("/contests", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) >= 1
    assert data["items"][0]["id"] == CONTEST_A_ID

    # Detail
    res_detail = await client.get(f"/contests/{CONTEST_A_ID}", headers=headers)
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert detail_data["id"] == CONTEST_A_ID
    assert len(detail_data["problems"]) == 3
    assert detail_data["problems"][0]["letter"] == "A"


@pytest.mark.asyncio
async def test_problem_hidden_test_redaction(client: AsyncClient):
    headers = {"X-User-Id": USER_ALICE, "X-Org-Id": ORG_A_ID}
    res = await client.get(f"/contests/{CONTEST_A_ID}/problems/{PROB_A_ID}", headers=headers)
    assert res.status_code == 200
    p_data = res.json()
    assert p_data["id"] == PROB_A_ID
    assert len(p_data["sample_test_cases"]) == 2  # Only 2 public cases, 1 hidden case redacted
    for tc in p_data["sample_test_cases"]:
        assert tc["is_hidden"] is False
        assert tc["input_data"] is not None
        assert tc["expected_output"] is not None


@pytest.mark.asyncio
async def test_end_to_end_submission_and_judge(client: AsyncClient):
    headers = {
        "X-User-Id": USER_ALICE,
        "X-Org-Id": ORG_A_ID,
        "Idempotency-Key": "test-alice-sub-new-1"
    }
    payload = {
        "problem_id": PROB_A_ID,
        "language": "python3",
        "code": SAMPLE_PROGRAMS["AC_TWO_SUM"]
    }
    # 1. Submit
    res = await client.post(f"/contests/{CONTEST_A_ID}/submissions", headers=headers, json=payload)
    assert res.status_code == 202
    sub = res.json()
    assert sub["status"] == "QUEUED"
    sub_id = sub["id"]

    # 2. Worker executes judging
    processed = await claim_and_process_one(worker_id="test-worker-1")
    assert processed is True

    # 3. Poll submission status
    res_check = await client.get(f"/contests/{CONTEST_A_ID}/submissions/{sub_id}", headers=headers)
    assert res_check.status_code == 200
    res_data = res_check.json()
    assert res_data["status"] == "COMPLETED"
    assert res_data["verdict"] == "ACCEPTED"
    assert len(res_data["attempts"]) == 1
    assert len(res_data["attempts"][0]["test_case_results"]) == 3


@pytest.mark.asyncio
async def test_verdict_taxonomy_wa_re_tle_ole(client: AsyncClient):
    """Verifies that WA, RE, TLE, and OLE are correctly diagnosed by the judge."""
    test_cases = [
        ("WA", SAMPLE_PROGRAMS["WA_TWO_SUM"], "WRONG_ANSWER"),
        ("RE", SAMPLE_PROGRAMS["RE_PROGRAM"], "RUNTIME_ERROR"),
        ("TLE", "# TRIGGER: TLE\nimport sys\n", "TIME_LIMIT_EXCEEDED"),
        ("OLE", "# TRIGGER: OLE\nimport sys\n", "OUTPUT_LIMIT_EXCEEDED"),
        ("INFRA", SAMPLE_PROGRAMS["INFRA_PROGRAM"], "INFRASTRUCTURE_ERROR"),
    ]

    for key, code, expected_verdict in test_cases:
        headers = {
            "X-User-Id": USER_CHARLIE,
            "X-Org-Id": ORG_A_ID,
            "Idempotency-Key": f"test-sub-tax-{key}"
        }
        payload = {
            "problem_id": PROB_A_ID,
            "language": "python3",
            "code": code
        }
        res = await client.post(f"/contests/{CONTEST_A_ID}/submissions", headers=headers, json=payload)
        assert res.status_code == 202
        sub_id = res.json()["id"]

        # Run worker
        processed = await claim_and_process_one(worker_id="test-worker-tax")
        assert processed is True

        res_check = await client.get(f"/contests/{CONTEST_A_ID}/submissions/{sub_id}", headers=headers)
        data = res_check.json()
        assert data["verdict"] == expected_verdict


@pytest.mark.asyncio
async def test_icpc_leaderboard_standings(client: AsyncClient):
    headers = {"X-User-Id": USER_ALICE, "X-Org-Id": ORG_A_ID}
    res = await client.get(f"/contests/{CONTEST_A_ID}/leaderboard", headers=headers)
    assert res.status_code == 200
    board = res.json()
    assert len(board["entries"]) >= 2

    # Alice solved Problem A at min 15 (0 failed attempts before AC) -> penalty = 15
    alice_entry = next(e for e in board["entries"] if e["user_id"] == USER_ALICE)
    assert alice_entry["solved_count"] >= 1
    assert alice_entry["rank"] == 1

    # Bob solved Problem A at min 25 with 1 failed attempt before AC (10 min WA) -> penalty = 25 + 20 = 45
    bob_entry = next(e for e in board["entries"] if e["user_id"] == USER_BOB)
    assert bob_entry["solved_count"] == 1
    assert bob_entry["total_penalty_minutes"] == 45
    assert bob_entry["rank"] > alice_entry["rank"]


@pytest.mark.asyncio
async def test_security_cross_organization_denial(client: AsyncClient):
    # David belongs to Org B. Attempting to access Contest A (Org A)
    headers = {"X-User-Id": USER_DAVID, "X-Org-Id": ORG_B_ID}
    res = await client.get(f"/contests/{CONTEST_A_ID}", headers=headers)
    assert res.status_code == 404

    # David attempting to submit to Contest A
    payload = {"problem_id": PROB_A_ID, "language": "python3", "code": "print(1)"}
    res_sub = await client.post(f"/contests/{CONTEST_A_ID}/submissions", headers=headers, json=payload)
    assert res_sub.status_code == 404


@pytest.mark.asyncio
async def test_security_role_authorization_and_privacy(client: AsyncClient):
    # Participant Alice attempting organizer rejudge action -> 403 Forbidden
    headers_alice = {"X-User-Id": USER_ALICE, "X-Org-Id": ORG_A_ID}
    res_rejudge = await client.post(
        f"/contests/{CONTEST_A_ID}/submissions/sub-bob-001/rejudge",
        headers=headers_alice,
        json={}
    )
    assert res_rejudge.status_code == 403

    # Alice attempting to inspect Bob's private submission -> 403 Forbidden
    res_inspect = await client.get(
        f"/contests/{CONTEST_A_ID}/submissions/sub-bob-001",
        headers=headers_alice
    )
    assert res_inspect.status_code == 403

    # Organizer Admin A inspecting Bob's submission -> 200 OK
    headers_admin = {"X-User-Id": USER_ADMIN_A, "X-Org-Id": ORG_A_ID}
    res_admin_inspect = await client.get(
        f"/contests/{CONTEST_A_ID}/submissions/sub-bob-001",
        headers=headers_admin
    )
    assert res_admin_inspect.status_code == 200
