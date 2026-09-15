"""Transcreate the three copy fields with Claude, then have a cheaper model read them back.

Nothing in here raises on a quality problem: every defect the deterministic checks find
is appended to ``CopyResult.review_reasons`` so the orchestrator can route the row to
Needs Review with the outputs still written.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from adtranslate.models import AdCreative, CopyResult, Locale

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = _PACKAGE_ROOT / "prompts"
EXAMPLE_PATH = _PACKAGE_ROOT.parent.parent / "fixtures" / "copy" / "nl-025.json"

AD_SECTION_MARKER = "# The ad"
JUDGE_SOURCE_MARKER = "# Source"

MAX_TOKENS = 16000
HEADLINE_LIMIT = 40
DESCRIPTION_LIMIT = 125
LINE_BREAK_TOLERANCE = 0.10
JUDGE_THRESHOLD = 4

# USD per million tokens, (input, output).
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "opus-5": (5.0, 25.0),
    "sonnet-5": (2.0, 10.0),
}
DEFAULT_PRICE = MODEL_PRICES["sonnet-5"]

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+|\b[\w-]+\.(?:com|net|org|nl|fr|io|co)\b")
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000027bf"
    "\U00002190-\U000021ff"
    "\U00002b00-\U00002bff"
    "\U0000fe0f"
    "]"
)


class CopyOut(BaseModel):
    """The structured output shape the copy model is asked for."""

    primary_text: str = Field(description="The full transcreated primary text.")
    headline: str = Field(description="The transcreated headline, 40 characters or fewer.")
    description: str = Field(description="The transcreated description, 125 characters or fewer.")
    translator_notes: str = Field(
        default="", description="Short English notes for the human reviewer."
    )


class JudgeOut(BaseModel):
    """The structured output shape the judge model is asked for."""

    fidelity: int = Field(description="1-5: does the target say what the source says?")
    fluency: int = Field(description="1-5: does it read as native copy?")
    issues: list[str] = Field(default_factory=list, description="One short sentence per problem.")


class JudgeResult(BaseModel):
    """What the judge returns to the pipeline."""

    fidelity: int
    fluency: int
    issues: list[str] = Field(default_factory=list)


@dataclass
class CallUsage:
    """One model call's token usage."""

    model: str
    input_tokens: int
    output_tokens: int


@dataclass
class CostMeter:
    """Accumulates token usage per call and estimates the spend in USD."""

    calls: list[CallUsage] = field(default_factory=list)

    def record(self, model: str, input_tokens: int, output_tokens: int) -> CallUsage:
        """Add one call's usage and return it."""
        usage = CallUsage(
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
        )
        self.calls.append(usage)
        return usage

    def record_response(self, model: str, response: Any) -> CallUsage:
        """Read ``response.usage`` and record it; a response without usage counts as zero."""
        usage = getattr(response, "usage", None)
        return self.record(
            model,
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
        )

    @property
    def input_tokens(self) -> int:
        """Total input tokens across every recorded call."""
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        """Total output tokens across every recorded call."""
        return sum(c.output_tokens for c in self.calls)

    def usd(self) -> float:
        """Estimated spend in USD at the published per-million prices."""
        total = 0.0
        for call in self.calls:
            price_in, price_out = price_for(call.model)
            total += call.input_tokens / 1_000_000 * price_in
            total += call.output_tokens / 1_000_000 * price_out
        return round(total, 6)


def price_for(model: str) -> tuple[float, float]:
    """The (input, output) USD-per-million price for a model name."""
    for key, price in MODEL_PRICES.items():
        if key in model:
            return price
    return DEFAULT_PRICE


