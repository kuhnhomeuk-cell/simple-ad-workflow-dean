"""The orchestrator: one row in, one row written back.

Every step is wrapped. A `PipelineError` ends the row as `Failed` with a one-line reason;
anything a step merely flags accumulates into `Needs Review`, and the outputs are written
either way. Each row leaves a `runs/<ID>/` folder carrying its timeline and its outcome.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from adtranslate.config import Settings
from adtranslate.fetch import AdNotFound, download_creative, parse_library_id
from adtranslate.image import NO_EDITOR, NOT_VERIFIED
from adtranslate.image.detect import has_translatable, translatable_strings
from adtranslate.image.edit import to_png
from adtranslate.models import AdCreative, AdRow, CopyResult, ImageResult, Locale, RowOutcome
from adtranslate.ports import PipelineError, Ports, copy_job_pending
from adtranslate.sheet import STATUS_PROCESSING, STATUS_TRANSLATE, SheetPort

__all__ = [
    "NO_LOCALE",
    "RowLog",
    "pending_jobs",
    "process_row",
    "run_once",
    "run_watch",
]

NO_LOCALE = "no Settings row for the target country"
MAX_EDIT_ATTEMPTS = 2

# Google allows 60 writes per minute per user, and a claim is a read-then-write. One lock
# across the pool keeps the sheet single-writer.
SHEET_LOCK = threading.Lock()


class RowLog:
    """The per-step seconds one row spent, written out as `timeline.json`."""

    def __init__(self) -> None:
        self.steps: dict[str, float] = {}
        self.started = time.monotonic()

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        start = time.monotonic()
        try:
            yield
        finally:
            self.steps[name] = round(time.monotonic() - start, 3)

    @property
    def total(self) -> float:
        return round(time.monotonic() - self.started, 3)

    def as_dict(self) -> dict[str, object]:
        return {"steps": dict(self.steps), "total_seconds": self.total}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _locale_for(row: AdRow, sheet: SheetPort) -> Locale:
    locales = sheet.read_locales()
    locale = locales.get(row.target_country.strip())
    if locale is None:
        raise PipelineError(f"{NO_LOCALE}: {row.target_country!r}")
    return locale


def _fetch(row: AdRow, ports: Ports, job_dir: Path, resume: bool) -> tuple[AdCreative, Path | None]:
    """The ad and its creative on disk, or a `PipelineError` naming what went wrong."""
    try:
        library_id = parse_library_id(row.facebook_ad_link)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc
    try:
        ad = ports.fetcher.fetch(library_id)
    except AdNotFound as exc:
        raise PipelineError(f"ad not found: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - any fetch fault is one honest line
        raise PipelineError(f"fetch failed: {exc}") from exc

    existing = [p for p in (job_dir / "creative.jpg", job_dir / "creative.mp4") if p.exists()]
    if resume and existing:
        return ad, existing[0]
    if ad.media_type == "none" or not (ad.image_url or ad.video_url):
        return ad, None
    try:
        return ad, download_creative(ad, job_dir)
    except Exception as exc:  # noqa: BLE001
        raise PipelineError(f"creative download failed: {exc}") from exc


def _copy(ad: AdCreative, locale: Locale, ports: Ports, job_dir: Path) -> CopyResult:
    result = ports.copy.transcreate(ad, locale, job_dir)
    ports.copy.judge(ad, result, job_dir)
    return result


def _image(
    creative: Path,
    locale: Locale,
    ports: Ports,
    job_dir: Path,
) -> ImageResult:
    """Detect → translate the strings → edit → read back, with one retry."""
    if creative.suffix.lower() == ".mp4":
        return ImageResult(path=str(creative), edited=False, review_reasons=["video creative"])

    scan = ports.vision.detect(creative)
    if not has_translatable(scan):
        return ImageResult(path=str(to_png(creative, job_dir)), edited=False)

    if not ports.image_edit.available:
        return ImageResult(
            path=str(to_png(creative, job_dir)), edited=False, review_reasons=[NO_EDITOR]
        )

    sources = translatable_strings(scan)
    targets = ports.copy.translate_strings(sources, locale, job_dir)
    if len(targets) != len(sources):
        raise PipelineError(f"translator returned {len(targets)} strings for {len(sources)}")
    replacements = [
        (source, target.strip())
        for source, target in zip(sources, targets, strict=True)
        if target.strip()
    ]
    if not replacements:
        return ImageResult(path=str(to_png(creative, job_dir)), edited=False)

    for _ in range(MAX_EDIT_ATTEMPTS):
        edited = ports.image_edit.edit(creative, replacements, job_dir)
        verdict = ports.vision.verify(creative, edited, replacements)
        if verdict.passed:
            return ImageResult(path=str(edited), edited=True)

    return ImageResult(
        path=str(to_png(creative, job_dir)),
        edited=False,
        review_reasons=[NOT_VERIFIED],
    )


def finalise_png(produced: Path, original: Path, job_dir: Path, row_id: str) -> Path:
    """`runs/<ID>/<ID>.png`, at the source creative's exact pixel size."""
    destination = job_dir / f"{row_id or original.stem}.png"
    with Image.open(produced) as out, Image.open(original) as source:
        image = out.convert("RGB")
        if image.size != source.size:
            image = image.resize(source.size, Image.Resampling.LANCZOS)
        image.save(destination, format="PNG")
    return destination


