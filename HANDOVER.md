# Handover: running the ad translator on your own machine

This file is written for the Claude Code agent (or the person) setting the engine up on the owner's computer.
Read it top to bottom once, then follow the steps in order.
Every step is safe to repeat.

## What this engine does

It reads the owner's Google Sheet.
Any row whose `Status` is `Translate` and that carries a Facebook Ad Library link and a `Target Country` gets processed.
The ad is fetched, the primary text, headline and description are rewritten into the target language, any text inside the ad image is translated and painted back, the image is uploaded to a Drive folder for that country, and the row is filled in.
A row the engine is not sure about ends as `Needs Review` with the reason in the `Review Note` cell.
Nothing else on the sheet is touched.

## What it needs from the owner

1. A Google account that can edit the sheet and a Drive folder for the images.
2. A Google Cloud project with the Sheets API and the Drive API enabled, and an OAuth client of type Desktop app.
   The downloaded JSON goes to `.secrets/google-oauth-client.json`.
   While the OAuth consent screen is in Testing, add the owner's Google account under Test users, or `auth` ends with `access_denied`.
   The engine asks for the Sheets scope and the full Drive scope, so it can use a folder the owner created by hand.
3. The sheet ID (the long id in the sheet's URL) and the ID of the Drive folder that will hold the country folders.
4. For the text inside images, one of: the Grok Imagine CLI on this machine (an X Premium subscription), or a Gemini API key.
   Without either, rows whose image carries text land on `Needs Review` with that reason.
   Everything else still runs.
5. No Anthropic API key is needed when a Claude Code session drives the run (see "Answering the model jobs").
   With an Anthropic key in `.env`, the engine calls the API itself.

## Setup, step by step

Run these from the repo root.

1. Python 3.12 and uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` if uv is missing.
2. `uv sync --group dev`
3. `uv run playwright install chromium`
4. `mkdir -p .secrets` and place the OAuth client JSON at `.secrets/google-oauth-client.json`.
5. `cp .env.example .env` and set `SHEET_ID` and `DRIVE_ROOT_FOLDER_ID`.
   Leave the API keys empty unless the owner has them.
6. `uv run adtranslate auth`. A browser opens once, the owner signs in, and the token is saved to `.secrets/google-token.json`.
7. `uv run adtranslate run --once --dry-run`. It is the setup check and starts no work.
   It prints the header row, the countries on the Settings tab, how many rows wait in `Translate`, the Drive folder's name and which image editor this machine has, then writes nothing.
   If it prints all five lines, the setup is done.
   Any problem prints one `error:` line naming what to fix.

`.env` and `.secrets/` are git-ignored and never leave the machine.

## Running a pass

`uv run adtranslate run --once --workers 5`

The engine claims each `Translate` row, fetches the ad and writes a job file for every step that needs a language model.
It then waits (up to 15 minutes per job) for the answer file to appear.
While it waits, the run is blocked.
Run it in the background and answer the jobs from the same session.

`uv run adtranslate jobs` lists every job file that is waiting, with its path.
`uv run adtranslate resume` picks up rows left in `Processing` after an interruption.

## Answering the model jobs

This is the part the Claude Code session does.
Each job is a small JSON file that says exactly what it needs.

**Copy job**: `runs/<ID>/copy.request.json`.
It carries the source ad fields, the locale, the full system prompt and user prompt to use, the judge rubric, and any image strings that need translating.
Its `task` field is `copy` or `image_strings`.
For `copy`, write the whole answer to the path in `answer_file` (`runs/<ID>/copy.json`).
For `image_strings`, a row whose image carries text is asking a second time: open the existing `copy.json`, keep every field, and add an `image_strings` entry for each source the request lists.
`adtranslate jobs` lists both kinds until the answer covers them.
The answer has exactly this shape:

```json
{
  "primary_text": "...",
  "headline": "...",
  "description": "...",
  "translator_notes": "...",
  "judge": {"fidelity": 5, "fluency": 5, "issues": []},
  "image_strings": [{"source": "...", "target": "..."}]
}
```

Follow the system prompt in the request word for word.
The judge block is a second, strict read of the answer against the source.
The writer and the judge must not be the same pass, so spawn a separate sub-agent for the judge if you can.
Every `image_strings` source in the request must appear with a target.

**Vision job**: `runs/<ID>/vision.request.json`, question `detect` or `verify`.
Open the image file named in the request with the Read tool and look at it.
For `detect`, answer with `{"has_text": bool, "blocks": [{"text": "...", "role": "promo|price|handwritten|logo|other", "translate": bool}]}`.
Logos and brand names are `translate: false`.
For `verify`, compare the original and the edited image and answer `{"targets_present": [...], "sources_remaining": [...], "unchanged_score": 1-5}`.
Write it to the `answer_file` path.

Answer every waiting job, then let the run continue.
A job left unanswered for 15 minutes ends its row as `Failed` with the reason "copy job not filled" (or "vision job not filled").
To recover, write the answer file, set that row's `Status` back to `Translate`, and run again.
A copy answer for the same ad and language is kept and used at once, a vision job is asked again.
An answer left over from a different ad or language is discarded, never reused.
`adtranslate resume` is only for rows left in `Processing` when a run was interrupted.

## Reading the result

The sheet is the result.
`Finished` rows carry the three fields, the Drive link, the language and a one-line note with the judge scores.
`Needs Review` rows say what to check.
`runs/<ID>/` holds the fetched ad, the before and after image and every job file, for anyone who wants to see the working.

## If something fails

- `auth` says the token has the wrong scopes. Delete `.secrets/google-token.json` and run `auth` again.
- `run` says the sheet cannot be read. The signed-in Google account must have edit access to that sheet.
- The fetcher returns no ad. Open the Ad Library link in a browser. The ad may have been taken down or be a video.
- Rows end `Needs Review` with "no image editor on this machine". Neither the Grok CLI nor a Gemini key is available. See "What it needs".
- The dry run says it cannot open the Drive folder. The signed-in account needs access to that folder, and `DRIVE_ROOT_FOLDER_ID` must be the folder's id from its URL.
- Anything else. The row's `Review Note` and `runs/<ID>/` say what happened. Nothing is lost.

## Checks

`uv run ruff check .` then `uv run mypy src` then `uv run pytest -q`.
All three should be clean on a fresh clone.
