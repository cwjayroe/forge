"""
Memory client for Forge.

Uses memory_core (mem0 + ChromaDB) when available; falls back to a local
JSON stub so the backend works without the optional dependency.
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_data_dir = Path(os.environ.get('FORGE_DATA_DIR', '.'))

# ---------------------------------------------------------------------------
# Optional memory_core import
# ---------------------------------------------------------------------------

try:
    from memory_core import MemoryClient as _CoreClient, MemoryConfig
    HAS_MEMORY_CORE = True
except ImportError:
    HAS_MEMORY_CORE = False
    logger.warning(
        "memory_core not installed — using JSON stub for memory. "
        "Install with: pip install memory-core"
    )


# ---------------------------------------------------------------------------
# JSON stub (private — used as fallback)
# ---------------------------------------------------------------------------

class _StubMemoryClient:
    def __init__(self, path: Path):
        self._path = path
        self._memories: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._memories, indent=2, default=str))

    def search(self, query: str) -> list[dict]:
        q = query.lower()
        return [m for m in self._memories if q in m.get("content", "").lower()]

    def store(self, content: str, metadata: Optional[dict] = None) -> str:
        entry = {
            "id": str(uuid.uuid4()),
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        self._memories.append(entry)
        self._save()
        return entry["id"]

    def list_all(self) -> list[dict]:
        return list(self._memories)

    def delete(self, memory_id: str) -> bool:
        before = len(self._memories)
        self._memories = [m for m in self._memories if m["id"] != memory_id]
        if len(self._memories) < before:
            self._save()
            return True
        return False


# ---------------------------------------------------------------------------
# Public async MemoryClient
# ---------------------------------------------------------------------------

def _to_dict(obj) -> dict:
    """Normalise a memory_core dataclass/object to a plain dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, '__dict__'):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
    return {"content": str(obj)}


class MemoryClient:
    def __init__(
        self,
        ollama_host: str = 'http://localhost:11434',
        memory_model: str = 'llama3.2',
    ):
        if HAS_MEMORY_CORE:
            self._core = _CoreClient(config=MemoryConfig(
                chroma_path=str(_data_dir / 'chroma'),
                ollama_host=ollama_host,
                ollama_model=memory_model,
                default_agent_id='forge',
            ))
            self._stub = None
        else:
            self._core = None
            self._stub = _StubMemoryClient(_data_dir / 'forge_memory.json')

    async def search(self, query: str) -> list[dict]:
        if self._core is not None:
            results = await asyncio.to_thread(self._core.search, query)
            return [_to_dict(r) for r in results]
        return self._stub.search(query)  # type: ignore[union-attr]

    async def store(self, content: str, metadata: Optional[dict] = None) -> str:
        if self._core is not None:
            entry = await asyncio.to_thread(self._core.store, content, metadata or {})
            d = _to_dict(entry)
            return str(d.get("id", ""))
        return self._stub.store(content, metadata)  # type: ignore[union-attr]

    async def list_all(self) -> list[dict]:
        if self._core is not None:
            results = await asyncio.to_thread(self._core.list)
            return [_to_dict(r) for r in results]
        return self._stub.list_all()  # type: ignore[union-attr]

    async def delete(self, memory_id: str) -> bool:
        if self._core is not None:
            try:
                await asyncio.to_thread(self._core.delete, memory_id)
                return True
            except Exception:
                return False
        return self._stub.delete(memory_id)  # type: ignore[union-attr]
