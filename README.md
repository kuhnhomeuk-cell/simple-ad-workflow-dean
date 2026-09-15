# ad-translate

Reads a Google Sheet of Facebook Ad Library links, translates each ad's copy into the target country's language, translates the text inside the ad image, uploads the image to Drive and writes the results back to the row.

Setup, running and troubleshooting are in `HANDOVER.md`. Follow it top to bottom.

Inputs: the sheet ID, a Drive folder ID, an OAuth desktop-client JSON and a Gemini API key, all from the owner's own Google Cloud project.
A Claude Code session writes the ad copy, so no Anthropic key is needed.

## Commands

- `uv run adtranslate auth`: sign in to Google once and save the token.
- `uv run adtranslate run --once --dry-run`: the setup check. Reads the sheet and the Drive folder, starts no work.
- `uv run adtranslate run --once --workers 5`: one pass over the `Translate` rows.
- `uv run adtranslate jobs`: the copy jobs waiting for the session.
- `uv run adtranslate resume`: finish rows left in `Processing` by an interrupted run.

## Checks

`uv run ruff check .` then `uv run mypy src` then `uv run pytest -q`.
