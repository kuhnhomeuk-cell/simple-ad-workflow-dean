# ad-translate

Reads a Google Sheet of Facebook Ad Library links, translates each ad's copy into the target country's language, translates the text inside the ad image, uploads the image to Drive and writes the results back to the row.

Setup, running and troubleshooting are in `HANDOVER.md`. Follow it top to bottom.

Inputs: the sheet ID, a Drive folder ID and an OAuth desktop-client JSON from the owner's own Google Cloud project, plus the Codex CLI signed in with the owner's ChatGPT subscription for the images.
A Claude Code session writes the ad copy and reads the images, so no API key is needed. A Gemini API key can do the image work instead of Codex.

## Commands

- `uv run adtranslate auth`: sign in to Google once and save the token.
- `uv run adtranslate run --once --dry-run`: the setup check. Reads the sheet and the Drive folder, starts no work.
- `uv run adtranslate run --once --workers 5`: one pass over the `Translate` rows.
- `uv run adtranslate jobs`: the copy and vision jobs waiting for the session.
- `uv run adtranslate resume`: finish rows left in `Processing` by an interrupted run.

## Checks

`uv run ruff check .` then `uv run mypy src` then `uv run pytest -q`.
