"""The orchestrator, on fakes only. Nothing here reaches a network or a real CLI."""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from adtranslate.config import Settings
from adtranslate.copy.transcreate import JudgeResult
from adtranslate.fetch import AdNotFound
from adtranslate.image import NO_EDITOR, NOT_VERIFIED
from adtranslate.image.detect import TextScan
from adtranslate.image.verify import VerifyResult
from adtranslate.models import AdCreative, AdRow, CopyResult, Locale, RowOutcome
from adtranslate.pipeline import pending_jobs, process_row, run_once
from adtranslate.ports import PipelineError, Ports, SessionCopyPort
from adtranslate.sheet import InMemorySheet

SIZE = (240, 240)
LOCALE = Locale(country="Netherlands", language_name="Dutch", tag="nl-NL", currency="EUR")

HEADERS = [
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
SETTINGS_GRID = [
    ["Target Country", "Default Language", "Currency"],
    ["Netherlands", "Dutch (nl-NL)", "EUR"],
]


def sheet_row(row_id: str, link: str = "https://www.facebook.com/ads/library/?id=111") -> list[str]:
    return [row_id, "Translate", "Netherlands", "", "", link, "", "", "", "", "", ""]


def make_sheet(ids: list[str]) -> InMemorySheet:
    return InMemorySheet([HEADERS, *[sheet_row(i) for i in ids]], SETTINGS_GRID)


def png(path: Path, size: tuple[int, int] = SIZE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "JPEG" if path.suffix == ".jpg" else "PNG"
    Image.new("RGB", size, (20, 90, 160)).save(path, format=fmt)
    return path


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeFetcher:
    def __init__(self, ad: AdCreative | None = None, raises: Exception | None = None) -> None:
        self.ad = ad or AdCreative(
            library_id="111",
            primary_text="Achetez 1 + obtenez 1 gratuit",
            headline="Ce soir seulement",
            description="Rouge a levres pH.",
            media_type="image",
            image_url="https://example.invalid/creative.jpg",
        )
        self.raises = raises
        self.delay = 0.0
        self.in_flight = 0
        self.peak = 0
        self._guard = threading.Lock()

    def fetch(self, library_id: str) -> AdCreative:
        with self._guard:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.raises is not None:
                raise self.raises
            return self.ad
        finally:
            with self._guard:
                self.in_flight -= 1


class FakeCopy:
    def __init__(self, result: CopyResult | None = None, targets: list[str] | None = None) -> None:
        self.result = result or CopyResult(
            primary_text="Koop 1 + krijg 1 gratis",
            headline="Alleen vanavond",
            description="pH lippenstift.",
        )
        self.targets = targets or ["Koop 1 +"]
        self.calls = 0
        self.string_calls = 0

    def transcreate(self, ad: AdCreative, locale: Locale, job_dir: Path) -> CopyResult:
        self.calls += 1
        return self.result.model_copy(deep=True)

    def judge(self, ad: AdCreative, result: CopyResult, job_dir: Path) -> JudgeResult:
        return JudgeResult(fidelity=5, fluency=5)

    def translate_strings(self, strings: list[str], locale: Locale, job_dir: Path) -> list[str]:
        self.string_calls += 1
        return list(self.targets)


class FakeVision:
    def __init__(self, scan: TextScan, verdicts: list[bool]) -> None:
        self.scan = scan
        self.verdicts = list(verdicts)
        self.verifies = 0

    def detect(self, image: Path) -> TextScan:
        return self.scan

    def verify(
        self, original: Path, edited: Path, replacements: list[tuple[str, str]]
    ) -> VerifyResult:
        self.verifies += 1
        passed = self.verdicts.pop(0) if self.verdicts else False
        return VerifyResult(unchanged_score=5, passed=passed)


class FakeEdit:
    label = "fake"

    def __init__(self, available: bool = True) -> None:
        self.calls = 0
        self.available = available

    def check(self) -> None:
        return None

    def edit(
        self, image_path: Path, replacements: list[tuple[str, str]], out_dir: Path, attempt: int = 0
    ) -> Path:
        self.calls += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        return png(out_dir / f"edited-{self.calls}.png")


class FakeGoogle:
    def __init__(self, sheet: InMemorySheet) -> None:
        self._sheet = sheet
        self.uploads: list[tuple[str, str, str]] = []
        self.probe: LockProbe | None = None

    def check_drive(self) -> str:
        return "Ad Translations"

    def upload_png(self, path: Path, name: str, country: str) -> str:
        self.uploads.append((str(path), name, country))
        return f"https://drive.google.com/file/d/{name}/view"

    def sheet(self) -> InMemorySheet:
        return self._sheet


class LockProbe(InMemorySheet):
    """An in-memory sheet that records whether two writes ever overlap."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.in_write = 0
        self.peak_write = 0
        self._guard = threading.Lock()

    def _write_cells(self, row_number: int, updates: dict[str, str]) -> None:
        with self._guard:
            self.in_write += 1
            self.peak_write = max(self.peak_write, self.in_write)
        time.sleep(0.02)
        super()._write_cells(row_number, updates)
        with self._guard:
            self.in_write -= 1


def build(
    sheet: InMemorySheet,
    fetcher: FakeFetcher | None = None,
    copy: FakeCopy | None = None,
    vision: FakeVision | None = None,
    edit: FakeEdit | None = None,
) -> tuple[Ports, FakeGoogle]:
    google = FakeGoogle(sheet)
    ports = Ports(
        fetcher=fetcher or FakeFetcher(),
        copy=copy or FakeCopy(),
        vision=vision or FakeVision(TextScan(has_text=False), []),
        image_edit=edit or FakeEdit(),
        google=google,  # type: ignore[arg-type]
    )
    return ports, google


@pytest.fixture
def settings() -> Settings:
    return Settings(session_wait_seconds=1, stale_claim_minutes=30)


@pytest.fixture(autouse=True)
def no_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """`download_creative` writes a real little JPEG instead of reaching the network."""

    def fake_download(creative: AdCreative, dest_dir: Path) -> Path:
        if creative.video_url:
            dest = dest_dir / "creative.mp4"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"not really a video")
            return dest
        return png(dest_dir / "creative.jpg")

    monkeypatch.setattr("adtranslate.pipeline.download_creative", fake_download)


# --------------------------------------------------------------------------
# One row, end to end
# --------------------------------------------------------------------------


def test_happy_path_finishes_the_row(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-027"])
    ports, google = build(sheet)
    row = sheet.read_rows()[0]

    outcome = process_row(row, ports, settings, tmp_path, locale=LOCALE)

    assert outcome.status == "Finished"
    assert outcome.drive_url == "https://drive.google.com/file/d/NL-027.png/view"
    assert sheet.cells(2, "Status") == "Finished"
    assert sheet.cells(2, "Headline") == "Alleen vanavond"
    assert sheet.cells(2, "Target Language") == "Dutch"
    assert sheet.cells(2, "Drive URL") == outcome.drive_url
    assert google.uploads[0][1] == "NL-027.png"

    job = tmp_path / "NL-027"
    assert json.loads((job / "outcome.json").read_text())["status"] == "Finished"
    timeline = json.loads((job / "timeline.json").read_text())
    assert {"claim", "fetch", "copy", "image", "png", "upload", "write"} <= set(timeline["steps"])
    with Image.open(job / "NL-027.png") as out:
        assert out.size == SIZE


def test_no_text_image_skips_the_edit(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-027"])
    edit = FakeEdit()
    ports, _ = build(sheet, vision=FakeVision(TextScan(has_text=False), []), edit=edit)
    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)
    assert outcome.status == "Finished"
    assert edit.calls == 0


def test_verify_failure_retries_once_then_needs_review_with_the_original(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-029"])
    scan = TextScan.model_validate(
        {"has_text": True, "blocks": [{"text": "Achetez 1 +", "translate": True}]}
    )
    vision = FakeVision(scan, [False, False])
    edit = FakeEdit()
    ports, google = build(sheet, vision=vision, edit=edit)

    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)

    assert edit.calls == 2
    assert vision.verifies == 2
    assert outcome.status == "Needs Review"
    assert NOT_VERIFIED in outcome.note
    assert sheet.cells(2, "Status") == "Needs Review"
    # The uploaded PNG is the original creative, not either failed edit.
    uploaded = Path(google.uploads[0][0])
    assert uploaded.name == "NL-029.png"
    assert "edited" not in json.loads((tmp_path / "NL-029" / "outcome.json").read_text())["note"]


def test_verify_passing_on_the_retry_finishes(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-029"])
    scan = TextScan.model_validate(
        {"has_text": True, "blocks": [{"text": "Achetez 1 +", "translate": True}]}
    )
    edit = FakeEdit()
    ports, _ = build(sheet, vision=FakeVision(scan, [False, True]), edit=edit)
    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)
    assert edit.calls == 2
    assert outcome.status == "Finished"


def test_ad_not_found_fails_the_row_and_writes_only_status_and_note(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027"])
    ports, google = build(sheet, fetcher=FakeFetcher(raises=AdNotFound("library id 111 not found")))

    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)

    assert outcome.status == "Failed"
    assert "not found" in outcome.note
    assert sheet.cells(2, "Status") == "Failed"
    assert sheet.cells(2, "Review Note") == outcome.note
    assert sheet.cells(2, "Drive URL") == ""
    assert sheet.cells(2, "Primary Text") == ""
    assert sheet.cells(2, "Target Language") == ""
    assert google.uploads == []
    last_write = sheet.writes[-1][1]
    assert set(last_write) == {"Status", "Review Note"}


def test_a_real_session_copy_timeout_fails_the_row(tmp_path: Path) -> None:
    """The real SessionCopyPort, given nobody to answer it, ends the row as Failed."""
    settings = Settings(session_wait_seconds=0)
    sheet = make_sheet(["NL-027"])
    ports, _ = build(sheet)
    ports.copy = SessionCopyPort(settings, sleep=lambda _: None)
    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)
    assert outcome.status == "Failed"
    assert outcome.note == "copy job not filled"
    assert sheet.cells(2, "Status") == "Failed"


def test_a_row_claimed_elsewhere_is_skipped(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-027"])
    sheet.rows[0][1] = "Processing"
    ports, google = build(sheet)
    row = AdRow(row_number=2, id="NL-027", status="Translate", target_country="Netherlands")
    outcome = process_row(row, ports, settings, tmp_path, locale=LOCALE)
    assert outcome == RowOutcome(status="Skipped", note="claimed elsewhere")
    assert google.uploads == []


def test_review_reasons_from_the_copy_become_needs_review(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027"])
    flagged = CopyResult(
        primary_text="x", headline="y", description="z", review_reasons=["judge: fluency 3/5"]
    )
    ports, _ = build(sheet, copy=FakeCopy(result=flagged))
    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)
    assert outcome.status == "Needs Review"
    assert "judge: fluency 3/5" in outcome.note
    assert sheet.cells(2, "Primary Text") == "x"


# --------------------------------------------------------------------------
# The pool
# --------------------------------------------------------------------------


def test_five_rows_run_concurrently(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet([f"NL-0{n}" for n in (30, 31, 32, 33, 34)])
    fetcher = FakeFetcher()
    fetcher.delay = 0.15
    ports, _ = build(sheet, fetcher=fetcher)

    outcomes = run_once(ports, settings, tmp_path, workers=5, echo=lambda _: None)

    assert len(outcomes) == 5
    assert all(o.status == "Finished" for o in outcomes)
    assert fetcher.peak == 5


def test_sheet_writes_never_overlap(tmp_path: Path, settings: Settings) -> None:
    grid = [HEADERS, *[sheet_row(f"NL-0{n}") for n in (40, 41, 42, 43, 44)]]
    probe = LockProbe(grid, SETTINGS_GRID)
    ports, _ = build(probe)
    run_once(ports, settings, tmp_path, workers=5, echo=lambda _: None)
    assert probe.peak_write == 1


def test_run_once_only_takes_translate_rows_and_honours_only(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027", "NL-028"])
    sheet.rows[1][1] = "Finished"
    ports, _ = build(sheet)
    lines: list[str] = []
    outcomes = run_once(ports, settings, tmp_path, workers=2, echo=lines.append)
    assert len(outcomes) == 1
    assert any("rows with Status=Translate: 1" in line for line in lines)

    again = run_once(ports, settings, tmp_path, workers=2, echo=lambda _: None)
    assert again == []


def test_only_selects_one_row(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-027", "NL-028"])
    ports, _ = build(sheet)
    outcomes = run_once(ports, settings, tmp_path, workers=2, only="NL-028", echo=lambda _: None)
    assert len(outcomes) == 1
    assert sheet.cells(3, "Status") == "Finished"
    assert sheet.cells(2, "Status") == "Translate"


def test_dry_run_writes_nothing(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-027"])
    fetcher = FakeFetcher()
    copy = FakeCopy()
    ports, google = build(sheet, fetcher=fetcher, copy=copy)
    lines: list[str] = []

    outcomes = run_once(ports, settings, tmp_path, workers=5, dry_run=True, echo=lines.append)

    assert outcomes == []
    assert sheet.writes == []
    assert sheet.cells(2, "Status") == "Translate"
    assert google.uploads == []
    assert any("nothing written" in line for line in lines)


def test_dry_run_is_a_setup_check_that_starts_no_work(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-027", "NL-028"])
    fetcher = FakeFetcher()
    copy = FakeCopy()
    ports, _ = build(sheet, fetcher=fetcher, copy=copy)
    lines: list[str] = []

    run_once(ports, settings, tmp_path, workers=5, dry_run=True, echo=lines.append)

    text = "\n".join(lines)
    assert "header: ID | Status | Target Country" in text
    assert "countries: Netherlands" in text
    assert "rows with Status=Translate: 2" in text
    assert "drive folder: Ad Translations" in text
    assert "image editor: fake" in text
    assert fetcher.peak == 0
    assert copy.calls == 0
    assert not any(tmp_path.iterdir())


def test_pending_jobs_lists_unanswered_requests(tmp_path: Path) -> None:
    (tmp_path / "NL-027").mkdir(parents=True)
    (tmp_path / "NL-027" / "copy.request.json").write_text("{}", encoding="utf-8")
    (tmp_path / "NL-028").mkdir(parents=True)
    (tmp_path / "NL-028" / "copy.request.json").write_text("{}", encoding="utf-8")
    (tmp_path / "NL-028" / "copy.json").write_text(
        '{"primary_text": "a", "headline": "b", "description": "c"}', encoding="utf-8"
    )
    (tmp_path / "NL-028" / "vision.request.json").write_text("{}", encoding="utf-8")

    pending = pending_jobs(tmp_path)
    assert [p[0] for p in pending] == ["NL-027", "NL-028"]
    assert pending[0][1].name == "copy.request.json"
    assert pending[1][1].name == "vision.request.json"


def test_a_blank_id_row_is_allocated_before_its_folder_is_named(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = InMemorySheet([HEADERS, sheet_row("NL-028"), sheet_row("")], SETTINGS_GRID)
    ports, google = build(sheet)
    row = sheet.read_rows()[1]
    outcome = process_row(row, ports, settings, tmp_path, locale=LOCALE)
    assert outcome.status == "Finished"
    assert row.id == "NL-029"
    assert (tmp_path / "NL-029" / "outcome.json").exists()
    assert google.uploads[0][1] == "NL-029.png"


def test_a_video_creative_needs_review_without_an_edit(
    tmp_path: Path, settings: Settings
) -> None:
    ad = AdCreative(
        library_id="111",
        primary_text="Achetez",
        headline="Ce soir",
        description="d",
        media_type="video",
        video_url="https://example.invalid/v.mp4",
    )
    sheet = make_sheet(["NL-031"])
    edit = FakeEdit()
    ports, google = build(sheet, fetcher=FakeFetcher(ad=ad), edit=edit)
    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)
    assert outcome.status == "Needs Review"
    assert "video creative" in outcome.note
    assert edit.calls == 0
    assert google.uploads == []


def test_a_creative_that_is_a_jpeg_becomes_a_png_at_source_size(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027"])
    ports, google = build(sheet)
    process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)
    uploaded = Path(google.uploads[0][0])
    assert uploaded.suffix == ".png"
    with Image.open(uploaded) as out, Image.open(tmp_path / "NL-027" / "creative.jpg") as src:
        assert out.size == src.size


def test_png_bytes_helper_is_a_real_image(tmp_path: Path) -> None:
    path = png(tmp_path / "x.png")
    with Image.open(io.BytesIO(path.read_bytes())) as image:
        assert image.size == SIZE


def test_pending_jobs_lists_a_copy_request_whose_image_strings_are_unanswered(
    tmp_path: Path,
) -> None:
    job = tmp_path / "NL-027"
    job.mkdir(parents=True)
    (job / "copy.json").write_text(
        json.dumps({"primary_text": "a", "headline": "b", "description": "c"}), encoding="utf-8"
    )
    (job / "copy.request.json").write_text(
        json.dumps({"task": "image_strings", "image_strings": ["Achetez 1 +"]}), encoding="utf-8"
    )
    assert [p[0] for p in pending_jobs(tmp_path)] == ["NL-027"]

    (job / "copy.json").write_text(
        json.dumps(
            {
                "primary_text": "a",
                "headline": "b",
                "description": "c",
                "image_strings": [{"source": "Achetez 1 +", "target": "Koop 1 +"}],
            }
        ),
        encoding="utf-8",
    )
    assert pending_jobs(tmp_path) == []


def test_text_in_the_image_with_no_editor_needs_review_not_failed(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027"])
    scan = TextScan.model_validate(
        {"has_text": True, "blocks": [{"text": "Achetez 1 +", "translate": True}]}
    )
    copy = FakeCopy()
    edit = FakeEdit(available=False)
    ports, google = build(sheet, copy=copy, vision=FakeVision(scan, []), edit=edit)

    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)

    assert outcome.status == "Needs Review"
    assert NO_EDITOR in outcome.note
    assert edit.calls == 0
    assert copy.string_calls == 0
    assert len(google.uploads) == 1


def test_dry_run_fails_when_no_image_editor_is_set(tmp_path: Path, settings: Settings) -> None:
    sheet = make_sheet(["NL-027"])
    ports, _ = build(sheet, edit=FakeEdit(available=False))
    lines: list[str] = []

    with pytest.raises(PipelineError, match="GEMINI_API_KEY"):
        run_once(ports, settings, tmp_path, workers=5, dry_run=True, echo=lines.append)

    assert any(line.startswith("header: ") for line in lines)
    assert not any(tmp_path.iterdir())


class AttemptEdit(FakeEdit):
    def __init__(self) -> None:
        super().__init__()
        self.attempts: list[int] = []

    def edit(
        self, image_path: Path, replacements: list[tuple[str, str]], out_dir: Path, attempt: int = 0
    ) -> Path:
        self.attempts.append(attempt)
        return super().edit(image_path, replacements, out_dir, attempt)


def test_every_row_starts_its_image_edit_on_the_first_attempt(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027", "NL-028"])
    scan = TextScan.model_validate(
        {"has_text": True, "blocks": [{"text": "Achetez 1 +", "translate": True}]}
    )
    edit = AttemptEdit()
    ports, _ = build(sheet, vision=FakeVision(scan, [False, True, False, True]), edit=edit)
    first, second = sheet.read_rows()
    process_row(first, ports, settings, tmp_path, locale=LOCALE)
    process_row(second, ports, settings, tmp_path, locale=LOCALE)
    assert edit.attempts == [0, 1, 0, 1]


def test_a_fresh_claim_clears_the_last_outcome_so_an_interrupted_row_resumes(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027"])
    stale = tmp_path / "NL-027" / "outcome.json"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"status": "Finished"}', encoding="utf-8")
    ports, _ = build(sheet, fetcher=FakeFetcher(raises=KeyboardInterrupt()))  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt):
        process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)

    assert sheet.cells(2, "Status") == "Processing"
    assert not stale.exists()


def test_a_real_run_refuses_to_start_without_an_image_editor(
    tmp_path: Path, settings: Settings
) -> None:
    sheet = make_sheet(["NL-027"])
    ports, _ = build(sheet, edit=FakeEdit(available=False))
    with pytest.raises(PipelineError, match="GEMINI_API_KEY"):
        run_once(ports, settings, tmp_path, workers=5, echo=lambda _: None)
    assert sheet.writes == []


def test_dry_run_stops_when_the_image_editor_check_fails(
    tmp_path: Path, settings: Settings
) -> None:
    class RefusedEdit(FakeEdit):
        def check(self) -> None:
            raise PipelineError("GEMINI_API_KEY was refused: 400 API key not valid")

    sheet = make_sheet(["NL-027"])
    ports, _ = build(sheet, edit=RefusedEdit())
    with pytest.raises(PipelineError, match="API key not valid"):
        run_once(ports, settings, tmp_path, workers=5, dry_run=True, echo=lambda _: None)


class BrokenEdit(FakeEdit):
    def edit(
        self, image_path: Path, replacements: list[tuple[str, str]], out_dir: Path, attempt: int = 0
    ) -> Path:
        raise RuntimeError("429 RESOURCE_EXHAUSTED: prepayment credits are depleted")


class BrokenVision(FakeVision):
    def detect(self, image: Path) -> TextScan:
        raise PipelineError("vision job not filled (detect)")


@pytest.mark.parametrize("broken", ["edit", "vision"])
def test_an_image_failure_keeps_the_translated_copy_on_the_row(
    tmp_path: Path, settings: Settings, broken: str
) -> None:
    sheet = make_sheet(["NL-027"])
    scan = TextScan.model_validate(
        {"has_text": True, "blocks": [{"text": "Achetez 1 +", "translate": True}]}
    )
    if broken == "edit":
        ports, google = build(sheet, vision=FakeVision(scan, []), edit=BrokenEdit())
    else:
        ports, google = build(sheet, vision=BrokenVision(scan, []))

    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)

    assert outcome.status == "Needs Review"
    assert outcome.note.startswith("image not translated:")
    assert sheet.cells(2, "Headline") == "Alleen vanavond"
    assert sheet.cells(2, "Primary Text") == "Koop 1 + krijg 1 gratis"
    assert google.uploads == []


def test_an_upload_failure_keeps_the_copy_and_says_so(tmp_path: Path, settings: Settings) -> None:
    class BrokenGoogle(FakeGoogle):
        def upload_png(self, path: Path, name: str, country: str) -> str:
            raise RuntimeError("403 insufficientFilePermissions")

    sheet = make_sheet(["NL-027"])
    ports, _ = build(sheet)
    ports.google = BrokenGoogle(sheet)  # type: ignore[assignment]

    outcome = process_row(sheet.read_rows()[0], ports, settings, tmp_path, locale=LOCALE)

    assert outcome.status == "Needs Review"
    assert outcome.note == "image not uploaded: 403 insufficientFilePermissions"
    assert sheet.cells(2, "Headline") == "Alleen vanavond"
