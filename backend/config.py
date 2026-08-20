"""Application settings loaded from environment variables.

Relative paths are resolved from the repository root instead of the current
working directory so ``uv run`` and direct module execution use the same
runtime locations.
"""

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def normalize_chroma_path(path: str | Path) -> Path:
    """Return a Chroma path that hnswlib can persist on Windows.

    The Windows build of hnswlib used by Chroma 0.5.x cannot create its
    binary index files below a path containing non-ASCII characters. The
    repository path may contain Chinese characters, so use an ASCII runtime
    directory in that case while keeping configured paths unchanged elsewhere.
    """
    resolved = Path(path).expanduser().resolve()
    if os.name != "nt" or str(resolved).isascii():
        return resolved

    system_drive = os.environ.get("SystemDrive", "C:")
    return (Path(system_drive) / "easy-teach-runtime" / "chroma").resolve()


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

    # Authentication
    jwt_secret_key: str = "easy-teach-development-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Database
    database_url: str = "sqlite:///./data/easy_teach.db"
    redis_url: str = "redis://localhost:6379/0"

    # Long-running task execution
    task_queue_enabled: bool = True
    task_queue_eager: bool = False
    task_queue_name: str = "easy_teach"
    task_max_retries: int = 2
    task_time_limit_seconds: int = 900
    task_soft_time_limit_seconds: int = 840
    task_stale_after_seconds: int = 1800
    task_retry_backoff_seconds: int = 15

    # Runtime storage
    data_dir: Path = PROJECT_ROOT / "data"
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    output_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "generated",
        validation_alias=AliasChoices("OUTPUT_DIR", "GENERATED_DIR"),
    )
    max_upload_size_mb: int = 50

    # ChromaDB
    chroma_persist_dir: Path = PROJECT_ROOT / "data" / "chroma"
    knowledge_base_dir: Path = PROJECT_ROOT / "knowledge-base"

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

    @model_validator(mode="after")
    def resolve_runtime_paths(self):
        for field_name in (
            "data_dir",
            "upload_dir",
            "output_dir",
            "knowledge_base_dir",
        ):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, (PROJECT_ROOT / path).resolve())

        self.chroma_persist_dir = normalize_chroma_path(self.chroma_persist_dir)

        if self.database_url.startswith("sqlite:///"):
            raw_path = self.database_url.removeprefix("sqlite:///")
            db_path = Path(raw_path)
            if not db_path.is_absolute():
                db_path = (PROJECT_ROOT / db_path).resolve()
                self.database_url = f"sqlite:///{db_path.as_posix()}"
        return self


settings = Settings()
