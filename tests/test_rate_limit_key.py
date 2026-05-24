"""Rate-limit key function — JWT-aware, falls back to IP."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

from app.core.rate_limit import _peek_jwt_sub, composite_key


def _make_jwt(claims: dict) -> str:
    """Build a syntactically-valid JWT (no signature) for tests."""
    header_b64 = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=")
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).rstrip(b"=")
    return f"{header_b64.decode()}.{payload_b64.decode()}.signature-ignored"


def _request(headers: dict[str, str], client_host: str = "10.0.0.42"):  # type: ignore[no-untyped-def]
    req = MagicMock()
    req.headers = headers
    req.client.host = client_host
    return req


def test_peek_jwt_sub_extracts_claim() -> None:
    token = _make_jwt({"sub": "user_abc", "exp": 9999999999})
    assert _peek_jwt_sub(token) == "user_abc"


def test_peek_jwt_sub_returns_none_on_garbage() -> None:
    assert _peek_jwt_sub("not.a.jwt") is None
    assert _peek_jwt_sub("malformed") is None
    assert _peek_jwt_sub("") is None


def test_peek_jwt_sub_handles_missing_sub() -> None:
    token = _make_jwt({"exp": 9999999999})
    assert _peek_jwt_sub(token) is None


def test_composite_key_uses_jwt_when_present() -> None:
    token = _make_jwt({"sub": "user_xyz"})
    req = _request({"Authorization": f"Bearer {token}"})
    assert composite_key(req) == "user:user_xyz"


def test_composite_key_falls_back_to_ip_no_token() -> None:
    req = _request({}, client_host="192.168.1.5")
    assert composite_key(req) == "ip:192.168.1.5"


def test_composite_key_falls_back_on_malformed_bearer() -> None:
    req = _request({"Authorization": "Bearer garbage"}, client_host="172.16.0.1")
    assert composite_key(req) == "ip:172.16.0.1"


def test_composite_key_handles_lowercase_bearer() -> None:
    """Some clients lowercase the scheme."""
    token = _make_jwt({"sub": "user_lower"})
    req = _request({"Authorization": f"bearer {token}"})
    assert composite_key(req) == "user:user_lower"


def test_composite_key_does_not_verify_signature() -> None:
    """The auth dependency does real verification; this is keying only."""
    # Token with claims but no real signature — should still produce a key.
    token = _make_jwt({"sub": "any_value"})
    req = _request({"Authorization": f"Bearer {token}"})
    assert composite_key(req).startswith("user:")
