"""Idempotency middleware — key validation + body hashing."""

from __future__ import annotations

import hashlib

import pytest

from app.core.idempotency import _KEY_PATTERN, IdempotencyContext, _hash_body


def test_key_pattern_accepts_uuid_like() -> None:
    assert _KEY_PATTERN.match("4f2b6f5e-7c0e-4f10-a5d4-9f3a8e2b1c0d")


def test_key_pattern_accepts_alphanumeric() -> None:
    assert _KEY_PATTERN.match("abc123XYZ_-")


def test_key_pattern_rejects_too_short() -> None:
    assert not _KEY_PATTERN.match("short")


def test_key_pattern_rejects_invalid_chars() -> None:
    assert not _KEY_PATTERN.match("has spaces in it")
    assert not _KEY_PATTERN.match("has/slash")
    assert not _KEY_PATTERN.match("has@symbol123")


def test_key_pattern_rejects_too_long() -> None:
    assert not _KEY_PATTERN.match("x" * 129)


def test_hash_body_deterministic() -> None:
    body = b'{"items":[{"variant_id":"abc"}]}'
    assert _hash_body(body) == _hash_body(body)
    assert _hash_body(body) == hashlib.sha256(body).hexdigest()


def test_hash_body_distinguishes_payloads() -> None:
    assert _hash_body(b"a") != _hash_body(b"b")
    # whitespace is significant — same intent, different bytes
    assert _hash_body(b'{"q":1}') != _hash_body(b'{ "q": 1 }')


def test_context_is_replay_flag() -> None:
    fresh = IdempotencyContext(
        key="k", route="POST:/x", request_hash="h", cached_status=None, cached_body=None
    )
    assert fresh.is_replay is False

    cached = IdempotencyContext(
        key="k", route="POST:/x", request_hash="h",
        cached_status=201, cached_body={"order": "OOR-X"},
    )
    assert cached.is_replay is True


# -- with_idempotency cache-replay behaviour ---------------------------------


@pytest.mark.asyncio
async def test_with_idempotency_replays_without_executing() -> None:
    """Replay must NOT invoke the execute() callable."""
    from app.core.idempotency import with_idempotency

    ctx = IdempotencyContext(
        key="k", route="POST:/checkout/orders", request_hash="h",
        cached_status=201, cached_body={"order_number": "OOR-CACHED"},
    )

    called = {"count": 0}

    async def execute() -> dict:
        called["count"] += 1
        return {"order_number": "OOR-FRESH"}

    status, body = await with_idempotency(
        ctx=ctx, session=None, user_id=None,  # type: ignore[arg-type]
        status_code=201, execute=execute,
    )
    assert called["count"] == 0
    assert body == {"order_number": "OOR-CACHED"}
    assert status == 201
