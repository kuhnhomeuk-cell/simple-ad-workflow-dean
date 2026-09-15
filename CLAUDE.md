# Ad translator: instructions for Claude Code

Read `HANDOVER.md` first, all of it, then follow it step by step. It is the runbook.
No API key is needed. This session writes the ad copy and the judge scores, and reads the images.
The Codex CLI, signed in with the owner's ChatGPT subscription, edits the text inside images (see "What it needs" in `HANDOVER.md`).

Setup, from the repo root:
1. `uv sync --group dev`
2. `uv run playwright install chromium`
3. Place the owner's OAuth desktop-client JSON at `.secrets/google-oauth-client.json`.
4. `cp .env.example .env`, then set `SHEET_ID` and `DRIVE_ROOT_FOLDER_ID`. Leave the rest blank. Check `codex login status` says logged in.
5. `uv run adtranslate auth`
6. `uv run adtranslate run --once --dry-run`. It starts no work. Setup is done when it ends with "dry run: nothing written". Otherwise fix the `error:` line it prints.

A run:
- Start `uv run adtranslate run --once --workers 5` in the background.
- While it runs, check `uv run adtranslate jobs` and answer every waiting copy and vision job exactly as `HANDOVER.md` describes.
- Never commit `.env`, `.secrets/` or `runs/`.
