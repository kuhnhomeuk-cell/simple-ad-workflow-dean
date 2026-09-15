"""The seams between the pipeline and everything outside it.

Every outside thing the orchestrator touches — the copy model, the vision model, the
image editor, Google — is a Protocol here with two implementations: one that spends an
API key, and one that spends nothing because a human session or a subscription CLI does
the work. `build_ports` picks per key. Nothing in this module reaches the network at
import time.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from adtranslate.config import Settings
from adtranslate.copy.transcreate import (
    JUDGE_THRESHOLD,
    JudgeResult,
    build_judge_prompts,
    build_transcreate_prompts,
    post_checks,
)
from adtranslate.fetch import Fetcher
from adtranslate.image.detect import TextScan
from adtranslate.image.verify import VerifyResult, score
from adtranslate.models import AdCreative, CopyResult, Locale
from adtranslate.sheet import (
    SETTINGS_TAB,
    SHEET_TAB,
    SheetPort,
    _BaseSheet,
    check_headers,
    column_letter,
    header_index,
)

__all__ = [
    "ApiCopyPort",
    "ApiImageEditPort",
    "ApiVisionPort",
    "CopyPort",
    "GlitchGooglePort",
    "GlitchSheet",
    "GooglePort",
    "GrokImageEditPort",
    "ImageEditPort",
    "OAuthGooglePort",
    "PipelineError",
    "Ports",
    "SessionCopyPort",
    "SessionVisionPort",
    "VisionPort",
    "build_ports",
    "copy_job_pending",
    "parse_external_data",
]

POLL_INTERVAL_SECONDS = 2.0
GLITCH_SCRIPTS = Path.home() / "glitch" / ".claude" / "scripts"
SHEET_READ_RANGE = f"{SHEET_TAB}!A1:L200"
SETTINGS_READ_RANGE = f"{SETTINGS_TAB}!A1:C50"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# The door prints an injection-defence preamble that names a BARE `<external_data>` tag, so
# the opener we want is the one carrying attributes (`source=`, `trust=`).
_EXTERNAL_DATA_RE = re.compile(r"<external_data\s[^>]*>(.*?)</external_data>", re.DOTALL)
_META_RE = re.compile(r"^\s*sheet=\S+\s+range=")
_ID_CELL_RE = re.compile(r"^[A-Z]{2}-\d{3}$|^$")
_FILE_ID_RE = re.compile(r"file id ([A-Za-z0-9_\-]+)")
_SEPARATOR = " | "
_MIN_SEPARATORS = 3
_STATUSES = frozenset({"Translate", "Processing", "Needs Review", "Failed", "Finished"})

# One image edit at a time: the Grok CLI drives a single interactive session.
_EDIT_SEMAPHORE = threading.Semaphore(1)


class PipelineError(RuntimeError):
    """A step could not complete. The row becomes Failed with `reason` as the note."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------
# Copy
# --------------------------------------------------------------------------


class CopyPort(Protocol):
    """Transcreation, the judge read-back, and the burned-in image strings."""

    def transcreate(self, ad: AdCreative, locale: Locale, job_dir: Path) -> CopyResult: ...

    def judge(self, ad: AdCreative, result: CopyResult, job_dir: Path) -> JudgeResult: ...

    def translate_strings(
        self, strings: list[str], locale: Locale, job_dir: Path
    ) -> list[str]: ...


def _apply_judge(result: CopyResult, verdict: JudgeResult) -> JudgeResult:
    """The one threshold rule, shared by both copy ports."""
    reasons: list[str] = []
    if verdict.fidelity < JUDGE_THRESHOLD:
        reasons.append(f"judge: fidelity {verdict.fidelity}/5")
    if verdict.fluency < JUDGE_THRESHOLD:
        reasons.append(f"judge: fluency {verdict.fluency}/5")
    if reasons:
        reasons += [f"judge: {issue}" for issue in verdict.issues]
        result.review_reasons = list(result.review_reasons) + reasons
    return verdict


