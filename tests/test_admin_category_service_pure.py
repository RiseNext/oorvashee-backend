"""Pure-logic tests for AdminCategoryService — no DB.

Covers:
- Slug generation rules.
- Schema-level image-pair validation.
- Update schema mass-assignment guards (slug, kind, is_active stripped).
- Schema-level slug pattern enforcement.
- MAX_DEPTH constant sanity.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.models.enums import CategoryKind
from app.schemas.admin_category import (
    AdminCategoryCreate,
    AdminCategoryUpdate,
    CategoryReorderRequest,
)
from app.services.admin_category_service import MAX_DEPTH, _slugify

# ---------- _slugify ----------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Kanchipuram Silk", "kanchipuram-silk"),
        ("  Bridal  ", "bridal"),
        ("South Indian — Tamil Nadu", "south-indian-tamil-nadu"),
        ("100% Pure", "100-pure"),
        ("---", "category"),
        ("", "category"),
    ],
)
def test_slugify_rules(name: str, expected: str) -> None:
    assert _slugify(name) == expected


def test_slugify_caps_at_160_chars() -> None:
    assert len(_slugify("a" * 500)) == 160


# ---------- Image-pair validator (Create) ----------


def test_create_image_pair_url_only_rejected() -> None:
    with pytest.raises(PydanticValidationError) as exc:
        AdminCategoryCreate(
            name="Test",
            kind=CategoryKind.FABRIC,
            image_url="https://example.com/a.jpg",
            image_public_id=None,
        )
    assert "together" in str(exc.value)


def test_create_image_pair_public_id_only_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        AdminCategoryCreate(
            name="Test",
            kind=CategoryKind.FABRIC,
            image_url=None,
            image_public_id="public-id-only",
        )


def test_create_image_pair_both_set_ok() -> None:
    cat = AdminCategoryCreate(
        name="Kanchipuram",
        kind=CategoryKind.FABRIC,
        image_url="https://res.cloudinary.com/x.webp",
        image_public_id="categories/abc/x",
    )
    assert cat.image_url is not None
    assert cat.image_public_id is not None


def test_create_image_pair_both_none_ok() -> None:
    cat = AdminCategoryCreate(name="Cotton", kind=CategoryKind.FABRIC)
    assert cat.image_url is None
    assert cat.image_public_id is None


# ---------- Mass-assignment guards (Update schema) ----------


def test_update_schema_rejects_slug() -> None:
    body = AdminCategoryUpdate.model_validate(
        {"slug": "anything-else", "name": "Valid Name"}
    )
    assert "slug" not in body.model_dump(exclude_unset=True)


def test_update_schema_rejects_kind() -> None:
    body = AdminCategoryUpdate.model_validate(
        {"kind": CategoryKind.OCCASION.value, "name": "Valid Name"}
    )
    assert "kind" not in body.model_dump(exclude_unset=True)


def test_update_schema_rejects_is_active() -> None:
    """Visibility flip must go through archive/unarchive endpoints."""
    body = AdminCategoryUpdate.model_validate(
        {"is_active": False, "name": "Valid Name"}
    )
    assert "is_active" not in body.model_dump(exclude_unset=True)


def test_update_schema_dump_only_contains_sent_fields() -> None:
    """PATCH semantics — exclude_unset means we never write fields the
    client didn't send. Critical for partial updates to not blow away
    other fields."""
    body = AdminCategoryUpdate.model_validate({"name": "Kanchipuram Silk"})
    dumped = body.model_dump(exclude_unset=True)
    assert dumped == {"name": "Kanchipuram Silk"}


def test_update_clearing_image_pair_requires_both() -> None:
    """Sending image_url=null without image_public_id=null would leave
    a stranded public_id. The service's runtime check catches this; the
    schema-level check is in AdminCategoryCreate only because Update is
    partial. (Documented behaviour — tests in test_admin_category_service_pure
    fixture-style would need DB.)"""
    # This is a behavioural note: PATCH validation happens at the service
    # layer with the merged final state, not at the schema layer.
    body = AdminCategoryUpdate.model_validate(
        {"image_url": None, "image_public_id": None}
    )
    dumped = body.model_dump(exclude_unset=True)
    # Both keys present, both null — service will accept this consistent
    # "clear both" pair.
    assert "image_url" in dumped
    assert "image_public_id" in dumped
    assert dumped["image_url"] is None
    assert dumped["image_public_id"] is None


# ---------- Slug pattern + name length ----------


def test_create_explicit_slug_pattern_enforced() -> None:
    with pytest.raises(PydanticValidationError):
        AdminCategoryCreate(
            name="Test", kind=CategoryKind.FABRIC, slug="Has Space",
        )
    with pytest.raises(PydanticValidationError):
        AdminCategoryCreate(
            name="Test", kind=CategoryKind.FABRIC, slug="UPPER",
        )
    with pytest.raises(PydanticValidationError):
        AdminCategoryCreate(
            name="Test", kind=CategoryKind.FABRIC, slug="-leading-dash",
        )


def test_create_name_bounded() -> None:
    with pytest.raises(PydanticValidationError):
        AdminCategoryCreate(name="A", kind=CategoryKind.FABRIC)
    with pytest.raises(PydanticValidationError):
        AdminCategoryCreate(name="x" * 200, kind=CategoryKind.FABRIC)


def test_create_parent_uuid_validated() -> None:
    # Valid UUID accepted.
    cat = AdminCategoryCreate(
        name="Test", kind=CategoryKind.FABRIC, parent_id=uuid4(),
    )
    assert cat.parent_id is not None
    # Garbage rejected by Pydantic.
    with pytest.raises(PydanticValidationError):
        AdminCategoryCreate(
            name="Test", kind=CategoryKind.FABRIC, parent_id="not-a-uuid",
        )


def test_reorder_request_bounded() -> None:
    with pytest.raises(PydanticValidationError):
        CategoryReorderRequest(display_order=-1)
    with pytest.raises(PydanticValidationError):
        CategoryReorderRequest(display_order=100_000)


# ---------- Constants ----------


def test_max_depth_sane() -> None:
    """MAX_DEPTH should be at least 2 (root + one nesting level) but not
    so high that the mega-menu can't render. Tightening triggers explicit
    review."""
    assert 2 <= MAX_DEPTH <= 5
