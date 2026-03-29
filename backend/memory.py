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

from memory_core import MemoryClient as _CoreClient, MemoryConfig

# ---------------------------------------------------------------------------
# Public async MemoryClient
# ---------------------------------------------------------------------------

class MemoryClient:
    def __init__(
        self,
        ollama_host: str = 'http://localhost:11434',
        memory_model: str = 'llama3.2',
        agent_id: str = 'forge',
    ):
        self._ollama_host = ollama_host
        self._memory_model = memory_model
        # Use MemoryConfig default chroma_path (~/.project-memory) so we share
        # the same storage as the MCP memory server.
        self._config = MemoryConfig(
            ollama_host=ollama_host,
            ollama_model=memory_model,
        )
        self._chroma_path = self._config.chroma_path
        self._core = _CoreClient(agent_id=agent_id, config=self._config)
        self._clients: dict[str, _CoreClient] = {agent_id: self._core}

    def _get_core(self, project_id: Optional[str] = None) -> _CoreClient:
        """Get or create a core client for the given project_id."""
        if not project_id:
            return self._core
        if project_id not in self._clients:
            self._clients[project_id] = _CoreClient(
                agent_id=project_id,
                config=MemoryConfig(
                    chroma_path=self._chroma_path,
                    ollama_host=self._ollama_host,
                    ollama_model=self._memory_model,
                ),
            )
        return self._clients[project_id]

    def list_projects(self) -> list[str]:
        """Scan chroma directory for available project-ids."""
        chroma_dir = Path(self._chroma_path)
        if not chroma_dir.exists():
            return []
        return sorted(
            d.name for d in chroma_dir.iterdir()
            if d.is_dir() and not d.name.startswith('.')
        )

    async def search(self, query: str, project_id: Optional[str] = None) -> list[dict]:
        core = self._get_core(project_id)
        results = await asyncio.to_thread(core.search, query)
        return [
            {"id": r.id, "content": r.content, "score": r.score, "metadata": r.metadata}
            for r in results
        ]

    async def get_by_key(self, upsert_key: str) -> Optional[dict]:
        """Retrieve a memory entry by its deterministic upsert key."""
        entries = await asyncio.to_thread(self._core.list, {"upsert_key": upsert_key})
        if entries:
            e = entries[0]
            return {"id": e.id, "content": e.content, "metadata": e.metadata}
        return None

    async def store(self, content: str, metadata: Optional[dict] = None) -> str:
        entry = await asyncio.to_thread(self._core.store, content, metadata or {})
        return entry.id

    async def list_all(self, project_id: Optional[str] = None) -> list[dict]:
        core = self._get_core(project_id)
        entries = await asyncio.to_thread(core.list)
        return [
            {"id": e.id, "content": e.content, "metadata": e.metadata,
             "created_at": e.created_at.isoformat() if getattr(e, 'created_at', None) else None}
            for e in entries
        ]

    async def delete(self, memory_id: str, project_id: Optional[str] = None) -> bool:
        core = self._get_core(project_id)
        try:
            await asyncio.to_thread(core.delete, memory_id)
            return True
        except Exception:
            return False