class ApiCopyPort:
    """The Anthropic path. Only built when ANTHROPIC_API_KEY is set."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def transcreate(self, ad: AdCreative, locale: Locale, job_dir: Path) -> CopyResult:
        from adtranslate.copy.transcreate import transcreate as _transcreate

        return _transcreate(ad, locale, self._settings)

    def judge(self, ad: AdCreative, result: CopyResult, job_dir: Path) -> JudgeResult:
        from adtranslate.copy.transcreate import judge as _judge

        return _judge(ad, result, self._settings)

    def translate_strings(self, strings: list[str], locale: Locale, job_dir: Path) -> list[str]:
        """Translate the burned-in strings with one small structured call."""
        from adtranslate.copy.transcreate import translate_image_strings

        return translate_image_strings(strings, locale, self._settings)


class SessionCopyPort:
    """The no-key path: a request file goes out, a human-or-agent session fills the answer.

    One job carries everything the row needs from a language model: the three copy fields,
    the judge scores, and the strings burned into the creative.
    """

    REQUEST_NAME = "copy.request.json"
    ANSWER_NAME = "copy.json"

    def __init__(self, settings: Settings, sleep: Any = time.sleep) -> None:
        self._settings = settings
        self._sleep = sleep
        self._cache: dict[str, dict[str, Any]] = {}
        self._ads: dict[str, AdCreative] = {}

    # --- the port ---------------------------------------------------------

    def transcreate(self, ad: AdCreative, locale: Locale, job_dir: Path) -> CopyResult:
        self._ads[str(job_dir)] = ad
        payload = self._job(ad, locale, job_dir, image_strings=[])
        result = CopyResult(
            primary_text=str(payload.get("primary_text", "")),
            headline=str(payload.get("headline", "")),
            description=str(payload.get("description", "")),
            translator_notes=str(payload.get("translator_notes", "")),
        )
        result.review_reasons = list(result.review_reasons) + post_checks(ad, result)
        return result

    def judge(self, ad: AdCreative, result: CopyResult, job_dir: Path) -> JudgeResult:
        payload = self._cache.get(str(job_dir), {})
        block = payload.get("judge")
        if not isinstance(block, dict):
            return _apply_judge(
                result, JudgeResult(fidelity=0, fluency=0, issues=["not filled in the copy job"])
            )
        verdict = JudgeResult(
            fidelity=int(block.get("fidelity", 0)),
            fluency=int(block.get("fluency", 0)),
            issues=[str(i) for i in block.get("issues", [])],
        )
        return _apply_judge(result, verdict)

    def translate_strings(self, strings: list[str], locale: Locale, job_dir: Path) -> list[str]:
        wanted = [s.strip() for s in strings]
        payload = self._cache.get(str(job_dir))
        if payload is None or not _covers(payload, wanted):
            payload = self._job(
                self._ads.get(str(job_dir)), locale, job_dir, image_strings=wanted
            )
        pairs = _image_pairs(payload)
        missing = [s for s in wanted if s.casefold() not in pairs]
        if missing:
            raise PipelineError(f"copy job carries no image string for {missing[0]!r}")
        return [pairs[s.casefold()] for s in wanted]

    # --- the mechanism ----------------------------------------------------

    def _job(
        self,
        ad: AdCreative | None,
        locale: Locale,
        job_dir: Path,
        image_strings: list[str],
    ) -> dict[str, Any]:
        """Write the request, wait for the answer, cache and return it."""
        job_dir.mkdir(parents=True, exist_ok=True)
        self._write_request(ad, locale, job_dir, image_strings)
        payload = self._wait(job_dir, image_strings)
        self._cache[str(job_dir)] = payload
        return payload

    def _write_request(
        self,
        ad: AdCreative | None,
        locale: Locale,
        job_dir: Path,
        image_strings: list[str],
    ) -> None:
        source = ad if ad is not None else AdCreative(library_id="")
        request_path = job_dir / self.REQUEST_NAME
        if not image_strings:
            _drop_answer_for_another_ad(request_path, job_dir / self.ANSWER_NAME, source, locale)
        system, user = build_transcreate_prompts(source, locale)
        judge_system, _ = build_judge_prompts(source, CopyResult(), locale)
        task = "image_strings" if image_strings else "copy"
        request = {
            "task": task,
            "instructions": (
                "Add an image_strings entry for every source listed here to the existing "
                "answer file. Keep its other fields as they are."
                if image_strings
                else "Write the whole answer file."
            ),
            "ad": {
                "library_id": source.library_id,
                "page_name": source.page_name,
                "primary_text": source.primary_text,
                "headline": source.headline,
                "description": source.description,
                "cta": source.cta,
                "landing_domain": source.landing_domain,
            },
            "locale": locale.model_dump(),
            "system_prompt": system,
            "user_prompt": user,
            "judge_rubric": judge_system,
            "image_strings": image_strings,
            "answer_file": str(job_dir / self.ANSWER_NAME),
            "answer_shape": {
                "primary_text": "str",
                "headline": "str",
                "description": "str",
                "translator_notes": "str",
                "judge": {"fidelity": "1-5", "fluency": "1-5", "issues": ["str"]},
                "image_strings": [{"source": "str", "target": "str"}],
            },
        }
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")

    def _wait(self, job_dir: Path, image_strings: list[str]) -> dict[str, Any]:
        answer = job_dir / self.ANSWER_NAME
        deadline = self._settings.session_wait_seconds
        waited = 0.0
        while True:
            payload = _read_json(answer)
            if payload is not None and _filled(payload) and _covers(payload, image_strings):
                return payload
            if waited >= deadline:
                raise PipelineError("copy job not filled")
            self._sleep(POLL_INTERVAL_SECONDS)
            waited += POLL_INTERVAL_SECONDS


def _drop_answer_for_another_ad(
    request_path: Path, answer_path: Path, ad: AdCreative, locale: Locale
) -> None:
    """Delete an answer written for a different ad or language in this job folder.

    A late answer for the same ad and language is kept, so a rerun picks it up.
    """
    previous = _read_json(request_path)
    if previous is None:
        return
    same_ad = str((previous.get("ad") or {}).get("library_id", "")) == ad.library_id
    same_locale = previous.get("locale") == locale.model_dump()
    if not (same_ad and same_locale):
        answer_path.unlink(missing_ok=True)


def copy_job_pending(job_dir: Path) -> bool:
    """True while the copy request in `job_dir` has no answer that satisfies it."""
    request = _read_json(job_dir / SessionCopyPort.REQUEST_NAME)
    if request is None:
        return False
    answer = _read_json(job_dir / SessionCopyPort.ANSWER_NAME)
    if answer is None or not _filled(answer):
        return True
    wanted = [str(s) for s in request.get("image_strings", []) or []]
    return not _covers(answer, wanted)


def _read_json(path: Path) -> dict[str, Any] | None:
    """The file as a dict, or None while it is absent or half-written."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _filled(payload: dict[str, Any]) -> bool:
    """True once the three copy fields are present as keys.

    Empty values are allowed: an ad the Library exposes no copy for (a "multiple versions"
    ad) has nothing to translate, and the post-checks turn the empties into review reasons.
    """
    return all(k in payload for k in ("primary_text", "headline", "description"))


