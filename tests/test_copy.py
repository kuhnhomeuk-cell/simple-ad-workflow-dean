"""Copy transcreation and judge, against a fake Anthropic client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from adtranslate.copy.transcreate import (
    CopyOut,
    CostMeter,
    JudgeOut,
    build_transcreate_prompts,
    judge,
    load_example,
    post_checks,
    transcreate,
)
from adtranslate.models import AdCreative, CopyResult, Locale

NL = Locale(country="Netherlands", language_name="Dutch", tag="nl-NL", currency="EUR")

SOURCE = AdCreative(
    library_id="1059406850353566",
    primary_text=(
        "J'ai eu toute une table pour moi.\n"
        "\n"
        "🌹 Un acheté, un offert\n"
        "\n"
        "→ Trouvez votre teinte sur avenorparis.com\n"
    ),
    headline="Un acheté, un offert - se termine ce soir",
    description="Un rouge à lèvres qui lit votre pH.",
    media_type="image",
)

GOOD = {
    "primary_text": (
        "Ik kreeg een hele tafel voor mezelf.\n"
        "\n"
        "🌹 Eén kopen, één gratis\n"
        "\n"
        "→ Ontdek uw tint op avenorparis.com\n"
    ),
    "headline": "Eén kopen, één gratis - vanavond",
    "description": "Een lippenstift die uw pH afleest.",
    "translator_notes": "BOGO kept as is.",
}


class FakeSettings:
    """The two model names transcreate/judge read off Settings."""

    copy_model = "claude-opus-5"
    judge_model = "claude-sonnet-5"
    anthropic_api_key = "unused"


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeParsed:
    def __init__(self, parsed: Any, usage: FakeUsage) -> None:
        self.parsed_output = parsed
        self.usage = usage


class FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeCreated:
    def __init__(self, payload: dict[str, Any], usage: FakeUsage) -> None:
        self.content = [FakeTextBlock(json.dumps(payload))]
        self.usage = usage


class FakeMessagesCreateOnly:
    """An older SDK: ``create`` and no ``parse``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []
        self.usage = FakeUsage(4000, 3000)

    def create(self, **kwargs: Any) -> FakeCreated:
        self.calls.append(kwargs)
        return FakeCreated(self.payload, self.usage)


class FakeMessages(FakeMessagesCreateOnly):
    """The current SDK: structured output through ``parse``."""

    def parse(self, **kwargs: Any) -> FakeParsed:
        self.calls.append(kwargs)
        model = kwargs["output_format"]
        return FakeParsed(model.model_validate(self.payload), self.usage)


class FakeClient:
    def __init__(self, payload: dict[str, Any], with_parse: bool = True) -> None:
        self.messages: FakeMessagesCreateOnly = (
            FakeMessages(payload) if with_parse else FakeMessagesCreateOnly(payload)
        )


def _copy(**overrides: Any) -> dict[str, Any]:
    payload = dict(GOOD)
    payload.update(overrides)
    return payload


def test_parses_into_copy_result() -> None:
    client = FakeClient(_copy())
    result = transcreate(SOURCE, NL, FakeSettings(), client=client)
    assert isinstance(result, CopyResult)
    assert result.headline == "Eén kopen, één gratis - vanavond"
    assert result.translator_notes == "BOGO kept as is."
    assert result.review_reasons == []
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["max_tokens"] == 16000
    assert call["output_format"] is CopyOut
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "high"}
    assert call["messages"][0]["role"] == "user"


def test_falls_back_to_create_when_parse_is_absent() -> None:
    client = FakeClient(_copy(), with_parse=False)
    assert not hasattr(client.messages, "parse")
    result = transcreate(SOURCE, NL, FakeSettings(), client=client)
    assert result.primary_text.startswith("Ik kreeg")
    schema = client.messages.calls[0]["output_config"]["format"]["schema"]
    assert schema["properties"]["headline"]


def test_post_check_line_breaks() -> None:
    short = _copy(primary_text="Ik kreeg een hele tafel voor mezelf. 🌹 avenorparis.com")
    result = transcreate(SOURCE, NL, FakeSettings(), client=FakeClient(short))
    assert any("line breaks" in r for r in result.review_reasons)
    assert not any("url missing" in r for r in result.review_reasons)


def test_post_check_missing_url() -> None:
    dropped = _copy(primary_text=GOOD["primary_text"].replace("avenorparis.com", "onze site"))
    result = transcreate(SOURCE, NL, FakeSettings(), client=FakeClient(dropped))
    assert "url missing from output: avenorparis.com" in result.review_reasons


def test_post_check_missing_emoji() -> None:
    dropped = _copy(primary_text=GOOD["primary_text"].replace("🌹 ", ""))
    result = transcreate(SOURCE, NL, FakeSettings(), client=FakeClient(dropped))
    assert "emoji missing from output: 🌹" in result.review_reasons


