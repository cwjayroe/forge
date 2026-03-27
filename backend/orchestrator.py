"""
Orchestrator: manages running agent tasks and WebSocket event broadcasting.
"""
import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from .agent.adapters.ollama import OllamaAdapter
from .agent.loop import Agent, AgentAbortedError
from .memory import MemoryClient
from .models import Run, RunEvent, Task

if TYPE_CHECKING:
    from fastapi import WebSocket

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

active_runs: dict[str, asyncio.Task] = {}
active_agents: dict[str, Agent] = {}

# Per run_id: list of asyncio.Queue for WebSocket listeners
_ws_queues: dict[str, list[asyncio.Queue]] = {}


# ---------------------------------------------------------------------------
# WebSocket broadcasting
# ---------------------------------------------------------------------------

def register_ws_listener(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _ws_queues.setdefault(run_id, []).append(q)
    return q


def deregister_ws_listener(run_id: str, q: asyncio.Queue) -> None:
    listeners = _ws_queues.get(run_id, [])
    try:
        listeners.remove(q)
    except ValueError:
        pass
    if not listeners:
        _ws_queues.pop(run_id, None)


async def _broadcast(run_id: str, event: dict) -> None:
    for q in list(_ws_queues.get(run_id, [])):
        await q.put(event)


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

def _make_adapter(task: Task) -> OllamaAdapter:
    """
    Build the appropriate model adapter from a task's model string.
    Format: "provider/model-name"
    """
    model_str = task.model or "ollama/qwen2.5-coder:32b"
    if "/" in model_str:
        provider, model_name = model_str.split("/", 1)
    else:
        provider, model_name = "ollama", model_str

    if provider == "anthropic":
        from .agent.adapters.anthropic import AnthropicAdapter
        return AnthropicAdapter(model=model_name)  # type: ignore[return-value]

    # Default: Ollama
    return OllamaAdapter(model=model_name)


async def _run_agent(run_id: str, task: Task, engine) -> None:
    """Background coroutine that runs the agent and persists events."""
    from sqlmodel import Session as _Session

    memory = MemoryClient()

    spec_content = None
    if task.spec_path:
        from pathlib import Path
        spec_file = Path(task.spec_path)
        if spec_file.exists():
            try:
                spec_content = spec_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    adapter = _make_adapter(task)
    agent = Agent(model=adapter, workspace=task.workspace, memory=memory)
    active_agents[run_id] = agent

    async def on_event(event: dict) -> None:
        # Persist to DB
        with _Session(engine) as session:
            run_event = RunEvent(
                run_id=run_id,
                type=event.get("type", "text"),
                content=json.dumps(event),
            )
            session.add(run_event)
            session.commit()
        # Broadcast to WebSocket listeners
        await _broadcast(run_id, event)

    summary = ""
    error_msg = None

    try:
        summary = await agent.run(
            task_description=task.description,
            spec_content=spec_content,
            on_event=on_event,
        )
        final_status = "review" if task.mode == "supervised" else "completed"
    except AgentAbortedError:
        final_status = "aborted"
        error_msg = "Run was aborted"
    except ConnectionError as e:
        final_status = "failed"
        error_msg = str(e)
        await on_event({"type": "error", "content": error_msg})
    except Exception as e:
        final_status = "failed"
        error_msg = str(e)
        await on_event({"type": "error", "content": error_msg})

    # Update Run record
    with _Session(engine) as session:
        run = session.get(Run, run_id)
        if run:
            run.status = final_status
            run.completed_at = datetime.utcnow()
            run.summary = summary or None
            run.error = error_msg
            session.add(run)

        # Update Task status
        db_task = session.get(Task, task.id)
        if db_task:
            if final_status == "completed":
                db_task.status = "done"
            elif final_status == "review":
                db_task.status = "review"
            elif final_status in ("failed", "aborted"):
                db_task.status = "failed"
            db_task.updated_at = datetime.utcnow()
            session.add(db_task)

        session.commit()

    # Persist run summary to memory (best-effort)
    if summary and final_status in ("completed", "review"):
        try:
            await memory.store(
                f"Task: {task.title}\n\n{summary}",
                {
                    "source": "run_completion",
                    "run_id": run_id,
                    "task_id": task.id,
                    "task_title": task.title,
                },
            )
        except Exception:
            pass  # memory failure must not affect run status

    # Signal WebSocket listeners that the stream is done
    await _broadcast(run_id, {"type": "done", "status": final_status})

    # Cleanup
    active_agents.pop(run_id, None)
    active_runs.pop(run_id, None)


async def start_run(run_id: str, task: Task, engine) -> None:
    """Kick off the agent as a background asyncio task."""
    coro = _run_agent(run_id, task, engine)
    task_handle = asyncio.create_task(coro)
    active_runs[run_id] = task_handle


async def abort_run(run_id: str) -> bool:
    """Abort a running agent. Returns True if it was running."""
    agent = active_agents.get(run_id)
    if agent:
        agent.abort()

    task_handle = active_runs.get(run_id)
    if task_handle and not task_handle.done():
        task_handle.cancel()
        return True
    return False
