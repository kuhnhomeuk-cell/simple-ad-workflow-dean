"""Live image branch against the two real creatives. Needs a Gemini key and costs money.

Run with `ADTRANSLATE_LIVE=1 uv run pytest -m live tests/live/test_image_live.py -q`.
Before/after images land in `runs/image/` for the human side-by-side.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from adtranslate.config import Settings
from adtranslate.image import translate_image
from adtranslate.image.detect import detect_text, has_translatable

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ADTRANSLATE_LIVE") != "1",
        reason="live image tests run only with ADTRANSLATE_LIVE=1",
    ),
]

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "ads"
RUNS = ROOT / "runs" / "image"

TEXTLESS_ID = "1059406850353566"
BADGE_ID = "934283416402786"
DUTCH = ["Eén kopen, één gratis", "eindigt vanavond"]


@pytest.fixture(scope="module")
def settings() -> Settings:
    loaded = Settings()
    if not loaded.gemini_api_key:
        pytest.skip("no gemini_api_key configured")
    return loaded


def creative(library_id: str) -> Path:
    path = FIXTURES / library_id / "creative.jpg"
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return path


def test_textless_creative_reports_no_text(settings: Settings) -> None:
    scan = detect_text(creative(TEXTLESS_ID), settings)
    assert scan.has_text is False, scan.model_dump()
    assert has_translatable(scan) is False


def test_badge_creative_is_edited_and_verified(settings: Settings) -> None:
    source = creative(BADGE_ID)
    out = RUNS / BADGE_ID
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, out / "before.jpg")

    def dutch(strings: list[str]) -> list[str]:
        assert strings, "the badge creative should carry translatable text"
        return [DUTCH[i] if i < len(DUTCH) else s for i, s in enumerate(strings)]

    result = translate_image(source, dutch, settings, out_dir=out)

    assert result.edited is True, result.review_reasons
    assert result.review_reasons == []
    edited = Path(result.path)
    assert edited.suffix == ".png"
    shutil.copy2(edited, out / "after.png")

    with Image.open(source) as original, Image.open(edited) as new:
        assert new.size == original.size
        assert new.format == "PNG"

    read_back = detect_text(edited, settings)
    read = " ".join(block.text.casefold() for block in read_back.blocks)
    assert "vanavond" in read, read
