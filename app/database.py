import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

Base = declarative_base()

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

try:
    async_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args
    )
    AsyncSessionLocal = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False
    )
except Exception as _e:
    async_engine = None
    AsyncSessionLocal = None

async def get_db():
    if AsyncSessionLocal is None:
        raise RuntimeError("Database engine not initialized. Please ensure database driver is installed.")
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
