import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Shodh a Code Contest Backend"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql+asyncpg://shodh_user:shodh_password@localhost:5432/shodh_db"
    )
    SYNC_DATABASE_URL: str = os.getenv(
        "SYNC_DATABASE_URL",
        "postgresql://shodh_user:shodh_password@localhost:5432/shodh_db"
    )
    
    # Judge & Runner Settings
    RUNNER_MODE: str = os.getenv("RUNNER_MODE", "REAL_DOCKER")  # REAL_DOCKER, SUBPROCESS_SANDBOX, MOCK
    WORKER_ID: str = os.getenv("WORKER_ID", "worker-1")
    LEASE_TIMEOUT_SECONDS: int = int(os.getenv("LEASE_TIMEOUT_SECONDS", "30"))
    MAX_JUDGE_RETRIES: int = int(os.getenv("MAX_JUDGE_RETRIES", "3"))
    DEFAULT_TIME_LIMIT_MS: int = 2000
    DEFAULT_MEMORY_LIMIT_MB: int = 128
    DEFAULT_OUTPUT_LIMIT_BYTES: int = 65536
    
    # Concurrency / Polling
    WORKER_POLL_INTERVAL_SECONDS: float = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "0.5"))

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
