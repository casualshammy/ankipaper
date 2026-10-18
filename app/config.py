"""Application configuration for AnkiPaper."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    trusted_upstream_https_proxy: bool = Field(
        default=False,
        description=(
            "True if the application runs behind a trusted upstream proxy "
            "that terminates TLS. Enables Secure cookies, X-Forwarded-For IP trust, "
            "and proxy-aware canonical URLs."
        ),
    )

    cookie_insecure_hosts: Annotated[list[str], NoDecode] = Field(
        default=[],
        description=(
            "Comma-separated list of Host header values (exact match or "
            "'*.suffix' wildcard) for which the session cookie should be "
            "set without the Secure flag. Required to allow LAN access over "
            "plain HTTP when ANKIPAPER_TRUSTED_UPSTREAM_HTTPS_PROXY=true "
            "and the same process serves both Cloudflare-fronted and direct "
            "LAN access. Example: 'ankipaper.lan,192.168.1.10'."
        ),
    )

    show_privacy_policy: bool = Field(
        default=False,
        description=(
            "If true, footers show a link to "
            "/static/privacy_policy.html. Off by default so self-hosted "
            "deployments stay clean; enable on public deployments."
        ),
    )

    contacts_email: str = Field(
        default="",
        description=(
            "Email address for contacting the site administrator. "
            "Shown in the footer if provided."
        ),
    )

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL used for rate limiting.",
    )

    metrics_username: str = Field(
        default="",
        description=(
            "HTTP Basic username required by the Prometheus scraper. "
            "Empty disables /metrics with HTTP 503."
        ),
    )
    metrics_password: str = Field(
        default="",
        description=(
            "HTTP Basic password required by the Prometheus scraper. "
            "Empty disables /metrics with HTTP 503."
        ),
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

    study_spa_enabled: bool = Field(
        default=False,
        description=(
            "When true, the deck-study link on the home page points to "
            "/deck/{id}/spa (the JS-SPA variant) instead of /deck/{id}/study."
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

    @field_validator("cookie_insecure_hosts", mode="before")
    @classmethod
    def _parse_cookie_insecure_hosts(cls, value: Any) -> Any:
        """Splits a comma-separated string into a list of host patterns."""

        if not isinstance(value, str):
            return value
        return [part.strip() for part in value.split(",") if part.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns the singleton Settings instance for the application."""

    return Settings()
