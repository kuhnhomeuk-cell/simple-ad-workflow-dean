"""Step 2: replace the burned-in strings and change nothing else."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import Settings
from .client import GenaiClient, ImageBranchError, image_part, resolve_client

INSTRUCTION_HEAD = (
    "Edit this advertising image so that the text reads in the new language.\n"
    "Replace exactly these strings, one for one:\n"
)
INSTRUCTION_TAIL = (
    "\nChange nothing else. Keep the same font style, weight, colour, size, alignment "
    "and position for every replaced string, keep every other piece of text exactly as "
    "it is, and keep the photograph, product, background, lighting and layout exactly "
    "as they are. Do not crop, do not re-frame, do not restyle, do not add or remove "
    "anything. Return the edited image at the same dimensions as the input."
)


def model_tag(model: str) -> str:
    """A filename-safe tag for a model id, e.g. `gemini-2-5-flash-image`."""
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-") or "model"


def build_instruction(replacements: list[tuple[str, str]]) -> str:
    """The one instruction listing every source string and its replacement."""
    lines = "".join(f'- "{source}" becomes "{target}"\n' for source, target in replacements)
    return INSTRUCTION_HEAD + lines + INSTRUCTION_TAIL


def edit_text(
    image_path: Path | str,
    replacements: list[tuple[str, str]],
    model: str,
    settings: Settings,
    client: GenaiClient | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Run one edit pass and write the result as a PNG the size of the source."""
    path = Path(image_path)
    if not replacements:
        raise ImageBranchError("edit_text called with no replacements")
    genai_client = resolve_client(settings, client)
    response = genai_client.models.generate_content(
        model=model,
        contents=[build_instruction(replacements), image_part(path)],
        config=None,
    )
    data = first_image_bytes(response)
    destination = (out_dir or path.parent) / f"{path.stem}.{model_tag(model)}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_png_matching(data, path, destination)
    return destination


def first_image_bytes(response: object) -> bytes:
    """Pull the first inline image payload out of a generate_content response."""
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline: Any = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if isinstance(data, bytes) and data:
                return data
    raise ImageBranchError("image model returned no image data")


def write_png_matching(data: bytes, source: Path, destination: Path) -> None:
    """Write `data` to `destination` as a PNG the exact pixel size of `source`."""
    with Image.open(io.BytesIO(data)) as edited, Image.open(source) as original:
        size = original.size
        out = edited.convert("RGB")
        if out.size != size:
            out = out.resize(size, Image.Resampling.LANCZOS)
        out.save(destination, format="PNG")


def to_png(source: Path | str, out_dir: Path | None = None) -> Path:
    """Convert a creative to PNG untouched, keeping its pixel size."""
    path = Path(source)
    target_dir = out_dir or path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{path.stem}.png"
    if destination.resolve() == path.resolve():
        destination = target_dir / f"{path.stem}.original.png"
    with Image.open(path) as original:
        original.convert("RGB").save(destination, format="PNG")
    return destination
