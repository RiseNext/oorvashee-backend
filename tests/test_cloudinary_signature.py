"""Cloudinary signature math + response verification — pure crypto."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import InvalidMediaSignatureError
from app.integrations.cloudinary_client import (
    CloudinaryClient,
    _canonicalize,
    _stringify,
    sign_params,
    verify_upload_response,
)


def _fake_settings(secret: str = "my-test-secret") -> MagicMock:
    s = MagicMock()
    s.cloudinary_cloud_name = "demo"
    s.cloudinary_api_key = "key-123"
    s.cloudinary_api_secret = secret
    return s


# ---------- _stringify ----------


def test_stringify_handles_primitives() -> None:
    assert _stringify("hello") == "hello"
    assert _stringify(42) == "42"
    assert _stringify(True) == "true"
    assert _stringify(False) == "false"


def test_stringify_collapses_lists_pipe_delimited() -> None:
    assert _stringify(["a", "b", "c"]) == "a|b|c"
    assert _stringify(("x", "y")) == "x|y"


def test_stringify_handles_empty_list() -> None:
    assert _stringify([]) == ""


# ---------- _canonicalize ----------


def test_canonicalize_sorts_alphabetically() -> None:
    result = _canonicalize({"zebra": "z", "alpha": "a", "mango": "m"})
    assert result == "alpha=a&mango=m&zebra=z"


def test_canonicalize_skips_file_and_api_key() -> None:
    """Cloudinary excludes these from the signature input by spec."""
    result = _canonicalize({
        "file": "binary",
        "api_key": "should-be-skipped",
        "timestamp": 1234,
    })
    assert result == "timestamp=1234"


def test_canonicalize_skips_signature_field() -> None:
    """Otherwise verifying a payload would self-reference."""
    result = _canonicalize({"timestamp": 1, "signature": "abc"})
    assert result == "timestamp=1"


def test_canonicalize_skips_none_and_empty_string() -> None:
    """Cloudinary's signing rule: omit empty / missing fields."""
    result = _canonicalize({"a": "x", "b": None, "c": ""})
    assert result == "a=x"


def test_canonicalize_serialises_eager_as_pipe_list() -> None:
    """`eager` is the single most fragile param — must match Cloudinary byte-for-byte."""
    result = _canonicalize({
        "eager": ["c_fill,w_320,h_400", "c_fill,w_640,h_800"],
        "timestamp": 1,
    })
    assert result == "eager=c_fill,w_320,h_400|c_fill,w_640,h_800&timestamp=1"


def test_canonicalize_serialises_tags_as_pipe_list() -> None:
    result = _canonicalize({"tags": ["bridal", "kanchipuram"], "timestamp": 1})
    assert result == "tags=bridal|kanchipuram&timestamp=1"


# ---------- sign_params ----------


def test_sign_params_is_sha1_of_canonical_plus_secret() -> None:
    params = {"timestamp": 1234567890, "folder": "products/abc"}
    expected = hashlib.sha1(
        b"folder=products/abc&timestamp=1234567890my-test-secret"
    ).hexdigest()
    assert sign_params(params, "my-test-secret") == expected


def test_sign_params_changes_when_any_field_changes() -> None:
    a = sign_params({"timestamp": 1, "folder": "p"}, "secret")
    b = sign_params({"timestamp": 2, "folder": "p"}, "secret")
    c = sign_params({"timestamp": 1, "folder": "q"}, "secret")
    assert a != b
    assert a != c


def test_sign_params_changes_when_secret_changes() -> None:
    p = {"timestamp": 1, "folder": "p"}
    assert sign_params(p, "secret-1") != sign_params(p, "secret-2")


# ---------- verify_upload_response ----------


def _signed_response(secret: str, public_id: str, version: int) -> dict:
    to_sign = f"public_id={public_id}&version={version}"
    return {
        "public_id": public_id,
        "version": version,
        "signature": hashlib.sha1((to_sign + secret).encode()).hexdigest(),
        "secure_url": "https://res.cloudinary.com/demo/image/upload/x",
    }


def test_verify_upload_response_accepts_valid() -> None:
    payload = _signed_response("my-test-secret", "products/abc/img1", 1234567890)
    assert verify_upload_response(payload, "my-test-secret") is True


def test_verify_upload_response_rejects_tampered_public_id() -> None:
    payload = _signed_response("my-test-secret", "products/abc/img1", 1)
    payload["public_id"] = "products/abc/img2"  # swap after signing
    assert verify_upload_response(payload, "my-test-secret") is False