def _image_pairs(payload: dict[str, Any]) -> dict[str, str]:
    """The filled `image_strings`, keyed by case-folded source."""
    pairs: dict[str, str] = {}
    for item in payload.get("image_strings", []) or []:
        if isinstance(item, dict):
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            if source and target:
                pairs[source.casefold()] = target
    return pairs


def _covers(payload: dict[str, Any], wanted: list[str]) -> bool:
    """True when every wanted source already has a target in the answer."""
    pairs = _image_pairs(payload)
    return all(s.casefold() in pairs for s in wanted if s)


# --------------------------------------------------------------------------
# Vision
# --------------------------------------------------------------------------


class VisionPort(Protocol):
    """Reading text off a creative, and reading an edit back."""

    def detect(self, image: Path) -> TextScan: ...

    def verify(
        self, original: Path, edited: Path, replacements: list[tuple[str, str]]
    ) -> VerifyResult: ...


class ApiVisionPort:
    """The Gemini path. Only built when GEMINI_API_KEY is set."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def detect(self, image: Path) -> TextScan:
        from adtranslate.image.detect import detect_text

        return detect_text(image, self._settings)

    def verify(
        self, original: Path, edited: Path, replacements: list[tuple[str, str]]
    ) -> VerifyResult:
        from adtranslate.image.verify import verify_edit

        return verify_edit(original, edited, replacements, self._settings)


class SessionVisionPort:
    """The no-key path: the session looks at the image and fills `vision.json`.

    The job directory is the creative's own folder — `runs/<ID>/` — unless one is pinned.
    """

    REQUEST_NAME = "vision.request.json"
    ANSWER_NAME = "vision.json"

    def __init__(
        self, settings: Settings, job_dir: Path | None = None, sleep: Any = time.sleep
    ) -> None:
        self._settings = settings
        self._job_dir = job_dir
        self._sleep = sleep

    def detect(self, image: Path) -> TextScan:
        payload = self._ask(
            Path(image),
            {
                "question": "detect",
                "image": str(Path(image).resolve()),
                "answer_shape": {
                    "has_text": "bool",
                    "blocks": [{"text": "str", "role": "promo|price|handwritten|logo|other",
                                "translate": "bool"}],
                },
            },
        )
        return TextScan.model_validate(payload.get("scan", payload))

    def verify(
        self, original: Path, edited: Path, replacements: list[tuple[str, str]]
    ) -> VerifyResult:
        payload = self._ask(
            Path(original),
            {
                "question": "verify",
                "original": str(Path(original).resolve()),
                "edited": str(Path(edited).resolve()),
                "replacements": [{"source": s, "target": t} for s, t in replacements],
                "answer_shape": {
                    "targets_present": ["str"],
                    "sources_remaining": ["str"],
                    "unchanged_score": "1-5",
                },
            },
        )
        raw = payload.get("verify", payload)
        return score(VerifyResult.model_validate(raw), replacements)

    def _ask(self, image: Path, request: dict[str, Any]) -> dict[str, Any]:
        job_dir = self._job_dir or image.parent
        job_dir.mkdir(parents=True, exist_ok=True)
        answer = job_dir / self.ANSWER_NAME
        answer.unlink(missing_ok=True)
        (job_dir / self.REQUEST_NAME).write_text(
            json.dumps({**request, "answer_file": str(answer)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        waited = 0.0
        while True:
            payload = _read_json(answer)
            if payload is not None:
                return payload
            if waited >= self._settings.session_wait_seconds:
                raise PipelineError(f"vision job not filled ({request['question']})")
            self._sleep(POLL_INTERVAL_SECONDS)
            waited += POLL_INTERVAL_SECONDS


# --------------------------------------------------------------------------
# Image edit
# --------------------------------------------------------------------------


class ImageEditPort(Protocol):
    """Replacing the burned-in strings, and changing nothing else."""

    @property
    def available(self) -> bool: ...

    @property
    def label(self) -> str: ...

    def edit(
        self, image_path: Path, replacements: list[tuple[str, str]], out_dir: Path
    ) -> Path: ...


class ApiImageEditPort:
    """The Gemini image path, first the flash tier then the pro tier."""

    available = True
    label = "gemini"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._attempt = 0

    def edit(self, image_path: Path, replacements: list[tuple[str, str]], out_dir: Path) -> Path:
        from adtranslate.image.edit import edit_text

        first = self._attempt == 0
        model = self._settings.image_model if first else self._settings.image_retry_model
        self._attempt += 1
        return edit_text(image_path, replacements, model, self._settings, None, out_dir)


GROK_PROMPT = """Use the image_edit tool (do not use image_gen, do not draw in code).
Reference image, absolute path: {reference}
Prompt: Keep this exact photo unchanged: the product, the background, the lighting, the \
framing, the layout and every other piece of text. Change only the text listed here, one \
for one. {replacements} Same style, same font or handwriting, same colour, same size and \
same placement for every replaced string. No other text anywhere.
Aspect ratio: 1:1.
Save under the working directory. Print only the final absolute file path as the last line.
"""


def build_grok_prompt(reference: Path, replacements: list[tuple[str, str]]) -> str:
    """The prompt file, in the shape today's hand-run rows used."""
    lines = " ".join(f'"{source}" becomes "{target}".' for source, target in replacements)
    return GROK_PROMPT.format(reference=reference.resolve(), replacements=lines)


