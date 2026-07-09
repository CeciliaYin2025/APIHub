"""Tests for get_client_ip (app/services/auth_service.py).

Anti-spoofing contract: X-Forwarded-For is only consulted when the direct
peer is a configured trusted reverse proxy. With no trusted_proxies
configured (the default), the direct peer is always returned, unchanged
from pre-existing behavior.
"""

from app.core.config import settings
from app.services.auth_service import get_client_ip


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, headers=None, client_host="10.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host)


def test_no_trusted_proxies_returns_peer_ignoring_xff(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "")
    request = _FakeRequest(headers={"X-Forwarded-For": "203.0.113.7"}, client_host="192.0.2.1")

    assert get_client_ip(request) == "192.0.2.1"


def test_peer_in_trusted_network_uses_xff(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    request = _FakeRequest(headers={"X-Forwarded-For": "203.0.113.7"}, client_host="10.0.0.5")

    assert get_client_ip(request) == "203.0.113.7"


def test_skips_trusted_hop_in_xff_chain(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    request = _FakeRequest(
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.9"},
        client_host="10.0.0.5",
    )

    assert get_client_ip(request) == "203.0.113.7"


def test_untrusted_peer_ignores_spoofed_xff(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    request = _FakeRequest(headers={"X-Forwarded-For": "10.1.2.3"}, client_host="198.51.100.4")

    assert get_client_ip(request) == "198.51.100.4"


def test_trusted_peer_no_xff_header_returns_peer(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    request = _FakeRequest(headers={}, client_host="10.0.0.5")

    assert get_client_ip(request) == "10.0.0.5"
