"""
Orchestrator: manages running agent tasks through plan → build → QA phases,
with dependency-aware scheduling and WebSocket event broadcasting.
"""
import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from .agent.adapters.ollama import OllamaAdapter
from .agent.loop import Agent, AgentAbortedError
from .agent.prompts import (
    BUILD_TOOLS,
    PLAN_TOOLS,
    QA_TOOLS,
    build_prompt,
    plan_prompt,
    qa_prompt,
)
from .memory import MemoryClient
from .models import Run, RunEvent, RunPhase, Task

if TYPE_CHECKING:
    from fastapi import WebSocket

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

active_runs: dict[str, asyncio.Task] = {}
active_agents: dict[str, Agent] = {}

# Per run_id: list of asyncio.Queue for WebSocket listeners
_ws_queues: dict[str, list[asyncio.Queue]] = {}

# Bash approval gates: run_id → (Event, result)
_bash_approvals: dict[str, asyncio.Event] = {}
_bash_results: dict[str, bool] = {}

# Pipeline pause flag
_pipeline_paused = False


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
# Model adapter factory
# ---------------------------------------------------------------------------

def _make_adapter(model_str: str):
    """
    Build the appropriate model adapter from a model string.
    Format: "provider/model-name"
    """
    model_str = model_str or "ollama/qwen2.5-coder:32b"
    if "/" in model_str:
        provider, model_name = model_str.split("/", 1)
    else:
        provider, model_name = "ollama", model_str

    if provider == "anthropic":
        from .agent.adapters.anthropic import AnthropicAdapter
        from .database import get_settings as _get_settings
        s = _get_settings()
        api_key = s.get("anthropic_api_key") or None
        return AnthropicAdapter(model=model_name, api_key=api_key)

    # Default: Ollama
    return OllamaAdapter(model=model_name)


# ---------------------------------------------------------------------------
# Phase execution
# ---------------------------------------------------------------------------

async def _run_single_phase(
    run_id: str,
    phase_name: str,
    attempt: int,
    model_str: str,
    workspace: str,
    system_prompt: str,
    user_message: str,
    allowed_tools: set[str],
    memory: MemoryClient,
    approval_gate,
    on_event,
    engine,
) -> tuple[str, str]:
    """
    Run a single phase of the pipeline.

    Returns:
        (status, artifact) — status is "completed" or "failed", artifact is the agent's output.
    """
    # Create RunPhase record
    phase_id = None
    with Session(engine) as session:
        run_phase = RunPhase(
            run_id=run_id,
            phase=phase_name,
            attempt=attempt,
            status="running",
        )
        session.add(run_phase)

        # Update Run.current_phase
        run = session.get(Run, run_id)
        if run:
            run.current_phase = phase_name
            session.add(run)

        session.commit()
        session.refresh(run_phase)
        phase_id = run_phase.id

    await on_event({
        "type": "phase_start",
        "phase": phase_name,
        "attempt": attempt,
    })

    adapter = _make_adapter(model_str)

    # Only pass approval_gate for build phase (the only phase with run_bash)
    gate = approval_gate if phase_name == "build" else None

    agent = Agent(
        model=adapter,
        workspace=workspace,
        memory=memory,
        approval_gate=gate,
        allowed_tools=allowed_tools,
    )
    active_agents[run_id] = agent

    artifact = ""
    status = "completed"
    error_msg = None

    try:
        artifact = await agent.run(
            task_description=user_message,
            on_event=on_event,
            system_prompt=system_prompt,
        )
    except AgentAbortedError:
        status = "failed"
        error_msg = "Phase aborted"
    except ConnectionError as e:
        status = "failed"
        error_msg = str(e)
        await on_event({"type": "error", "content": error_msg})
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        await on_event({"type": "error", "content": error_msg})

    # Update RunPhase record
    with Session(engine) as session:
        rp = session.get(RunPhase, phase_id)
        if rp:
            rp.status = status
            rp.completed_at = datetime.utcnow()
            rp.artifact = artifact or None
            rp.error = error_msg
            session.add(rp)
            session.commit()

    await on_event({
        "type": "phase_end",
        "phase": phase_name,
        "attempt": attempt,
        "status": status,
    })

    return status, artifact