class GrokImageEditPort:
    """The no-key path: Dean's Grok CLI subscription does the edit, one at a time."""

    PROMPT_NAME = "prompt.txt"
    label = "grok"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def available(self) -> bool:
        return grok_available(self._settings)

    def edit(self, image_path: Path, replacements: list[tuple[str, str]], out_dir: Path) -> Path:
        if not replacements:
            raise PipelineError("image edit called with no replacements")
        source = Path(image_path).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = out_dir / self.PROMPT_NAME
        prompt_path.write_text(build_grok_prompt(source, replacements), encoding="utf-8")

        argv = [
            str(Path(self._settings.grok_bin).expanduser()),
            "--prompt-file",
            str(prompt_path),
            "--always-approve",
            "--max-turns",
            "150",
            "--effort",
            "high",
            "--cwd",
            str(out_dir.resolve()),
        ]
        started = time.time()
        with _EDIT_SEMAPHORE:
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._settings.grok_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise PipelineError("image edit timed out") from exc
            except OSError as exc:
                raise PipelineError(f"image edit could not start: {exc}") from exc
        if completed.returncode != 0:
            raise PipelineError(f"image edit failed (exit {completed.returncode})")
        return _newest_image(out_dir, since=started, exclude=source)


def _newest_image(out_dir: Path, since: float, exclude: Path) -> Path:
    """The newest image the edit wrote into `out_dir`."""
    candidates = [
        p
        for p in out_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_SUFFIXES
        and p.resolve() != exclude
        and p.stat().st_mtime >= since - 1
    ]
    if not candidates:
        raise PipelineError("image edit produced no image")
    return max(candidates, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------


class GooglePort(Protocol):
    """Drive uploads and the sheet, however this machine is authorised."""

    def check_drive(self) -> str: ...

    def upload_png(self, path: Path, name: str, country: str) -> str: ...

    def sheet(self) -> SheetPort: ...


def view_url(file_id: str) -> str:
    """The /view link a Drive file id resolves to."""
    return f"https://drive.google.com/file/d/{file_id}/view"


class OAuthGooglePort:
    """This project's own OAuth token: gspread for the sheet, Drive API for the upload."""

    FOLDER_MIME = "application/vnd.google-apps.folder"

    def __init__(self, settings: Settings, sheet_id: str = "") -> None:
        self._settings = settings
        self._sheet_id = sheet_id or settings.sheet_id
        self._sheet: SheetPort | None = None
        self._drive: Any = None
        self._folders: dict[str, str] = {}

    def _credentials(self) -> Any:
        from adtranslate.google_auth import get_credentials

        return get_credentials(self._settings)

    def _service(self) -> Any:
        if self._drive is None:
            from googleapiclient.discovery import build

            self._drive = build("drive", "v3", credentials=self._credentials())
        return self._drive

    def check_drive(self) -> str:
        """The root folder's name, or a `PipelineError` saying why it cannot be used."""
        root = self._settings.drive_root_folder_id
        if not root:
            raise PipelineError("no Drive folder id — set DRIVE_ROOT_FOLDER_ID in .env")
        service = self._service()
        try:
            found = service.files().get(fileId=root, fields="name,mimeType").execute()
        except Exception as exc:  # noqa: BLE001 - one honest line
            raise PipelineError(
                f"cannot open the Drive folder {root} with the signed-in account: {exc}"
            ) from exc
        if found.get("mimeType") != self.FOLDER_MIME:
            raise PipelineError(f"DRIVE_ROOT_FOLDER_ID {root} is not a folder")
        return str(found.get("name", root))

    def ensure_folder(self, country: str) -> str:
        """The per-country folder under the root, created once and remembered."""
        name = country.strip() or "Other"
        if name in self._folders:
            return self._folders[name]
        service = self._service()
        root = self._settings.drive_root_folder_id
        query = (
            f"name = '{name}' and mimeType = '{self.FOLDER_MIME}' "
            f"and '{root}' in parents and trashed = false"
        )
        found = service.files().list(q=query, fields="files(id)").execute()
        files = found.get("files", [])
        if files:
            folder_id = str(files[0]["id"])
        else:
            created = (
                service.files()
                .create(
                    body={"name": name, "mimeType": self.FOLDER_MIME, "parents": [root]},
                    fields="id",
                )
                .execute()
            )
            folder_id = str(created["id"])
        self._folders[name] = folder_id
        return folder_id

    def upload_png(self, path: Path, name: str, country: str) -> str:
        from googleapiclient.http import MediaFileUpload

        service = self._service()
        folder_id = self.ensure_folder(country)
        media = MediaFileUpload(str(path), mimetype="image/png", resumable=False)
        query = f"name = '{name}' and '{folder_id}' in parents and trashed = false"
        existing = service.files().list(q=query, fields="files(id)").execute().get("files", [])
        if existing:
            file_id = str(existing[0]["id"])
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            created = (
                service.files()
                .create(
                    body={"name": name, "parents": [folder_id]},
                    media_body=media,
                    fields="id",
                )
                .execute()
            )
            file_id = str(created["id"])
        service.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"}
        ).execute()
        return view_url(file_id)

    def sheet(self) -> SheetPort:
        if self._sheet is None:
            from adtranslate.sheet import GoogleSheet

            self._sheet = GoogleSheet(self._credentials(), self._sheet_id)
        return self._sheet


