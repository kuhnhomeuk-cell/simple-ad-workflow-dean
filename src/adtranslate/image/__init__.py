"""The image branch: detect burned-in text, replace it, read the result back.

No text means no edit. Text means edit, verify, one retry on the Pro tier, then
the original with a review reason and both attempts kept on disk.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..config import Settings
from ..models import ImageResult
from .client import GenaiClient, ImageBranchError
from .detect import (
    TextBlock,
    TextScan,
    detect_text,
    has_translatable,
    translatable_strings,
)
from .edit import edit_text, to_png
from .verify import VerifyResult, verify_edit

NOT_VERIFIED = "image text not verified"
NO_EDITOR = "no image editor on this machine: the text in the image was not translated"

__all__ = [
    "NO_EDITOR",
    "NOT_VERIFIED",
    "GenaiClient",
    "ImageBranchError",
    "TextBlock",
    "TextScan",
    "VerifyResult",
    "detect_text",
    "edit_text",
    "has_translatable",
    "to_png",
    "translatable_strings",
    "translate_image",
    "verify_edit",
]


def translate_image(
    image_path: Path | str,
    translations: Callable[[list[str]], list[str]],
    settings: Settings,
    client: GenaiClient | None = None,
    out_dir: Path | None = None,
) -> ImageResult:
    """Run the whole branch on one creative and return what should be uploaded."""
    path = Path(image_path)
    scan = detect_text(path, settings, client)
    if not has_translatable(scan):
        return ImageResult(path=str(to_png(path, out_dir)), edited=False)

    sources = translatable_strings(scan)
    targets = translations(sources)
    if len(targets) != len(sources):
        raise ImageBranchError(
            f"translator returned {len(targets)} strings for {len(sources)} sources"
        )
    replacements = [
        (source, target.strip())
        for source, target in zip(sources, targets, strict=True)
        if target.strip()
    ]
    if not replacements:
        return ImageResult(path=str(to_png(path, out_dir)), edited=False)

    for model in (settings.image_model, settings.image_retry_model):
        edited = edit_text(path, replacements, model, settings, client, out_dir)
        result = verify_edit(path, edited, replacements, settings, client)
        if result.passed:
            return ImageResult(path=str(edited), edited=True)

    return ImageResult(
        path=str(to_png(path, out_dir)),
        edited=False,
        review_reasons=[NOT_VERIFIED],
    )
