"""Phase 2: the ad fetcher.

Extraction runs against the saved card HTML in `fixtures/ads/`, so these tests are fast and
need no network. The fixtures were recorded live on 2026-09-11 with
`PlaywrightFetcher.fetch_with_html`, and `test_fixture_matches_recorded_json` pins the saved
HTML to the `AdCreative` recorded beside it in the same run.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from adtranslate.fetch import AdNotFound, download_creative, parse_library_id
from adtranslate.fetch.apify_fetch import ApifyFetcher, map_item
from adtranslate.fetch.playwright_fetch import (
    CAROUSEL_REASON,
    VIDEO_REASON,
    extract_from_html,
)
from adtranslate.models import AdCreative

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ads"

STORY_AD = "1059406850353566"
PROMO_AD = "934283416402786"
MULTI_AD = "27369494302749728"

#: What each recorded card should yield. STORY_AD's listing card carries no link-card block,
#: but the "Link to ad" dialog the ad-id URL opens does (domain, headline, description, CTA),
#: so the fetcher prefers that dialog when it holds exactly one Library ID. MULTI_AD shows
#: "This ad has multiple versions" and no copy anywhere in the Library (verified live
#: 2026-09-11), so its text fields are genuinely empty.
EXPECTED = {
    STORY_AD: {
        "page_name": "Valérie.L",
        "first_line": (
            "On m'a laissé toute une table à moi seule, parce que mon mari et sa mère "
            "avaient oublié de me garder une chaise."
        ),
        "headline": "Un acheté, un offert - se termine ce soir",
        "cta": "Learn more",
        "media_type": "image",
    },
    PROMO_AD: {
        "page_name": "Valérie.L",
        "first_line": "Vous hésitez encore ?",
        "headline": "1 ACHETÉ = 1 OFFERT - Se termine ce soir",
        "cta": "Shop now",
        "media_type": "image",
    },
    MULTI_AD: {
        "page_name": "Valérie.L",
        "first_line": "",
        "headline": "",
        "cta": "Learn More",
        "media_type": "image",
    },
}


@pytest.fixture(scope="module")
def extracted() -> dict[str, AdCreative]:
    """Every fixture card extracted once — one Chromium launch per ad, not per assertion."""
    cards = {
        ad_id: (FIXTURES / ad_id / "page.html").read_text(encoding="utf-8") for ad_id in EXPECTED
    }
    return {ad_id: extract_from_html(html, ad_id) for ad_id, html in cards.items()}


# --- parse_library_id -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://www.facebook.com/ads/library/?id=1059406850353566", "1059406850353566"),
        ("http://facebook.com/ads/library/?id=934283416402786", "934283416402786"),
        (
            "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            "&country=GB&id=27369494302749728&is_targeted_country=false&media_type=all"
            "&search_type=page&sort_data[mode]=total_impressions&view_all_page_id=1092394923965348",
            "27369494302749728",
        ),
        ("934283416402786", "934283416402786"),
        ("  934283416402786  ", "934283416402786"),
    ],
)
def test_parse_library_id_accepts(given: str, expected: str) -> None:
    assert parse_library_id(given) == expected


def test_parse_library_id_ignores_other_id_keys() -> None:
    """`view_all_page_id` is not the ad's id, even though the substring `id=` matches."""
    url = (
        "https://www.facebook.com/ads/library/?view_all_page_id=1092394923965348&id=934283416402786"
    )
    assert parse_library_id(url) == "934283416402786"


@pytest.mark.parametrize(
    "given",
    [
        "",
        "   ",
        "not an ad",
        "https://www.facebook.com/ads/library/?view_all_page_id=1092394923965348",
        "https://www.facebook.com/ads/library/?id=abc",
    ],
)
def test_parse_library_id_rejects(given: str) -> None:
    with pytest.raises(ValueError):
        parse_library_id(given)


# --- extraction against the recorded cards ----------------------------------------------


