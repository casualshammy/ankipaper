"""Application configuration for AnkiPaper."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings read from environment variables and the .env file."""

    model_config = SettingsConfigDict(
        env_prefix="ANKIPAPER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cookie_max_age_days: int = Field(
        default=30,
        ge=1,
        description="Cookie session lifetime in days.",
    )

    behind_proxy: bool = Field(
        default=False,
        description="True if the application runs behind a reverse proxy (nginx).",
    )

    show_privacy_policy: bool = Field(
        default=False,
        description=(
            "If true, the login and deck-list footers show a link to "
            "/static/privacy_policy.html. Off by default so self-hosted "
            "deployments stay clean; enable on public deployments."
        ),
    )

    debug_headers: bool = Field(
        default=False,
        description=(
            "If true, the access-log middleware dumps every incoming request "
            "header. Useful when diagnosing client-IP / proxy-header issues. "
            "Off in production — it logs the full Cookie header and any "
            "Authorization values."
        ),
    )

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used for rate limiting.",
    )

    login_ip_max_attempts: int = Field(
        default=5,
        ge=1,
        description="Maximum failed login attempts per IP within the IP window.",
    )
    login_ip_window_seconds: int = Field(
        default=60,
        ge=1,
        description="Rolling window for the per-IP login rate limit.",
    )
    login_user_max_attempts: int = Field(
        default=10,
        ge=1,
        description="Maximum failed login attempts per username within the user window.",
    )
    login_user_window_seconds: int = Field(
        default=3600,
        ge=1,
        description="Rolling window for the per-username login rate limit.",
    )

    data_max_bytes: int = Field(
        default=0,
        ge=0,
        description=(
            "Maximum total size of /data in bytes for new accounts. "
            "Zero disables the limit."
        ),
    )
    media_max_file_bytes: int = Field(
        default=1 * 1024 * 1024,
        ge=1,
        description=(
            "Maximum size of a single media file in bytes. Files larger "
            "than this are skipped during media-sync (with a warning)."
        ),
    )
    media_max_collection_bytes: int = Field(
        default=200 * 1024 * 1024,
        ge=1,
        description=(
            "Maximum total size of the user's collection.media/ directory "
            "in bytes. When the existing directory already exceeds this "
            "limit, no new media files are written to disk during sync."
        ),
    )

    public_url: str = Field(
        default="",
        description=(
            "Public origin used in canonical, Open Graph and sitemap URLs "
            "(for example, https://ankipaper.study). Empty -> the URL is "
            "derived from the incoming request."
        ),
    )

    brand_name: str = Field(
        default="AnkiPaper",
        description="Product name surfaced in OG/site_name and brand alt text.",
    )

    meta_description: str = Field(
        default=(
            "AnkiPaper is a free web app that lets you review your Anki "
            "flashcards in a Kindle or e-ink browser. Sign in with your "
            "AnkiWeb account — no signup, no install, no tracking."
        ),
        description=(
            "Default meta description and OG/Twitter description used on "
            "the landing page."
        ),
    )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns the singleton Settings instance for the application."""

    return Settings()