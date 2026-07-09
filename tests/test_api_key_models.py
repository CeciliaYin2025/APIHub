"""Tests that new ORM models expose the expected columns."""

from app.models.audit_log import AuditLog
from app.models.user import ApiKey, User


def test_user_has_service_account_flag():
    u = User(username="svc", is_service_account=True)
    assert u.is_service_account is True


def test_apikey_columns_and_table():
    assert ApiKey.__tablename__ == "api_hub_api_keys"
    key = ApiKey(user_id=1, name="k", key_prefix="ahk_abc", key_hash="h")
    assert key.name == "k"
    for col in (
        "enabled",
        "expires_at",
        "ip_allowlist",
        "rate_limit_per_minute",
        "last_used_at",
        "created_by",
        "revoked_at",
    ):
        assert hasattr(key, col)


def test_auditlog_columns_and_table():
    assert AuditLog.__tablename__ == "api_hub_audit_logs"
    row = AuditLog(principal_type="api_key", principal="svc", method="POST", path="/x")
    for col in ("ts", "api_key_id", "api_key_name", "client_ip", "db_alias", "action", "status_code", "duration_ms"):
        assert hasattr(row, col)
