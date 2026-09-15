"""One live proof that the real Sheets path works. Never run by default.

Runs only with `ADTRANSLATE_LIVE=1` and a configured DEV sheet:

    ADTRANSLATE_LIVE=1 uv run pytest -m live tests/live/test_sheet_live.py -q

It claims a Translate row on the DEV copy, writes a Finished outcome, reads it
back, then restores every cell it touched.
"""

import os

import pytest

from adtranslate.config import Settings
from adtranslate.models import CopyResult, RowOutcome
from adtranslate.sheet import WRITABLE_HEADERS, GoogleSheet

pytestmark = pytest.mark.live

LIVE = os.environ.get("ADTRANSLATE_LIVE") == "1"


@pytest.mark.skipif(not LIVE, reason="set ADTRANSLATE_LIVE=1 to run against the DEV sheet")
def test_claim_write_read_back_and_restore() -> None:
    settings = Settings()
    if not settings.sheet_id:
        pytest.skip("no sheet_id configured — set SHEET_ID in .env")

    from adtranslate.google_auth import get_credentials

    sheet = GoogleSheet(get_credentials(settings), settings.sheet_id)

    target = next((r for r in sheet.read_rows() if r.status.strip() == "Translate"), None)
    if target is None:
        pytest.skip("no Translate row on the DEV sheet")

    original = {h: sheet.cells_of(target.row_number, h) for h in WRITABLE_HEADERS + ["ID"]}
    try:
        assert sheet.claim(target) is True

        outcome = RowOutcome(
            status="Finished",
            copy_result=CopyResult(
                primary_text="live test primary",
                headline="live test headline",
                description="live test description",
            ),
            drive_url="https://drive.google.com/file/d/live-test/view",
            note="live test",
        )
        sheet.write(target, outcome)

        after = {h: sheet.cells_of(target.row_number, h) for h in WRITABLE_HEADERS}
        assert after["Status"] == "Finished"
        assert after["Primary Text"] == "live test primary"
        assert after["Headline"] == "live test headline"
        assert after["Description"] == "live test description"
        assert after["Drive URL"] == "https://drive.google.com/file/d/live-test/view"
        assert after["Review Note"] == "live test"
    finally:
        sheet.restore(target.row_number, original)
