"""The typed shapes that move through the pipeline."""

from typing import Literal

from pydantic import BaseModel


class AdRow(BaseModel):
    """One row of the client's sheet, mapped by header name."""

    row_number: int
    id: str = ""
    status: str = ""
    target_country: str = ""
    facebook_ad_link: str = ""
    drive_url: str = ""
    primary_text: str = ""
    headline: str = ""
    description: str = ""
    target_language: str = ""
    review_note: str = ""
    store_url: str = ""
    trendtrack_link: str = ""


class Locale(BaseModel):
    """One row of the Settings tab."""

    country: str
    language_name: str
    tag: str
    currency: str


class AdCreative(BaseModel):
    """What the fetcher pulls off the Ad Library page."""

    library_id: str
    page_name: str = ""
    primary_text: str = ""
    headline: str = ""
    description: str = ""
    cta: str = ""
    landing_domain: str = ""
    media_type: Literal["image", "video", "carousel", "none"] = "none"
    image_url: str = ""
    video_url: str = ""
    needs_review_reason: str | None = None


class CopyResult(BaseModel):
    """The transcreated three fields plus anything the judge flagged."""

    primary_text: str = ""
    headline: str = ""
    description: str = ""
    translator_notes: str = ""
    review_reasons: list[str] = []


class ImageResult(BaseModel):
    """The creative as it will be uploaded."""

    path: str
    edited: bool = False
    review_reasons: list[str] = []


class RowOutcome(BaseModel):
    """What gets written back to the row."""

    status: Literal["Finished", "Needs Review", "Failed", "Skipped"]
    copy_result: CopyResult | None = None
    drive_url: str | None = None
    note: str = ""
