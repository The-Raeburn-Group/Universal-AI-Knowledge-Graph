from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="UKG_", extra="ignore")

    app_name: str = "Universal AI Knowledge Graph"
    environment: str = Field(default="development")
    api_key: str | None = Field(
        default=None,
        description="Optional static API key for private deployments",
    )
    storage_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = "postgresql+psycopg://ukg:ukg@localhost:5432/ukg"
    embedding_provider: str = "local-hash"
    embedding_dimensions: int = Field(default=384, ge=1, le=4096)
    openai_api_key: str | None = None
    max_chunk_chars: int = 1600
    chunk_overlap_chars: int = 200
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_production_storage(self) -> Settings:
        if self.environment.lower() == "production" and self.storage_backend != "postgres":
            raise ValueError("production requires UKG_STORAGE_BACKEND=postgres")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
