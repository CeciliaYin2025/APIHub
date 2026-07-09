"""Tests for API key generation and hashing."""

from app.core.security import API_KEY_PREFIX, generate_api_key, hash_api_key


def test_generate_api_key_shape():
    plaintext, key_prefix, key_hash = generate_api_key()
    assert plaintext.startswith(API_KEY_PREFIX)
    assert len(plaintext) > 20
    assert key_prefix == plaintext[:16]
    assert len(key_hash) == 64  # sha256 hex
    assert key_hash == hash_api_key(plaintext)


def test_generate_api_key_is_unique():
    a, _, _ = generate_api_key()
    b, _, _ = generate_api_key()
    assert a != b


def test_hash_api_key_is_stable():
    assert hash_api_key("ahk_abc") == hash_api_key("ahk_abc")
    assert hash_api_key("ahk_abc") != hash_api_key("ahk_xyz")
