"""MCP settings load from MCP_-prefixed env vars."""

from mcp_server.config import McpSettings


def test_defaults():
    s = McpSettings(_env_file=None)
    assert s.api_hub_url == "http://localhost:8000"
    assert s.page_size_max == 100
    assert s.watch_interval_seconds == 30
    assert s.oauth_enabled is False


def test_catalog_ttl_defaults_to_the_introspection_lag():
    """60 s: the fingerprint does not cover roles, so this is the role-change lag —
    the same one OAuth introspection already imposes."""
    assert McpSettings(_env_file=None).catalog_ttl_seconds == 60.0


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("MCP_API_HUB_URL", "http://api:9000")
    monkeypatch.setenv("MCP_API_HUB_API_KEY", "sk-test-123")
    monkeypatch.setenv("MCP_PAGE_SIZE_MAX", "50")
    s = McpSettings(_env_file=None)
    assert s.api_hub_url == "http://api:9000"
    assert s.api_hub_api_key == "sk-test-123"
    assert s.page_size_max == 50


def test_db_aliases_default_empty_means_all():
    s = McpSettings(_env_file=None)
    assert s.db_aliases == ""
