"""Live golden run: the FR source of 1059406850353566 transcreated to nl-NL.

Skipped unless ``ADTRANSLATE_LIVE=1`` and an Anthropic key is configured. It spends real
money and is never part of the default suite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from adtranslate.config import Settings
from adtranslate.copy.transcreate import CostMeter, judge, transcreate
from adtranslate.models import AdCreative, Locale

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ADTRANSLATE_LIVE") != "1",
        reason="set ADTRANSLATE_LIVE=1 to run the golden copy run",
    ),
]

ROOT = Path(__file__).resolve().parents[2]
FETCH_FIXTURE = ROOT / "fixtures" / "ads" / "1059406850353566" / "creative.json"
OUT_PATH = ROOT / "runs" / "golden" / "nl-025.json"

NL = Locale(country="Netherlands", language_name="Dutch", tag="nl-NL", currency="EUR")


def _source() -> AdCreative:
    if not FETCH_FIXTURE.exists():
        pytest.skip(f"no recorded source at {FETCH_FIXTURE}; run Phase 2 first")
    data = json.loads(FETCH_FIXTURE.read_text(encoding="utf-8"))
    return AdCreative.model_validate(data)


def test_golden_nl_025() -> None:
    settings = Settings()
    if not settings.anthropic_api_key:
        pytest.skip("no ANTHROPIC_API_KEY configured")

    ad = _source()
    meter = CostMeter()
    result = transcreate(ad, NL, settings, meter=meter)
    verdict = judge(ad, result, settings, locale=NL, meter=meter)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {
                "library_id": ad.library_id,
                "locale": NL.model_dump(),
                "source": {
                    "primary_text": ad.primary_text,
                    "headline": ad.headline,
                    "description": ad.description,
                },
                "target": result.model_dump(),
                "judge": verdict.model_dump(),
                "usd": meter.usd(),
                "tokens": {"input": meter.input_tokens, "output": meter.output_tokens},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    joined = " ".join([result.primary_text, result.headline, result.description]).lower()
    assert "vanavond" in result.headline.lower()
    assert "gratis" in joined
    for url in ("avenorparis.com",):
        assert url in " ".join([result.primary_text, result.headline, result.description])
    assert verdict.fidelity >= 4, verdict.issues
    assert verdict.fluency >= 4, verdict.issues
