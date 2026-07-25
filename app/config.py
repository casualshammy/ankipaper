"""Application configuration for kindlanki."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings read from environment variables and the .env file."""

    model_config = SettingsConfigDict(
        env_prefix="KINDLANKI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = Field(
        default="http://localhost:8000",
        description="Public application URL used for absolute links.",
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

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns the singleton Settings instance for the application."""

    return Settings()