def load_prompt(name: str) -> str:
    """Read one prompt template from ``prompts/``."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def load_example(path: Path | None = None) -> dict[str, Any]:
    """Read the NL-025 worked-example pair."""
    data: dict[str, Any] = json.loads((path or EXAMPLE_PATH).read_text(encoding="utf-8"))
    return data


def render_example(example: dict[str, Any]) -> str:
    """Render the worked example as the block the system prompt carries."""
    src = example.get("source", {})
    tgt = example.get("target", {})
    locale = example.get("locale", {})
    lines = [
        f"Row {example.get('id', '')} — {example.get('source_language', 'source')} → "
        f"{locale.get('language_name', '')} ({locale.get('tag', '')}). "
        "The target below is the client's own human-written copy.",
        "",
    ]
    if not example.get("source_complete", False):
        lines += [
            "NOTE: only part of the source was recorded when this example was written, so the "
            "target is shown as a style and structure reference rather than a line-for-line pair.",
            "",
        ]
    for label, block in (("SOURCE", src), ("TARGET", tgt)):
        lines += [
            f"{label} primary text:",
            str(block.get("primary_text", "")),
            "",
            f"{label} headline:",
            str(block.get("headline", "")),
            "",
            f"{label} description:",
            str(block.get("description", "")),
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def build_transcreate_prompts(
    ad: AdCreative, locale: Locale, example: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Return the (system, user) prompt pair for the copy call."""
    rendered = render_example(example if example is not None else load_example())
    filled = load_prompt("transcreate").format(
        language_name=locale.language_name,
        tag=locale.tag,
        country=locale.country,
        currency=locale.currency,
        example=rendered,
        primary_text=ad.primary_text,
        headline=ad.headline,
        description=ad.description,
    )
    return _split(filled, AD_SECTION_MARKER)


def build_judge_prompts(ad: AdCreative, result: CopyResult, locale: Locale) -> tuple[str, str]:
    """Return the (system, user) prompt pair for the judge call."""
    filled = load_prompt("judge").format(
        language_name=locale.language_name,
        tag=locale.tag,
        country=locale.country,
        currency=locale.currency,
        src_primary_text=ad.primary_text,
        src_headline=ad.headline,
        src_description=ad.description,
        tgt_primary_text=result.primary_text,
        tgt_headline=result.headline,
        tgt_description=result.description,
    )
    return _split(filled, JUDGE_SOURCE_MARKER)


def _split(filled: str, marker: str) -> tuple[str, str]:
    """Split a rendered template into the system half and the user half."""
    head, sep, tail = filled.partition(marker)
    if not sep:
        return filled, ""
    return head.rstrip() + "\n", (marker + tail).strip() + "\n"


def _structured_call(
    client: Any,
    model: str,
    system: str,
    user: str,
    output_format: type[BaseModel],
    effort: str,
    meter: CostMeter | None,
) -> tuple[dict[str, Any], Any]:
    """One structured-output call, through ``messages.parse`` where the SDK has it."""
    messages = [{"role": "user", "content": user}]
    parse = getattr(client.messages, "parse", None)
    if parse is not None:
        response = parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            output_format=output_format,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
        )
        parsed = response.parsed_output
    else:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": output_format.model_json_schema()},
            },
        )
        parsed = json.loads(_text_of(response))
    if meter is not None:
        meter.record_response(model, response)
    return _as_dict(parsed), response


def _text_of(response: Any) -> str:
    """The first text block of a non-parse response."""
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            return str(text)
    raise ValueError("model response carried no text block")


def _as_dict(parsed: Any) -> dict[str, Any]:
    """Normalise a parsed payload to a plain dict."""
    if isinstance(parsed, BaseModel):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return dict(parsed)
    raise TypeError(f"unexpected parsed output: {type(parsed)!r}")


def _client_for(settings: Any, client: Any | None) -> Any:
    """The passed client, or a real Anthropic client built from settings."""
    if client is not None:
        return client
    from anthropic import Anthropic

    return Anthropic(api_key=settings.anthropic_api_key)


def post_checks(ad: AdCreative, result: CopyResult) -> list[str]:
    """Every deterministic defect in the transcreated copy, as review reasons."""
    reasons: list[str] = []

    for name, value in (
        ("primary_text", result.primary_text),
        ("headline", result.headline),
        ("description", result.description),
    ):
        if not value.strip():
            reasons.append(f"{name} is empty")

    src_breaks = ad.primary_text.count("\n")
    out_breaks = result.primary_text.count("\n")
    if src_breaks:
        low = src_breaks * (1 - LINE_BREAK_TOLERANCE)
        high = src_breaks * (1 + LINE_BREAK_TOLERANCE)
        if not low <= out_breaks <= high:
            reasons.append(f"line breaks {out_breaks} outside 10% of source {src_breaks}")
    elif out_breaks:
        reasons.append(f"line breaks {out_breaks} outside 10% of source 0")

    joined = "\n".join([result.primary_text, result.headline, result.description])
    src_joined = "\n".join([ad.primary_text, ad.headline, ad.description])

    for url in _unique(_URL_RE.findall(src_joined)):
        if url not in joined:
            reasons.append(f"url missing from output: {url}")

    for emoji in _unique(_EMOJI_RE.findall(src_joined)):
        if emoji not in joined:
            reasons.append(f"emoji missing from output: {emoji}")

    if len(result.headline) > HEADLINE_LIMIT:
        reasons.append(f"headline {len(result.headline)} chars over {HEADLINE_LIMIT}")
    if len(result.description) > DESCRIPTION_LIMIT:
        reasons.append(f"description {len(result.description)} chars over {DESCRIPTION_LIMIT}")

    return reasons


