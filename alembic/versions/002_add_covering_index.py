"""002_add_covering_index

Revision ID: 002_add_covering_index
Revises: 001_initial_schema
Create Date: 2026-11-18 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "002_add_covering_index"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Optimized covering index for high-concurrency leaderboard queries (Stage 4 bottleneck optimization)
    # Allows Index-Only Scan on submissions by contest_id, avoiding sequential table scans
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect == "postgresql":
        op.execute("""
            CREATE INDEX idx_sub_contest_auth 
            ON submissions (contest_id, user_id, authoritative_attempt_id) 
            INCLUDE (problem_version_id, created_at) 
            WHERE authoritative_attempt_id IS NOT NULL;
        """)
    else:
        # SQLite / generic fallback
        op.execute("""
            CREATE INDEX idx_sub_contest_auth 
            ON submissions (contest_id, user_id, authoritative_attempt_id, problem_version_id, created_at) 
            WHERE authoritative_attempt_id IS NOT NULL;
        """)


def downgrade() -> None:
    op.drop_index("idx_sub_contest_auth", table_name="submissions")
