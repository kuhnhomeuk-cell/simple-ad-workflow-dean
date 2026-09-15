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
   In Testing, Google ends the sign-in after 7 days, and `run` then says to run `uv run adtranslate auth` again.
   To stop that, set the consent screen's publishing status to In production.
   Google then shows an unverified-app warning at sign-in, because the app is the owner's own. The owner continues past it.
   The engine asks for the Sheets scope and the full Drive scope, so it can use a folder the owner created by hand.
3. The sheet ID (the long id in the sheet's URL) and the ID of the Drive folder that will hold the country folders.
   The sheet must have the layout in "The sheet" below.
4. A Gemini API key, for the text inside ad images.
   Create it at https://aistudio.google.com/apikey in the same Google Cloud project, with billing turned on for that project.
   Gemini reads the text in each image, paints the translation back and checks the result.
   It is the only paid key, and it is required: the setup check stops without it.
5. No Anthropic API key. The Claude Code session writes the ad copy and the judge scores (see "Answering the model jobs").
   With an Anthropic key in `.env`, the engine calls the API itself instead.

## The sheet

The engine reads two tabs and checks their headers before it does anything.

Tab `Sheet1` has these 12 headers in row 1, spelled exactly:
`ID`, `Status`, `Target Country`, `Store / product URL`, `Trendtrack Ad Link`, `Facebook Ad Link`, `Drive URL`, `Primary Text`, `Headline`, `Description`, `Target Language`, `Review Note`.
A row is picked up when `Status` is `Translate`, `Facebook Ad Link` holds an Ad Library link and `Target Country` names a country on the Settings tab.
`ID` may be blank: the engine fills it, for example `NL-029` or `FR-001`.
The engine writes only `ID`, `Status`, `Drive URL`, `Primary Text`, `Headline`, `Description`, `Target Language` and `Review Note`.

Tab `Settings` has the headers `Target Country`, `Default Language`, `Currency`, one row per country.
`Default Language` carries the language name and its tag in brackets, for example `Dutch (nl-NL)` or `French (fr-FR)`.
The country name must match `Target Country` on `Sheet1` exactly.

Every uploaded image is shared as "anyone with the link can view", so the link in `Drive URL` opens for whoever has it.

## Setup, step by step

Run these from the repo root.

1. uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` if uv is missing. The repo pins Python 3.12 and uv fetches it.
2. `uv sync --group dev`
3. `uv run playwright install chromium` (on Linux: `uv run playwright install --with-deps chromium`)
4. `mkdir -p .secrets` and place the OAuth client JSON at `.secrets/google-oauth-client.json`.
5. `cp .env.example .env` and set `SHEET_ID`, `DRIVE_ROOT_FOLDER_ID` and `GEMINI_API_KEY`.
   Leave every other line blank.
6. `uv run adtranslate auth`. A browser opens once, the owner signs in, and the token is saved to `.secrets/google-token.json`.
7. `uv run adtranslate run --once --dry-run`. It is the setup check and starts no work.
   It prints the header row, the countries on the Settings tab, how many rows wait in `Translate`, the Drive folder's name and the image editor (`gemini`), then writes nothing.
   It also asks Gemini for the two image models, so a wrong key or an unavailable model stops here.
   If it prints all five lines and ends with "dry run: nothing written", the setup is done.
   Any problem prints one `error:` line naming what to fix.

`.env` and `.secrets/` are git-ignored and never leave the machine.

## Running a pass

`uv run adtranslate run --once --workers 5`

The engine claims each `Translate` row, fetches the ad, writes a copy job for the session, and sends the image to Gemini.
It will not start without `GEMINI_API_KEY`.
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

Answer every waiting job, then let the run continue.
A `copy` job left unanswered for 15 minutes ends its row as `Failed` with the reason "copy job not filled".
An unanswered `image_strings` job keeps the copy on the row and ends it `Needs Review` with "image not translated: copy job not filled".
To recover, write the answer file, set that row's `Status` back to `Translate`, and run again.
A copy answer for the same ad and language is kept and used at once.
An answer left over from a different ad or language is discarded, never reused.
`adtranslate resume` is only for rows left in `Processing` when a run was interrupted.

## Reading the result

The sheet is the result.
`Finished` rows carry the three fields, the Drive link, the language and a one-line note with the judge scores.
`Needs Review` rows say what to check.
`runs/<ID>/` holds the fetched ad, the before and after image and every job file, for anyone who wants to see the working.

## If something fails

- `auth` says the token has the wrong scopes. Delete `.secrets/google-token.json` and run `auth` again.
- `run` says the Google sign-in has expired or that it is not signed in. Run `uv run adtranslate auth`, sign in, then run again.
- `run` says the sheet cannot be read. The signed-in Google account must have edit access to that sheet.
- The fetcher returns no ad. Open the Ad Library link in a browser. The ad may have been taken down or be a video.
- The dry run or `run` says "no image editor". Set `GEMINI_API_KEY` in `.env`. See "What it needs".
- The dry run says the Gemini key or its image models were refused. Check the key in AI Studio and that billing is on.
- A row ends `Needs Review` with "image not translated: …" (for example a Gemini `429 RESOURCE_EXHAUSTED` billing message). The translated copy is already on the row. Fix the cause, such as billing or credit on the key's project, then set the row back to `Translate` to get the image.
- The dry run says it cannot open the Drive folder. The signed-in account needs access to that folder, and `DRIVE_ROOT_FOLDER_ID` must be the folder's id from its URL.
- Anything else. The row's `Review Note` and `runs/<ID>/` say what happened. Nothing is lost.

## Checks

`uv run ruff check .` then `uv run mypy src` then `uv run pytest -q`.
All three should be clean on a fresh clone.
