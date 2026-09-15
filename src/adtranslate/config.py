"""Runtime settings, read from the environment and `.env`."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every external key and tunable the engine needs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        # `cp .env.example .env` leaves every key blank; a blank means "use the default".
        env_ignore_empty=True,
    )

    sheet_id: str = ""
    drive_root_folder_id: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    apify_token: str | None = None

    copy_model: str = "claude-opus-5"
    judge_model: str = "claude-sonnet-5"
    vision_model: str = "gemini-2.5-flash"
    image_model: str = "gemini-2.5-flash-image"
    image_retry_model: str = "gemini-3-pro-image"

    # The no-key runtime: a human-or-agent session answers the model jobs, and the Grok
    # CLI subscription does the image edits.
    session_wait_seconds: int = 900
    grok_bin: str = "~/.grok/bin/grok"
    grok_timeout_seconds: int = 600

    stale_claim_minutes: int = 30
    poll_seconds: int = 90

    google_token_path: str = ".secrets/google-token.json"
    google_client_secret_path: str = ".secrets/google-oauth-client.json"
