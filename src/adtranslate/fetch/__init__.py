"""Turning a Facebook Ad Library link into an `AdCreative`.

The Library ID is the key. `https://www.facebook.com/ads/library/?id=<id>` opens the
advertiser's whole listing with the target ad's card in it, so every extraction is
scoped to the card that carries the text `Library ID: <id>`.
"""

from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

import httpx

from adtranslate.models import AdCreative

__all__ = [
    "AdNotFound",
    "Fetcher",
    "download_creative",
    "parse_library_id",
]


class AdNotFound(Exception):
    """The Library ID never appeared on the page."""


class Fetcher(Protocol):
    """One way of turning a Library ID into an `AdCreative`."""

    def fetch(self, library_id: str) -> AdCreative:
        """Return the ad's copy and creative URLs, or raise `AdNotFound`."""
        ...


def parse_library_id(url: str) -> str:
    """Pull the Library ID out of an Ad Library link, or accept a bare ID.

    Accepts the plain `?id=123` form, the long filtered form the client's Looms show
    (`...&id=123&...&view_all_page_id=456`, where only the exact `id` key counts), and a
    bare numeric string. Anything else is a `ValueError`.
    """
    candidate = url.strip()
    if not candidate:
        raise ValueError("empty ad reference")
    if candidate.isdigit():
        return candidate

    parsed = urlparse(candidate)
    if not parsed.scheme and not parsed.query:
        raise ValueError(f"not an ad library link or a library id: {url!r}")
    values = parse_qs(parsed.query).get("id", [])
    for value in values:
        stripped = value.strip()
        if stripped.isdigit():
            return stripped
    raise ValueError(f"no library id in {url!r}")


def download_creative(creative: AdCreative, dest_dir: Path) -> Path:
    """Save the ad's creative into `dest_dir` and return the file written.

    Videos land as `creative.mp4`, everything else as `creative.jpg`. A carousel keeps the
    first image, which is what `needs_review_reason` already says.
    """
    if creative.video_url:
        url, dest = creative.video_url, dest_dir / "creative.mp4"
    elif creative.image_url:
        url, dest = creative.image_url, dest_dir / "creative.jpg"
    else:
        raise ValueError(f"ad {creative.library_id} carries no creative to download")

    dest_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)
    return dest