def process_row(
    row: AdRow,
    ports: Ports,
    settings: Settings,
    runs_dir: Path,
    *,
    locale: Locale | None = None,
    dry_run: bool = False,
    resume: bool = False,
) -> RowOutcome:
    """One row, end to end. Never raises: every fault comes back as an outcome."""
    sheet = ports.google.sheet()
    log = RowLog()
    reasons: list[str] = []

    if not (dry_run or resume):
        with log.step("claim"), SHEET_LOCK:
            if not sheet.claim(row):
                return RowOutcome(status="Skipped", note="claimed elsewhere")

    job_dir = runs_dir / (row.id.strip() or f"row-{row.row_number}")
    job_dir.mkdir(parents=True, exist_ok=True)

    outcome: RowOutcome
    try:
        if locale is None:
            with log.step("locale"):
                locale = _locale_for(row, sheet)

        with log.step("fetch"):
            ad, creative = _fetch(row, ports, job_dir, resume)
        if ad.needs_review_reason:
            reasons.append(ad.needs_review_reason)
        _write_json(job_dir / "ad.json", ad.model_dump())

        with log.step("copy"):
            copy_result = _copy(ad, locale, ports, job_dir)
        reasons += copy_result.review_reasons
        _write_json(job_dir / "target.json", copy_result.model_dump())

        if dry_run:
            outcome = RowOutcome(
                status="Needs Review" if reasons else "Finished",
                copy_result=copy_result,
                drive_url=None,
                note="; ".join(reasons),
            )
            return outcome

        drive_url: str | None = None
        if creative is None:
            reasons.append("no creative on the ad")
        else:
            with log.step("image"):
                image = _image(creative, locale, ports, job_dir)
            reasons += image.review_reasons
            if image.edited or Path(image.path).suffix.lower() != ".mp4":
                with log.step("png"):
                    png = finalise_png(Path(image.path), creative, job_dir, row.id)
                with log.step("upload"):
                    drive_url = ports.google.upload_png(png, f"{row.id}.png", row.target_country)

        outcome = RowOutcome(
            status="Needs Review" if reasons else "Finished",
            copy_result=copy_result,
            drive_url=drive_url,
            note="; ".join(reasons),
        )
    except PipelineError as exc:
        outcome = RowOutcome(status="Failed", note=exc.reason)
    except Exception as exc:  # noqa: BLE001 - an unexpected fault is still one row's failure
        outcome = RowOutcome(status="Failed", note=f"unexpected: {exc}")
    finally:
        _write_json(job_dir / "timeline.json", log.as_dict())

    if not dry_run:
        with log.step("write"), SHEET_LOCK:
            sheet.write(row, outcome)
    _write_json(job_dir / "outcome.json", outcome.model_dump())
    _write_json(job_dir / "timeline.json", log.as_dict())
    return outcome


def _log_line(row: AdRow, outcome: RowOutcome, runs_dir: Path) -> str:
    """The one line a processed row prints."""
    timeline = runs_dir / (row.id.strip() or f"row-{row.row_number}") / "timeline.json"
    steps = ""
    if timeline.exists():
        data = json.loads(timeline.read_text(encoding="utf-8"))
        steps = " ".join(f"{k}={v}s" for k, v in data.get("steps", {}).items())
        total = data.get("total_seconds", 0)
    else:
        total = 0
    note = f" — {outcome.note}" if outcome.note else ""
    return f"{row.id or '(no id)'} {outcome.status} {total}s {steps}{note}"


