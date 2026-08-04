"""Application configuration loaded from environment variables."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Populate os.environ from .env before anything reads it.
#
# Necessary because the per-alias DB_{ALIAS_UPPER}_PASSWORD entries are NOT Settings
# fields — app/db/multi_pool.py reads them straight from os.environ. pydantic-settings
# parses .env for its own fields but never exports to os.environ, so without this every
# data-source password resolves to "" under a bare `uvicorn`/`celery` run.
#
# This module is the right home: it is the one import both app/main.py and
# app/tasks/celery_app.py go through, so the load is guaranteed to happen before
# multi_pool reads the environment.
#
# override=False -> real environment variables win. Under Docker the passwords arrive
# via compose `env_file:` as real env vars and .env is excluded from the image
# (.dockerignore), so this call is a harmless no-op there.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env", override=False)


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

    # Comma-separated CIDRs/IPs of trusted reverse proxies (e.g. the ISAP gateway).
    # When the direct peer matches one of these, X-Forwarded-For is consulted to recover
    # the real client IP for the API-key IP allowlist and audit logging. SCOPE TO THE
    # EXACT REVERSE-PROXY IP(S) / SMALLEST CIDR — too broad a range lets any host in that
    # range spoof X-Forwarded-For. Empty (default) = trust none, use the direct peer as-is.
    trusted_proxies: str = ""

    # API key last_used_at bookkeeping: skip the write (and its commit) when
    # the key was already recorded within this many seconds.
    api_key_last_used_throttle_seconds: int = 60

    # MCP identity bridge (Phase 2 OAuth): shared secret required in X-Bridge-Secret.
    # Empty (default) disables the endpoint entirely.
    mcp_bridge_secret: str = ""
    # Auto-create a role-less local user on first SSO login via the bridge.
    mcp_bridge_autocreate: bool = False

    # ── isap-auth SSO ────────────────────────────────────────────────────────
    # Admin UI SSO login via isap-auth (legacy app-code flow). Off by default so
    # deployments without isap-auth (bare uvicorn, root docker-compose) are
    # unaffected: both /auth/sso/* endpoints 404 and the UI hides the button.
    admin_sso_enabled: bool = False
    # This service's own public address, e.g. https://isap.sercomm.com/apihub.
    # The redirect_uri handed to isap-auth must be ABSOLUTE and must match a line
    # in ISAP_REDIRECT_URIS exactly — root_path alone ("/apihub") cannot build it.
    apihub_public_url: str = ""
    # isap-auth base URLs. Public = browser-facing /authorize; internal = the
    # docker-network POST /token, which never leaves the gateway network.
    isap_auth_public_url: str = ""
    isap_auth_internal_url: str = ""
    isap_app_id: str = "apihub"
    # Auto-create a role-less local user on first SSO login. Applies to BOTH the
    # Admin UI callback and the MCP identity bridge; mcp_bridge_autocreate stays
    # as a backward-compatible alias (see sso_service.autocreate_enabled).
    sso_autocreate: bool = False

    # Read only by scripts/register_isap_app.py — the api container is not given
    # these, so the bootstrap token never sits in a long-running process.
    isap_app_name: str = "Oracle API Hub"
    isap_registration_token: str = ""
    isap_redirect_uris: str = ""  # newline- or comma-separated

    # SQL definitions
    sql_definitions_dir: str = "app/sql_definitions"

    # Database definitions (multi-database support)
    database_definitions_dir: str = "app/database_definitions"

    # Health check: seconds allowed for ALL database probes combined
    db_health_check_timeout: float = 5.0

    # Usage report / audit retention
    audit_retention_days: int = 365
    # Rows whose serialized params exceed this many bytes are returned with
    # params omitted in the detail list; the UI fetches them per row on
    # expand. Guards the API response size, not the stored size.
    audit_params_inline_limit: int = 4096

    # extra="ignore" is required, not cosmetic: .env also carries the per-alias
    # DB_{ALIAS_UPPER}_PASSWORD entries, which are consumed directly from os.environ by
    # app/db/multi_pool.py and are intentionally not Settings fields. pydantic-settings
    # ignores unknown OS env vars but forbids unknown keys read from the env_file, so
    # without this the app starts under Docker (env_file: -> real env vars) and crashes
    # in local dev (.env read from disk).
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
