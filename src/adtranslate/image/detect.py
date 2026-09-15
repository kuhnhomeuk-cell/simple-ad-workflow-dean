"""Step 1: does this creative carry text, and which of it should be translated."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..config import Settings
from .client import GenaiClient, ImageBranchError, image_part, json_config, resolve_client

Role = Literal["promo", "price", "handwritten", "logo", "other"]

PROMPT = """You are reading an advertising creative.

List every piece of text burned into the image, exactly as it appears, in reading order.
For each piece give:
- text: the exact string, including accents and punctuation
- role: promo (an offer or marketing line), price, handwritten, logo (a brand mark,
  wordmark or watermark), or other
- translate: true if the string should be translated into another language for a
  different market; false for logos, brand names, watermarks, URLs, handles and
  anything else that must stay exactly as it is.

Set has_text to false and return no blocks if the image carries no legible text at all.
Report only what you can actually read. Never invent text."""


class TextBlock(BaseModel):
    """One run of text found in the creative."""

    text: str
    role: Role = "other"
    translate: bool = True


class TextScan(BaseModel):
    """What the vision model read off one creative."""

    has_text: bool = False
    blocks: list[TextBlock] = Field(default_factory=list)


def has_translatable(scan: TextScan) -> bool:
    """True when the scan found at least one block worth translating."""
    return scan.has_text and any(b.translate and b.text.strip() for b in scan.blocks)


def translatable_strings(scan: TextScan) -> list[str]:
    """The block strings to send to the copy model, de-duplicated, order kept."""
    seen: list[str] = []
    for block in scan.blocks:
        text = block.text.strip()
        if block.translate and text and text not in seen:
            seen.append(text)
    return seen


def detect_text(
    image_path: Path | str,
    settings: Settings,
    client: GenaiClient | None = None,
) -> TextScan:
    """Read `image_path` with the vision model and return the typed scan."""
    path = Path(image_path)
    genai_client = resolve_client(settings, client)
    response = genai_client.models.generate_content(
        model=settings.vision_model,
        contents=[PROMPT, image_part(path)],
        config=json_config(TextScan),
    )
    return parse_scan(response)


def parse_scan(response: object) -> TextScan:
    """Take `TextScan` off a response, preferring `.parsed`, falling back to `.text`."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, TextScan):
        return parsed
    if isinstance(parsed, dict):
        return TextScan.model_validate(parsed)
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return TextScan.model_validate_json(text)
    raise ImageBranchError("vision model returned no readable text scan")
