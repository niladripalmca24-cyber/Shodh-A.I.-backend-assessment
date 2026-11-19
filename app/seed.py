import asyncio
from datetime import datetime, timezone, timedelta
from app.database import AsyncSessionLocal, async_engine, Base
from app.models import (
    Organization, User, OrgMembership, Contest, ContestParticipant,
    Problem, ContestProblem, ProblemVersion, TestSuiteVersion,
    TestCase, Submission, JudgingAttempt, TestCaseResult
)

# Canonical Seed IDs
ORG_A_ID = "org-alpha-001"
ORG_B_ID = "org-beta-002"

USER_ADMIN_A = "user-org-admin-1"
USER_ALICE = "user-alice-101"
USER_BOB = "user-bob-102"
USER_CHARLIE = "user-charlie-103"

USER_ADMIN_B = "user-beta-admin-2"
USER_DAVID = "user-david-201"

CONTEST_A_ID = "contest-alpha-2026-1"
CONTEST_B_ID = "contest-beta-2026-1"

PROB_A_ID = "prob-two-sum-001"
PROB_B_ID = "prob-rev-string-002"
PROB_C_ID = "prob-even-odd-003"

# Canonical sample programs
SAMPLE_PROGRAMS = {
    "AC_TWO_SUM": "import sys\ndata = sys.stdin.read().split()\nif data:\n    print(int(data[0]) + int(data[1]))\n",
    "WA_TWO_SUM": "import sys\nprint(9999)\n",
    "RE_PROGRAM": "import sys\n1 / 0\n",
    "TLE_PROGRAM": "import time\ntime.sleep(10)\n",
    "OLE_PROGRAM": "import sys\nprint('A' * 200000)\n",
    "INFRA_PROGRAM": "# TRIGGER: INFRASTRUCTURE_ERROR\nimport sys\nprint('trigger')\n",
    "AC_REVERSE": "import sys\ns = sys.stdin.read().strip()\nprint(s[::-1])\n",
    "AC_EVEN_ODD": "import sys\nn = int(sys.stdin.read().strip())\nprint('EVEN' if n % 2 == 0 else 'ODD')\n"
}

