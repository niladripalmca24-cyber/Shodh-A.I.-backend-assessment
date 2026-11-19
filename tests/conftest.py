import asyncio
import os
import pytest
try:
    import pytest_asyncio
    async_fixture = pytest_asyncio.fixture
except ImportError:
    import pytest as pytest_asyncio
    async_fixture = pytest.fixture
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base, get_db
from app.main import app
from app.seed import seed_database
from app.config import settings

# Use an isolated test database
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///test_shodh.db")

connect_args = {}
engine_kwargs = {"echo": False}
if "sqlite" in TEST_DB_URL:
    connect_args["check_same_thread"] = False
    connect_args["timeout"] = 30
    from sqlalchemy.pool import NullPool
    engine_kwargs["poolclass"] = NullPool
else:
    connect_args["statement_cache_size"] = 0

engine_kwargs["connect_args"] = connect_args

test_engine = create_async_engine(
    TEST_DB_URL,
    **engine_kwargs
)

TestAsyncSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def override_get_db():
    async with TestAsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

app.dependency_overrides[get_db] = override_get_db

@async_fixture(scope="function", autouse=True)
async def prepare_database():
    # Patch main session local so worker/seed use the test engine
    import app.database
    import app.worker
    import app.seed
    app.database.async_engine = test_engine
    app.database.AsyncSessionLocal = TestAsyncSessionLocal
    app.worker.AsyncSessionLocal = TestAsyncSessionLocal
    app.seed.AsyncSessionLocal = TestAsyncSessionLocal

    dialect_name = test_engine.dialect.name
    async with test_engine.begin() as conn:
        if dialect_name == "postgresql":
            from sqlalchemy import text
            await conn.execute(text("""
                TRUNCATE TABLE 
                    test_case_results,
                    judging_attempts,
                    submissions,
                    idempotency_records,
                    test_cases,
                    test_suite_versions,
                    problem_versions,
                    contest_problems,
                    problems,
                    contest_participants,
                    contests,
                    org_memberships,
                    users,
                    organizations
                CASCADE;
            """))
        else:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    # Run deterministic seed
    await seed_database()

    yield

@async_fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

@async_fixture
async def db_session():
    async with TestAsyncSessionLocal() as session:
        yield session
