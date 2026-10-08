"""Shared lightweight Chinese embedding function for Chroma collections."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from backend.config import settings

# HF_ENDPOINT 必须在 fastembed（进而 huggingface_hub）被导入之前写进环境变量：
# huggingface_hub 在 import 那一刻就把 HF_ENDPOINT 读成了模块常量，之后再设无效。
# 这个镜像决定模型能否下载成功 —— 官方两个源（huggingface.co 与
# storage.googleapis.com）在国内网络下都不可达，实测建索引会直接失败：
#   Could not load model BAAI/bge-small-zh-v1.5 from any source.
os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
# 镜像的元数据请求偶发很慢（实测出现过 30 秒以上无响应），默认 10 秒的超时会误判成
# 下载失败。这两个值同样只在 import 时读取一次，所以必须和 HF_ENDPOINT 放在一起。
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "60")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")

from fastembed import TextEmbedding  # noqa: E402


class EmbeddingModelError(RuntimeError):
    """Raised when the local embedding model cannot be loaded or executed."""


# 直接用 fastembed 内置条目，不再重定向到自定义别名。
#
# 内置条目同时带 hf 与 url 两个源，fastembed 的取源顺序是「先 HuggingFace，失败再退
# GCS」；而自定义别名（easy-teach/bge-small-zh-v1.5，只有 GCS 一个源）会把
# HuggingFace 这一路整条跳过 —— 于是可用的镜像通道被自己关掉了。
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


@lru_cache(maxsize=4)
def _get_embedding_model(model_name: str, cache_dir: str, threads: int) -> TextEmbedding:
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        return TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            threads=threads,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:
        raise EmbeddingModelError(
            f"向量模型加载失败：无法取得 {model_name}。请确认镜像 "
            f"HF_ENDPOINT={settings.hf_endpoint} 可达（无法访问 huggingface.co 与 "
            "storage.googleapis.com 时才需要镜像），并检查磁盘空间与缓存目录后重试"
        ) from exc


class FastEmbedEmbeddingFunction:
    """Chroma-compatible adapter backed by FastEmbed's quantized ONNX model."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        cache_dir: str | Path | None = None,
        threads: int | None = None,
    ) -> None:
        self.model_name = model_name or settings.embedding_model
        self.cache_dir = str(Path(cache_dir or settings.embedding_cache_dir).resolve())
        self.threads = threads or settings.embedding_threads

    def __call__(self, input: Sequence[str]) -> list[list[float]]:
        texts = [str(text) for text in input]
        if not texts:
            return []
        try:
            model = _get_embedding_model(self.model_name, self.cache_dir, self.threads)
            return [vector.tolist() for vector in model.embed(texts, batch_size=64)]
        except EmbeddingModelError:
            raise
        except Exception as exc:
            raise EmbeddingModelError("文本向量化失败，请稍后重试") from exc

    def name(self) -> str:
        """Chroma 1.x 调用 `name()` 来判断集合配置里的向量函数是否冲突。

        旧版 Chroma 没有这个要求，所以本项目钉在 0.5.3 时缺失也能跑；升级到 1.x 后
        缺少它会直接 AttributeError。
        """
        return "fastembed-bge-small-zh-v1.5"

    def embed_query(self, input: Sequence[str]) -> list[list[float]]:
        """Chroma 1.x 的查询路径调用 `embed_query`。

        协议基类把这个方法默认转发给 `__call__`，但本类没有继承它，所以这里显式转发，
        与 Chroma 的约定保持一致。
        """
        return self.__call__(input)


def reset_embedding_model_cache() -> None:
    """Release cached ONNX sessions, primarily for tests and controlled reloads."""
    _get_embedding_model.cache_clear()
