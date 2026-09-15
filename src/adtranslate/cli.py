"""The `adtranslate` command line."""

import sys
from pathlib import Path

import typer

from adtranslate.config import Settings
from adtranslate.fetch import Fetcher
from adtranslate.google_auth import SCOPES, AuthError, get_credentials

app = typer.Typer(add_completion=False, help="Translate ads listed in a Google Sheet.")


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


@app.command()
def auth() -> None:
    """Mint or refresh the Google token and print the scopes it carries."""
    settings = Settings()
    try:
        creds = get_credentials(settings)
    except AuthError as exc:
        _fail(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - one line out, never a traceback
        _fail(f"google auth failed: {exc}")
        return
    typer.echo(f"token: {settings.google_token_path}")
    for scope in creds.scopes or SCOPES:
        typer.echo(f"scope: {scope}")


@app.command()
def run(
    once: bool = typer.Option(True, "--once/--watch", help="One pass, or poll forever."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Read only; write nothing."),
    sheet: str = typer.Option("", "--sheet", help="Sheet ID override."),
    only: str = typer.Option("", "--only", help="Process only this row ID."),
    workers: int = typer.Option(5, "--workers", help="How many rows run at once."),
    runs: str = typer.Option("runs", "--runs", help="Where each row's folder is written."),
) -> None:
    """Process every row whose Status is Translate."""
    settings = Settings()
    sheet_id = sheet or settings.sheet_id
    if not sheet_id:
        _fail("no sheet id — set SHEET_ID in .env or pass --sheet")

    from adtranslate.pipeline import run_once, run_watch
    from adtranslate.ports import PipelineError, build_ports

    ports = build_ports(settings, sheet_id, fetcher=_fetcher(dry_run))
    runs_dir = Path(runs)
    try:
        if once:
            run_once(ports, settings, runs_dir, workers, only or None, dry_run, typer.echo)
        else:
            run_watch(ports, settings, runs_dir, workers, only or None, dry_run, typer.echo)
    except PipelineError as exc:
        _fail(exc.reason)
    except AuthError as exc:
        _fail(str(exc))


@app.command()
def resume(
    sheet: str = typer.Option("", "--sheet", help="Sheet ID override."),
    only: str = typer.Option("", "--only", help="Resume only this row ID."),
    workers: int = typer.Option(5, "--workers", help="How many rows run at once."),
    runs: str = typer.Option("runs", "--runs", help="Where each row's folder is written."),
) -> None:
    """Finish the rows this machine claimed but never wrote an outcome for."""
    settings = Settings()
    sheet_id = sheet or settings.sheet_id
    if not sheet_id:
        _fail("no sheet id — set SHEET_ID in .env or pass --sheet")

    from adtranslate.pipeline import run_resume
    from adtranslate.ports import PipelineError, build_ports

    ports = build_ports(settings, sheet_id, fetcher=_fetcher(False))
    try:
        run_resume(ports, settings, Path(runs), workers, only or None, typer.echo)
    except PipelineError as exc:
        _fail(exc.reason)
    except AuthError as exc:
        _fail(str(exc))


@app.command()
def jobs(
    runs: str = typer.Option("runs", "--runs", help="Where each row's folder is written."),
) -> None:
    """List the session jobs still waiting to be filled in."""
    from adtranslate.pipeline import pending_jobs

    pending = pending_jobs(Path(runs))
    if not pending:
        typer.echo("no pending jobs")
        return
    for row_id, request in pending:
        typer.echo(f"{row_id}: {request}")


def _fetcher(dry_run: bool) -> Fetcher:
    """The live fetcher. A dry run still fetches — it only stops short of writing."""
    from adtranslate.fetch.playwright_fetch import PlaywrightFetcher

    return PlaywrightFetcher()


@app.command()
def fetch(
    ad: str = typer.Argument(..., help="Library ID, or any Ad Library link carrying one."),
    out: str = typer.Option("runs/fetch", "--out", help="Where the creative is saved."),
    headed: bool = typer.Option(False, "--headed", help="Show the browser window."),
) -> None:
    """Read one ad off the Ad Library: print its JSON and save its creative."""
    from adtranslate.fetch import AdNotFound, download_creative, parse_library_id
    from adtranslate.fetch.playwright_fetch import PlaywrightFetcher, as_json

    try:
        library_id = parse_library_id(ad)
    except ValueError as exc:
        _fail(str(exc))
        return

    try:
        creative = PlaywrightFetcher(headless=not headed).fetch(library_id)
    except AdNotFound as exc:
        _fail(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - one line out, never a traceback
        _fail(f"fetch failed for {library_id}: {exc}")
        return

    typer.echo(as_json(creative), nl=False)

    dest_dir = Path(out) / library_id
    if creative.media_type == "none":
        typer.echo("no creative to save", err=True)
        return
    try:
        path = download_creative(creative, dest_dir)
    except Exception as exc:  # noqa: BLE001 - one line out, never a traceback
        _fail(f"could not download the creative: {exc}")
        return
    typer.echo(f"saved: {path}")
    if creative.needs_review_reason:
        typer.echo(f"needs review: {creative.needs_review_reason}")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
