"""API keys must expose their restrictions, and allow changing them.

Two gaps this covers:
  - ip_allowlist / rate_limit_per_minute could be SET at creation but were
    absent from every read, so there was no way to audit what a key actually
    permits without opening the database.
  - There was no update endpoint at all, so tightening an existing key (adding
    an expiry, capping its rate) meant editing rows by hand.
"""

import datetime as dt

import pytest
from fastapi import HTTPException

from app.api.v1.auth import _key_to_out, update_api_key_endpoint
from app.models.user import ApiKey, User
from app.schemas.api_key import ApiKeyUpdate


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, key=None):
        self._key = key
        self.commits = 0

    def query(self, model):
        return _FakeQuery(self._key)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass


def _key(**over):
    row = ApiKey(
        id=7,
        name="eo-mcp",
        key_prefix="ahk_abc",
        key_hash="x" * 64,
        enabled=True,
        expires_at=None,
        ip_allowlist=None,
        rate_limit_per_minute=None,
        revoked_at=None,
    )
    row.user = User(username="svc_eo_mcp", is_active=True, is_service_account=True)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _admin():
    return User(username="admin", is_active=True, is_superuser=True)


# ── read: restrictions must be visible ───────────────────────────────────────


def test_key_out_exposes_ip_allowlist_and_rate_limit():
    out = _key_to_out(_key(ip_allowlist=["10.0.0.0/8"], rate_limit_per_minute=300))
    assert out["ip_allowlist"] == ["10.0.0.0/8"]
    assert out["rate_limit_per_minute"] == 300


def test_key_out_reports_unrestricted_as_none():
    out = _key_to_out(_key())
    assert out["ip_allowlist"] is None
    assert out["rate_limit_per_minute"] is None


def test_key_out_never_exposes_the_secret():
    out = _key_to_out(_key())
    assert "key_hash" not in out
    assert "api_key" not in out


# ── update ───────────────────────────────────────────────────────────────────


def test_update_sets_each_restriction():
    row = _key()
    db = _FakeDB(row)
    when = dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc)
    out = update_api_key_endpoint(
        7,
        ApiKeyUpdate(expires_at=when, ip_allowlist=["10.1.2.0/24"], rate_limit_per_minute=120),
        db=db,
        _admin=_admin(),
    )
    assert row.expires_at == when
    assert row.ip_allowlist == ["10.1.2.0/24"]
    assert row.rate_limit_per_minute == 120
    assert out["rate_limit_per_minute"] == 120
    assert db.commits == 1


def test_update_leaves_omitted_fields_untouched():
    row = _key(expires_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), rate_limit_per_minute=50)
    update_api_key_endpoint(7, ApiKeyUpdate(ip_allowlist=["10.0.0.1"]), db=_FakeDB(row), _admin=_admin())
    assert row.expires_at == dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    assert row.rate_limit_per_minute == 50
    assert row.ip_allowlist == ["10.0.0.1"]


def test_update_can_clear_a_restriction_with_an_explicit_null():
    # PATCH semantics: an omitted field means "leave alone", an explicit null
    # means "remove the restriction". Without the distinction there is no way
    # to lift an expiry once set.
    row = _key(expires_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), rate_limit_per_minute=50)
    update_api_key_endpoint(7, ApiKeyUpdate(expires_at=None), db=_FakeDB(row), _admin=_admin())
    assert row.expires_at is None
    assert row.rate_limit_per_minute == 50, "omitted field must not be cleared"


def test_update_rejects_an_unknown_key():
    with pytest.raises(HTTPException) as exc:
        update_api_key_endpoint(999, ApiKeyUpdate(rate_limit_per_minute=10), db=_FakeDB(None), _admin=_admin())
    assert exc.value.status_code == 404


def test_update_refuses_a_revoked_key():
    # Revocation is final: a revoked key's secret is treated as compromised, so
    # it must not be possible to bring it back or re-scope it.
    row = _key(enabled=False, revoked_at=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc))
    with pytest.raises(HTTPException) as exc:
        update_api_key_endpoint(7, ApiKeyUpdate(rate_limit_per_minute=10), db=_FakeDB(row), _admin=_admin())
    assert exc.value.status_code == 409
    assert row.rate_limit_per_minute is None


def test_update_cannot_re_enable_or_rename():
    # enabled / name are deliberately not part of the update surface.
    assert "enabled" not in ApiKeyUpdate.model_fields
    assert "name" not in ApiKeyUpdate.model_fields


def test_update_rejects_a_malformed_cidr():
    row = _key()
    with pytest.raises(HTTPException) as exc:
        update_api_key_endpoint(7, ApiKeyUpdate(ip_allowlist=["not-an-ip"]), db=_FakeDB(row), _admin=_admin())
    assert exc.value.status_code == 400
    assert row.ip_allowlist is None


def test_update_accepts_bare_addresses_and_cidrs():
    row = _key()
    update_api_key_endpoint(7, ApiKeyUpdate(ip_allowlist=["10.0.0.5", "192.168.0.0/16"]), db=_FakeDB(row), _admin=_admin())
    assert row.ip_allowlist == ["10.0.0.5", "192.168.0.0/16"]


def test_update_rejects_a_non_positive_rate_limit():
    with pytest.raises(ValueError):
        ApiKeyUpdate(rate_limit_per_minute=0)
