"""Конфигурация приложения kindlanki."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения, читаемые из переменных окружения и .env-файла."""

    model_config = SettingsConfigDict(
        env_prefix="KINDLANKI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = Field(
        default="http://localhost:8000",
        description="Публичный URL приложения, используется для абсолютных ссылок.",
    )

    cookie_max_age_days: int = Field(
        default=30,
        ge=1,
        description="Время жизни cookie-сессии в днях.",
    )

    behind_proxy: bool = Field(
        default=False,
        description="True, если приложение работает за reverse proxy (nginx).",
    )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает singleton-объект настроек приложения."""

    return Settings()