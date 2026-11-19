"""001_initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-11-18 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Organizations
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])

    # 2. Users
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    # 3. Org Memberships
    op.create_table(
        "org_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", name="uq_org_membership")
    )
    op.create_index("ix_org_memberships_org_id", "org_memberships", ["org_id"])
    op.create_index("ix_org_memberships_user_id", "org_memberships", ["user_id"])

    # 4. Contests
    op.create_table(
        "contests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("freeze_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_published", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "slug", name="uq_contest_org_slug")
    )
    op.create_index("ix_contests_org_id", "contests", ["org_id"])
    op.create_index("ix_contests_slug", "contests", ["slug"])

    # 5. Contest Participants
    op.create_table(
        "contest_participants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contest_id", sa.String(36), sa.ForeignKey("contests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("contest_id", "user_id", name="uq_contest_participant")
    )
    op.create_index("ix_contest_participants_contest_id", "contest_participants", ["contest_id"])
    op.create_index("ix_contest_participants_user_id", "contest_participants", ["user_id"])

    # 6. Problems
    op.create_table(
        "problems",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("active_problem_version_id", sa.String(36), nullable=True),
        sa.Column("active_suite_version_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "slug", name="uq_problem_org_slug")
    )
    op.create_index("ix_problems_org_id", "problems", ["org_id"])
    op.create_index("ix_problems_slug", "problems", ["slug"])

    # 7. Contest Problems
    op.create_table(
        "contest_problems",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contest_id", sa.String(36), sa.ForeignKey("contests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("letter", sa.String(10), nullable=False),
        sa.Column("order_index", sa.Integer(), default=0, nullable=False),
        sa.UniqueConstraint("contest_id", "problem_id", name="uq_contest_problem"),
        sa.UniqueConstraint("contest_id", "letter", name="uq_contest_problem_letter")
    )
    op.create_index("ix_contest_problems_contest_id", "contest_problems", ["contest_id"])
    op.create_index("ix_contest_problems_problem_id", "contest_problems", ["problem_id"])

    # 8. Problem Versions
    op.create_table(
        "problem_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("time_limit_ms", sa.Integer(), default=2000, nullable=False),
        sa.Column("memory_limit_mb", sa.Integer(), default=128, nullable=False),
        sa.Column("output_limit_bytes", sa.Integer(), default=65536, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("problem_id", "version_num", name="uq_problem_version")
    )
    op.create_index("ix_problem_versions_problem_id", "problem_versions", ["problem_id"])

    # 9. Test Suite Versions
    op.create_table(
        "test_suite_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("problem_id", "version_num", name="uq_test_suite_version")
    )
    op.create_index("ix_test_suite_versions_problem_id", "test_suite_versions", ["problem_id"])

    # 10. Test Cases
    op.create_table(
        "test_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("suite_version_id", sa.String(36), sa.ForeignKey("test_suite_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_data", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=False),
        sa.Column("is_hidden", sa.Boolean(), default=False, nullable=False),
        sa.UniqueConstraint("suite_version_id", "ordinal", name="uq_test_case_ordinal")
    )
    op.create_index("ix_test_cases_suite_version_id", "test_cases", ["suite_version_id"])

    # 11. Idempotency Records
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contest_id", sa.String(36), sa.ForeignKey("contests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("submission_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("contest_id", "user_id", "idempotency_key", name="uq_contest_user_idempotency")
    )
    op.create_index("ix_idempotency_records_contest_id", "idempotency_records", ["contest_id"])
    op.create_index("ix_idempotency_records_user_id", "idempotency_records", ["user_id"])
    op.create_index("ix_idempotency_records_idempotency_key", "idempotency_records", ["idempotency_key"])

    # 12. Submissions
    op.create_table(
        "submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contest_id", sa.String(36), sa.ForeignKey("contests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("problem_id", sa.String(36), sa.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("problem_version_id", sa.String(36), sa.ForeignKey("problem_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("test_suite_version_id", sa.String(36), sa.ForeignKey("test_suite_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("language", sa.String(50), default="python3", nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), default="QUEUED", nullable=False),
        sa.Column("verdict", sa.String(50), nullable=True),
        sa.Column("authoritative_attempt_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_submissions_contest_id", "submissions", ["contest_id"])
    op.create_index("ix_submissions_user_id", "submissions", ["user_id"])
    op.create_index("ix_submissions_problem_id", "submissions", ["problem_id"])
    op.create_index("idx_submission_contest_user", "submissions", ["contest_id", "user_id"])
    op.create_index("idx_submission_created_at", "submissions", ["created_at"])

    # 13. Judging Attempts
    op.create_table(
        "judging_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("submission_id", sa.String(36), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("problem_version_id", sa.String(36), sa.ForeignKey("problem_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("test_suite_version_id", sa.String(36), sa.ForeignKey("test_suite_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(50), default="QUEUED", nullable=False),
        sa.Column("verdict", sa.String(50), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("memory_used_kb", sa.Integer(), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), default=0, nullable=False),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("submission_id", "attempt_number", name="uq_submission_attempt_number")
    )
    op.create_index("ix_judging_attempts_submission_id", "judging_attempts", ["submission_id"])
    op.create_index("idx_attempt_queue", "judging_attempts", ["status", "lease_expires_at"])
    op.create_index("ix_judging_attempts_lease_token", "judging_attempts", ["lease_token"])

    # 14. Test Case Results
    op.create_table(
        "test_case_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("judging_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", sa.String(36), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("verdict", sa.String(50), nullable=False),
        sa.Column("execution_time_ms", sa.Integer(), default=0, nullable=False),
        sa.Column("actual_output", sa.Text(), nullable=True),
        sa.Column("is_hidden", sa.Boolean(), default=False, nullable=False),
        sa.UniqueConstraint("attempt_id", "ordinal", name="uq_attempt_test_case_ordinal")
    )
    op.create_index("ix_test_case_results_attempt_id", "test_case_results", ["attempt_id"])


def downgrade() -> None:
    op.drop_table("test_case_results")
    op.drop_table("judging_attempts")
    op.drop_table("submissions")
    op.drop_table("idempotency_records")
    op.drop_table("test_cases")
    op.drop_table("test_suite_versions")
    op.drop_table("problem_versions")
    op.drop_table("contest_problems")
    op.drop_table("problems")
    op.drop_table("contest_participants")
    op.drop_table("contests")
    op.drop_table("org_memberships")
    op.drop_table("users")
    op.drop_table("organizations")
