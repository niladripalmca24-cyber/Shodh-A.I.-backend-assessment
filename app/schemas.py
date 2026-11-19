from datetime import datetime
from typing import List, Optional, Any, Dict, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

# Pagination
class PaginationMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool

class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    meta: PaginationMeta

# Auth & User
class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: str
    created_at: datetime

    class Config:
        from_attributes = True

class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime

    class Config:
        from_attributes = True

# Problems & Test Cases
class TestCasePublicResponse(BaseModel):
    ordinal: int
    input_data: Optional[str] = None  # None if hidden
    expected_output: Optional[str] = None  # None if hidden
    is_hidden: bool

    class Config:
        from_attributes = True

class ProblemVersionResponse(BaseModel):
    id: str
    version_num: int
    title: str
    statement: str
    time_limit_ms: int
    memory_limit_mb: int
    output_limit_bytes: int
    created_at: datetime

    class Config:
        from_attributes = True

class ProblemSummaryResponse(BaseModel):
    id: str
    slug: str
    title: str
    letter: Optional[str] = None
    created_at: datetime

class ProblemDetailResponse(BaseModel):
    id: str
    slug: str
    title: str
    statement: str
    time_limit_ms: int
    memory_limit_mb: int
    output_limit_bytes: int
    active_problem_version_id: Optional[str]
    active_suite_version_id: Optional[str]
    sample_test_cases: List[TestCasePublicResponse] = []

# Contests
class ContestResponse(BaseModel):
    id: str
    org_id: str
    title: str
    slug: str
    starts_at: datetime
    ends_at: datetime
    freeze_at: Optional[datetime] = None
    is_published: bool
    created_at: datetime

    class Config:
        from_attributes = True

class ContestDetailResponse(ContestResponse):
    problems: List[ProblemSummaryResponse] = []

class ContestParticipantResponse(BaseModel):
    contest_id: str
    user_id: str
    registered_at: datetime

    class Config:
        from_attributes = True

# Submissions & Attempts
class SubmissionCreate(BaseModel):
    problem_id: str
    language: str = "python3"
    code: str = Field(..., max_length=65536)

class TestCaseResultResponse(BaseModel):
    ordinal: int
    passed: bool
    verdict: str
    execution_time_ms: int
    actual_output: Optional[str] = None  # None if hidden test case or non-organizer
    is_hidden: bool

    class Config:
        from_attributes = True

class JudgingAttemptResponse(BaseModel):
    id: str
    attempt_number: int
    problem_version_id: str
    test_suite_version_id: str
    status: str
    verdict: Optional[str]
    execution_time_ms: Optional[int]
    memory_used_kb: Optional[int]
    retry_count: int
    created_at: datetime
    completed_at: Optional[datetime]
    test_case_results: List[TestCaseResultResponse] = []

    class Config:
        from_attributes = True

class SubmissionResponse(BaseModel):
    id: str
    contest_id: str
    user_id: str
    problem_id: str
    problem_version_id: str
    test_suite_version_id: str
    language: str
    status: str
    verdict: Optional[str]
    authoritative_attempt_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class SubmissionDetailResponse(SubmissionResponse):
    code: Optional[str] = None  # Redacted if viewed by another participant
    attempts: List[JudgingAttemptResponse] = []

class RejudgeRequest(BaseModel):
    problem_version_id: Optional[str] = None
    test_suite_version_id: Optional[str] = None

# Leaderboard
class ProblemScoreDetail(BaseModel):
    problem_id: str
    letter: str
    solved: bool
    attempts_before_ac: int
    penalty_minutes: int
    first_ac_time_seconds: Optional[int] = None

class LeaderboardEntry(BaseModel):
    rank: int
    user_id: str
    username: str
    full_name: str
    solved_count: int
    total_penalty_minutes: int
    problem_scores: Dict[str, ProblemScoreDetail]

class LeaderboardResponse(BaseModel):
    contest_id: str
    contest_title: str
    is_frozen: bool
    generated_at: datetime
    entries: List[LeaderboardEntry]

# Error Structure
class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

class ErrorEnvelope(BaseModel):
    error: ErrorDetail
