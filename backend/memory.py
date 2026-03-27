"""
Stub MCP memory client backed by a local JSON file.
Supports search (substring), store, list, and delete.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

_data_dir = Path(os.environ.get('FORGE_DATA_DIR', '.'))
MEMORY_PATH = _data_dir / 'forge_memory.json'


class MemoryClient:
    def __init__(self, path: Path = MEMORY_PATH):
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

    async def search(self, query: str) -> list[dict]:
        q = query.lower()
        return [m for m in self._memories if q in m.get("content", "").lower()]

    async def store(self, content: str, metadata: Optional[dict] = None) -> str:
        entry = {
            "id": str(uuid.uuid4()),
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        self._memories.append(entry)
        self._save()
        return entry["id"]

    async def list_all(self) -> list[dict]:
        return list(self._memories)

    async def delete(self, memory_id: str) -> bool:
        before = len(self._memories)
        self._memories = [m for m in self._memories if m["id"] != memory_id]
        if len(self._memories) < before:
            self._save()
            return True
        return False