def run_once(
    ports: Ports,
    settings: Settings,
    runs_dir: Path,
    workers: int = 5,
    only: str | None = None,
    dry_run: bool = False,
    echo: Callable[[str], None] = print,
) -> list[RowOutcome]:
    """Reset stale claims, then run every Translate row through the pool.

    A dry run is the setup check: it reads the sheet, the Settings tab and the Drive folder,
    names the image editor, and starts no work.
    """
    if dry_run:
        _dry_run(ports, only, echo)
        return []

    sheet = ports.google.sheet()
    with SHEET_LOCK:
        reset = sheet.reset_stale(settings.stale_claim_minutes)
    if reset:
        echo(f"reset {reset} stale claim(s)")

    rows = [r for r in sheet.read_rows() if r.status.strip() == STATUS_TRANSLATE]
    if only:
        rows = [r for r in rows if r.id.strip() == only.strip()]
    echo(f"rows with Status={STATUS_TRANSLATE}: {len(rows)}")
    if not rows:
        return []

    locales = sheet.read_locales()
    runs_dir.mkdir(parents=True, exist_ok=True)

    def one(row: AdRow) -> RowOutcome:
        return process_row(
            row,
            ports,
            settings,
            runs_dir,
            locale=locales.get(row.target_country.strip()),
        )

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(one, rows))

    for row, outcome in zip(rows, outcomes, strict=True):
        echo(_log_line(row, outcome, runs_dir))
    return outcomes


def _dry_run(ports: Ports, only: str | None, echo: Callable[[str], None]) -> None:
    try:
        sheet = ports.google.sheet()
        headers = sheet.read_headers()
        rows = [r for r in sheet.read_rows() if r.status.strip() == STATUS_TRANSLATE]
        countries = sorted(sheet.read_locales())
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001 - the setup check speaks in one line
        raise PipelineError(f"cannot read the sheet: {exc}") from exc
    if only:
        rows = [r for r in rows if r.id.strip() == only.strip()]
    echo("header: " + " | ".join(headers))
    echo("countries: " + (", ".join(countries) or "none — fill the Settings tab"))
    echo(f"rows with Status={STATUS_TRANSLATE}: {len(rows)}")
    echo(f"drive folder: {ports.google.check_drive()}")
    editor = ports.image_edit
    if not editor.available:
        raise PipelineError(
            "no image editor — set GEMINI_API_KEY in .env; text inside ad images needs it"
        )
    echo(f"image editor: {editor.label}")
    echo("dry run: nothing written to the sheet or Drive")


def run_resume(
    ports: Ports,
    settings: Settings,
    runs_dir: Path,
    workers: int = 5,
    only: str | None = None,
    echo: Callable[[str], None] = print,
) -> list[RowOutcome]:
    """Finish the rows this machine already claimed but never wrote an outcome for."""
    sheet = ports.google.sheet()
    rows = [
        r
        for r in sheet.read_rows()
        if r.status.strip() == STATUS_PROCESSING
        and (runs_dir / r.id.strip()).is_dir()
        and not (runs_dir / r.id.strip() / "outcome.json").exists()
    ]
    if only:
        rows = [r for r in rows if r.id.strip() == only.strip()]
    echo(f"rows to resume: {len(rows)}")
    if not rows:
        return []

    locales = sheet.read_locales()

    def one(row: AdRow) -> RowOutcome:
        return process_row(
            row,
            ports,
            settings,
            runs_dir,
            locale=locales.get(row.target_country.strip()),
            resume=True,
        )

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(one, rows))
    for row, outcome in zip(rows, outcomes, strict=True):
        echo(_log_line(row, outcome, runs_dir))
    return outcomes


def run_watch(
    ports: Ports,
    settings: Settings,
    runs_dir: Path,
    workers: int = 5,
    only: str | None = None,
    dry_run: bool = False,
    echo: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    passes: int | None = None,
) -> None:
    """Poll every `poll_seconds` until Ctrl-C, or for `passes` passes in a test."""
    done = 0
    while passes is None or done < passes:
        try:
            run_once(ports, settings, runs_dir, workers, only, dry_run, echo)
        except KeyboardInterrupt:
            echo("stopped")
            return
        done += 1
        if passes is not None and done >= passes:
            return
        try:
            sleep(float(settings.poll_seconds))
        except KeyboardInterrupt:
            echo("stopped")
            return


def pending_jobs(runs_dir: Path) -> list[tuple[str, Path]]:
    """Every session job under `runs/` still waiting for its answer file."""
    out: list[tuple[str, Path]] = []
    if not runs_dir.exists():
        return out
    for job_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        copy_request = job_dir / "copy.request.json"
        if copy_request.exists() and (
            not (job_dir / "copy.json").exists() or copy_job_pending(job_dir)
        ):
            out.append((job_dir.name, copy_request))
        vision_request = job_dir / "vision.request.json"
        if vision_request.exists() and not (job_dir / "vision.json").exists():
            out.append((job_dir.name, vision_request))
    return out


def rows_of(sheet: SheetPort, statuses: Iterable[str]) -> list[AdRow]:
    """The rows carrying any of `statuses`. Small helper for the CLI."""
    wanted = {s.strip() for s in statuses}
    return [r for r in sheet.read_rows() if r.status.strip() in wanted]
