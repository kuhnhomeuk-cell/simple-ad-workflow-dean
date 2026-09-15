"""Step 3: an independent read-back of the edit, never the edit's own claim."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..config import Settings
from .client import GenaiClient, ImageBranchError, image_part, json_config, resolve_client

UNCHANGED_MIN = 4

PROMPT_HEAD = (
    "Two images: the FIRST is the original advertising creative, the SECOND is an edited "
    "version where some burned-in text was meant to be replaced.\n\n"
    "The intended replacements were:\n"
)
PROMPT_TAIL = (
    "\nRead the SECOND image and answer:\n"
    "- targets_present: which of the target strings you can actually read in it\n"
    "- sources_remaining: which of the source strings are still visible in it\n"
    "- unchanged_score: 1 to 5 for how completely everything else (the photo, layout, "
    "colours, other text, fonts, positions) is unchanged from the first image, where 5 "
    "means identical apart from the replaced strings.\n\n"
    "Report only what you can actually read. Never assume a replacement worked."
)


class VerifyResult(BaseModel):
    """The read-back gate's verdict on one edited creative."""

    targets_present: list[str] = Field(default_factory=list)
    sources_remaining: list[str] = Field(default_factory=list)
    unchanged_score: int = 0
    passed: bool = False


def build_prompt(replacements: list[tuple[str, str]]) -> str:
    """The read-back prompt naming every source and target string."""
    lines = "".join(f'- "{source}" should now read "{target}"\n' for source, target in replacements)
    return PROMPT_HEAD + lines + PROMPT_TAIL


def score(result: VerifyResult, replacements: list[tuple[str, str]]) -> VerifyResult:
    """Decide `passed` here, deterministically, whatever the model claimed."""
    present = {t.strip().casefold() for t in result.targets_present}
    remaining = {s.strip().casefold() for s in result.sources_remaining}
    targets = {target.strip().casefold() for _, target in replacements}
    sources = {source.strip().casefold() for source, _ in replacements}
    return result.model_copy(
        update={
            "passed": targets <= present
            and not (remaining & sources)
            and result.unchanged_score >= UNCHANGED_MIN
        }
    )


def verify_edit(
    original: Path | str,
    edited: Path | str,
    replacements: list[tuple[str, str]],
    settings: Settings,
    client: GenaiClient | None = None,
) -> VerifyResult:
    """Read the edited creative back with vision and rule on it."""
    genai_client = resolve_client(settings, client)
    response = genai_client.models.generate_content(
        model=settings.vision_model,
        contents=[
            build_prompt(replacements),
            image_part(Path(original)),
            image_part(Path(edited)),
        ],
        config=json_config(VerifyResult),
    )
    return score(parse_verify(response), replacements)


def parse_verify(response: object) -> VerifyResult:
    """Take `VerifyResult` off a response, preferring `.parsed`, falling back to `.text`."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, VerifyResult):
        return parsed
    if isinstance(parsed, dict):
        return VerifyResult.model_validate(parsed)
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return VerifyResult.model_validate_json(text)
    raise ImageBranchError("vision model returned no readable verification")
