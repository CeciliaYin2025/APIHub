"""Tests for PermissionChecker RBAC matching (exact, full `:*`, prefix `*`)."""

import pytest
from fastapi import HTTPException

from app.models.user import Permission, Role, User
from app.services.auth_service import PermissionChecker, has_permission


def _user(*perms: tuple[str, str], is_superuser: bool = False) -> User:
    """Build an in-memory user owning a single role with the given (resource, action) perms."""
    role = Role(name="r", permissions=[Permission(resource=r, action=a) for r, a in perms])
    return User(username="u", is_superuser=is_superuser, is_active=True, roles=[role])


def _allows(user: User, resource: str, action: str) -> bool:
    try:
        PermissionChecker(resource, action)(user)
        return True
    except HTTPException as exc:
        assert exc.status_code == 403
        return False


# ── exact match ──────────────────────────────────────────────────────────


def test_exact_match_allows():
    assert _allows(_user(("query:employees", "read")), "query:employees", "read")


def test_exact_match_denies_other_resource():
    assert not _allows(_user(("query:employees", "read")), "query:departments", "read")


def test_action_must_match():
    assert not _allows(_user(("query:employees", "read")), "query:employees", "write")


# ── full-category `:*` wildcard (existing spec) ────────────────────────────


def test_full_wildcard_allows_any_in_category():
    user = _user(("query:*", "read"))
    assert _allows(user, "query:employees", "read")
    assert _allows(user, "query:departments", "read")


def test_full_wildcard_respects_action():
    assert not _allows(_user(("query:*", "read")), "query:employees", "write")


# ── prefix `*` wildcard (new behavior) ─────────────────────────────────────


def test_prefix_wildcard_allows_matching_prefix():
    assert _allows(_user(("query:RD_TC*", "read")), "query:RD_TC_All", "read")


def test_prefix_wildcard_allows_multiple_matches():
    user = _user(("query:HR*", "read"))
    assert _allows(user, "query:HR_ORG", "read")
    assert _allows(user, "query:HR_Emps", "read")


def test_prefix_wildcard_denies_non_matching_prefix():
    assert not _allows(_user(("query:HR*", "read")), "query:RD_TC_All", "read")


def test_prefix_wildcard_denies_outside_prefix():
    assert not _allows(_user(("query:RD_TC*", "read")), "query:RD_OTHER", "read")


def test_prefix_wildcard_respects_action():
    assert not _allows(_user(("query:RD_TC*", "read")), "query:RD_TC_All", "write")


# ── superuser bypass ───────────────────────────────────────────────────────


def test_superuser_bypasses_all_checks():
    assert _allows(_user(is_superuser=True), "anything:at:all", "write")


# ── has_permission: the same rules as a bool, for filtering listings ───────
# Callers that filter (MCP tool catalog, Query Console picker) must not drift
# from the gate on /execute, so both go through one implementation.


@pytest.mark.parametrize(
    "perms,resource,expected",
    [
        ((("query:employees", "read"),), "query:employees", True),
        ((("query:employees", "read"),), "query:departments", False),
        ((("query:*", "read"),), "query:anything", True),
        ((("query:RD_TC*", "read"),), "query:RD_TC_All", True),
        ((("query:RD_TC*", "read"),), "query:RD_OTHER", False),
        ((), "query:employees", False),
    ],
)
def test_has_permission_matches_the_checker(perms, resource, expected):
    user = _user(*perms)
    assert has_permission(user, resource, "read") is expected
    assert _allows(user, resource, "read") is expected


def test_has_permission_respects_action():
    assert has_permission(_user(("query:employees", "read")), "query:employees", "write") is False


def test_has_permission_superuser_always_true():
    assert has_permission(_user(is_superuser=True), "anything:at:all", "write") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