async def seed_database():
    print("Beginning deterministic database seeding...")
    async with AsyncSessionLocal() as db:
        # Check if already seeded
        existing_org = await db.get(Organization, ORG_A_ID)
        if existing_org:
            print("Database already contains seed data. Skipping.")
            return

        now = datetime.now(timezone.utc)
        contest_start = now - timedelta(hours=2)
        contest_end = now + timedelta(days=30)

        # 1. Organizations
        org_a = Organization(id=ORG_A_ID, name="Alpha Institute of Technology", slug="alpha-tech")
        org_b = Organization(id=ORG_B_ID, name="Beta Engineering College", slug="beta-college")
        db.add_all([org_a, org_b])

        # 2. Users
        admin_a = User(id=USER_ADMIN_A, username="admin_alpha", email="admin@alpha.edu", full_name="Alpha Organizer")
        alice = User(id=USER_ALICE, username="alice", email="alice@alpha.edu", full_name="Alice Student")
        bob = User(id=USER_BOB, username="bob", email="bob@alpha.edu", full_name="Bob Student")
        charlie = User(id=USER_CHARLIE, username="charlie", email="charlie@alpha.edu", full_name="Charlie Student")

        admin_b = User(id=USER_ADMIN_B, username="admin_beta", email="admin@beta.edu", full_name="Beta Organizer")
        david = User(id=USER_DAVID, username="david", email="david@beta.edu", full_name="David Student")
        db.add_all([admin_a, alice, bob, charlie, admin_b, david])

        # 3. Memberships
        db.add_all([
            OrgMembership(org_id=ORG_A_ID, user_id=USER_ADMIN_A, role="ORGANIZER"),
            OrgMembership(org_id=ORG_A_ID, user_id=USER_ALICE, role="PARTICIPANT"),
            OrgMembership(org_id=ORG_A_ID, user_id=USER_BOB, role="PARTICIPANT"),
            OrgMembership(org_id=ORG_A_ID, user_id=USER_CHARLIE, role="PARTICIPANT"),
            OrgMembership(org_id=ORG_B_ID, user_id=USER_ADMIN_B, role="ORGANIZER"),
            OrgMembership(org_id=ORG_B_ID, user_id=USER_DAVID, role="PARTICIPANT"),
        ])

        # 4. Contests
        contest_a = Contest(
            id=CONTEST_A_ID,
            org_id=ORG_A_ID,
            title="Alpha Code Sprint 2026",
            slug="alpha-code-sprint-2026",
            starts_at=contest_start,
            ends_at=contest_end,
            is_published=True
        )
        contest_b = Contest(
            id=CONTEST_B_ID,
            org_id=ORG_B_ID,
            title="Beta Annual Hackathon",
            slug="beta-hackathon-2026",
            starts_at=contest_start,
            ends_at=contest_end,
            is_published=True
        )
        db.add_all([contest_a, contest_b])

        # 5. Contest Registrations
        db.add_all([
            ContestParticipant(contest_id=CONTEST_A_ID, user_id=USER_ALICE),
            ContestParticipant(contest_id=CONTEST_A_ID, user_id=USER_BOB),
            ContestParticipant(contest_id=CONTEST_A_ID, user_id=USER_CHARLIE),
            ContestParticipant(contest_id=CONTEST_B_ID, user_id=USER_DAVID),
        ])

        # 6. Problems & Versions for Contest A
        # Problem A: Two Sum
        prob_a = Problem(id=PROB_A_ID, org_id=ORG_A_ID, slug="two-sum")
        pv_a = ProblemVersion(
            id="pv-two-sum-v1",
            problem_id=PROB_A_ID,
            version_num=1,
            title="Two Sum Problem",
            statement="Read two whitespace-separated integers from stdin and print their sum.",
            time_limit_ms=2000,
            memory_limit_mb=128,
            output_limit_bytes=65536
        )
        tsv_a = TestSuiteVersion(id="tsv-two-sum-v1", problem_id=PROB_A_ID, version_num=1)
        tc_a1 = TestCase(suite_version_id=tsv_a.id, ordinal=1, input_data="2 3\n", expected_output="5", is_hidden=False)
        tc_a2 = TestCase(suite_version_id=tsv_a.id, ordinal=2, input_data="-10 5\n", expected_output="-5", is_hidden=False)
        tc_a3 = TestCase(suite_version_id=tsv_a.id, ordinal=3, input_data="1000000 2000000\n", expected_output="3000000", is_hidden=True)
        prob_a.active_problem_version_id = pv_a.id
        prob_a.active_suite_version_id = tsv_a.id

        # Problem B: Reverse String
        prob_b = Problem(id=PROB_B_ID, org_id=ORG_A_ID, slug="reverse-string")
        pv_b = ProblemVersion(
            id="pv-rev-string-v1",
            problem_id=PROB_B_ID,
            version_num=1,
            title="Reverse String",
            statement="Read a single line string from standard input and print its reversed characters.",
            time_limit_ms=2000,
            memory_limit_mb=128,
            output_limit_bytes=65536
        )
        tsv_b = TestSuiteVersion(id="tsv-rev-string-v1", problem_id=PROB_B_ID, version_num=1)
        tc_b1 = TestCase(suite_version_id=tsv_b.id, ordinal=1, input_data="hello\n", expected_output="olleh", is_hidden=False)
        tc_b2 = TestCase(suite_version_id=tsv_b.id, ordinal=2, input_data="ShodhACode\n", expected_output="edoCAhdohS", is_hidden=True)
        prob_b.active_problem_version_id = pv_b.id
        prob_b.active_suite_version_id = tsv_b.id

        # Problem C: Even or Odd
        prob_c = Problem(id=PROB_C_ID, org_id=ORG_A_ID, slug="even-or-odd")
        pv_c = ProblemVersion(
            id="pv-even-odd-v1",
            problem_id=PROB_C_ID,
            version_num=1,
            title="Even or Odd Number",
            statement="Read an integer N from standard input. Print 'EVEN' if even, or 'ODD' if odd.",
            time_limit_ms=2000,
            memory_limit_mb=128,
            output_limit_bytes=65536
        )
        tsv_c = TestSuiteVersion(id="tsv-even-odd-v1", problem_id=PROB_C_ID, version_num=1)
        tc_c1 = TestCase(suite_version_id=tsv_c.id, ordinal=1, input_data="4\n", expected_output="EVEN", is_hidden=False)
        tc_c2 = TestCase(suite_version_id=tsv_c.id, ordinal=2, input_data="7\n", expected_output="ODD", is_hidden=True)
        prob_c.active_problem_version_id = pv_c.id
        prob_c.active_suite_version_id = tsv_c.id

        db.add_all([
            prob_a, pv_a, tsv_a, tc_a1, tc_a2, tc_a3,
            prob_b, pv_b, tsv_b, tc_b1, tc_b2,
            prob_c, pv_c, tsv_c, tc_c1, tc_c2
        ])

        # Contest Problems linking
        db.add_all([
            ContestProblem(contest_id=CONTEST_A_ID, problem_id=PROB_A_ID, letter="A", order_index=1),
            ContestProblem(contest_id=CONTEST_A_ID, problem_id=PROB_B_ID, letter="B", order_index=2),
            ContestProblem(contest_id=CONTEST_A_ID, problem_id=PROB_C_ID, letter="C", order_index=3),
        ])

        # 7. Historical Submissions & Judging Attempts
        # Alice: AC on Problem A at minute 15
        sub_alice = Submission(
            id="sub-alice-001",
            contest_id=CONTEST_A_ID,
            user_id=USER_ALICE,
            problem_id=PROB_A_ID,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            language="python3",
            code=SAMPLE_PROGRAMS["AC_TWO_SUM"],
            status="COMPLETED",
            verdict="ACCEPTED",
            created_at=contest_start + timedelta(minutes=15)
        )
        att_alice = JudgingAttempt(
            id="att-alice-001",
            submission_id=sub_alice.id,
            attempt_number=1,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            status="COMPLETED",
            verdict="ACCEPTED",
            execution_time_ms=45,
            memory_used_kb=2048,
            created_at=contest_start + timedelta(minutes=15),
            completed_at=contest_start + timedelta(minutes=15, seconds=2)
        )
        sub_alice.authoritative_attempt_id = att_alice.id

        # Bob: WA on Problem A at minute 10, then AC on Problem A at minute 25 (+20 min penalty)
        sub_bob_1 = Submission(
            id="sub-bob-001",
            contest_id=CONTEST_A_ID,
            user_id=USER_BOB,
            problem_id=PROB_A_ID,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            language="python3",
            code=SAMPLE_PROGRAMS["WA_TWO_SUM"],
            status="COMPLETED",
            verdict="WRONG_ANSWER",
            created_at=contest_start + timedelta(minutes=10)
        )
        att_bob_1 = JudgingAttempt(
            id="att-bob-001",
            submission_id=sub_bob_1.id,
            attempt_number=1,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            status="COMPLETED",
            verdict="WRONG_ANSWER",
            execution_time_ms=30,
            created_at=contest_start + timedelta(minutes=10),
            completed_at=contest_start + timedelta(minutes=10, seconds=1)
        )
        sub_bob_1.authoritative_attempt_id = att_bob_1.id

        sub_bob_2 = Submission(
            id="sub-bob-002",
            contest_id=CONTEST_A_ID,
            user_id=USER_BOB,
            problem_id=PROB_A_ID,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            language="python3",
            code=SAMPLE_PROGRAMS["AC_TWO_SUM"],
            status="COMPLETED",
            verdict="ACCEPTED",
            created_at=contest_start + timedelta(minutes=25)
        )
        att_bob_2 = JudgingAttempt(
            id="att-bob-002",
            submission_id=sub_bob_2.id,
            attempt_number=1,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            status="COMPLETED",
            verdict="ACCEPTED",
            execution_time_ms=40,
            created_at=contest_start + timedelta(minutes=25),
            completed_at=contest_start + timedelta(minutes=25, seconds=2)
        )
        sub_bob_2.authoritative_attempt_id = att_bob_2.id

        # Charlie: RE on Problem A at minute 30
        sub_charlie_1 = Submission(
            id="sub-charlie-001",
            contest_id=CONTEST_A_ID,
            user_id=USER_CHARLIE,
            problem_id=PROB_A_ID,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            language="python3",
            code=SAMPLE_PROGRAMS["RE_PROGRAM"],
            status="COMPLETED",
            verdict="RUNTIME_ERROR",
            created_at=contest_start + timedelta(minutes=30)
        )
        att_charlie_1 = JudgingAttempt(
            id="att-charlie-001",
            submission_id=sub_charlie_1.id,
            attempt_number=1,
            problem_version_id=pv_a.id,
            test_suite_version_id=tsv_a.id,
            status="COMPLETED",
            verdict="RUNTIME_ERROR",
            created_at=contest_start + timedelta(minutes=30),
            completed_at=contest_start + timedelta(minutes=30, seconds=1)
        )
        sub_charlie_1.authoritative_attempt_id = att_charlie_1.id

        db.add_all([
            sub_alice, att_alice,
            sub_bob_1, att_bob_1,
            sub_bob_2, att_bob_2,
            sub_charlie_1, att_charlie_1
        ])

        await db.commit()
        print("Deterministic seed bundle successfully created!")

if __name__ == "__main__":
    asyncio.run(seed_database())
