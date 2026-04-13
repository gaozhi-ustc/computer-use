"""Tests for JWT and password utilities."""

import pytest


def test_hash_and_verify_password():
    from server.auth import hash_password, verify_password
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert verify_password("secret123", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_create_and_decode_access_token():
    from server.auth import create_access_token, decode_token
    token = create_access_token(user_id=42, username="alice", role="admin")
    payload = decode_token(token)
    assert payload["sub"] == 42
    assert payload["username"] == "alice"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_create_and_decode_refresh_token():
    from server.auth import create_refresh_token, decode_token
    token = create_refresh_token(user_id=42)
    payload = decode_token(token)
    assert payload["sub"] == 42
    assert payload["type"] == "refresh"


def test_expired_token_raises(monkeypatch):
    from server.auth import create_access_token, decode_token, AuthError
    monkeypatch.setattr("server.auth.ACCESS_TOKEN_EXPIRE_MINUTES", -1)
    token = create_access_token(user_id=1, username="x", role="employee")
    with pytest.raises(AuthError, match="expired"):
        decode_token(token)


def test_invalid_token_raises():
    from server.auth import decode_token, AuthError
    with pytest.raises(AuthError):
        decode_token("not.a.valid.jwt")
