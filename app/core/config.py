"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Oracle Database (data source/target)
    oracle_dsn: str = "localhost:1521/orcl"
    oracle_user: str = "api_user"
    oracle_password: str = "changeme"
    oracle_pool_min: int = 2
    oracle_pool_max: int = 10
    oracle_pool_increment: int = 1
    oracle_client_dir: str = ""  # Oracle Instant Client path for thick mode (required for 11g)

    # PostgreSQL (internal user & permission management)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "api_hub"
    postgres_user: str = "api_hub"
    postgres_password: str = "changeme"

    # JWT
    secret_key: str = "replace-with-a-secure-random-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # API
    api_title: str = "Oracle API Hub"
    api_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    root_path: str = ""  # URL prefix when served behind a reverse proxy (e.g. /apihub)
    debug: bool = False

    # Rate limiting
    rate_limit_per_minute: int = 60

    # SQL definitions
    sql_definitions_dir: str = "app/sql_definitions"

    # Database definitions (multi-database support)
    database_definitions_dir: str = "app/database_definitions"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
