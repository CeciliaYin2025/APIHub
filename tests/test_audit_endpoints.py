"""Report endpoints: superuser gating, parameter validation, wiring.

The service layer is stubbed — its own behaviour is covered by
tests/test_audit_report_service.py. What matters here is that the router
validates input, maps ValueError to 400, and passes the resolved values
through.
"""

from datetime import date

import pytest
from fastapi import HTTPException

import app.api.v1.audit as audit_api


class _State:
    principal = None


class _Req:
    def __init__(self):
        self.state = _State()


def _ok(**overrides):
    """Default kwargs for audit_api.usage()."""
    base = dict(request=_Req(), date_from=None, date_to=None, principal=None, db_alias=None,
                action=None, action_type=None, group_by=None, sort=None, page=1, page_size=50,
                _admin=object(), pgdb=object())
    base.update(overrides)
    return base


def test_usage_rejects_inverted_date_range():
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(date_from=date(2026, 7, 30), date_to=date(2026, 7, 1)))
    assert excinfo.value.status_code == 400
    assert "date_from" in excinfo.value.detail


def test_usage_rejects_unknown_group_by():
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(group_by="user,ip"))
    assert excinfo.value.status_code == 400
    assert "ip" in excinfo.value.detail


def test_usage_rejects_unknown_sort():
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(sort="-principal"))
    assert excinfo.value.status_code == 400


def test_usage_rejects_unknown_action_type():
    with pytest.raises(HTTPException) as excinfo:
        audit_api.usage(**_ok(action_type="query,login"))
    assert excinfo.value.status_code == 400


def test_usage_passes_resolved_values_to_the_service(monkeypatch):
    seen = {}

    def _fake(db, **kwargs):
        seen.update(kwargs)
        return {"total_groups": 0, "page": 1, "page_size": 50, "totals": {}, "items": []}

    monkeypatch.setattr(audit_api.svc, "usage_report", _fake)
    audit_api.usage(**_ok(group_by="day", sort="avg_duration_ms", principal="alice"))

    assert seen["group_by"] == ["day"]
    assert seen["sort_column"] == "avg_duration_ms"
    assert seen["sort_desc"] is False
    # ts window + action_type default + principal
    assert len(seen["clauses"]) == 4


def test_usage_marks_itself_as_a_report_call(monkeypatch):
    monkeypatch.setattr(audit_api.svc, "usage_report", lambda db, **k: {
        "total_groups": 0, "page": 1, "page_size": 50, "totals": {}, "items": []})
    kwargs = _ok()
    audit_api.usage(**kwargs)
    assert kwargs["request"].state.audit_action_type == "report"
    assert kwargs["request"].state.audit_action == "usage"


def test_log_detail_404s_when_missing(monkeypatch):
    monkeypatch.setattr(audit_api.svc, "get_log", lambda db, log_id: None)
    with pytest.raises(HTTPException) as excinfo:
        audit_api.log_detail(_Req(), 999, _admin=object(), pgdb=object())
    assert excinfo.value.status_code == 404