def _unique(items: list[str]) -> list[str]:
    """The items in order, without repeats."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def transcreate(
    ad: AdCreative,
    locale: Locale,
    settings: Any,
    client: Any | None = None,
    meter: CostMeter | None = None,
    example: dict[str, Any] | None = None,
) -> CopyResult:
    """Transcreate the three copy fields, then append every deterministic defect found."""
    system, user = build_transcreate_prompts(ad, locale, example)
    payload, _ = _structured_call(
        _client_for(settings, client),
        settings.copy_model,
        system,
        user,
        CopyOut,
        "high",
        meter,
    )
    out = CopyOut.model_validate(payload)
    result = CopyResult(
        primary_text=out.primary_text,
        headline=out.headline,
        description=out.description,
        translator_notes=out.translator_notes,
    )
    result.review_reasons = list(result.review_reasons) + post_checks(ad, result)
    return result


def judge(
    ad: AdCreative,
    result: CopyResult,
    settings: Any,
    client: Any | None = None,
    locale: Locale | None = None,
    meter: CostMeter | None = None,
) -> JudgeResult:
    """Read the target back against the source; a score below 4 becomes a review reason."""
    locale = locale or Locale(country="", language_name="", tag="", currency="")
    system, user = build_judge_prompts(ad, result, locale)
    payload, _ = _structured_call(
        _client_for(settings, client),
        settings.judge_model,
        system,
        user,
        JudgeOut,
        "low",
        meter,
    )
    out = JudgeOut.model_validate(payload)
    verdict = JudgeResult(fidelity=out.fidelity, fluency=out.fluency, issues=list(out.issues))

    reasons: list[str] = []
    if verdict.fidelity < JUDGE_THRESHOLD:
        reasons.append(f"judge: fidelity {verdict.fidelity}/5")
    if verdict.fluency < JUDGE_THRESHOLD:
        reasons.append(f"judge: fluency {verdict.fluency}/5")
    if reasons:
        reasons += [f"judge: {issue}" for issue in verdict.issues]
        result.review_reasons = list(result.review_reasons) + reasons
    return verdict


class ImageStringsOut(BaseModel):
    """The structured output shape for the burned-in image strings."""

    targets: list[str] = Field(
        default_factory=list,
        description="One translation per source string, in the same order.",
    )


IMAGE_STRINGS_SYSTEM = (
    "You translate short strings burned into an advertising image. Keep each translation "
    "close to the source in length so it still fits where it sits, keep the register and "
    "the punctuation style, and translate nothing that is a brand name, a URL or a handle. "
    "Return exactly one target string per source, in the same order."
)


def build_image_strings_prompts(strings: list[str], locale: Locale) -> tuple[str, str]:
    """The (system, user) pair for the image-string call."""
    listed = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(strings))
    user = (
        f"Target language: {locale.language_name} ({locale.tag}), {locale.country}, "
        f"currency {locale.currency}.\n\nStrings:\n{listed}\n"
    )
    return IMAGE_STRINGS_SYSTEM, user


def translate_image_strings(
    strings: list[str],
    locale: Locale,
    settings: Any,
    client: Any | None = None,
    meter: CostMeter | None = None,
) -> list[str]:
    """Translate the strings burned into a creative, one target per source."""
    if not strings:
        return []
    system, user = build_image_strings_prompts(strings, locale)
    payload, _ = _structured_call(
        _client_for(settings, client),
        settings.copy_model,
        system,
        user,
        ImageStringsOut,
        "low",
        meter,
    )
    out = ImageStringsOut.model_validate(payload)
    if len(out.targets) != len(strings):
        raise ValueError(f"model returned {len(out.targets)} targets for {len(strings)} strings")
    return list(out.targets)
