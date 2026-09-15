"""The row state machine, proven on the in-memory twin. No network."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adtranslate.models import CopyResult, RowOutcome
from adtranslate.sheet import (
    REQUIRED_HEADERS,
    InMemorySheet,
    SheetContractError,
    country_prefix,
    parse_locale,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sheet"
SHEET_CSV = FIXTURES / "sheet_tab0.csv"
SETTINGS_CSV = FIXTURES / "sheet_tab1.csv"

FROZEN = datetime(2026, 9, 11, 16, 20, 31, tzinfo=UTC)


def make_sheet(now: datetime = FROZEN) -> InMemorySheet:
    return InMemorySheet.from_csv(SHEET_CSV, SETTINGS_CSV, now=lambda: now)


def row_by_id(sheet: InMemorySheet, ad_id: str):
    return next(r for r in sheet.read_rows() if r.id == ad_id)


def blank_id_rows(sheet: InMemorySheet):
    return [r for r in sheet.read_rows() if not r.id.strip()]


def test_fixture_carries_every_required_header() -> None:
    sheet = make_sheet()
    assert sheet.headers == REQUIRED_HEADERS


def test_claim_is_exclusive() -> None:
    sheet = make_sheet()
    row = row_by_id(sheet, "NL-027")

    assert sheet.claim(row) is True
    assert sheet.cells(row.row_number, "Status") == "Processing"
    assert sheet.cells(row.row_number, "Review Note") == "claimed 2026-09-11T16:20:31Z"

    second = row_by_id(sheet, "NL-027")
    assert sheet.claim(second) is False
    assert sheet.cells(row.row_number, "Status") == "Processing"


def test_claim_never_touches_a_row_that_is_not_translate() -> None:
    sheet = make_sheet()
    finished = row_by_id(sheet, "NL-025")
    assert sheet.claim(finished) is False
    assert sheet.writes == []


def test_blank_ids_are_allocated_in_sequence() -> None:
    sheet = make_sheet()
    first, second = blank_id_rows(sheet)

    assert sheet.claim(first) is True
    assert sheet.cells(first.row_number, "ID") == "NL-029"
    assert first.id == "NL-029"

    assert sheet.claim(second) is True
    assert sheet.cells(second.row_number, "ID") == "NL-030"


def test_a_country_outside_the_map_takes_its_prefix_from_the_settings_tag() -> None:
    rows = [
        REQUIRED_HEADERS,
        ["", "Translate", "France", "", "", "https://fb/1", "", "", "", "", "", ""],
        ["", "Translate", "France", "", "", "https://fb/2", "", "", "", "", "", ""],
    ]
    settings = [
        ["Target Country", "Default Language", "Currency"],
        ["France", "French (fr-FR)", "EUR"],
    ]
    sheet = InMemorySheet(rows, settings, now=lambda: FROZEN)
    first, second = sheet.read_rows()

    assert sheet.claim(first) is True
    assert sheet.claim(second) is True
    assert (first.id, second.id) == ("FR-001", "FR-002")


def test_a_country_nowhere_still_gets_a_unique_id() -> None:
    rows = [
        REQUIRED_HEADERS,
        ["", "Translate", "Narnia", "", "", "https://fb/1", "", "", "", "", "", ""],
        ["", "Translate", "Narnia", "", "", "https://fb/2", "", "", "", "", "", ""],
    ]
    settings = [["Target Country", "Default Language", "Currency"]]
    sheet = InMemorySheet(rows, settings, now=lambda: FROZEN)
    first, second = sheet.read_rows()

    assert country_prefix("Narnia") is None
    assert sheet.claim(first) is True
    assert sheet.claim(second) is True
    assert (sheet.cells(2, "ID"), sheet.cells(3, "ID")) == ("NA-001", "NA-002")


def test_stale_reset_fires_after_the_window_and_not_before() -> None:
    sheet = make_sheet()
    row = row_by_id(sheet, "NL-027")
    assert sheet.claim(row) is True

    sheet._now = lambda: FROZEN + timedelta(minutes=29)
    assert sheet.reset_stale(30) == 0
    assert sheet.cells(row.row_number, "Status") == "Processing"

    sheet._now = lambda: FROZEN + timedelta(minutes=31)
    assert sheet.reset_stale(30) == 1
    assert sheet.cells(row.row_number, "Status") == "Translate"
    assert sheet.cells(row.row_number, "Review Note") == "reset: stale claim"


def test_processing_row_with_no_stamp_is_reset() -> None:
    rows = [
        REQUIRED_HEADERS,
        ["NL-031", "Processing", "Netherlands", "", "", "https://fb/1", "", "", "", "", "", ""],
    ]
    settings = [["Target Country", "Default Language", "Currency"]]
    sheet = InMemorySheet(rows, settings, now=lambda: FROZEN)

    assert sheet.reset_stale(30) == 1
    assert sheet.cells(2, "Status") == "Translate"
    assert sheet.cells(2, "Review Note") == "reset: stale claim"


def test_missing_header_names_itself() -> None:
    headers = [h for h in REQUIRED_HEADERS if h != "Review Note"]
    sheet = InMemorySheet(
        [headers, [""] * len(headers)],
        [["Target Country", "Default Language", "Currency"]],
    )
    with pytest.raises(SheetContractError) as exc:
        sheet.read_rows()
    assert "Review Note" in str(exc.value)


def test_pass_through_columns_are_byte_identical_after_claim_and_write() -> None:
    sheet = make_sheet()
    row = row_by_id(sheet, "NL-028")
    pass_through = [
        "Store / product URL",
        "Trendtrack Ad Link",
        "Facebook Ad Link",
        "Target Country",
    ]
    before = {h: sheet.cells(row.row_number, h) for h in pass_through}

    assert sheet.claim(row) is True
    sheet.write(
        row,
        RowOutcome(
            status="Finished",
            copy_result=CopyResult(
                primary_text="tekst", headline="kop", description="omschrijving"
            ),
            drive_url="https://drive.google.com/file/d/abc/view",
            note="",
        ),
    )

    assert {h: sheet.cells(row.row_number, h) for h in pass_through} == before
    assert sheet.cells(row.row_number, "Status") == "Finished"
    assert sheet.cells(row.row_number, "Primary Text") == "tekst"
    assert sheet.cells(row.row_number, "Headline") == "kop"
    assert sheet.cells(row.row_number, "Description") == "omschrijving"
    assert sheet.cells(row.row_number, "Drive URL") == "https://drive.google.com/file/d/abc/view"
    assert sheet.cells(row.row_number, "Review Note") == ""


def test_target_language_comes_from_the_locale() -> None:
    sheet = make_sheet()
    row = row_by_id(sheet, "NL-027")
    assert sheet.claim(row) is True
    sheet.write(row, RowOutcome(status="Needs Review", note="check the image"))
    assert sheet.cells(row.row_number, "Target Language") == "Dutch"
    assert sheet.cells(row.row_number, "Review Note") == "check the image"


def test_failed_writes_only_status_and_note() -> None:
    sheet = make_sheet()
    row = row_by_id(sheet, "NL-027")
    assert sheet.claim(row) is True
    untouched = [h for h in REQUIRED_HEADERS if h not in ("Status", "Review Note")]
    before = {h: sheet.cells(row.row_number, h) for h in untouched}

    sheet.write(row, RowOutcome(status="Failed", note="unknown target country"))

    assert sheet.writes[-1][1] == {"Status": "Failed", "Review Note": "unknown target country"}
    assert {h: sheet.cells(row.row_number, h) for h in untouched} == before


def test_parse_locale_on_every_settings_row() -> None:
    locales = make_sheet().read_locales()
    assert {k: (v.language_name, v.tag, v.currency) for k, v in locales.items()} == {
        "United States": ("English", "en-US", "USD"),
        "Brazil": ("Portuguese", "pt-BR", "BRL"),
        "Netherlands": ("Dutch", "nl-NL", "EUR"),
        "Sweden": ("Swedish", "sv-SE", "SEK"),
    }


def test_parse_locale_without_a_bracket_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        parse_locale("Netherlands", "Dutch", "EUR")
