"""The sheet is the interface and the database.

Every read and write is header-mapped, deterministic and idempotent. Two
implementations share one body of logic: `InMemorySheet` for the tests and
`GoogleSheet` for the real spreadsheet.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from adtranslate.models import AdRow, Locale, RowOutcome

# The Sheet1 headers, in the order the client's sheet carries them.
REQUIRED_HEADERS: list[str] = [
    "ID",
    "Status",
    "Target Country",
    "Store / product URL",
    "Trendtrack Ad Link",
    "Facebook Ad Link",
    "Drive URL",
    "Primary Text",
    "Headline",
    "Description",
    "Target Language",
    "Review Note",
]

SETTINGS_HEADERS: list[str] = ["Target Country", "Default Language", "Currency"]

# Headers `write()` is allowed to touch. Store / product URL, Trendtrack Ad
# Link, Facebook Ad Link and Target Country are pass-through, always.
WRITABLE_HEADERS: list[str] = [
    "Drive URL",
    "Primary Text",
    "Headline",
    "Description",
    "Target Language",
    "Status",
    "Review Note",
]

COUNTRY_PREFIXES: dict[str, str] = {
    "Netherlands": "NL",
    "United States": "US",
    "Brazil": "BR",
    "Sweden": "SE",
}

STATUS_TRANSLATE = "Translate"
STATUS_PROCESSING = "Processing"
CLAIM_PREFIX = "claimed "
STALE_NOTE = "reset: stale claim"

SHEET_TAB = "Sheet1"
SETTINGS_TAB = "Settings"


class SheetContractError(Exception):
    """The sheet does not carry a header the engine needs."""


class SheetPort(Protocol):
    """What the pipeline needs from the sheet."""

    def read_headers(self) -> list[str]: ...

    def read_rows(self) -> list[AdRow]: ...

    def read_locales(self) -> dict[str, Locale]: ...

    def claim(self, row: AdRow) -> bool: ...

    def write(self, row: AdRow, outcome: RowOutcome) -> None: ...

    def reset_stale(self, minutes: int) -> int: ...


# --------------------------------------------------------------------------
# Shared logic — pure functions, used by both implementations.
# --------------------------------------------------------------------------


def check_headers(headers: Sequence[str], required: Sequence[str] = REQUIRED_HEADERS) -> None:
    """Raise `SheetContractError` naming the first missing header."""
    present = {h.strip() for h in headers}
    for name in required:
        if name not in present:
            raise SheetContractError(f"missing required header: {name!r}")


def header_index(headers: Sequence[str], name: str) -> int:
    """The column position of `name`. Lookup is by header name, never by letter."""
    for i, header in enumerate(headers):
        if header.strip() == name:
            return i
    raise SheetContractError(f"missing required header: {name!r}")


def cell(headers: Sequence[str], values: Sequence[str], name: str) -> str:
    index = header_index(headers, name)
    return values[index] if index < len(values) else ""


def row_from_cells(headers: Sequence[str], values: Sequence[str], row_number: int) -> AdRow:
    """Map one sheet row onto `AdRow` by header name."""
    return AdRow(
        row_number=row_number,
        id=cell(headers, values, "ID"),
        status=cell(headers, values, "Status"),
        target_country=cell(headers, values, "Target Country"),
        facebook_ad_link=cell(headers, values, "Facebook Ad Link"),
        drive_url=cell(headers, values, "Drive URL"),
        primary_text=cell(headers, values, "Primary Text"),
        headline=cell(headers, values, "Headline"),
        description=cell(headers, values, "Description"),
        target_language=cell(headers, values, "Target Language"),
        review_note=cell(headers, values, "Review Note"),
        store_url=cell(headers, values, "Store / product URL"),
        trendtrack_link=cell(headers, values, "Trendtrack Ad Link"),
    )


def parse_locale(country: str, default_language: str, currency: str) -> Locale:
    """Split `Dutch (nl-NL)` into a language name and a BCP-47 tag."""
    text = default_language.strip()
    open_bracket = text.find("(")
    close_bracket = text.rfind(")")
    if open_bracket == -1 or close_bracket == -1 or close_bracket < open_bracket:
        raise ValueError(f"no BCP-47 tag in default language {default_language!r} for {country!r}")
    name = text[:open_bracket].strip()
    tag = text[open_bracket + 1 : close_bracket].strip()
    if not name or not tag:
        raise ValueError(f"no BCP-47 tag in default language {default_language!r} for {country!r}")
    return Locale(country=country.strip(), language_name=name, tag=tag, currency=currency.strip())


def country_prefix(country: str) -> str | None:
    """The ID prefix for a country, or None when the country is unknown."""
    return COUNTRY_PREFIXES.get(country.strip())


def next_id(prefix: str, existing_ids: Iterable[str]) -> str:
    """`NL-028` present → `NL-029`. Nothing present → `NL-001`."""
    highest = 0
    head = f"{prefix}-"
    for value in existing_ids:
        text = value.strip()
        if not text.startswith(head):
            continue
        tail = text[len(head) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}-{highest + 1:03d}"


def claim_stamp(now: datetime) -> str:
    """The Review Note a claim writes: `claimed 2026-09-11T16:20:31Z`."""
    moment = now.astimezone(UTC).replace(microsecond=0)
    return CLAIM_PREFIX + moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_claim_stamp(note: str) -> datetime | None:
    """Read the timestamp out of a claim note, or None when there isn't one."""
    text = note.strip()
    if not text.startswith(CLAIM_PREFIX):
        return None
    rest = text[len(CLAIM_PREFIX) :].strip().split()
    if not rest:
        return None
    raw = rest[0]
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def outcome_updates(outcome: RowOutcome, locale: Locale | None) -> dict[str, str]:
    """The exact cells `write()` sets, keyed by header name."""
    updates: dict[str, str] = {"Status": outcome.status, "Review Note": outcome.note}
    if outcome.status == "Failed":
        return updates
    if outcome.drive_url is not None:
        updates["Drive URL"] = outcome.drive_url
    if outcome.copy_result is not None:
        updates["Primary Text"] = outcome.copy_result.primary_text
        updates["Headline"] = outcome.copy_result.headline
        updates["Description"] = outcome.copy_result.description
    if locale is not None:
        updates["Target Language"] = locale.language_name
    return updates


