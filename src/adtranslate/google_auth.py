"""OAuth installed-app flow against the owner's own Google account."""

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from adtranslate.config import Settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class AuthError(RuntimeError):
    """Raised when the token or the OAuth client JSON is not usable."""


def get_credentials(settings: Settings, interactive: bool = False) -> Credentials:
    """Return usable credentials, refreshing the token as needed.

    Only `adtranslate auth` is interactive: it opens the browser when there is no token or
    the token can no longer be refreshed. A run never opens a browser; it names the fix.
    """
    token_path = Path(settings.google_token_path)
    client_secret_path = Path(settings.google_client_secret_path)

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path))
        if not set(SCOPES) <= set(creds.scopes or []):
            raise AuthError(
                f"the token at {token_path} has the wrong scopes — "
                "delete it and run `uv run adtranslate auth` again"
            )

    if creds is not None and creds.valid:
        return creds

    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            if not interactive:
                raise AuthError(
                    "the Google sign-in has expired or was revoked — "
                    "run `uv run adtranslate auth` to sign in again"
                ) from exc
        else:
            _save(creds, token_path)
            return creds

    if not client_secret_path.exists():
        raise AuthError(
            f"missing OAuth client file at {client_secret_path} — "
            "download it from the Google Cloud console "
            "(APIs & Services → Credentials → OAuth client ID → Desktop app) "
            "and save it there"
        )
    if not interactive:
        raise AuthError("not signed in to Google — run `uv run adtranslate auth` first")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0)
    _save(creds, token_path)
    return creds


def _save(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
