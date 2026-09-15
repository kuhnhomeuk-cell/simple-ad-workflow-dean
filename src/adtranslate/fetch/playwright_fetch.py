"""The primary fetcher: read the ad straight off the Ad Library page with Chromium.

Everything is scoped to the one card that carries the text `Library ID: <id>`, because the
`?id=` URL opens the advertiser's whole listing (~42 cards for this advertiser), not a
single ad.

Card shape, verified against the three example ads on 2026-09-11:

    Library ID: <id> / Started running on <date> / Platforms / ...
    <page name>
    Sponsored
    <primary text>                      <- long story ads are in the DOM in full
    <img> or <video>                    <- the creative; scontent-*.xx.fbcdn.net
    AVENORPARIS.COM                     <- the link card: domain in caps
    <headline>
    <description>
    [Shop now]                          <- the CTA button, when the ad has one

Two shapes the walk-up has to survive. On 934283416402786 the link card sits inside the
container that first holds the creative and the "Sponsored" label. On 1059406850353566 the
creative `<img>` is itself wrapped in the `l.php` anchor, and the link card is a sibling one
level higher, so the walk keeps widening while the ancestor still describes exactly one ad
(one `Library ID:` occurrence). Wheel-scrolling the virtualised list makes Meta re-render a
card and drop its text, so the fetcher reads without scrolling.
"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from adtranslate.fetch import AdNotFound
from adtranslate.models import AdCreative

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
AD_LIBRARY_URL = "https://www.facebook.com/ads/library/?id={library_id}"
ID_TIMEOUT_MS = 20_000

COOKIE_BUTTONS = (
    "Decline optional cookies",
    "Only allow essential cookies",
    "Refuser les cookies facultatifs",
    "Decline",
)

#: Labels Meta renders on the link card's call-to-action button. "See details" and
#: "See summary details" are card controls, not a CTA, so they are deliberately absent.
CTA_LABELS = frozenset(
    label.casefold()
    for label in (
        "Apply Now",
        "Book Now",
        "Buy Now",
        "Contact Us",
        "Donate Now",
        "Download",
        "Get Offer",
        "Get Quote",
        "Install Now",
        "Learn More",
        "Listen Now",
        "Order Now",
        "Play Game",
        "See Menu",
        "Send Message",
        "Shop Now",
        "Sign Up",
        "Subscribe",
        "Watch More",
    )
)

#: Meta's profile-picture variants. Never the ad's creative.
PROFILE_MARKERS = ("p148x148", "s60x60", "p50x50", "s50x50")
MIN_CREATIVE_PX = 100

_DOMAIN_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]*\.[A-Z]{2,}$")
_ZERO_WIDTH = "​‎‏﻿"

VIDEO_REASON = "video creative: out of scope in v1"
CAROUSEL_REASON = "carousel creative: first image used"

# Clicking through Playwright needs the element visible; the card's own "See more" is a
# plain div, so a DOM click is both enough and scroll-free.
_EXPAND_JS = """
(lid) => {
  const container = window.__adtranslate_container(lid);
  if (!container) return 0;
  let clicked = 0;
  for (const el of container.querySelectorAll('div, span, [role=button]')) {
    const t = (el.innerText || '').trim();
    if ((t === 'See more' || t === 'Voir plus' || t === 'See More') && el.children.length === 0) {
      el.click();
      clicked += 1;
    }
  }
  return clicked;
}
"""

# Finds the one ad card and reads it. Installed as a global so the expand step and the
# extract step agree on exactly which element they are talking about.
_CONTAINER_JS = """
window.__adtranslate_container = (lid) => {
  const needle = 'Library ID: ' + lid;
  // The ad-id URL opens a "Link to ad" dialog for that one ad; it carries the link card
  // (domain, headline, description, CTA) even when the listing card behind it does not.
  for (const d of document.querySelectorAll('[role="dialog"]')) {
    const dt = d.innerText || '';
    if (dt.includes(needle) && ((dt.match(/Library ID:/g) || []).length === 1)) return d;
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node = null, hit = null;
  while ((node = walker.nextNode())) {
    if ((node.textContent || '').includes(needle)) { hit = node.parentElement; break; }
  }
  if (!hit) return null;
  let el = hit;
  // The floor the brief names: the nearest ancestor holding the creative and the label.
  while (el && el !== document.body) {
    if (el.querySelector('img, video') && (el.innerText || '').includes('Sponsored')) break;
    el = el.parentElement;
  }
  if (!el || el === document.body) return null;
  // Then widen while the ancestor still describes this one ad, so a link card rendered as
  // a sibling of the creative comes with it.
  const ads = (e) => ((e.innerText || '').match(/Library ID:/g) || []).length;
  while (el.parentElement && el.parentElement !== document.body && ads(el.parentElement) === 1) {
    el = el.parentElement;
  }
  return el;
};
"""

_EXTRACT_JS = """
(lid) => {
  const container = window.__adtranslate_container(lid);
  if (!container) return null;
  const images = [...container.querySelectorAll('img')]
    .filter((i) => (i.src || '').includes('fbcdn'))
    .map((i) => ({ src: i.src, w: i.naturalWidth, h: i.naturalHeight }));
  const videos = [];
  for (const v of container.querySelectorAll('video')) {
    if (v.src) videos.push(v.src);
    for (const s of v.querySelectorAll('source')) if (s.src) videos.push(s.src);
  }
  const anchors = [...container.querySelectorAll('a')]
    .filter((a) => (a.href || '').includes('l.php') || (a.href || '').includes('l.facebook.com'))
    .map((a) => a.href);
  // The link card: the OUTERMOST element whose first line is a bare capitalised domain.
  // Any ancestor of the real card starts with the primary text instead, so the longest
  // still-domain-first block is the whole card; the innermost is the domain alone.
  let card = null;
  for (const el of container.querySelectorAll('div, a')) {
    const text = (el.innerText || '').trim();
    if (!text || text.includes('Sponsored') || text.includes('Library ID:')) continue;
    const first = text.split('\\n').map((l) => l.trim()).filter(Boolean)[0] || '';
    if (!/^[A-Z0-9][A-Z0-9.\\-]*\\.[A-Z]{2,}$/.test(first)) continue;
    if (card === null || text.length > card.length) card = text;
  }
  return {
    text: container.innerText || '',
    card: card,
    images: images,
    videos: videos,
    anchors: anchors,
    html: container.outerHTML,
  };
}
"""


def _clean(line: str) -> str:
    for ch in _ZERO_WIDTH:
        line = line.replace(ch, "")
    return line.strip()


def _page_name(text: str) -> str:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if _clean(line) == "Sponsored":
            for previous in reversed(lines[:index]):
                candidate = _clean(previous)
                if candidate and candidate != "Sponsored":
                    return candidate
            return ""
    return ""


def _primary_text(text: str, card: str | None) -> str:
    marker = "\nSponsored\n"
    start = text.find(marker)
    if start < 0:
        return ""
    body = text[start + len(marker) :]
    if card:
        cut = body.find(card.split("\n")[0])
        if cut >= 0:
            body = body[:cut]
    return body.strip()


def _card_fields(card: str | None) -> tuple[str, str, str, str]:
    """Split the link card into (landing_domain, headline, description, cta)."""
    if not card:
        return "", "", "", ""
    lines = [_clean(line) for line in card.split("\n")]
    lines = [line for line in lines if line]
    if not lines or not _DOMAIN_RE.match(lines[0]):
        return "", "", "", ""
    domain, rest = lines[0], lines[1:]
    cta = ""
    if rest and rest[-1].casefold() in CTA_LABELS:
        cta = rest.pop()
    headline = rest[0] if rest else ""
    description = "\n".join(rest[1:]) if len(rest) > 1 else ""
    return domain, headline, description, cta


def _domain_from_anchor(anchors: list[str]) -> str:
    from urllib.parse import parse_qs, urlparse

    for href in anchors:
        target = parse_qs(urlparse(href).query).get("u", [])
        for value in target:
            host = urlparse(value).hostname or ""
            if host:
                return host.removeprefix("www.").upper()
    return ""


def _is_profile(image: dict[str, Any]) -> bool:
    src = str(image.get("src", ""))
    if any(marker in src for marker in PROFILE_MARKERS):
        return True
    width, height = int(image.get("w") or 0), int(image.get("h") or 0)
    return 0 < max(width, height) <= MIN_CREATIVE_PX


def _creative_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ad creatives only, one per distinct fbcdn path, largest variant of each kept."""
    by_path: dict[str, dict[str, Any]] = {}
    for image in images:
        if _is_profile(image):
            continue
        path = str(image.get("src", "")).split("?")[0]
        area = int(image.get("w") or 0) * int(image.get("h") or 0)
        best = by_path.get(path)
        if best is None or area > int(best.get("w") or 0) * int(best.get("h") or 0):
            by_path[path] = image
    ordered = sorted(
        by_path.values(),
        key=lambda i: int(i.get("w") or 0) * int(i.get("h") or 0),
        reverse=True,
    )
    return ordered


def _build(library_id: str, payload: dict[str, Any]) -> AdCreative:
    text = str(payload.get("text", ""))
    card = cast("str | None", payload.get("card"))
    images = cast("list[dict[str, Any]]", payload.get("images") or [])
    videos = [v for v in cast("list[str]", payload.get("videos") or []) if v]
    anchors = cast("list[str]", payload.get("anchors") or [])

    domain, headline, description, cta = _card_fields(card)
    if not domain:
        domain = _domain_from_anchor(anchors)
    creatives = _creative_images(images)

    if videos:
        media_type = "video"
        reason: str | None = VIDEO_REASON
    elif len(creatives) > 1:
        media_type = "carousel"
        reason = CAROUSEL_REASON
    elif len(creatives) == 1:
        media_type = "image"
        reason = None
    else:
        media_type = "none"
        reason = None

    return AdCreative(
        library_id=library_id,
        page_name=_page_name(text),
        primary_text=_primary_text(text, card),
        headline=headline,
        description=description,
        cta=cta,
        landing_domain=domain,
        media_type=cast("Any", media_type),
        image_url=str(creatives[0]["src"]) if creatives else "",
        video_url=videos[0] if videos else "",
        needs_review_reason=reason,
    )


def _dismiss_cookies(page: Page) -> None:
    for label in COOKIE_BUTTONS:
        try:
            button = page.get_by_role("button", name=label, exact=True)
            if button.count():
                button.first.click(timeout=3_000)
                return
        except PlaywrightTimeoutError:
            continue
        except Exception:  # noqa: BLE001 - a missing banner is the normal case
            continue


def _read_card(page: Page, library_id: str) -> AdCreative:
    page.evaluate(_CONTAINER_JS)
    page.evaluate(_EXPAND_JS, library_id)
    payload = page.evaluate(_EXTRACT_JS, library_id)
    if payload is None:
        raise AdNotFound(f"no ad card for library id {library_id}")
    return _build(library_id, cast("dict[str, Any]", payload))


def container_html(page: Page, library_id: str) -> str:
    """The one ad card's `outerHTML` — what a fixture records."""
    page.evaluate(_CONTAINER_JS)
    payload = page.evaluate(_EXTRACT_JS, library_id)
    if payload is None:
        raise AdNotFound(f"no ad card for library id {library_id}")
    return str(cast("dict[str, Any]", payload).get("html", ""))


class PlaywrightFetcher:
    """Read the ad off `facebook.com/ads/library/?id=<id>` with headless Chromium."""

    def __init__(self, headless: bool = True, timeout_ms: int = ID_TIMEOUT_MS) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms

    @contextmanager
    def _card_page(self, library_id: str) -> Iterator[Page]:
        """Open the listing and hold a page parked on this ad's card."""
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=self.headless)
            try:
                context = browser.new_context(
                    user_agent=DESKTOP_UA,
                    locale="en-GB",
                    timezone_id="Europe/London",
                    viewport={"width": 1440, "height": 1200},
                )
                page = context.new_page()
                page.goto(
                    AD_LIBRARY_URL.format(library_id=library_id),
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                _dismiss_cookies(page)
                try:
                    page.locator(f"text=Library ID: {library_id}").first.wait_for(
                        timeout=self.timeout_ms
                    )
                except PlaywrightTimeoutError as exc:
                    raise AdNotFound(
                        f"library id {library_id} never appeared within {self.timeout_ms // 1000}s"
                    ) from exc
                # The card renders its link block a beat after the ID text lands, and
                # wheel-scrolling would make the virtualised list re-render it away.
                page.wait_for_timeout(4_000)
                yield page
            finally:
                browser.close()

    def fetch(self, library_id: str) -> AdCreative:
        """Load the listing, scope to this ad's card and read it. `AdNotFound` on a miss."""
        with self._card_page(library_id) as page:
            return _read_card(page, library_id)

    def fetch_with_html(self, library_id: str) -> tuple[AdCreative, str]:
        """Fetch, and hand back the card's HTML too — the record mode behind the fixtures."""
        with self._card_page(library_id) as page:
            return _read_card(page, library_id), container_html(page, library_id)


def extract_from_html(html: str, library_id: str) -> AdCreative:
    """Run the live extraction over saved HTML, so fixture tests need no network."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            # A fixture is read offline: nothing in the saved HTML may reach the network.
            page.route("**/*", lambda route: route.abort())
            page.set_content(html, wait_until="domcontentloaded")
            return _read_card(page, library_id)
        finally:
            browser.close()


def as_json(creative: AdCreative) -> str:
    """The creative as a fixture records it."""
    return json.dumps(creative.model_dump(), indent=2, ensure_ascii=False) + "\n"