@pytest.mark.parametrize("ad_id", list(EXPECTED))
def test_extract_from_html(ad_id: str, extracted: dict[str, AdCreative]) -> None:
    creative = extracted[ad_id]
    expected = EXPECTED[ad_id]
    assert creative.library_id == ad_id
    assert creative.page_name == expected["page_name"]
    first_line = creative.primary_text.split("\n")[0]
    assert first_line == expected["first_line"]
    assert creative.headline == expected["headline"]
    assert creative.cta == expected["cta"]
    assert creative.media_type == expected["media_type"]
    assert creative.landing_domain == "AVENORPARIS.COM"


@pytest.mark.parametrize("ad_id", list(EXPECTED))
def test_creative_image_is_fbcdn(ad_id: str, extracted: dict[str, AdCreative]) -> None:
    host = httpx.URL(extracted[ad_id].image_url).host
    assert host.endswith("fbcdn.net"), host
    assert "s60x60" not in extracted[ad_id].image_url  # never the page's profile picture


@pytest.mark.parametrize("ad_id", list(EXPECTED))
def test_fixture_matches_recorded_json(ad_id: str, extracted: dict[str, AdCreative]) -> None:
    """The saved HTML still yields exactly what the live run recorded beside it."""
    recorded = json.loads((FIXTURES / ad_id / "creative.json").read_text(encoding="utf-8"))
    assert extracted[ad_id].model_dump() == recorded


def test_story_ad_keeps_the_whole_body(extracted: dict[str, AdCreative]) -> None:
    """A long story ad is in the DOM in full — no truncation, no "See more" left unclicked."""
    creative = extracted[STORY_AD]
    assert len(creative.primary_text) > 4_000
    assert creative.primary_text.endswith("Un pour vous. Un pour celle qui en a besoin.")
    assert "See more" not in creative.primary_text


def test_image_ads_need_no_review(extracted: dict[str, AdCreative]) -> None:
    assert all(c.needs_review_reason is None for c in extracted.values())


def test_unknown_library_id_raises_ad_not_found() -> None:
    html = (FIXTURES / PROMO_AD / "page.html").read_text(encoding="utf-8")
    with pytest.raises(AdNotFound):
        extract_from_html(html, "1234567890")


def test_missing_card_raises_ad_not_found() -> None:
    with pytest.raises(AdNotFound):
        extract_from_html("<div>nothing here</div>", PROMO_AD)


# --- download_creative ------------------------------------------------------------------


@respx.mock
def test_download_creative_saves_a_jpg(tmp_path: Path) -> None:
    url = "https://scontent-lhr6-1.xx.fbcdn.net/v/t39.35426-6/creative.jpg"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"\xff\xd8jpegbytes"))
    creative = AdCreative(library_id=PROMO_AD, media_type="image", image_url=url)
    path = download_creative(creative, tmp_path / PROMO_AD)
    assert path.name == "creative.jpg"
    assert path.read_bytes() == b"\xff\xd8jpegbytes"


@respx.mock
def test_download_creative_saves_a_video_as_mp4(tmp_path: Path) -> None:
    url = "https://video.xx.fbcdn.net/v/t42.1790-2/clip.mp4"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"mp4bytes"))
    creative = AdCreative(
        library_id="1", media_type="video", video_url=url, needs_review_reason=VIDEO_REASON
    )
    assert download_creative(creative, tmp_path).name == "creative.mp4"


