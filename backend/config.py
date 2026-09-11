"""Application settings loaded from environment variables.

Relative paths are resolved from the repository root instead of the current
working directory so ``uv run`` and direct module execution use the same
runtime locations.
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

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
    deepseek_api_key_file: Path | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-flash"

    # Authentication
    jwt_secret_key: str = "easy-teach-development-secret-change-me"
    jwt_secret_key_file: Path | None = None
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Database
    database_url: str = "sqlite:///./data/easy_teach.db"
    database_password_file: Path | None = None
    database_host: str = "postgres"
    database_port: int = 5432
    database_name: str = "easy_teach"
    database_user: str = "easy_teach"
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

    # User safety limits
    rate_limit_requests_per_minute: int = 300
    rate_limit_auth_requests_per_minute: int = 60
    daily_model_request_limit: int = 200
    max_concurrent_tasks_per_user: int = 8
    storage_quota_mb_per_user: int = 1024

    # Runtime storage
    data_dir: Path = PROJECT_ROOT / "data"
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    output_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "generated",
        validation_alias=AliasChoices("OUTPUT_DIR", "GENERATED_DIR"),
    )
    max_upload_size_mb: int = 50
    speech_max_upload_size_mb: int = 25
    upload_chunk_size_kb: int = 1024
    video_parser_output_dir: Path = PROJECT_ROOT / "data" / "video-parser"
    video_parser_enabled: bool = False
    video_parser_type: str = "auto"
    video_parser_transcribe: bool = False
    video_parser_ocr: bool = False
    video_parser_vision: bool = False
    video_parser_understanding: bool = False
    video_parser_max_keyframes: int = 12
    video_parser_max_shots: int = 36
    video_parser_output_width: int = 960

    # ChromaDB
    chroma_persist_dir: Path = PROJECT_ROOT / "data" / "chroma"
    chroma_anonymized_telemetry: bool = Field(
        default=False,
        validation_alias=AliasChoices("ANONYMIZED_TELEMETRY", "CHROMA_ANONYMIZED_TELEMETRY"),
    )
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_cache_dir: Path = PROJECT_ROOT / "data" / "embedding-models"
    embedding_threads: int = Field(default=2, ge=1, le=8)

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
        if not self.data_dir.is_absolute():
            self.data_dir = (PROJECT_ROOT / self.data_dir).resolve()
        if "embedding_cache_dir" not in self.model_fields_set:
            self.embedding_cache_dir = self.data_dir / "embedding-models"

        for field_name in (
            "upload_dir",
            "output_dir",
            "video_parser_output_dir",
            "embedding_cache_dir",
        ):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, (PROJECT_ROOT / path).resolve())

        self.chroma_persist_dir = normalize_chroma_path(self.chroma_persist_dir)

        for field_name, target_name in (
            ("deepseek_api_key_file", "deepseek_api_key"),
            ("jwt_secret_key_file", "jwt_secret_key"),
        ):
            secret_path = getattr(self, field_name)
            if secret_path is None:
                continue
            if not secret_path.is_absolute():
                secret_path = (PROJECT_ROOT / secret_path).resolve()
                setattr(self, field_name, secret_path)
            try:
                secret_value = secret_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(f"Cannot read {field_name.upper()}") from exc
            if not secret_value:
                raise ValueError(f"{field_name.upper()} is empty")
            setattr(self, target_name, secret_value)

        if self.database_password_file is not None:
            password_path = self.database_password_file
            if not password_path.is_absolute():
                password_path = (PROJECT_ROOT / password_path).resolve()
                self.database_password_file = password_path
            try:
                database_password = password_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError("Cannot read DATABASE_PASSWORD_FILE") from exc
            if not database_password:
                raise ValueError("DATABASE_PASSWORD_FILE is empty")
            self.database_url = (
                f"postgresql+psycopg://{quote_plus(self.database_user)}:"
                f"{quote_plus(database_password)}@{self.database_host}:"
                f"{self.database_port}/{self.database_name}"
            )

        if self.database_url.startswith("sqlite:///"):
            raw_path = self.database_url.removeprefix("sqlite:///")
            db_path = Path(raw_path)
            if not db_path.is_absolute():
                db_path = (PROJECT_ROOT / db_path).resolve()
                self.database_url = f"sqlite:///{db_path.as_posix()}"

        if self.environment.lower() in {"production", "prod"}:
            errors: list[str] = []
            if self.debug:
                errors.append("DEBUG must be false")
            if self.allowed_origins == ["*"] or "*" in self.allowed_origins:
                errors.append("ALLOWED_ORIGINS cannot contain '*'")
            weak_jwt_values = {
                "easy-teach-development-secret-change-me",
                "change-this-development-secret",
            }
            if len(self.jwt_secret_key) < 32 or self.jwt_secret_key in weak_jwt_values:
                errors.append("JWT_SECRET_KEY must be a strong secret of at least 32 characters")
            if not self.deepseek_api_key or self.deepseek_api_key == "your_api_key_here":
                errors.append("DEEPSEEK_API_KEY must be configured")
            if self.database_url.startswith("sqlite"):
                errors.append("DATABASE_URL must use the production database")
            if not self.task_queue_enabled:
                errors.append("TASK_QUEUE_ENABLED must be true")
            if self.task_queue_eager:
                errors.append("TASK_QUEUE_EAGER must be false")
            for field_name in (
                "upload_dir",
                "output_dir",
                "chroma_persist_dir",
                "embedding_cache_dir",
            ):
                try:
                    getattr(self, field_name).resolve().relative_to(self.data_dir.resolve())
                except ValueError:
                    errors.append(f"{field_name.upper()} must be inside DATA_DIR")
            if errors:
                raise ValueError("Unsafe production configuration: " + "; ".join(errors))
        return self


settings = Settings()
