"""
Memory client for Forge.

Wraps memory_core (MemoryManager + ServerConfig) so the rest of the backend
can call simple async methods without knowing the request-dataclass details.
"""
import asyncio
import dataclasses
import logging
from pathlib import Path
from typing import Optional
from memory_core.memory_manager import MemoryManager
from memory_core.server_config import ServerConfig
from memory_core.scoring import ScoringEngine, RerankerManager
from memory_core.dataclasses.memory_types import (
    DeleteMemoryRequest,
    ListMemoriesRequest,
    SearchContextRequest,
    StoreMemoryRequest,
)
from memory_core.database_scope_registry import DatabaseScopeRegistry


logger = logging.getLogger(__name__)

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
        self._default_project_id = agent_id
        base_config = ServerConfig.from_env()
        self._config = dataclasses.replace(
            base_config,
            ollama_base_url=ollama_host,
            ollama_model=memory_model,
            default_project_id=agent_id,
        )
        self._scoring_engine = ScoringEngine(
            reranker=RerankerManager(self._config.reranker_model_name),
        )
        self._manager = MemoryManager(
            config=self._config,
            scoring_engine=self._scoring_engine,
            logger=logger,
            default_project_id=agent_id,
            get_all_limit=self._config.get_all_limit,
        )
        self._registry = DatabaseScopeRegistry(dsn=self._config.pg_dsn)

    def list_projects(self) -> list[str]:
        projects = self._registry.list_projects()
        return [p.display_name for p in projects]

    async def search(self, query: str, project_id: Optional[str] = None) -> list[dict]:
        pid = project_id or self._default_project_id
        request = SearchContextRequest(
            query=query,
            project_id=pid,
            project_ids=[pid],
            repo=None,
            path_prefix=None,
            tags=[],
            categories=[],
            limit=8,
            ranking_mode=self._config.default_ranking_mode,
            token_budget=self._config.default_token_budget,
            candidate_pool=self._config.max_candidate_pool,
            rerank_top_n=self._config.default_rerank_top_n,
            debug=False,
            response_format="json",
            include_full_text=True,
            excerpt_chars=500,
        )
        results, _ = await self._manager.search(request)
        return results

    async def get_by_key(self, upsert_key: str) -> Optional[dict]:
        """Retrieve a memory entry by its deterministic upsert key."""
        request = ListMemoriesRequest(
            project_id=self._default_project_id,
            repo=None,
            category=None,
            tag=None,
            path_prefix=None,
            offset=0,
            limit=self._config.get_all_limit,
            response_format="json",
            include_full_text=True,
            excerpt_chars=2000,
        )
        page, _, _ = await asyncio.to_thread(self._manager.list_memories, request)
        for item in page:
            if item.metadata.upsert_key == upsert_key:
                return {"id": item.id, "content": item.memory, "metadata": item.metadata.as_dict()}
        return None

    async def store(self, content: str, metadata: Optional[dict] = None) -> str:
        meta = metadata or {}
        request = StoreMemoryRequest(
            project_id=meta.get("project_id") or self._default_project_id,
            content=content,
            repo=meta.get("repo"),
            source_path=meta.get("source_path"),
            source_kind=meta.get("source_kind") or "summary",
            category=meta.get("category") or "general",
            module=meta.get("module"),
            tags=meta.get("tags") or [],
            upsert_key=meta.get("upsert_key"),
            fingerprint=meta.get("fingerprint"),
            priority=meta.get("priority") or "normal",
        )
        _, new_ids = await asyncio.to_thread(self._manager.store_memory, request)
        return new_ids[0] if new_ids else ""

    async def list_all(self, project_id: Optional[str] = None) -> list[dict]:
        pid = project_id or self._default_project_id
        request = ListMemoriesRequest(
            project_id=pid,
            repo=None,
            category=None,
            tag=None,
            path_prefix=None,
            offset=0,
            limit=self._config.get_all_limit,
            response_format="json",
            include_full_text=True,
            excerpt_chars=2000,
        )
        page, _, _ = await asyncio.to_thread(self._manager.list_memories, request)
        return [
            {
                "id": e.id,
                "content": e.memory,
                "metadata": e.metadata.as_dict(),
                "created_at": e.metadata.updated_at,
            }
            for e in page
        ]

    async def get_stats(self, project_id: Optional[str] = None) -> dict:
        pid = project_id or self._default_project_id
        return await asyncio.to_thread(self._manager.get_stats, pid)

    async def delete(self, memory_id: str, project_id: Optional[str] = None) -> bool:
        pid = project_id or self._default_project_id
        request = DeleteMemoryRequest(
            project_id=pid,
            memory_id=memory_id,
            upsert_key=None,
        )
        try:
            _, count = await asyncio.to_thread(self._manager.delete_memory, request)
            return count > 0
        except Exception:
            return False
