"""Unit tests for app.core.validators.extract_youtube_id.

Guards the video-wall admin endpoint: the admin pastes any YouTube link form
and we must store ONLY the canonical 11-char id (never the raw URL), rejecting
foreign hosts / junk so nothing arbitrary lands in the storefront iframe src.
"""

from __future__ import annotations

import pytest

from app.core.validators import extract_youtube_id

_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value",
    [
        _ID,
        f"https://www.youtube.com/watch?v={_ID}",
        f"https://youtube.com/watch?v={_ID}&t=42s",
        f"https://www.youtube.com/watch?list=PL123&v={_ID}",
        f"https://youtu.be/{_ID}",
        f"https://youtu.be/{_ID}?si=abcdEFGH",
        f"https://www.youtube.com/shorts/{_ID}",
        f"https://www.youtube.com/embed/{_ID}",
        f"https://www.youtube-nocookie.com/embed/{_ID}",
        f"https://www.youtube.com/live/{_ID}",
        f"  https://youtu.be/{_ID}  ",  # trimmed before checking
    ],
)
def test_extracts_canonical_id(value: str) -> None:
    assert extract_youtube_id(value) == _ID


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "not a url",
        "https://vimeo.com/123456789",
        "https://example.com/watch?v=dQw4w9WgXcQ",  # foreign host
        "https://www.youtube.com/watch?v=tooShort",  # < 11 chars
        "https://www.youtube.com/channel/UCabcdefghij",  # not a video URL
        "dQw4w9WgXc",  # 10 chars
        "dQw4w9WgXcQextra",  # bare value, wrong length
    ],
)
def test_rejects_non_youtube_and_malformed(value: str | None) -> None:
    assert extract_youtube_id(value) is None
