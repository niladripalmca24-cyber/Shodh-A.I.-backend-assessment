import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Integer, Boolean, DateTime, ForeignKey,
    UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from app.database import Base

def generate_uuid() -> str:
    return str(uuid.uuid4())

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    memberships = relationship("OrgMembership", back_populates="organization", cascade="all, delete-orphan")
    contests = relationship("Contest", back_populates="organization", cascade="all, delete-orphan")
    problems = relationship("Problem", back_populates="organization", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    username = Column(String(100), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    full_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    memberships = relationship("OrgMembership", back_populates="user", cascade="all, delete-orphan")
    contest_participations = relationship("ContestParticipant", back_populates="user", cascade="all, delete-orphan")
    submissions = relationship("Submission", back_populates="user")


class OrgMembership(Base):
    __tablename__ = "org_memberships"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    org_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(50), nullable=False)  # 'ORGANIZER', 'PARTICIPANT'
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization = relationship("Organization", back_populates="memberships")
    user = relationship("User", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_membership"),
    )


class Contest(Base):
    __tablename__ = "contests"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    org_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, index=True)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    freeze_at = Column(DateTime(timezone=True), nullable=True)
    is_published = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization = relationship("Organization", back_populates="contests")
    participants = relationship("ContestParticipant", back_populates="contest", cascade="all, delete-orphan")
    problems = relationship("ContestProblem", back_populates="contest", cascade="all, delete-orphan")
    submissions = relationship("Submission", back_populates="contest", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_contest_org_slug"),
    )


class ContestParticipant(Base):
    __tablename__ = "contest_participants"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    contest_id = Column(String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    registered_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    contest = relationship("Contest", back_populates="participants")
    user = relationship("User", back_populates="contest_participations")

    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", name="uq_contest_participant"),
    )


class Problem(Base):
    __tablename__ = "problems"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    org_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    slug = Column(String(100), nullable=False, index=True)
    active_problem_version_id = Column(String(36), nullable=True)
    active_suite_version_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization = relationship("Organization", back_populates="problems")
    versions = relationship("ProblemVersion", back_populates="problem", cascade="all, delete-orphan")
    test_suites = relationship("TestSuiteVersion", back_populates="problem", cascade="all, delete-orphan")
    contest_problems = relationship("ContestProblem", back_populates="problem")
    submissions = relationship("Submission", back_populates="problem")

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_problem_org_slug"),
    )


class ContestProblem(Base):
    __tablename__ = "contest_problems"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    contest_id = Column(String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True)
    problem_id = Column(String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    letter = Column(String(10), nullable=False)  # e.g. "A", "B", "C"
    order_index = Column(Integer, default=0, nullable=False)

    contest = relationship("Contest", back_populates="problems")
    problem = relationship("Problem", back_populates="contest_problems")

    __table_args__ = (
        UniqueConstraint("contest_id", "problem_id", name="uq_contest_problem"),
        UniqueConstraint("contest_id", "letter", name="uq_contest_problem_letter"),
    )


class ProblemVersion(Base):
    __tablename__ = "problem_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    problem_id = Column(String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    version_num = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    statement = Column(Text, nullable=False)
    time_limit_ms = Column(Integer, default=2000, nullable=False)
    memory_limit_mb = Column(Integer, default=128, nullable=False)
    output_limit_bytes = Column(Integer, default=65536, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    problem = relationship("Problem", back_populates="versions")

    __table_args__ = (
        UniqueConstraint("problem_id", "version_num", name="uq_problem_version"),
    )


class TestSuiteVersion(Base):
    __tablename__ = "test_suite_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    problem_id = Column(String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    version_num = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    problem = relationship("Problem", back_populates="test_suites")
    test_cases = relationship("TestCase", back_populates="suite_version", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("problem_id", "version_num", name="uq_test_suite_version"),
    )


class TestCase(Base):
    __tablename__ = "test_cases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    suite_version_id = Column(String(36), ForeignKey("test_suite_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal = Column(Integer, nullable=False)
    input_data = Column(Text, nullable=False)
    expected_output = Column(Text, nullable=False)
    is_hidden = Column(Boolean, default=False, nullable=False)

    suite_version = relationship("TestSuiteVersion", back_populates="test_cases")

    __table_args__ = (
        UniqueConstraint("suite_version_id", "ordinal", name="uq_test_case_ordinal"),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    contest_id = Column(String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key = Column(String(255), nullable=False, index=True)
    payload_sha256 = Column(String(64), nullable=False)
    submission_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", "idempotency_key", name="uq_contest_user_idempotency"),
    )


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    contest_id = Column(String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    problem_id = Column(String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True)
    problem_version_id = Column(String(36), ForeignKey("problem_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    test_suite_version_id = Column(String(36), ForeignKey("test_suite_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    language = Column(String(50), default="python3", nullable=False)
    code = Column(Text, nullable=False)
    status = Column(String(50), default="QUEUED", nullable=False)  # QUEUED, RUNNING, COMPLETED, FAILED
    verdict = Column(String(50), nullable=True)  # ACCEPTED, WRONG_ANSWER, etc.
    authoritative_attempt_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    contest = relationship("Contest", back_populates="submissions")
    user = relationship("User", back_populates="submissions")
    problem = relationship("Problem", back_populates="submissions")
    problem_version = relationship("ProblemVersion")
    test_suite_version = relationship("TestSuiteVersion")
    attempts = relationship("JudgingAttempt", back_populates="submission", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_submission_contest_user", "contest_id", "user_id"),
        Index("idx_submission_created_at", "created_at"),
    )


class JudgingAttempt(Base):
    __tablename__ = "judging_attempts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    submission_id = Column(String(36), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    problem_version_id = Column(String(36), ForeignKey("problem_versions.id", ondelete="RESTRICT"), nullable=False)
    test_suite_version_id = Column(String(36), ForeignKey("test_suite_versions.id", ondelete="RESTRICT"), nullable=False)
    status = Column(String(50), default="QUEUED", nullable=False)  # QUEUED, RUNNING, COMPLETED, FAILED
    verdict = Column(String(50), nullable=True)  # ACCEPTED, WRONG_ANSWER, RUNTIME_ERROR, etc.
    execution_time_ms = Column(Integer, nullable=True)
    memory_used_kb = Column(Integer, nullable=True)
    lease_token = Column(String(64), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    worker_id = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    submission = relationship("Submission", back_populates="attempts")
    test_case_results = relationship("TestCaseResult", back_populates="attempt", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("submission_id", "attempt_number", name="uq_submission_attempt_number"),
        Index("idx_attempt_queue", "status", "lease_expires_at"),
    )


class TestCaseResult(Base):
    __tablename__ = "test_case_results"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    attempt_id = Column(String(36), ForeignKey("judging_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    test_case_id = Column(String(36), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    passed = Column(Boolean, nullable=False)
    verdict = Column(String(50), nullable=False)
    execution_time_ms = Column(Integer, default=0, nullable=False)
    actual_output = Column(Text, nullable=True)
    is_hidden = Column(Boolean, default=False, nullable=False)

    attempt = relationship("JudgingAttempt", back_populates="test_case_results")

    __table_args__ = (
        UniqueConstraint("attempt_id", "ordinal", name="uq_attempt_test_case_ordinal"),
    )