def _run_glitch(argv: list[str], timeout: float = 180.0) -> str:
    """Run one Glitch engine door and return its stdout, or raise with its own words."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"glitch door timed out: {argv[-1]}") from exc
    if completed.returncode != 0:
        tail = (completed.stdout or completed.stderr or "").strip().splitlines()
        raise PipelineError(f"glitch door failed: {tail[-1] if tail else 'no output'}")
    return completed.stdout


def _glitch_argv(script: str, *args: str) -> list[str]:
    """The exact `uv run --directory ... python integrations/<script>` invocation."""
    return [
        "uv",
        "run",
        "--directory",
        str(GLITCH_SCRIPTS),
        "python",
        f"integrations/{script}",
        *args,
    ]


def parse_external_data(text: str, *, multiline: bool = True) -> tuple[list[str], list[list[str]]]:
    """Read the pipe table Glitch's read door wraps in `<external_data>` tags.

    A record starts where a line's first field is an ID (`NL-025`) or empty AND its second
    field is one of the five Status values; every other line belongs to the record above
    it, so a multi-line cell keeps its newlines. Trailing empty cells are trimmed by the
    door, so short records are padded back to the header width. `multiline=False` treats
    every line as its own record, which is what the narrow Settings tab needs.
    """
    match = _EXTERNAL_DATA_RE.search(text)
    inner = match.group(1) if match else text
    lines = inner.split("\n")

    headers: list[str] | None = None
    records: list[str] = []
    for line in lines:
        if headers is None:
            if not line.strip() or _META_RE.match(line):
                continue
            headers = [c.strip() for c in line.split(_SEPARATOR)]
            continue
        if _starts_record(line, multiline):
            records.append(line)
        elif records:
            records[-1] += "\n" + line
    if headers is None:
        return [], []
    rows = [[c.strip() for c in r.split(_SEPARATOR)] for r in records]
    width = len(headers)
    return headers, [r + [""] * (width - len(r)) if len(r) < width else r for r in rows]


def _starts_record(line: str, multiline: bool) -> bool:
    """True when this physical line opens a new record rather than continuing one."""
    if not multiline:
        return bool(line.strip())
    # The door trims trailing empty cells, so a fresh Translate row (nothing in G:L yet)
    # arrives with only five separators. Anchor on the Status cell instead of a count.
    cells = line.split(_SEPARATOR)
    if len(cells) < _MIN_SEPARATORS:
        return False
    return bool(_ID_CELL_RE.match(cells[0].strip())) and cells[1].strip() in _STATUSES


class GlitchSheet(_BaseSheet):
    """The client's sheet, read and written through Glitch's connected Google account."""

    def __init__(self, sheet_id: str, runner: Any = _run_glitch, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._sheet_id = sheet_id
        self._run = runner

    def _read(self, a1_range: str, *, multiline: bool) -> tuple[list[str], list[list[str]]]:
        stdout = self._run(
            _glitch_argv("query.py", "gsheets", "read", self._sheet_id, "--range", a1_range)
        )
        return parse_external_data(stdout, multiline=multiline)

    def _load(self) -> tuple[list[str], list[list[str]]]:
        return self._read(SHEET_READ_RANGE, multiline=True)

    def _load_settings(self) -> tuple[list[str], list[list[str]]]:
        return self._read(SETTINGS_READ_RANGE, multiline=False)

    def _write_cells(self, row_number: int, updates: dict[str, str]) -> None:
        headers, _ = self._load()
        check_headers(headers)
        for header, value in updates.items():
            letter = column_letter(header_index(headers, header))
            self._run(
                _glitch_argv(
                    "gsheets.py",
                    "write",
                    self._sheet_id,
                    f"{SHEET_TAB}!{letter}{row_number}",
                    "--values",
                    json.dumps([[value]], ensure_ascii=False),
                    "--account",
                    "personal",
                    "--confirm",
                )
            )


class GlitchGooglePort:
    """Drive and Sheets through the Glitch engine's own doors — no key of this project's."""

    def __init__(self, settings: Settings, sheet_id: str = "", runner: Any = _run_glitch) -> None:
        self._settings = settings
        self._sheet_id = sheet_id or settings.sheet_id
        self._run = runner
        self._sheet: SheetPort | None = None

    def check_drive(self) -> str:
        return f"{self._settings.drive_root_folder_id} (through Glitch, not checked)"

    def upload_png(self, path: Path, name: str, country: str) -> str:
        stdout = self._run(
            _glitch_argv(
                "gdrive.py",
                "create",
                name,
                "--file",
                str(Path(path).resolve()),
                "--parent",
                self._settings.drive_root_folder_id,
                "--account",
                "personal",
                "--confirm",
            )
        )
        match = _FILE_ID_RE.search(stdout)
        if match is None:
            raise PipelineError(f"drive upload printed no file id for {name}")
        file_id = match.group(1)
        self._run(
            _glitch_argv(
                "gdrive.py",
                "share",
                file_id,
                "--type",
                "anyone",
                "--role",
                "reader",
                "--account",
                "personal",
                "--confirm",
            )
        )
        return view_url(file_id)

    def sheet(self) -> SheetPort:
        if self._sheet is None:
            self._sheet = GlitchSheet(self._sheet_id, runner=self._run)
        return self._sheet


# --------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------


@dataclass
class Ports:
    """Everything outside the pipeline, in one place."""

    fetcher: Fetcher
    copy: CopyPort
    vision: VisionPort
    image_edit: ImageEditPort
    google: GooglePort


def build_ports(settings: Settings, sheet_id: str = "", fetcher: Fetcher | None = None) -> Ports:
    """Pick the API implementation where a key exists, the no-key twin where it does not."""
    if fetcher is None:
        from adtranslate.fetch.playwright_fetch import PlaywrightFetcher

        fetcher = PlaywrightFetcher()

    copy_port: CopyPort = (
        ApiCopyPort(settings) if settings.anthropic_api_key else SessionCopyPort(settings)
    )
    vision_port: VisionPort = (
        ApiVisionPort(settings) if settings.gemini_api_key else SessionVisionPort(settings)
    )
    edit_port: ImageEditPort = (
        ApiImageEditPort(settings) if settings.gemini_api_key else GrokImageEditPort(settings)
    )
    # Glitch's doors only where this machine has them; elsewhere the project's own OAuth
    # token, which names the missing client file if `auth` has not been run.
    google_port: GooglePort = (
        GlitchGooglePort(settings, sheet_id)
        if not Path(settings.google_token_path).exists() and GLITCH_SCRIPTS.is_dir()
        else OAuthGooglePort(settings, sheet_id)
    )
    return Ports(
        fetcher=fetcher,
        copy=copy_port,
        vision=vision_port,
        image_edit=edit_port,
        google=google_port,
    )


def grok_available(settings: Settings) -> bool:
    """True when the Grok CLI this machine would drive is actually on disk."""
    binary = Path(settings.grok_bin).expanduser()
    return binary.exists() or shutil.which(str(binary)) is not None
