# ad-translate

Private engine. Reads a Google Sheet of Facebook ad links, transcreates the ad copy into the
target country's language, translates any text baked into the creative, uploads the creative to
Drive and writes the results back to the row. The sheet is the interface: set `Status` to
`Translate` and a `Target Country`, and the engine does the rest.

Not published. Runs on Dean's own Google account via an OAuth installed-app flow.

## Setup

1. `uv sync --group dev`
2. `uv run playwright install chromium`
3. In the Google Cloud console, create an OAuth client ID of type Desktop app, download the JSON
   and save it as `.secrets/google-oauth-client.json`.
4. `cp .env.example .env` and fill in the sheet ID, the Drive root folder ID and the API keys.
5. `uv run adtranslate auth` — opens a browser once and writes `.secrets/google-token.json`.

`.env` and `.secrets/` stay on the machine and are git-ignored.

## Commands

- `uv run adtranslate auth` — mint or refresh the Google token, print its scopes.
- `uv run adtranslate run --once --dry-run` — read the sheet, print the header row and the number
  of rows waiting in `Translate`, write nothing.
- `uv run adtranslate run --once` — one pass over the `Translate` rows.
- `uv run adtranslate run --watch` — poll the sheet every `POLL_SECONDS`.
- `--sheet <id>` overrides the sheet from `.env`; `--only <row id>` limits the pass to one row.

Phase 0 implements `auth` and `run --once --dry-run`. Any other `run` combination exits 2.

## Checks

```
uv run ruff check .
uv run mypy src
uv run pytest -q
```

State and next step: `STATE.md`. Plan: `specs/ad-translation-pipeline.html`.
