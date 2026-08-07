"""Application settings loaded from environment variables."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "easy-teach"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    allowed_origins: list[str] = ["*"]

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # Database
    database_url: str = "sqlite:///./data/easy_teach.db"

    # Runtime storage
    data_dir: Path = Path("./data")
    upload_dir: Path = Path("./uploads")
    output_dir: Path = Path("./output")
    max_upload_size_mb: int = 50

    # ChromaDB
    chroma_persist_dir: Path = Path("./data/chroma")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


settings = Settings()