def column_letter(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA. For the Sheets A1 ranges only."""
    letters = ""
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


# --------------------------------------------------------------------------
# The shared state machine. Both implementations inherit it.
# --------------------------------------------------------------------------


class _BaseSheet(ABC):
    """Claim / write / reset, once, over an abstract grid of cells."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now: Callable[[], datetime] = now or (lambda: datetime.now(UTC))

    # --- what each implementation supplies -------------------------------

    @abstractmethod
    def _load(self) -> tuple[list[str], list[list[str]]]:
        """Headers plus the data rows, read once. Row N of the data is sheet row N + 2."""

    @abstractmethod
    def _load_settings(self) -> tuple[list[str], list[list[str]]]:
        """Headers plus the data rows of the Settings tab."""

    @abstractmethod
    def _write_cells(self, row_number: int, updates: dict[str, str]) -> None:
        """Set the named cells on one sheet row."""

    # --- the port --------------------------------------------------------

    def read_headers(self) -> list[str]:
        headers, _ = self._load()
        check_headers(headers)
        return headers

    def read_rows(self) -> list[AdRow]:
        headers, values = self._load()
        check_headers(headers)
        return [row_from_cells(headers, v, i + 2) for i, v in enumerate(values)]

    def read_locales(self) -> dict[str, Locale]:
        headers, values = self._load_settings()
        check_headers(headers, SETTINGS_HEADERS)
        locales: dict[str, Locale] = {}
        for v in values:
            country = cell(headers, v, "Target Country").strip()
            if not country:
                continue
            locale = parse_locale(
                country,
                cell(headers, v, "Default Language"),
                cell(headers, v, "Currency"),
            )
            locales[locale.country] = locale
        return locales

    def claim(self, row: AdRow) -> bool:
        headers, values = self._load()
        check_headers(headers)
        index = row.row_number - 2
        if index < 0 or index >= len(values):
            return False
        current = row_from_cells(headers, values[index], row.row_number)
        if current.status.strip() != STATUS_TRANSLATE:
            return False

        stamp = claim_stamp(self._now())
        updates: dict[str, str] = {"Status": STATUS_PROCESSING, "Review Note": stamp}

        allocated = current.id.strip()
        if not allocated:
            prefix = country_prefix(current.target_country)
            if prefix is not None:
                allocated = next_id(prefix, (cell(headers, v, "ID") for v in values))
                updates["ID"] = allocated

        self._write_cells(row.row_number, updates)

        headers, values = self._load()
        confirmed = row_from_cells(headers, values[index], row.row_number)
        if confirmed.status.strip() != STATUS_PROCESSING or confirmed.review_note.strip() != stamp:
            return False
        row.status = STATUS_PROCESSING
        row.review_note = stamp
        if allocated:
            row.id = allocated
        return True

    def write(self, row: AdRow, outcome: RowOutcome) -> None:
        headers, _ = self._load()
        check_headers(headers)
        locale = None
        if outcome.status != "Failed":
            locale = self.read_locales().get(row.target_country.strip())
        self._write_cells(row.row_number, outcome_updates(outcome, locale))

    def cells_of(self, row_number: int, header: str) -> str:
        """One cell, read fresh. The live test uses it to snapshot and verify."""
        headers, values = self._load()
        index = row_number - 2
        if index < 0 or index >= len(values):
            raise IndexError(f"no row {row_number}")
        return cell(headers, values[index], header)

    def restore(self, row_number: int, cells: dict[str, str]) -> None:
        """Put named cells back exactly as they were. For the live test's cleanup."""
        self._write_cells(row_number, cells)

    def reset_stale(self, minutes: int) -> int:
        headers, values = self._load()
        check_headers(headers)
        now = self._now().astimezone(UTC)
        reset = 0
        for i, v in enumerate(values):
            if cell(headers, v, "Status").strip() != STATUS_PROCESSING:
                continue
            stamp = parse_claim_stamp(cell(headers, v, "Review Note"))
            if stamp is not None and (now - stamp).total_seconds() <= minutes * 60:
                continue
            self._write_cells(i + 2, {"Status": STATUS_TRANSLATE, "Review Note": STALE_NOTE})
            reset += 1
        return reset


# --------------------------------------------------------------------------
# In-memory twin.
# --------------------------------------------------------------------------


class InMemorySheet(_BaseSheet):
    """A sheet held as a 2-D list of cells, so tests can assert exact writes."""

    def __init__(
        self,
        rows: Sequence[Sequence[str]],
        settings: Sequence[Sequence[str]],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(now=now)
        grid = [list(r) for r in rows]
        settings_grid = [list(r) for r in settings]
        if not grid or not settings_grid:
            raise SheetContractError("sheet has no header row")
        self.headers: list[str] = [h.strip() for h in grid[0]]
        self.rows: list[list[str]] = grid[1:]
        self.settings_headers: list[str] = [h.strip() for h in settings_grid[0]]
        self.settings_rows: list[list[str]] = settings_grid[1:]
        self.writes: list[tuple[int, dict[str, str]]] = []

    @classmethod
    def from_csv(
        cls,
        sheet_path: str | Path,
        settings_path: str | Path,
        now: Callable[[], datetime] | None = None,
    ) -> InMemorySheet:
        return cls(_read_csv(sheet_path), _read_csv(settings_path), now=now)

    def cells(self, row_number: int, header: str) -> str:
        """The exact cell as it stands now. `row_number` is the sheet row (data starts at 2)."""
        index = row_number - 2
        if index < 0 or index >= len(self.rows):
            raise IndexError(f"no row {row_number}")
        return cell(self.headers, self.rows[index], header)

    def _load(self) -> tuple[list[str], list[list[str]]]:
        return list(self.headers), [list(r) for r in self.rows]

    def _load_settings(self) -> tuple[list[str], list[list[str]]]:
        return list(self.settings_headers), [list(r) for r in self.settings_rows]

    def _write_cells(self, row_number: int, updates: dict[str, str]) -> None:
        index = row_number - 2
        if index < 0 or index >= len(self.rows):
            raise IndexError(f"no row {row_number}")
        target = self.rows[index]
        while len(target) < len(self.headers):
            target.append("")
        for header, value in updates.items():
            target[header_index(self.headers, header)] = value
        self.writes.append((row_number, dict(updates)))


def _read_csv(path: str | Path) -> list[list[str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [list(r) for r in csv.reader(handle)]


# --------------------------------------------------------------------------
# The real spreadsheet.
# --------------------------------------------------------------------------


class GoogleSheet(_BaseSheet):
    """gspread against the client's sheet, opened by key, tabs found by title."""

    def __init__(
        self,
        credentials: Any,
        sheet_id: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(now=now)
        import gspread

        self._spreadsheet = gspread.authorize(credentials).open_by_key(sheet_id)
        self._sheet = self._spreadsheet.worksheet(SHEET_TAB)
        self._settings = self._spreadsheet.worksheet(SETTINGS_TAB)

    def _load(self) -> tuple[list[str], list[list[str]]]:
        return _split_values(self._sheet.get_all_values())

    def _load_settings(self) -> tuple[list[str], list[list[str]]]:
        return _split_values(self._settings.get_all_values())

    def _write_cells(self, row_number: int, updates: dict[str, str]) -> None:
        headers, _ = self._load()
        check_headers(headers)
        body = [
            {
                "range": f"{column_letter(header_index(headers, header))}{row_number}",
                "values": [[value]],
            }
            for header, value in updates.items()
        ]
        if body:
            self._sheet.batch_update(body, value_input_option="RAW")


def _split_values(values: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    if not values:
        raise SheetContractError("sheet has no header row")
    return [h.strip() for h in values[0]], [list(r) for r in values[1:]]
