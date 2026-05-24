"""MediaContext configs — every enum value has a usable config, all eager
transformations are well-formed, and constraints are sane.

These tests catch the most common drift bugs when adding a new media context:
forgetting the CONTEXT_CONFIGS entry, mistyping a transformation, leaving a
zero/None size cap, or shipping an allowed_formats list that doesn't match
the resource_type.
"""

from __future__ import annotations

import re

import pytest

from app.schemas.media import MediaContext
from app.services.media_service import CONTEXT_CONFIGS

_TRANSFORMATION_PATTERN = re.compile(r"^[a-z]_[A-Za-z0-9_,]+$")
_IMAGE_FORMATS = {"jpg", "jpeg", "png", "webp", "avif", "gif"}
_VIDEO_FORMATS = {"mp4", "mov", "webm"}


@pytest.mark.parametrize("ctx", list(MediaContext))
def test_every_context_has_a_config(ctx: MediaContext) -> None:
    assert ctx in CONTEXT_CONFIGS, f"Missing CONTEXT_CONFIGS entry for {ctx}"


@pytest.mark.parametrize("ctx", list(MediaContext))
def test_context_constraints_are_sane(ctx: MediaContext) -> None:
    cfg = CONTEXT_CONFIGS[ctx]
    assert cfg.max_bytes > 0, "max_bytes must be positive"
    assert cfg.max_bytes <= 500 * 1024 * 1024, "max_bytes suspiciously large"
    assert cfg.allowed_formats, "allowed_formats must not be empty"
    assert cfg.resource_type in {"image", "video", "raw"}


@pytest.mark.parametrize("ctx", list(MediaContext))
def test_formats_match_resource_type(ctx: MediaContext) -> None:
    cfg = CONTEXT_CONFIGS[ctx]
    fmt_set = {f.lower() for f in cfg.allowed_formats}
    if cfg.resource_type == "image":
        assert fmt_set <= _IMAGE_FORMATS, (
            f"{ctx} declares image but allows non-image formats: {fmt_set - _IMAGE_FORMATS}"
        )
    elif cfg.resource_type == "video":
        assert fmt_set <= _VIDEO_FORMATS, (
            f"{ctx} declares video but allows non-video formats: {fmt_set - _VIDEO_FORMATS}"
        )


@pytest.mark.parametrize("ctx", list(MediaContext))
def test_eager_transformations_are_well_formed(ctx: MediaContext) -> None:
    cfg = CONTEXT_CONFIGS[ctx]
    for transform in cfg.eager:
        # Each transformation chain is comma-separated. Each segment is
        # "<single-letter>_<value>" — c_fill, w_320, f_auto, etc.
        for seg in transform.split(","):
            assert _TRANSFORMATION_PATTERN.match(seg.strip()), (
                f"Bad transformation segment in {ctx}: {seg!r}"
            )


def test_product_context_requires_entity_id() -> None:
    """Product images live under /products/{id}/... — never shared."""
    assert CONTEXT_CONFIGS[MediaContext.PRODUCT].requires_entity_id is True


def test_shared_contexts_do_not_require_entity_id() -> None:
    """Reels / influencer / whatsapp catalog are pool buckets."""
    for ctx in (
        MediaContext.REEL,
        MediaContext.INFLUENCER,
        MediaContext.WHATSAPP_CATALOG,
    ):
        assert CONTEXT_CONFIGS[ctx].requires_entity_id is False


def test_folder_template_uses_placeholder() -> None:
    """Templates must use either {entity_id} or {shared} — anything else
    silently breaks the .format() call at runtime."""
    for ctx, cfg in CONTEXT_CONFIGS.items():
        # Smoke-test the format call with both placeholders supplied.
        result = cfg.folder_template.format(entity_id="ABC", shared="SHARED")
        assert result, f"Empty folder template for {ctx}"
        assert "{" not in result, f"Unfilled placeholder in {ctx}: {result}"


def test_whatsapp_context_respects_meta_size_cap() -> None:
    """Meta caps WhatsApp commerce images at 5 MB."""
    assert CONTEXT_CONFIGS[MediaContext.WHATSAPP_CATALOG].max_bytes <= 5 * 1024 * 1024