def test_download_creative_refuses_an_empty_ad(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        download_creative(AdCreative(library_id="1", media_type="none"), tmp_path)


# --- the Apify adapter ------------------------------------------------------------------

APIFY_ITEM = {
    "ad_archive_id": "934283416402786",
    "page_name": "Valérie.L",
    "snapshot": {
        "body": {"text": "Vous hésitez encore ?\n\nDes milliers de femmes…"},
        "title": "1 ACHETÉ = 1 OFFERT - Se termine ce soir",
        "link_description": "Rouge à lèvres PH Avenor™ : hydrate, sublime.",
        "cta_text": "Shop now",
        "caption": "avenorparis.com",
        "link_url": "https://avenorparis.com/products/rouge-a-levres-ph-avenor",
        "images": [{"original_image_url": "https://scontent.xx.fbcdn.net/v/full.jpg"}],
        "videos": [],
    },
}


def test_apify_mapping() -> None:
    creative = map_item(APIFY_ITEM, "934283416402786")
    assert creative.library_id == "934283416402786"
    assert creative.page_name == "Valérie.L"
    assert creative.primary_text.startswith("Vous hésitez encore ?")
    assert creative.headline == "1 ACHETÉ = 1 OFFERT - Se termine ce soir"
    assert creative.description == "Rouge à lèvres PH Avenor™ : hydrate, sublime."
    assert creative.cta == "Shop now"
    assert creative.landing_domain == "AVENORPARIS.COM"
    assert creative.media_type == "image"
    assert creative.image_url == "https://scontent.xx.fbcdn.net/v/full.jpg"
    assert creative.needs_review_reason is None


def test_apify_mapping_derives_the_domain_from_the_link_when_the_caption_is_missing() -> None:
    item = {**APIFY_ITEM, "snapshot": {**APIFY_ITEM["snapshot"], "caption": ""}}
    assert map_item(item, "1").landing_domain == "AVENORPARIS.COM"


def test_apify_mapping_flags_video_and_carousel() -> None:
    video = {
        "snapshot": {"videos": [{"video_hd_url": "https://video.xx.fbcdn.net/a.mp4"}], "images": []}
    }
    mapped = map_item(video, "1")
    assert mapped.media_type == "video"
    assert mapped.video_url == "https://video.xx.fbcdn.net/a.mp4"
    assert mapped.needs_review_reason == VIDEO_REASON

    carousel = {
        "snapshot": {
            "images": [
                {"original_image_url": "https://scontent.xx.fbcdn.net/1.jpg"},
                {"original_image_url": "https://scontent.xx.fbcdn.net/2.jpg"},
            ]
        }
    }
    mapped = map_item(carousel, "2")
    assert mapped.media_type == "carousel"
    assert mapped.image_url == "https://scontent.xx.fbcdn.net/1.jpg"
    assert mapped.needs_review_reason == CAROUSEL_REASON


@respx.mock
def test_apify_fetcher_posts_the_ad_url_and_maps_the_first_item() -> None:
    route = respx.post(
        "https://api.apify.com/v2/acts/curious_coder/"
        "facebook-ads-library-scraper/run-sync-get-dataset-items"
    ).mock(return_value=httpx.Response(200, json=[APIFY_ITEM]))
    creative = ApifyFetcher(token="apify_test_token").fetch("934283416402786")
    assert creative.headline == "1 ACHETÉ = 1 OFFERT - Se termine ce soir"
    request = route.calls.last.request
    assert request.url.params["token"] == "apify_test_token"
    assert "934283416402786" in json.loads(request.content)["urls"][0]["url"]


@respx.mock
def test_apify_fetcher_raises_ad_not_found_on_an_empty_dataset() -> None:
    respx.post(url__regex=r".*run-sync-get-dataset-items.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(AdNotFound):
        ApifyFetcher(token="apify_test_token").fetch("1")


def test_apify_fetcher_needs_a_token() -> None:
    with pytest.raises(ValueError):
        ApifyFetcher(token="")


def test_primary_text_keeps_a_domain_mentioned_inside_the_copy() -> None:
    from adtranslate.fetch.playwright_fetch import _primary_text

    card = "EXAMPLE.COM\nOne bought, one free\nEnds tonight\nShop now"
    text = f"Brand\nSponsored\nShop at EXAMPLE.COM tonight.\nTwo for one.\n{card}\n"
    assert _primary_text(text, card) == "Shop at EXAMPLE.COM tonight.\nTwo for one."
