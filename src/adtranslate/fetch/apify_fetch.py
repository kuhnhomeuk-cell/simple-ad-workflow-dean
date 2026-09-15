"""The fallback fetcher: Apify's `curious_coder/facebook-ads-library-scraper`.

Off by default. It exists for the day Meta changes the Ad Library DOM and the Playwright
path stops parsing: $0.75 per 1,000 ads buys the same `AdCreative` behind the same
`Fetcher` protocol. Selected when `APIFY_TOKEN` is set and the Playwright fetch raised.

Field mapping, from the actor's documented dataset output to `AdCreative`:

====================================  ==========================
actor field                           AdCreative
====================================  ==========================
``ad_archive_id`` / ``adArchiveID``   ``library_id``
``page_name``                         ``page_name``
``snapshot.body.text``                ``primary_text``
``snapshot.title``                    ``headline``
``snapshot.link_description``         ``description``
``snapshot.cta_text``                 ``cta``
``snapshot.caption``                  ``landing_domain`` (upper-cased; the actor
                                      reports it the way the card shows it, and
                                      ``snapshot.link_url``'s host is the fallback)
``snapshot.images[].original_image_url`` ``image_url`` (first image)
``snapshot.videos[].video_hd_url``    ``video_url`` (HD first, then SD)
====================================  ==========================

`media_type` is derived here rather than trusted from the actor, so both fetchers decide it
the same way: video wins, then more than one image is a carousel, then one image, then none.
The actor's own `display_format` is not read, because its spelling has changed between
actor versions and the derived answer needs no version.
"""

from typing import Any, cast
from urllib.parse import urlparse

import httpx

from adtranslate.fetch import AdNotFound
from adtranslate.fetch.playwright_fetch import CAROUSEL_REASON, VIDEO_REASON
from adtranslate.models import AdCreative

ACTOR = "curious_coder/facebook-ads-library-scraper"
RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
AD_URL = "https://www.facebook.com/ads/library/?id={library_id}"


def _first(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if record.get(name) not in (None, ""):
            return record[name]
    return None


def _media(snapshot: dict[str, Any]) -> tuple[list[str], list[str]]:
    images: list[str] = []
    for image in cast("list[dict[str, Any]]", snapshot.get("images") or []):
        url = _first(image, "original_image_url", "resized_image_url", "url")
        if url:
            images.append(str(url))
    videos: list[str] = []
    for video in cast("list[dict[str, Any]]", snapshot.get("videos") or []):
        url = _first(video, "video_hd_url", "video_sd_url", "url")
        if url:
            videos.append(str(url))
    return images, videos


def map_item(item: dict[str, Any], library_id: str) -> AdCreative:
    """Turn one actor dataset item into an `AdCreative`. Public so the mapping is testable."""
    snapshot = cast("dict[str, Any]", item.get("snapshot") or {})
    body = snapshot.get("body")
    if isinstance(body, dict):
        primary_text = str(body.get("text") or "")
    else:
        primary_text = str(body or "")

    domain = str(_first(snapshot, "caption") or "")
    if not domain:
        link = str(_first(snapshot, "link_url") or "")
        host = urlparse(link).hostname or ""
        domain = host.removeprefix("www.")
    domain = domain.removeprefix("www.").upper()

    images, videos = _media(snapshot)
    if videos:
        media_type, reason = "video", VIDEO_REASON
    elif len(images) > 1:
        media_type, reason = "carousel", CAROUSEL_REASON
    elif images:
        media_type, reason = "image", None
    else:
        media_type, reason = "none", None

    return AdCreative(
        library_id=str(_first(item, "ad_archive_id", "adArchiveID") or library_id),
        page_name=str(_first(item, "page_name", "pageName") or ""),
        primary_text=primary_text,
        headline=str(_first(snapshot, "title") or ""),
        description=str(_first(snapshot, "link_description") or ""),
        cta=str(_first(snapshot, "cta_text") or ""),
        landing_domain=domain,
        media_type=cast("Any", media_type),
        image_url=images[0] if images else "",
        video_url=videos[0] if videos else "",
        needs_review_reason=reason,
    )


class ApifyFetcher:
    """`Fetcher` backed by the Apify actor. Never called unless a token is configured."""

    def __init__(self, token: str, timeout_s: float = 180.0, actor: str = ACTOR) -> None:
        if not token:
            raise ValueError("apify fetcher needs a token")
        self.token = token
        self.timeout_s = timeout_s
        self.actor = actor

    def fetch(self, library_id: str) -> AdCreative:
        """Run the actor synchronously on this ad's URL and map the first item back."""
        payload = {
            "urls": [{"url": AD_URL.format(library_id=library_id)}],
            "count": 1,
            "scrapePageAds.activeStatus": "all",
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(
                RUN_URL.format(actor=self.actor),
                params={"token": self.token},
                json=payload,
            )
            response.raise_for_status()
            items = response.json()
        if not items:
            raise AdNotFound(f"apify returned no ad for library id {library_id}")
        return map_item(cast("dict[str, Any]", items[0]), library_id)
