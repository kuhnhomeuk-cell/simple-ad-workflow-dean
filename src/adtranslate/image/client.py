"""The one seam between this branch and `google-genai`.

Every call in the image branch goes through `GenaiClient`, so a test can hand
in a fake and no test ever reaches the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..config import Settings


class ModelsPort(Protocol):
    """The two `google.genai` methods this branch uses."""

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> Any: ...

    def get(self, *, model: str) -> Any: ...


class GenaiClient(Protocol):
    """Structural stand-in for `google.genai.Client`."""

    @property
    def models(self) -> ModelsPort: ...


class ImageBranchError(RuntimeError):
    """The image branch could not complete a step."""


def resolve_client(settings: Settings, client: GenaiClient | None) -> GenaiClient:
    """Return the passed client, or build a real one from the settings key."""
    if client is not None:
        return client
    if not settings.gemini_api_key:
        raise ImageBranchError("no gemini_api_key configured and no client passed")
    from google import genai  # imported lazily so tests never need the key

    return genai.Client(api_key=settings.gemini_api_key)


_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def image_part(path: Path) -> Any:
    """Read `path` into a `types.Part` the models accept."""
    from google.genai import types

    mime = _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)


def json_config(schema: type) -> Any:
    """A JSON-out config pinned to a pydantic response schema."""
    from google.genai import types

    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    )