def test_post_check_long_headline() -> None:
    long_headline = "Eén kopen, één gratis en het eindigt vanavond om middernacht"
    result = transcreate(
        SOURCE, NL, FakeSettings(), client=FakeClient(_copy(headline=long_headline))
    )
    assert f"headline {len(long_headline)} chars over 40" in result.review_reasons


def test_post_check_long_description() -> None:
    long_description = "Een lippenstift die uw pH afleest. " * 6
    result = transcreate(
        SOURCE, NL, FakeSettings(), client=FakeClient(_copy(description=long_description))
    )
    assert f"description {len(long_description)} chars over 125" in result.review_reasons


def test_post_check_empty_field() -> None:
    result = transcreate(SOURCE, NL, FakeSettings(), client=FakeClient(_copy(description="  ")))
    assert "description is empty" in result.review_reasons


def test_post_checks_never_raise_on_empty_source() -> None:
    empty = AdCreative(library_id="x")
    assert post_checks(empty, CopyResult(primary_text="a", headline="b", description="c")) == []


def test_judge_below_threshold_appends_reasons() -> None:
    result = transcreate(SOURCE, NL, FakeSettings(), client=FakeClient(_copy()))
    payload = {"fidelity": 3, "fluency": 5, "issues": ["the guarantee window changed"]}
    client = FakeClient(payload)
    verdict = judge(SOURCE, result, FakeSettings(), client=client, locale=NL)
    assert verdict.fidelity == 3
    assert "judge: fidelity 3/5" in result.review_reasons
    assert "judge: the guarantee window changed" in result.review_reasons
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["output_format"] is JudgeOut
    assert call["output_config"] == {"effort": "low"}


def test_judge_at_threshold_appends_nothing() -> None:
    result = transcreate(SOURCE, NL, FakeSettings(), client=FakeClient(_copy()))
    client = FakeClient({"fidelity": 4, "fluency": 4, "issues": ["a nit"]})
    verdict = judge(SOURCE, result, FakeSettings(), client=client, locale=NL)
    assert verdict.issues == ["a nit"]
    assert result.review_reasons == []


def test_judge_low_fluency_appends() -> None:
    result = CopyResult(primary_text="a", headline="b", description="c")
    client = FakeClient({"fidelity": 5, "fluency": 2, "issues": []})
    judge(SOURCE, result, FakeSettings(), client=client, locale=NL)
    assert result.review_reasons == ["judge: fluency 2/5"]


def test_cost_meter_arithmetic() -> None:
    meter = CostMeter()
    meter.record("claude-opus-5", 1_000_000, 1_000_000)
    meter.record("claude-sonnet-5", 500_000, 100_000)
    assert meter.input_tokens == 1_500_000
    assert meter.output_tokens == 1_100_000
    assert meter.usd() == pytest.approx(5.0 + 25.0 + 1.0 + 1.0)


def test_cost_meter_reads_usage_from_the_calls() -> None:
    meter = CostMeter()
    result = transcreate(SOURCE, NL, FakeSettings(), client=FakeClient(_copy()), meter=meter)
    judge(
        SOURCE,
        result,
        FakeSettings(),
        client=FakeClient({"fidelity": 5, "fluency": 5, "issues": []}),
        locale=NL,
        meter=meter,
    )
    assert [c.model for c in meter.calls] == ["claude-opus-5", "claude-sonnet-5"]
    assert meter.calls[0].input_tokens == 4000
    expected = (4000 * 5 + 3000 * 25 + 4000 * 2 + 3000 * 10) / 1_000_000
    assert meter.usd() == pytest.approx(expected)


def test_prompt_carries_the_example_and_the_locale() -> None:
    system, user = build_transcreate_prompts(SOURCE, NL)
    example = load_example()
    assert "Dutch" in system and "nl-NL" in system and "EUR" in system
    assert "Netherlands" in system
    assert "NL-025" in system
    assert example["target"]["headline"] in system
    assert example["target"]["primary_text"][:80] in system
    assert "40 characters or fewer" in system
    assert "125 characters or fewer" in system
    assert "Keep every line break" in system
    assert "translator_notes" in system
    # The ad itself rides in the user turn, not the system prompt.
    assert SOURCE.headline in user
    assert SOURCE.primary_text.strip() in user
    assert SOURCE.primary_text.strip() not in system


def test_example_fixture_is_row_nl_025() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "copy" / "nl-025.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["id"] == "NL-025"
    assert data["library_id"] == "1059406850353566"
    assert data["locale"]["tag"] == "nl-NL"
    assert data["target"]["headline"] == "Eén kopen, één gratis - eindigt vanavond"
    assert data["target"]["primary_text"].startswith("Ik kreeg een hele tafel voor mezelf")
