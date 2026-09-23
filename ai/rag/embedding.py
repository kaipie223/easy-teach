"""Shared lightweight Chinese embedding function for Chroma collections."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Sequence

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

from backend.config import settings


class EmbeddingModelError(RuntimeError):
    """Raised when the local embedding model cannot be loaded or executed."""


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DIRECT_EMBEDDING_MODEL = "easy-teach/bge-small-zh-v1.5"
DIRECT_EMBEDDING_URL = (
    "https://storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz"
)


def _register_direct_embedding_model() -> None:
    if any(
        item["model"].lower() == DIRECT_EMBEDDING_MODEL.lower()
        for item in TextEmbedding.list_supported_models()
    ):
        return
    TextEmbedding.add_custom_model(
        model=DIRECT_EMBEDDING_MODEL,
        pooling=PoolingType.CLS,
        normalization=True,
        sources=ModelSource(url=DIRECT_EMBEDDING_URL, _deprecated_tar_struct=True),
        dim=512,
        model_file="model_optimized.onnx",
        description="Chinese BGE small v1.5 via the Qdrant FastEmbed mirror",
        license="mit",
        size_in_gb=0.09,
    )


_register_direct_embedding_model()


@lru_cache(maxsize=4)
def _get_embedding_model(model_name: str, cache_dir: str, threads: int) -> TextEmbedding:
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        runtime_model_name = (
            DIRECT_EMBEDDING_MODEL
            if model_name == DEFAULT_EMBEDDING_MODEL
            else model_name
        )
        return TextEmbedding(
            model_name=runtime_model_name,
            cache_dir=cache_dir,
            threads=threads,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:
        raise EmbeddingModelError(
            "向量模型加载失败，请检查服务器网络、磁盘空间和模型缓存后重试"
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
