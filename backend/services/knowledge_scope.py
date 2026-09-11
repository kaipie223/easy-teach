"""Teacher-specific storage and vector-index names for private knowledge bases."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from backend.config import settings


def _safe_owner_id(owner_id: str) -> str:
    safe_owner_id = re.sub(r"[^a-zA-Z0-9_-]", "_", owner_id).strip("_-")
    if not safe_owner_id:
        raise ValueError("owner_id cannot produce an empty knowledge namespace")
    return safe_owner_id


def knowledge_collection_name(owner_id: str) -> str:
    """Return the legacy stable collection name for a teacher."""
    return f"knowledge_user_{_safe_owner_id(owner_id)}"[:63].rstrip("_-")


def staged_knowledge_collection_name(owner_id: str) -> str:
    """Return a unique collection name used during an atomic rebuild."""
    owner = _safe_owner_id(owner_id)[:30]
    return f"knowledge_user_{owner}_{uuid.uuid4().hex[:12]}"


def _active_pointer_path(owner_id: str) -> Path:
    return settings.chroma_persist_dir / ".active" / f"{_safe_owner_id(owner_id)[:48]}.txt"


def active_knowledge_collection_name(owner_id: str) -> str:
    """Resolve the currently active collection, falling back to the legacy name."""
    pointer = _active_pointer_path(owner_id)
    try:
        collection_name = pointer.read_text(encoding="ascii").strip()
    except OSError:
        return knowledge_collection_name(owner_id)
    if not collection_name.startswith("knowledge_user_") or len(collection_name) > 63:
        return knowledge_collection_name(owner_id)
    return collection_name


def activate_knowledge_collection(owner_id: str, collection_name: str) -> None:
    """Atomically switch future readers to a fully built collection."""
    if not collection_name.startswith("knowledge_user_") or len(collection_name) > 63:
        raise ValueError("invalid knowledge collection name")
    pointer = _active_pointer_path(owner_id)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(collection_name, encoding="ascii")
    temporary.replace(pointer)


def knowledge_storage_dir(owner_id: str) -> Path:
    """Return the private managed-document directory for one teacher."""
    return settings.data_dir / "knowledge" / owner_id
