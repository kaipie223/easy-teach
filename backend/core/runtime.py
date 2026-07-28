from pathlib import Path

from config import settings


def ensure_runtime_directories() -> None:
    paths: list[Path] = [
        settings.data_dir,
        settings.upload_dir,
        settings.generated_dir,
        settings.chroma_persist_dir,
    ]

    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
