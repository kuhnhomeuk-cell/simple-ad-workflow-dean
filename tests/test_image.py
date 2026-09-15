"""Routing tests for the image branch. Every model call is faked; nothing leaves the box."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from adtranslate.config import Settings
from adtranslate.image import NOT_VERIFIED, translate_image
from adtranslate.image.detect import TextScan, has_translatable, parse_scan
from adtranslate.image.edit import build_instruction, first_image_bytes, model_tag
from adtranslate.image.verify import VerifyResult, parse_verify, score

SOURCE_SIZE = (300, 200)


def png_bytes(size: tuple[int, int], colour: tuple[int, int, int] = (10, 120, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gemini_api_key="fake-not-a-key",
        vision_model="vision-x",
        image_model="flash-image",
        image_retry_model="pro-image",
    )


@pytest.fixture
def creative(tmp_path: Path) -> Path:
    path = tmp_path / "creative.jpg"
    Image.new("RGB", SOURCE_SIZE, (200, 40, 40)).save(path, format="JPEG")
    return path


class FakeResponse:
    """A response carrying either a parsed object or image bytes, like the real one."""

    def __init__(self, parsed: Any = None, image: bytes | None = None) -> None:
        self.parsed = parsed
        self.text = None
        self.candidates = []
        if image is not None:
            part = type("Part", (), {"inline_data": type("Blob", (), {"data": image})()})()
            content = type("Content", (), {"parts": [part]})()
            self.candidates = [type("Candidate", (), {"content": content})()]


class FakeModels:
    def __init__(self, owner: FakeClient) -> None:
        self.owner = owner

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> Any:
        self.owner.calls.append((model, contents))
        if model == self.owner.vision_model:
            if self.owner.scan_done:
                verdict = self.owner.verdicts.pop(0)
                return FakeResponse(parsed=verdict)
            self.owner.scan_done = True
            return FakeResponse(parsed=self.owner.scan)
        return FakeResponse(image=self.owner.edited_bytes)


class FakeClient:
    """Answers the scan first, then one verdict per edit, and image bytes for edits."""

    def __init__(
        self,
        scan: TextScan,
        verdicts: list[VerifyResult],
        edited_bytes: bytes,
        vision_model: str = "vision-x",
    ) -> None:
        self.scan = scan
        self.verdicts = list(verdicts)
        self.edited_bytes = edited_bytes
        self.vision_model = vision_model
        self.scan_done = False
        self.calls: list[tuple[str, Any]] = []
        self.models = FakeModels(self)

    def edit_models(self) -> list[str]:
        return [m for m, _ in self.calls if m != self.vision_model]


def upper(strings: list[str]) -> list[str]:
    return [s.upper() for s in strings]


BADGE_SCAN = TextScan.model_validate(
    {
        "has_text": True,
        "blocks": [
            {"text": "Un achete, un offert", "role": "promo", "translate": True},
            {"text": "ACME", "role": "logo", "translate": False},
        ],
    }
)


def verdict(passed: bool) -> VerifyResult:
    if passed:
        return VerifyResult(
            targets_present=["UN ACHETE, UN OFFERT"], sources_remaining=[], unchanged_score=5
        )
    return VerifyResult(
        targets_present=[], sources_remaining=["Un achete, un offert"], unchanged_score=2
    )


def test_no_text_skips_the_edit(settings: Settings, creative: Path, tmp_path: Path) -> None:
    client = FakeClient(TextScan(has_text=False), [], png_bytes(SOURCE_SIZE))
    result = translate_image(creative, upper, settings, client, out_dir=tmp_path / "out")

    assert result.edited is False
    assert result.review_reasons == []
    assert client.edit_models() == []
    assert Path(result.path).suffix == ".png"
    with Image.open(result.path) as image:
        assert image.size == SOURCE_SIZE


def test_logo_only_text_is_not_translatable(settings: Settings, creative: Path) -> None:
    scan = TextScan.model_validate(
        {"has_text": True, "blocks": [{"text": "ACME", "role": "logo", "translate": False}]}
    )
    assert has_translatable(scan) is False
    client = FakeClient(scan, [], png_bytes(SOURCE_SIZE))
    result = translate_image(creative, upper, settings, client)
    assert result.edited is False
    assert client.edit_models() == []


def test_first_pass_pass_means_no_retry(settings: Settings, creative: Path) -> None:
    client = FakeClient(BADGE_SCAN, [verdict(True)], png_bytes(SOURCE_SIZE))
    result = translate_image(creative, upper, settings, client)

    assert result.edited is True
    assert client.edit_models() == ["flash-image"]
    assert result.review_reasons == []


def test_verify_failure_triggers_exactly_one_retry(settings: Settings, creative: Path) -> None:
    client = FakeClient(BADGE_SCAN, [verdict(False), verdict(True)], png_bytes(SOURCE_SIZE))
    result = translate_image(creative, upper, settings, client)

    assert client.edit_models() == ["flash-image", "pro-image"]
    assert result.edited is True
    assert Path(result.path).name == "creative.pro-image.png"


def test_second_failure_returns_original_and_keeps_both_attempts(
    settings: Settings, creative: Path, tmp_path: Path
) -> None:
    out = tmp_path / "runs"
    client = FakeClient(BADGE_SCAN, [verdict(False), verdict(False)], png_bytes(SOURCE_SIZE))
    result = translate_image(creative, upper, settings, client, out_dir=out)

    assert client.edit_models() == ["flash-image", "pro-image"]
    assert result.edited is False
    assert result.review_reasons == [NOT_VERIFIED]
    assert (out / "creative.flash-image.png").exists()
    assert (out / "creative.pro-image.png").exists()
    with Image.open(result.path) as image:
        assert image.size == SOURCE_SIZE


def test_output_dimensions_survive_a_resized_edit(
    settings: Settings, creative: Path, tmp_path: Path
) -> None:
    client = FakeClient(BADGE_SCAN, [verdict(True)], png_bytes((1024, 1024)))
    result = translate_image(creative, upper, settings, client, out_dir=tmp_path / "out")

    with Image.open(result.path) as image:
        assert image.size == SOURCE_SIZE
        assert image.format == "PNG"


def test_only_translatable_blocks_reach_the_translator(settings: Settings, creative: Path) -> None:
    seen: list[list[str]] = []

    def record(strings: list[str]) -> list[str]:
        seen.append(strings)
        return [s.upper() for s in strings]

    client = FakeClient(BADGE_SCAN, [verdict(True)], png_bytes(SOURCE_SIZE))
    translate_image(creative, record, settings, client)

    assert seen == [["Un achete, un offert"]]


def test_instruction_names_every_replacement_and_forbids_other_change() -> None:
    text = build_instruction([("Un achete, un offert", "Eén kopen, één gratis")])
    assert '"Un achete, un offert" becomes "Eén kopen, één gratis"' in text
    assert "Change nothing else" in text
    assert "same font style" in text


def test_text_scan_parses_from_json_text() -> None:
    payload = json.dumps(
        {
            "has_text": True,
            "blocks": [{"text": "2 pour 1", "role": "promo", "translate": True}],
        }
    )
    response = type("R", (), {"parsed": None, "text": payload})()
    scan = parse_scan(response)
    assert scan.blocks[0].role == "promo"
    assert has_translatable(scan) is True


def test_verify_result_parses_from_json_text_and_is_scored_here() -> None:
    payload = json.dumps(
        {
            "targets_present": ["Eén kopen, één gratis"],
            "sources_remaining": [],
            "unchanged_score": 5,
            "passed": False,
        }
    )
    response = type("R", (), {"parsed": None, "text": payload})()
    result = score(parse_verify(response), [("Un achete, un offert", "Eén kopen, één gratis")])
    assert result.passed is True


@pytest.mark.parametrize(
    ("present", "remaining", "unchanged", "expected"),
    [
        (["B"], [], 5, True),
        ([], [], 5, False),
        (["B"], ["A"], 5, False),
        (["B"], [], 3, False),
        (["B"], [], 4, True),
    ],
)
def test_pass_rule(
    present: list[str], remaining: list[str], unchanged: int, expected: bool
) -> None:
    result = score(
        VerifyResult(
            targets_present=present, sources_remaining=remaining, unchanged_score=unchanged
        ),
        [("A", "B")],
    )
    assert result.passed is expected


def test_model_tag_is_filename_safe() -> None:
    assert model_tag("gemini-2.5-flash-image") == "gemini-2-5-flash-image"


def test_first_image_bytes_raises_when_the_model_returns_none() -> None:
    from adtranslate.image.client import ImageBranchError

    with pytest.raises(ImageBranchError):
        first_image_bytes(FakeResponse(parsed=None))
