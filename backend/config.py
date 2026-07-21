"""应用配置 — 从环境变量加载"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "easy-teach"
    debug: bool = True

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # Database
    database_url: str = "sqlite:///./data/easy_teach.db"

    # Upload
    upload_dir: str = "./uploads"
    max_upload_size_mb: int = 50

    # ChromaDB
    chroma_persist_dir: str = "./data/chroma"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