# ---------------------------------------------------------------------------
# Main task execution: plan → build → QA pipeline
# ---------------------------------------------------------------------------

async def _run_task_phases(run_id: str, task: Task, engine) -> None:
    """Background coroutine that runs a task through plan → build → QA phases."""

    memory = MemoryClient()

    # Load spec content
    spec_content = None
    if task.spec_path:
        from pathlib import Path
        spec_file = Path(task.spec_path)
        if spec_file.exists():
            try:
                spec_content = spec_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    # Load settings
    from .database import get_settings as _get_settings
    settings = _get_settings()
    require_approval = settings.get("require_bash_approval", False)

    # Build approval gate
    async def bash_gate(command: str) -> bool:
        event = asyncio.Event()
        _bash_approvals[run_id] = event
        _bash_results[run_id] = False
        await on_event({"type": "bash_approval_request", "command": command})
        try:
            await asyncio.wait_for(event.wait(), timeout=300.0)
            return _bash_results.get(run_id, False)
        except asyncio.TimeoutError:
            return False
        finally:
            _bash_approvals.pop(run_id, None)
            _bash_results.pop(run_id, None)

    # Event callback: persist + broadcast
    async def on_event(event: dict) -> None:
        with Session(engine) as session:
            run_event = RunEvent(
                run_id=run_id,
                type=event.get("type", "text"),
                content=json.dumps(event),
            )
            session.add(run_event)
            session.commit()
        await _broadcast(run_id, event)

    # Gather memory context
    memory_results = await memory.search(task.description[:100])
    memory_context = ""
    if memory_results:
        memory_context = json.dumps(memory_results, indent=2, default=str)

    # Gather upstream context from completed dependencies
    upstream_context = ""
    if task.depends_on:
        dep_ids = [d.strip() for d in task.depends_on.split(",") if d.strip()]
        if dep_ids:
            upstream_parts = []
            with Session(engine) as session:
                for dep_id in dep_ids:
                    dep_task = session.get(Task, dep_id)
                    if not dep_task:
                        continue
                    # Get the latest completed run's plan artifact
                    dep_runs = session.exec(
                        select(Run)
                        .where(Run.task_id == dep_id, Run.status == "completed")
                        .order_by(Run.started_at.desc())
                    ).all()
                    if dep_runs:
                        latest_run = dep_runs[0]
                        # Get plan phase artifact
                        plan_phases = session.exec(
                            select(RunPhase)
                            .where(RunPhase.run_id == latest_run.id, RunPhase.phase == "plan", RunPhase.status == "completed")
                        ).all()
                        plan_summary = plan_phases[-1].artifact if plan_phases else None
                        upstream_parts.append(
                            f"### Task: {dep_task.title}\n"
                            f"Summary: {latest_run.summary or 'N/A'}\n"
                            f"Plan: {plan_summary or 'N/A'}"
                        )
            if upstream_parts:
                upstream_context = "\n\n".join(upstream_parts)

    # Resolve models for each phase
    plan_model_str = task.plan_model or task.model
    build_model_str = task.model
    qa_model_str = task.qa_model or task.model

    error_msg = None
    final_status = "completed"
    summary = ""

    try:
        # ---- PHASE 1: PLAN ----
        with Session(engine) as session:
            db_task = session.get(Task, task.id)
            if db_task:
                db_task.status = "planning"
                db_task.updated_at = datetime.utcnow()
                session.add(db_task)
                session.commit()

        plan_sys = plan_prompt(
            workspace=task.workspace,
            spec_content=spec_content,
            memory_context=memory_context,
            upstream_context=upstream_context,
        )
        plan_status, plan_artifact = await _run_single_phase(
            run_id=run_id,
            phase_name="plan",
            attempt=1,
            model_str=plan_model_str,
            workspace=task.workspace,
            system_prompt=plan_sys,
            user_message=task.description,
            allowed_tools=PLAN_TOOLS,
            memory=memory,
            approval_gate=None,
            on_event=on_event,
            engine=engine,
        )

        if plan_status != "completed":
            raise RuntimeError(f"Planning phase failed: {plan_artifact}")

        # ---- PHASE 2+3: BUILD → QA LOOP ----
        qa_passed = False
        qa_feedback = None
        max_retries = task.max_retries or 3

        for attempt in range(1, max_retries + 1):
            # BUILD
            with Session(engine) as session:
                db_task = session.get(Task, task.id)
                if db_task:
                    db_task.status = "building"
                    db_task.updated_at = datetime.utcnow()
                    session.add(db_task)
                    session.commit()

            build_sys = build_prompt(
                workspace=task.workspace,
                plan_artifact=plan_artifact,
                spec_content=spec_content,
                memory_context=memory_context,
                upstream_context=upstream_context,
                qa_feedback=qa_feedback,
            )
            build_status, build_artifact = await _run_single_phase(
                run_id=run_id,
                phase_name="build",
                attempt=attempt,
                model_str=build_model_str,
                workspace=task.workspace,
                system_prompt=build_sys,
                user_message=task.description,
                allowed_tools=BUILD_TOOLS,
                memory=memory,
                approval_gate=bash_gate if require_approval else None,
                on_event=on_event,
                engine=engine,
            )

            if build_status != "completed":
                raise RuntimeError(f"Build phase failed (attempt {attempt}): {build_artifact}")

            # QA
            with Session(engine) as session:
                db_task = session.get(Task, task.id)
                if db_task:
                    db_task.status = "qa"
                    db_task.updated_at = datetime.utcnow()
                    session.add(db_task)
                    session.commit()

            qa_sys = qa_prompt(
                workspace=task.workspace,
                plan_artifact=plan_artifact,
                build_artifact=build_artifact,
                task_description=task.description,
                spec_content=spec_content,
                memory_context=memory_context,
            )
            qa_status, qa_artifact = await _run_single_phase(
                run_id=run_id,
                phase_name="qa",
                attempt=attempt,
                model_str=qa_model_str,
                workspace=task.workspace,
                system_prompt=qa_sys,
                user_message="Review the implementation and provide your verdict.",
                allowed_tools=QA_TOOLS,
                memory=memory,
                approval_gate=None,
                on_event=on_event,
                engine=engine,
            )

            if qa_status != "completed":
                raise RuntimeError(f"QA phase failed (attempt {attempt}): {qa_artifact}")

            # Check QA verdict
            if qa_artifact.strip().startswith("VERDICT: PASS"):
                qa_passed = True
                summary = build_artifact
                await on_event({
                    "type": "text",
                    "content": f"QA passed on attempt {attempt}.",
                })
                break
            else:
                qa_feedback = qa_artifact
                await on_event({
                    "type": "text",
                    "content": f"QA failed on attempt {attempt}/{max_retries}. "
                               + ("Retrying..." if attempt < max_retries else "Max retries exhausted."),
                })

        if not qa_passed:
            final_status = "failed"
            error_msg = f"QA failed after {max_retries} attempts"
            summary = qa_feedback or ""
        else:
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
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if run:
            run.status = final_status
            run.current_phase = None
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
            pass

    # Signal WebSocket listeners that the stream is done
    await _broadcast(run_id, {"type": "done", "status": final_status})

    # Cleanup
    active_agents.pop(run_id, None)
    active_runs.pop(run_id, None)

    # Trigger scheduler to check for ready dependent tasks
    if final_status in ("completed", "review"):
        try:
            from .scheduler import check_ready_tasks
            await check_ready_tasks(engine)
        except Exception:
            pass  # scheduler failure must not affect run status reporting


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def start_run(run_id: str, task: Task, engine) -> None:
    """Kick off the agent as a background asyncio task."""
    coro = _run_task_phases(run_id, task, engine)
    task_handle = asyncio.create_task(coro)
    active_runs[run_id] = task_handle


def resolve_bash_approval(run_id: str, approved: bool) -> bool:
    """Signal a pending bash approval gate. Returns False if no gate is waiting."""
    event = _bash_approvals.get(run_id)
    if not event:
        return False
    _bash_results[run_id] = approved
    event.set()
    return True


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


def set_pipeline_paused(paused: bool) -> None:
    global _pipeline_paused
    _pipeline_paused = paused


def is_pipeline_paused() -> bool:
    return _pipeline_paused
