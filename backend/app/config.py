from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DASHSCOPE_RERANK_URL: str = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
    EMBEDDING_MODEL: str = "text-embedding-v4"
    EMBEDDING_DIMENSION: int = 1024
    RERANK_MODEL: str = "qwen3-rerank"
    GENERATION_MODEL: str = "qwen3.7-plus-2026-05-26"
    ELASTICSEARCH_URL: str = "http://localhost:9200"
    CVRAG_DATABASE_URL: str = "sqlite:///./data/runtime/cvrag.db"
    CVRAG_MODEL_DIR: Path = Path("models/deepdoc")
    CVRAG_PROVIDER: str = Field(default="dashscope", pattern="^(dashscope|offline)$")
    CVRAG_INDEX_NAME: str = "cvrag-chunks-v1"
    CVRAG_EVALUATION_REPORT: Path = Path("artifacts/evaluation/latest.json")
    CVRAG_REFUSAL_THRESHOLD: float = Field(default=0.45, ge=0.0, le=1.0)
    CVRAG_REQUIRE_EVALUATION_LOCK: bool = False
    CVRAG_ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:8080"

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.CVRAG_ALLOWED_ORIGINS.split(",") if value.strip()]

    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.CVRAG_DATABASE_URL.startswith(prefix):
            raise ValueError("CVRAG_DATABASE_URL must use sqlite:///")
        return Path(self.CVRAG_DATABASE_URL.removeprefix(prefix)).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