def test_verify_upload_response_rejects_wrong_secret() -> None:
    payload = _signed_response("my-test-secret", "x", 1)
    assert verify_upload_response(payload, "different-secret") is False


def test_verify_upload_response_rejects_missing_fields() -> None:
    assert verify_upload_response({}, "secret") is False
    assert verify_upload_response({"public_id": "x"}, "secret") is False
    assert verify_upload_response({"public_id": "x", "version": 1}, "secret") is False


# ---------- CloudinaryClient.verify_response ----------


def test_client_verify_response_raises_on_tamper() -> None:
    client = CloudinaryClient(_fake_settings("real-secret"))
    bad = {
        "public_id": "x",
        "version": 1,
        "signature": "deadbeef" * 5,
        "secure_url": "https://...",
    }
    with pytest.raises(InvalidMediaSignatureError):
        client.verify_response(bad)


def test_client_verify_response_passes_on_valid() -> None:
    client = CloudinaryClient(_fake_settings("real-secret"))
    good = _signed_response("real-secret", "products/a/b", 1)
    client.verify_response(good)  # does not raise


# ---------- Round-trip: build_signed_envelope + verify ----------


def test_envelope_signature_is_verifiable_by_independent_recompute() -> None:
    """If we ever change the canonical params, this test catches a drift
    between sign_params and what the frontend submits to Cloudinary."""
    client = CloudinaryClient(_fake_settings("my-test-secret"))
    env = client.build_signed_envelope(
        folder="prod/products/abc",
        eager=["c_fill,w_320,h_400"],
        allowed_formats=["jpg", "webp"],
        max_file_size=10_000_000,
        timestamp=1700000000,
        tags=["ctx:product"],
    )
    # Independently recompute exactly what the frontend will submit.
    submitted = {
        "timestamp": 1700000000,
        "folder": "prod/products/abc",
        "eager": ["c_fill,w_320,h_400"],
        "allowed_formats": ["jpg", "webp"],
        "max_file_size": 10_000_000,
        "tags": ["ctx:product"],
    }
    assert sign_params(submitted, "my-test-secret") == env.signature


def test_context_value_signed_verbatim_without_redundant_prefix() -> None:
    """Regression: the `context` value is signed AND transmitted verbatim.

    A prior bug pre-wrapped it into a `{"context": ...}` dict, so canonicalisation
    produced `context=context=product|entity_id=...` (the param name added a
    SECOND `context=`), which Cloudinary rejected as 401 Invalid Signature.
    """
    client = CloudinaryClient(_fake_settings("my-test-secret"))
    env = client.build_signed_envelope(
        folder="prod/products/abc",
        eager=["c_fill,w_320,h_400"],
        allowed_formats=["jpg", "webp"],
        max_file_size=10_000_000,
        timestamp=1700000000,
        tags=["ctx:product"],
        context="product|entity_id=abc",
    )
    # Envelope returns the value unchanged (frontend POSTs it verbatim).
    assert env.context == "product|entity_id=abc"

    # The frontend submits the same context value as-is.
    submitted = {
        "timestamp": 1700000000,
        "folder": "prod/products/abc",
        "eager": ["c_fill,w_320,h_400"],
        "allowed_formats": ["jpg", "webp"],
        "max_file_size": 10_000_000,
        "tags": ["ctx:product"],
        "context": "product|entity_id=abc",
    }
    assert sign_params(submitted, "my-test-secret") == env.signature

    # Canonical contains `context=` exactly once — no double prefix.
    canonical = _canonicalize(submitted)
    assert "context=product|entity_id=abc" in canonical
    assert "context=context=" not in canonical


# ---------- Delivery URL builder ----------


def test_delivery_url_format() -> None:
    client = CloudinaryClient(_fake_settings())
    url = client.build_delivery_url(
        "products/abc/img1",
        transformations="c_fill,w_640,h_800,q_auto,f_auto",
        version=1234567890,
    )
    assert url == (
        "https://res.cloudinary.com/demo/image/upload/"
        "c_fill,w_640,h_800,q_auto,f_auto/v1234567890/products/abc/img1"
    )


def test_delivery_url_no_transformations() -> None:
    client = CloudinaryClient(_fake_settings())
    url = client.build_delivery_url("x/y")
    assert url == "https://res.cloudinary.com/demo/image/upload/x/y"
