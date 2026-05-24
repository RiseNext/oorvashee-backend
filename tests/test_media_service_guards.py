"""Pure-logic guards in MediaService that don't need a DB."""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import ValidationError
from app.schemas.media import CloudinaryUploadResult
from app.services.media_service import MediaService


def _payload(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "public_id": "local/products/abc/img-1",
        "version": 1,
        "signature": "dead",
        "secure_url": "https://res.cloudinary.com/demo/image/upload/x",
        "format": "webp",
        "width": 800,
        "height": 1000,
        "bytes": 12345,
        "resource_type": "image",
    }
    base.update(overrides)
    return CloudinaryUploadResult.model_validate(base)


def test_payload_belongs_to_product_passes_when_segment_present() -> None:
    pid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    payload = _payload(public_id=f"local/products/{pid}/some-img-name")
    MediaService._verify_payload_belongs_to_product(payload, pid)


def test_payload_belongs_to_product_rejects_wrong_product() -> None:
    pid_a = uuid.uuid4()
    pid_b = uuid.uuid4()
    payload = _payload(public_id=f"local/products/{pid_a}/x")
    with pytest.raises(ValidationError) as exc_info:
        MediaService._verify_payload_belongs_to_product(payload, pid_b)
    assert "does not belong" in exc_info.value.message
    assert str(pid_b) in exc_info.value.extra["expected_segment"]


def test_payload_belongs_to_product_rejects_wrong_context() -> None:
    """A banner asset must not be attachable to a product."""
    pid = uuid.uuid4()
    payload = _payload(public_id=f"local/banners/{pid}/hero")
    with pytest.raises(ValidationError):
        MediaService._verify_payload_belongs_to_product(payload, pid)


def test_cloudinary_upload_result_ignores_unknown_fields() -> None:
    """Cloudinary adds fields over time; the model must not break."""
    payload = CloudinaryUploadResult.model_validate({
        "public_id": "x",
        "version": 1,
        "signature": "sig",
        "secure_url": "https://x",
        "future_field": "anything",
        "asset_id": "asset_xyz",
    })
    assert payload.public_id == "x"
