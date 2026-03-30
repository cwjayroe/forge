"""
Orchestrator: manages running agent tasks through the full pipeline:

  plan → validate → (approval gate) → batched build + review → QA

Aligned with the feature-plan-and-build SKILL.md orchestration pattern:
- 2-pass planner with enriched task specs
- Plan validator for dependency/interface/completeness checks
- Batched parallel build execution with per-batch reviewer
- End-to-end QA with test baseline comparison and failure attribution
- Memory-based inter-agent communication via deterministic upsert keys
"""
import asyncio
import hashlib
import json
import re
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlmodel import Session, select

from .agent.adapters.ollama import OllamaAdapter
from .agent.loop import Agent, AgentAbortedError
from .agent.prompts import (
    BUILD_TOOLS,
    PLAN_TOOLS,
    QA_TOOLS,
    REVIEWER_TOOLS,
    VALIDATOR_TOOLS,
    build_prompt,
    plan_prompt,
    qa_prompt,
    reviewer_prompt,
    validator_prompt,
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

# Plan approval gates: run_id → (Event, result)
_plan_approvals: dict[str, asyncio.Event] = {}
_plan_results: dict[str, bool] = {}

# Pipeline pause flags
_pipeline_paused = False          # set by manual pause/resume
_pipeline_paused_by_window = False  # set by schedule window checker


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
# Helpers
# ---------------------------------------------------------------------------

def _generate_build_id(title: str) -> str:
    """Generate a deterministic build_id: slugified title + 6-char timestamp hash."""
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40]
    ts_hash = hashlib.sha256(datetime.utcnow().isoformat().encode()).hexdigest()[:6]
    return f"{slug}-{ts_hash}"


def _is_valid_plan(plan_artifact: str) -> bool:
    """Check if the plan artifact looks like a structured plan vs. garbage/empty."""
    if not plan_artifact or len(plan_artifact.strip()) < 50:
        return False
    # Must contain at least one indicator of a structured plan
    indicators = ["Phase", "task_type", "file_path", "create", "modify", "## ", "### ", "Step"]
    return any(ind.lower() in plan_artifact.lower() for ind in indicators)


def _verify_build_changed(workspace: str) -> bool:
    """Return True if the build modified at least one file in the workspace."""
    try:
        diff = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(diff.stdout.strip() or untracked.stdout.strip())
    except Exception:
        return True  # Can't verify — assume OK to avoid false failures


async def _create_task_branch(task_id: str, task_title: str, workspace: str) -> Optional[str]:
    """Create and checkout a git branch for this task. Returns branch name or None."""
    slug = re.sub(r"[^a-z0-9]+", "-", task_title.lower()).strip("-")[:40]
    branch_name = f"forge/task-{task_id[:8]}-{slug}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", branch_name,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0:
            return branch_name
        # Branch may already exist — try checking it out
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", branch_name,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0:
            return branch_name
    except Exception:
        pass
    return None


async def _commit_task_changes(task_title: str, workspace: str) -> None:
    """Stage and commit all changes made during a task run. Non-fatal if nothing to commit."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"forge: {task_title}",
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass


def _is_claude_code(model_str: str) -> bool:
    """Check if the model string uses the claude-code CLI provider."""
    return (model_str or "").startswith("claude-code/")


def _get_claude_code_model(model_str: str) -> str:
    """Extract model name from a claude-code/model string."""
    return model_str.split("/", 1)[1]


def _is_cursor(model_str: str) -> bool:
    """Check if the model string uses the cursor-code CLI provider."""
    return (model_str or "").startswith("cursor-code/")


def _get_cursor_model(model_str: str) -> str:
    """Extract model name from a cursor-code/model string."""
    return model_str.split("/", 1)[1]


def _make_adapter(model_str: str):
    """Build the appropriate model adapter from a model string (provider/model-name)."""
    model_str = model_str or "ollama/qwen2.5-coder:latest"
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


async def _capture_test_baseline(workspace: str) -> str:
    """Run the test suite and capture results as a baseline for QA regression attribution."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["python", "-m", "pytest", "-q", "--tb=no"],
            capture_output=True, text=True, timeout=120,
            cwd=workspace,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return f"Pre-build test baseline:\n{output}\nAll tests passed."
        else:
            return f"Pre-build test baseline:\n{output}\nSome tests failed (exit code {result.returncode})."
    except FileNotFoundError:
        return "No test suite found (pytest not available)."
    except subprocess.TimeoutExpired:
        return "Test suite timed out (120s limit)."
    except Exception as e:
        return f"Could not capture test baseline: {e}"


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
    batch: Optional[int] = None,
    task_index: Optional[int] = None,
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
            batch=batch,
            task_index=task_index,
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
        "batch": batch,
        "task_index": task_index,
    })

    adapter = _make_adapter(model_str)

    # Only pass approval_gate for build phase (the only phase with run_bash)
    gate = approval_gate if phase_name in ("build", "qa") else None

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
        "batch": batch,
        "task_index": task_index,
        "status": status,
    })

    return status, artifact


# ---------------------------------------------------------------------------
# Plan parsing helpers
# ---------------------------------------------------------------------------

def _parse_plan_phases(plan_artifact: str) -> list[dict]:
    """
    Parse the structured plan into phases and tasks.

    Returns a list of phase dicts:
    [
        {
            "name": "Phase 1: Foundation",
            "tasks": [
                {"file_path": "...", "description": "...", "task_type": "create", ...},
                ...
            ]
        },
        ...
    ]

    Falls back to a single phase with the entire plan if parsing fails.
    """
    phases = []
    # Try to find phase headers: ### Phase N: Name
    phase_pattern = re.compile(r'^###\s+Phase\s+(\d+):\s*(.+)', re.MULTILINE)
    matches = list(phase_pattern.finditer(plan_artifact))

    if not matches:
        # Fallback: treat entire plan as a single phase
        return [{"name": "Phase 1: Build", "tasks": [], "raw": plan_artifact}]

    for i, match in enumerate(matches):
        phase_num = int(match.group(1))
        phase_name = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plan_artifact)
        phase_content = plan_artifact[start:end].strip()

        # Try to parse YAML task specs from the phase content
        tasks = _extract_tasks_from_phase(phase_content)
        phases.append({
            "name": f"Phase {phase_num}: {phase_name}",
            "tasks": tasks,
            "raw": phase_content,
        })

    return phases


def _extract_tasks_from_phase(phase_content: str) -> list[dict]:
    """Extract task specs from a phase section. Returns list of task dicts."""
    tasks = []

    # Look for task blocks starting with "- task_type:" or "- file_path:"
    task_pattern = re.compile(
        r'^\s*-\s+task_type:\s*(create|modify)',
        re.MULTILINE,
    )
    task_matches = list(task_pattern.finditer(phase_content))

    if not task_matches:
        # Fallback: create a single task from the raw content
        return [{"raw": phase_content}]

    for i, match in enumerate(task_matches):
        start = match.start()
        end = task_matches[i + 1].start() if i + 1 < len(task_matches) else len(phase_content)
        task_block = phase_content[start:end].strip()

        task = {"raw": task_block, "task_type": match.group(1)}

        # Extract file_path
        fp_match = re.search(r'file_path:\s*(.+)', task_block)
        if fp_match:
            task["file_path"] = fp_match.group(1).strip()

        # Extract description
        desc_match = re.search(r'description:\s*(.+)', task_block)
        if desc_match:
            task["description"] = desc_match.group(1).strip()

        # Extract high_risk
        hr_match = re.search(r'high_risk:\s*(true|false)', task_block, re.IGNORECASE)
        if hr_match:
            task["high_risk"] = hr_match.group(1).lower() == "true"

        # Extract model_hint
        mh_match = re.search(r'model_hint:\s*(\S+)', task_block)
        if mh_match:
            task["model_hint"] = mh_match.group(1).strip()

        tasks.append(task)

    return tasks


def _batch_tasks(tasks: list[dict], max_per_batch: int = 3) -> list[list[dict]]:
    """
    Group tasks into parallel batches respecting dependencies.

    Rules:
    - Tasks on the same file must be sequential (different batches).
    - Tasks whose depends_on includes a file from another task in the same batch
      must be in a later batch.
    - Max tasks per batch = max_per_batch.
    """
    if not tasks:
        return []

    batches: list[list[dict]] = []
    remaining = list(tasks)
    completed_files: set[str] = set()

    while remaining:
        batch: list[dict] = []
        batch_files: set[str] = set()
        still_remaining = []

        for task in remaining:
            fp = task.get("file_path", "")
            deps = task.get("depends_on", [])
            if isinstance(deps, str):
                deps = [d.strip() for d in deps.split(",") if d.strip()]

            # Check constraints
            if fp in batch_files:
                # Same file already in this batch
                still_remaining.append(task)
                continue

            unmet = [d for d in deps if d not in completed_files]
            if unmet:
                still_remaining.append(task)
                continue

            if len(batch) >= max_per_batch:
                still_remaining.append(task)
                continue

            batch.append(task)
            if fp:
                batch_files.add(fp)

        if not batch:
            # Deadlock prevention: force remaining into a sequential batch
            batch = [still_remaining.pop(0)]

        batches.append(batch)
        for t in batch:
            fp = t.get("file_path", "")
            if fp:
                completed_files.add(fp)
        remaining = still_remaining

    return batches


# ---------------------------------------------------------------------------
# Main task execution: plan → validate → build → review → QA pipeline
# ---------------------------------------------------------------------------

async def _run_task_phases(run_id: str, task: Task, engine) -> None:
    """Background coroutine that runs a task through the full pipeline."""

    memory = MemoryClient()

    # Generate build_id for memory key schema
    build_id = _generate_build_id(task.title)
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if run:
            run.build_id = build_id
            session.add(run)
            session.commit()

    # Create an isolated git branch for this task
    branch_name = await _create_task_branch(task.id, task.title, task.workspace)
    if branch_name:
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.branch_name = branch_name
                session.add(run)
            db_task = session.get(Task, task.id)
            if db_task:
                db_task.branch_name = branch_name
                session.add(db_task)
            session.commit()
        await _broadcast(run_id, {"type": "branch_created", "branch": branch_name})

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
    capture_baseline = settings.get("capture_test_baseline", True)
    max_builders = settings.get("max_concurrent_builders", 3)

    # Load skill (if any) for this task
    from .models import Skill as _Skill
    skill = None
    if task.skill_id:
        with Session(engine) as session:
            skill = session.get(_Skill, task.skill_id)

    # Build approval gate for bash commands
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
        from .webhooks import send_webhook_notifications
        await send_webhook_notifications(event, task.title, run_id, settings)

    # ---------------------------------------------------------------
    # Claude Code path: single pass via /feature-plan-and-build skill
    # ---------------------------------------------------------------
    if _is_claude_code(task.model):
        from .agent.adapters.claude_code import run_claude_code_task

        model_name = _get_claude_code_model(task.model)

        # Build the full task description, including spec if available
        full_description = task.description
        if spec_content:
            full_description += f"\n\n## Spec / PRD\n{spec_content}"

        with Session(engine) as session:
            db_task = session.get(Task, task.id)
            if db_task:
                db_task.status = "building"
                db_task.updated_at = datetime.utcnow()
                session.add(db_task)
                session.commit()

        # Create RunPhase record so the frontend can track progress
        phase_id = None
        with Session(engine) as session:
            run_phase = RunPhase(
                run_id=run_id,
                phase="build",
                attempt=1,
                status="running",
            )
            session.add(run_phase)
            run = session.get(Run, run_id)
            if run:
                run.current_phase = "build"
                session.add(run)
            session.commit()
            session.refresh(run_phase)
            phase_id = run_phase.id

        await on_event({"type": "phase_start", "phase": "build", "attempt": 1})

        try:
            status, artifact = await run_claude_code_task(
                run_id=run_id,
                model_name=model_name,
                workspace=task.workspace,
                task_description=full_description,
                on_event=on_event,
                skill_slash_command=skill.claude_code_skill if skill and skill.claude_code_skill else None,
            )
        except Exception as e:
            status = "failed"
            artifact = str(e)
            await on_event({"type": "error", "content": artifact})

        # Update RunPhase record
        with Session(engine) as session:
            rp = session.get(RunPhase, phase_id)
            if rp:
                rp.status = "completed" if status == "completed" else "failed"
                rp.completed_at = datetime.utcnow()
                rp.artifact = artifact if status == "completed" else None
                rp.error = artifact if status == "failed" else None
                session.add(rp)
                session.commit()

        await on_event({"type": "phase_end", "phase": "build", "attempt": 1, "status": status})

        # Finalize run
        final_status = "completed" if status == "completed" else "failed"
        if final_status == "completed" and branch_name:
            await _commit_task_changes(task.title, task.workspace)
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.status = final_status
                run.current_phase = None
                run.completed_at = datetime.utcnow()
                run.summary = artifact if status == "completed" else None
                run.error = artifact if status == "failed" else None
                session.add(run)

            db_task = session.get(Task, task.id)
            if db_task:
                db_task.status = "done" if final_status == "completed" else "failed"
                db_task.updated_at = datetime.utcnow()
                session.add(db_task)

            session.commit()

        await _broadcast(run_id, {"type": "done", "status": final_status})
        active_agents.pop(run_id, None)
        active_runs.pop(run_id, None)

        if final_status == "completed":
            try:
                from .scheduler import check_ready_tasks
                await check_ready_tasks(engine)
            except Exception:
                pass

        return  # Skip the multi-phase pipeline below

    # ---------------------------------------------------------------
    # Cursor CLI path: single pass via /feature-plan-and-build skill
    # ---------------------------------------------------------------
    if _is_cursor(task.model):
        from .agent.adapters.cursor import run_cursor_task

        model_name = _get_cursor_model(task.model)

        # Build the full task description, including spec if available
        full_description = task.description
        if spec_content:
            full_description += f"\n\n## Spec / PRD\n{spec_content}"

        with Session(engine) as session:
            db_task = session.get(Task, task.id)
            if db_task:
                db_task.status = "building"
                db_task.updated_at = datetime.utcnow()
                session.add(db_task)
                session.commit()

        # Create RunPhase record so the frontend can track progress
        phase_id = None
        with Session(engine) as session:
            run_phase = RunPhase(
                run_id=run_id,
                phase="build",
                attempt=1,
                status="running",
            )
            session.add(run_phase)
            run = session.get(Run, run_id)
            if run:
                run.current_phase = "build"
                session.add(run)
            session.commit()
            session.refresh(run_phase)
            phase_id = run_phase.id

        await on_event({"type": "phase_start", "phase": "build", "attempt": 1})

        try:
            status, artifact = await run_cursor_task(
                run_id=run_id,
                model_name=model_name,
                workspace=task.workspace,
                task_description=full_description,
                on_event=on_event,
                skill_slash_command=skill.cursor_skill if skill and skill.cursor_skill else None,
            )
        except Exception as e:
            status = "failed"
            artifact = str(e)
            await on_event({"type": "error", "content": artifact})

        # Update RunPhase record
        with Session(engine) as session:
            rp = session.get(RunPhase, phase_id)
            if rp:
                rp.status = "completed" if status == "completed" else "failed"
                rp.completed_at = datetime.utcnow()
                rp.artifact = artifact if status == "completed" else None
                rp.error = artifact if status == "failed" else None
                session.add(rp)
                session.commit()

        await on_event({"type": "phase_end", "phase": "build", "attempt": 1, "status": status})

        # Finalize run
        final_status = "completed" if status == "completed" else "failed"
        if final_status == "completed" and branch_name:
            await _commit_task_changes(task.title, task.workspace)
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.status = final_status
                run.current_phase = None
                run.completed_at = datetime.utcnow()
                run.summary = artifact if status == "completed" else None
                run.error = artifact if status == "failed" else None
                session.add(run)

            db_task = session.get(Task, task.id)
            if db_task:
                db_task.status = "done" if final_status == "completed" else "failed"
                db_task.updated_at = datetime.utcnow()
                session.add(db_task)

            session.commit()

        await _broadcast(run_id, {"type": "done", "status": final_status})
        active_agents.pop(run_id, None)
        active_runs.pop(run_id, None)

        if final_status == "completed":
            try:
                from .scheduler import check_ready_tasks
                await check_ready_tasks(engine)
            except Exception:
                pass

        return  # Skip the multi-phase pipeline below

    # ---------------------------------------------------------------
    # Traditional multi-phase pipeline (ollama / anthropic)
    # ---------------------------------------------------------------

    # Resolve models for each phase
    plan_model_str = task.plan_model or task.model
    build_model_str = task.model
    qa_model_str = task.qa_model or task.model

    error_msg = None
    final_status = "completed"
    summary = ""
    architecture_snapshot = None

    try:
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
                        dep_runs = session.exec(
                            select(Run)
                            .where(Run.task_id == dep_id, Run.status == "completed")
                            .order_by(Run.started_at.desc())
                        ).all()
                        if dep_runs:
                            latest_run = dep_runs[0]
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
        # ================================================================
        # PHASE 0: Capture test baseline (before any changes)
        # ================================================================
        test_baseline = None
        if capture_baseline:
            await on_event({"type": "text", "content": "Capturing pre-build test baseline..."})
            test_baseline = await _capture_test_baseline(task.workspace)
            with Session(engine) as session:
                run = session.get(Run, run_id)
                if run:
                    run.test_baseline = test_baseline
                    session.add(run)
                    session.commit()
            await on_event({"type": "text", "content": f"Test baseline captured.\n{test_baseline[:200]}"})

        # ================================================================
        # PHASE 1: PLAN (2-pass deep analysis)
        # ================================================================
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

        # Check if the plan artifact is actually a structured plan
        if not _is_valid_plan(plan_artifact):
            await on_event({
                "type": "text",
                "content": "Plan output is not a structured plan. Re-running planner with explicit instructions...",
            })
            plan_sys_retry = plan_prompt(
                workspace=task.workspace,
                spec_content=spec_content,
                memory_context=memory_context,
                upstream_context=upstream_context,
            )
            plan_sys_retry += (
                "\n\n## CRITICAL CORRECTION\n"
                "Your previous attempt did NOT produce a structured implementation plan. "
                "You MUST output a structured plan starting with '# Build Plan' and containing "
                "'### Phase N:' headers with task specs.\n"
                "Do NOT just call read_file or describe actions. Your FINAL MESSAGE must be "
                "the structured plan text.\n\n"
                f"Your previous (invalid) output was:\n{plan_artifact[:2000]}"
            )
            plan_status, plan_artifact = await _run_single_phase(
                run_id=run_id,
                phase_name="plan",
                attempt=2,
                model_str=plan_model_str,
                workspace=task.workspace,
                system_prompt=plan_sys_retry,
                user_message=task.description,
                allowed_tools=PLAN_TOOLS,
                memory=memory,
                approval_gate=None,
                on_event=on_event,
                engine=engine,
            )
            if plan_status != "completed":
                raise RuntimeError(f"Planning phase failed on retry: {plan_artifact}")

        # ================================================================
        # PHASE 1b: VALIDATE the plan
        # ================================================================
        await on_event({"type": "text", "content": "Validating plan..."})

        validate_sys = validator_prompt(
            workspace=task.workspace,
            plan_artifact=plan_artifact,
            spec_content=spec_content,
            memory_context=memory_context,
        )
        val_status, val_artifact = await _run_single_phase(
            run_id=run_id,
            phase_name="validate",
            attempt=1,
            model_str=qa_model_str,  # Use QA model for validation (lightweight)
            workspace=task.workspace,
            system_prompt=validate_sys,
            user_message="Validate the implementation plan for internal consistency.",
            allowed_tools=VALIDATOR_TOOLS,
            memory=memory,
            approval_gate=None,
            on_event=on_event,
            engine=engine,
        )

        if val_status != "completed":
            await on_event({"type": "text", "content": "Plan validation phase failed. Proceeding with plan as-is."})
        elif not val_artifact.strip().startswith("VERDICT: PASS"):
            # Plan has issues — re-run planner with validation feedback (1 correction cycle)
            await on_event({"type": "text", "content": "Plan validation failed. Re-running planner with feedback..."})

            plan_sys_v2 = plan_prompt(
                workspace=task.workspace,
                spec_content=spec_content,
                memory_context=memory_context,
                upstream_context=upstream_context,
            )
            # Append validation feedback
            plan_sys_v2 += (
                f"\n\n## Plan Validation Feedback\n"
                f"Your previous plan was rejected by the validator. Fix these issues:\n"
                f"{val_artifact}"
            )
            plan_status2, plan_artifact2 = await _run_single_phase(
                run_id=run_id,
                phase_name="plan",
                attempt=2,
                model_str=plan_model_str,
                workspace=task.workspace,
                system_prompt=plan_sys_v2,
                user_message=task.description,
                allowed_tools=PLAN_TOOLS,
                memory=memory,
                approval_gate=None,
                on_event=on_event,
                engine=engine,
            )

            if plan_status2 == "completed":
                plan_artifact = plan_artifact2

                # Re-validate
                validate_sys2 = validator_prompt(
                    workspace=task.workspace,
                    plan_artifact=plan_artifact,
                    spec_content=spec_content,
                    memory_context=memory_context,
                )
                val_status2, val_artifact2 = await _run_single_phase(
                    run_id=run_id,
                    phase_name="validate",
                    attempt=2,
                    model_str=qa_model_str,
                    workspace=task.workspace,
                    system_prompt=validate_sys2,
                    user_message="Validate the corrected implementation plan.",
                    allowed_tools=VALIDATOR_TOOLS,
                    memory=memory,
                    approval_gate=None,
                    on_event=on_event,
                    engine=engine,
                )

                if val_status2 == "completed" and not val_artifact2.strip().startswith("VERDICT: PASS"):
                    await on_event({
                        "type": "text",
                        "content": "Plan still has issues after correction. Proceeding with best-effort plan.",
                    })
        else:
            await on_event({"type": "text", "content": "Plan validation passed."})

        # ================================================================
        # PHASE 1c: APPROVAL GATE (supervised mode only)
        # ================================================================
        if task.mode == "supervised":
            await on_event({
                "type": "plan_approval_request",
                "plan_summary": plan_artifact[:2000],  # Truncate for display
            })

            # Wait for user approval
            approval_event = asyncio.Event()
            _plan_approvals[run_id] = approval_event
            _plan_results[run_id] = False

            try:
                await asyncio.wait_for(approval_event.wait(), timeout=600.0)
                approved = _plan_results.get(run_id, False)
                if not approved:
                    raise RuntimeError("Plan rejected by user")
            except asyncio.TimeoutError:
                raise RuntimeError("Plan approval timed out (10 minutes)")
            finally:
                _plan_approvals.pop(run_id, None)
                _plan_results.pop(run_id, None)

            await on_event({"type": "text", "content": "Plan approved. Starting build..."})

        # ================================================================
        # PHASE 2: BATCHED BUILD + REVIEW
        # ================================================================
        parsed_phases = _parse_plan_phases(plan_artifact)
        all_build_results = []

        for phase_idx, phase in enumerate(parsed_phases, start=1):
            await on_event({
                "type": "text",
                "content": f"Starting build phase {phase_idx}/{len(parsed_phases)}: {phase['name']}",
            })

            with Session(engine) as session:
                db_task = session.get(Task, task.id)
                if db_task:
                    db_task.status = "building"
                    db_task.updated_at = datetime.utcnow()
                    session.add(db_task)
                    session.commit()

            tasks_in_phase = phase.get("tasks", [])

            # If no parsed tasks, fall back to running the raw phase content as a single build
            if not tasks_in_phase or (len(tasks_in_phase) == 1 and "raw" in tasks_in_phase[0] and "file_path" not in tasks_in_phase[0]):
                # Single monolithic build for this phase
                build_sys = build_prompt(
                    workspace=task.workspace,
                    plan_artifact=plan_artifact,
                    spec_content=spec_content,
                    memory_context=memory_context,
                    upstream_context=upstream_context,
                    task_spec=phase.get("raw", ""),
                    architecture_snapshot=architecture_snapshot,
                )
                if skill and skill.prompt_addon:
                    build_sys += f"\n\n{skill.prompt_addon}"
                build_status, build_artifact = await _run_single_phase(
                    run_id=run_id,
                    phase_name="build",
                    attempt=1,
                    model_str=build_model_str,
                    workspace=task.workspace,
                    system_prompt=build_sys,
                    user_message=task.description,
                    allowed_tools=BUILD_TOOLS,
                    memory=memory,
                    approval_gate=bash_gate if require_approval else None,
                    on_event=on_event,
                    engine=engine,
                    batch=phase_idx,
                )

                if build_status != "completed":
                    raise RuntimeError(f"Build phase {phase_idx} failed: {build_artifact}")

                # Retry loop: if the build produced no file changes, nudge the agent
                MAX_BUILD_NO_CHANGE_RETRIES = 2
                for no_change_retry in range(MAX_BUILD_NO_CHANGE_RETRIES):
                    if _verify_build_changed(task.workspace):
                        break
                    await on_event({
                        "type": "text",
                        "content": (
                            f"Build phase {phase_idx} produced no file changes. "
                            f"Retry {no_change_retry + 1}/{MAX_BUILD_NO_CHANGE_RETRIES} with explicit feedback..."
                        ),
                    })
                    no_change_feedback = (
                        "YOUR PREVIOUS ATTEMPT FAILED: You described file changes in text "
                        "but did NOT call the write_file tool. No files were actually modified.\n\n"
                        "You MUST call write_file(...) for every file that needs to be created or modified. "
                        "Describing changes in prose is NOT sufficient. The build is verified by checking "
                        "git diff -- if write_file is not called, the build fails.\n\n"
                        f"Previous output that failed:\n{build_artifact[:2000]}"
                    )
                    retry_sys = build_prompt(
                        workspace=task.workspace,
                        plan_artifact=plan_artifact,
                        spec_content=spec_content,
                        memory_context=memory_context,
                        upstream_context=upstream_context,
                        qa_feedback=no_change_feedback,
                        task_spec=phase.get("raw", ""),
                        architecture_snapshot=architecture_snapshot,
                    )
                    if skill and skill.prompt_addon:
                        retry_sys += f"\n\n{skill.prompt_addon}"
                    build_status, build_artifact = await _run_single_phase(
                        run_id=run_id,
                        phase_name="build",
                        attempt=no_change_retry + 2,
                        model_str=build_model_str,
                        workspace=task.workspace,
                        system_prompt=retry_sys,
                        user_message=task.description,
                        allowed_tools=BUILD_TOOLS,
                        memory=memory,
                        approval_gate=bash_gate if require_approval else None,
                        on_event=on_event,
                        engine=engine,
                        batch=phase_idx,
                    )
                    if build_status != "completed":
                        raise RuntimeError(f"Build phase {phase_idx} failed on retry: {build_artifact}")
                else:
                    if not _verify_build_changed(task.workspace):
                        await on_event({"type": "error", "content": "Build completed but no files were modified after all retries. The agent did not call write_file."})
                        raise RuntimeError(f"Build phase {phase_idx} produced no file changes after {MAX_BUILD_NO_CHANGE_RETRIES + 1} attempts.")

                all_build_results.append(build_artifact)
                continue

            # Batch the tasks for parallel execution
            batches = _batch_tasks(tasks_in_phase, max_per_batch=max_builders)

            for batch_idx, batch in enumerate(batches, start=1):
                await on_event({
                    "type": "text",
                    "content": f"  Batch {batch_idx}/{len(batches)}: {len(batch)} task(s)",
                })

                # Launch builders in parallel
                async def run_builder(t_idx: int, task_spec_dict: dict) -> tuple[str, str]:
                    task_spec_str = task_spec_dict.get("raw", json.dumps(task_spec_dict, indent=2))
                    model_hint = task_spec_dict.get("model_hint")
                    model = build_model_str
                    # TODO: support model_hint="fast" mapping

                    b_sys = build_prompt(
                        workspace=task.workspace,
                        plan_artifact=plan_artifact,
                        spec_content=spec_content,
                        memory_context=memory_context,
                        upstream_context=upstream_context,
                        task_spec=task_spec_str,
                        architecture_snapshot=architecture_snapshot,
                    )
                    if skill and skill.prompt_addon:
                        b_sys += f"\n\n{skill.prompt_addon}"
                    desc = task_spec_dict.get("description", task.description)
                    return await _run_single_phase(
                        run_id=run_id,
                        phase_name="build",
                        attempt=1,
                        model_str=model,
                        workspace=task.workspace,
                        system_prompt=b_sys,
                        user_message=desc,
                        allowed_tools=BUILD_TOOLS,
                        memory=memory,
                        approval_gate=bash_gate if require_approval else None,
                        on_event=on_event,
                        engine=engine,
                        batch=phase_idx * 100 + batch_idx,
                        task_index=t_idx,
                    )

                builder_results = await asyncio.gather(
                    *[run_builder(i, t) for i, t in enumerate(batch)],
                    return_exceptions=True,
                )

                # Collect results and check for failures
                batch_artifacts = []
                touched_files = []
                failed_tasks = []

                for i, result in enumerate(builder_results):
                    if isinstance(result, Exception):
                        failed_tasks.append((i, str(result)))
                        continue
                    b_status, b_artifact = result
                    if b_status != "completed":
                        failed_tasks.append((i, b_artifact))
                    else:
                        batch_artifacts.append(b_artifact)
                        fp = batch[i].get("file_path", "")
                        if fp:
                            touched_files.append(fp)

                if failed_tasks:
                    await on_event({
                        "type": "text",
                        "content": f"  {len(failed_tasks)} task(s) failed in batch {batch_idx}.",
                    })

                if batch_artifacts and not _verify_build_changed(task.workspace):
                    await on_event({
                        "type": "text",
                        "content": f"Batch {batch_idx} produced no file changes. This may indicate agents described writes without calling write_file.",
                    })

                all_build_results.extend(batch_artifacts)

                # ---- REVIEW this batch ----
                if touched_files and batch_artifacts:
                    await on_event({
                        "type": "text",
                        "content": f"  Reviewing batch {batch_idx}...",
                    })

                    review_sys = reviewer_prompt(
                        workspace=task.workspace,
                        plan_artifact=plan_artifact,
                        touched_files=touched_files,
                        batch_results="\n\n---\n\n".join(batch_artifacts),
                        architecture_snapshot=architecture_snapshot,
                        spec_content=spec_content,
                        memory_context=memory_context,
                    )
                    rev_status, rev_artifact = await _run_single_phase(
                        run_id=run_id,
                        phase_name="review",
                        attempt=1,
                        model_str=qa_model_str,
                        workspace=task.workspace,
                        system_prompt=review_sys,
                        user_message="Review the builder output for correctness.",
                        allowed_tools=REVIEWER_TOOLS,
                        memory=memory,
                        approval_gate=None,
                        on_event=on_event,
                        engine=engine,
                        batch=phase_idx * 100 + batch_idx,
                    )

                    if rev_status == "completed" and not rev_artifact.strip().startswith("VERDICT: PASS"):
                        # Review failed — retry failed builders with review feedback (max 3 retries)
                        for retry_attempt in range(2, 4):  # attempts 2 and 3
                            await on_event({
                                "type": "text",
                                "content": f"  Review failed. Retry attempt {retry_attempt}/3...",
                            })

                            retry_sys = build_prompt(
                                workspace=task.workspace,
                                plan_artifact=plan_artifact,
                                spec_content=spec_content,
                                memory_context=memory_context,
                                upstream_context=upstream_context,
                                review_feedback=rev_artifact,
                                architecture_snapshot=architecture_snapshot,
                            )
                            if skill and skill.prompt_addon:
                                retry_sys += f"\n\n{skill.prompt_addon}"
                            retry_status, retry_artifact = await _run_single_phase(
                                run_id=run_id,
                                phase_name="build",
                                attempt=retry_attempt,
                                model_str=build_model_str,
                                workspace=task.workspace,
                                system_prompt=retry_sys,
                                user_message=task.description,
                                allowed_tools=BUILD_TOOLS,
                                memory=memory,
                                approval_gate=bash_gate if require_approval else None,
                                on_event=on_event,
                                engine=engine,
                                batch=phase_idx * 100 + batch_idx,
                            )

                            if retry_status != "completed":
                                continue

                            # Re-review
                            review_sys2 = reviewer_prompt(
                                workspace=task.workspace,
                                plan_artifact=plan_artifact,
                                touched_files=touched_files,
                                batch_results=retry_artifact,
                                architecture_snapshot=architecture_snapshot,
                                spec_content=spec_content,
                                memory_context=memory_context,
                            )
                            rev_status2, rev_artifact2 = await _run_single_phase(
                                run_id=run_id,
                                phase_name="review",
                                attempt=retry_attempt,
                                model_str=qa_model_str,
                                workspace=task.workspace,
                                system_prompt=review_sys2,
                                user_message="Review the corrected builder output.",
                                allowed_tools=REVIEWER_TOOLS,
                                memory=memory,
                                approval_gate=None,
                                on_event=on_event,
                                engine=engine,
                                batch=phase_idx * 100 + batch_idx,
                            )

                            if rev_status2 == "completed" and rev_artifact2.strip().startswith("VERDICT: PASS"):
                                all_build_results.append(retry_artifact)
                                break
                        else:
                            await on_event({
                                "type": "text",
                                "content": f"  Review still failing after max retries for batch {batch_idx}.",
                            })

        combined_build_summary = "\n\n---\n\n".join(all_build_results) if all_build_results else ""

        # ================================================================
        # PHASE 3: QA (end-to-end with baseline comparison)
        # ================================================================
        qa_passed = False
        qa_feedback = None
        max_qa_cycles = 2

        for qa_cycle in range(1, max_qa_cycles + 1):
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
                build_artifact=combined_build_summary,
                task_description=task.description,
                spec_content=spec_content,
                memory_context=memory_context,
                test_baseline=test_baseline,
            )
            qa_status, qa_artifact = await _run_single_phase(
                run_id=run_id,
                phase_name="qa",
                attempt=qa_cycle,
                model_str=qa_model_str,
                workspace=task.workspace,
                system_prompt=qa_sys,
                user_message="Perform end-to-end QA on the completed build.",
                allowed_tools=QA_TOOLS,
                memory=memory,
                approval_gate=bash_gate if require_approval else None,
                on_event=on_event,
                engine=engine,
            )

            if qa_status != "completed":
                raise RuntimeError(f"QA phase failed (cycle {qa_cycle}): {qa_artifact}")

            if qa_artifact.strip().startswith("VERDICT: PASS"):
                qa_passed = True
                summary = combined_build_summary
                await on_event({
                    "type": "text",
                    "content": f"QA passed on cycle {qa_cycle}.",
                })
                break
            else:
                qa_feedback = qa_artifact
                await on_event({
                    "type": "text",
                    "content": f"QA failed on cycle {qa_cycle}/{max_qa_cycles}. "
                               + ("Launching targeted fixes..." if qa_cycle < max_qa_cycles else "Max QA cycles exhausted."),
                })

                if qa_cycle < max_qa_cycles:
                    # Launch targeted builder to fix QA blockers
                    fix_sys = build_prompt(
                        workspace=task.workspace,
                        plan_artifact=plan_artifact,
                        spec_content=spec_content,
                        memory_context=memory_context,
                        upstream_context=upstream_context,
                        qa_feedback=qa_feedback,
                        architecture_snapshot=architecture_snapshot,
                    )
                    if skill and skill.prompt_addon:
                        fix_sys += f"\n\n{skill.prompt_addon}"
                    fix_status, fix_artifact = await _run_single_phase(
                        run_id=run_id,
                        phase_name="build",
                        attempt=qa_cycle + 10,  # offset to distinguish QA fix attempts
                        model_str=build_model_str,
                        workspace=task.workspace,
                        system_prompt=fix_sys,
                        user_message="Fix the issues identified by QA.",
                        allowed_tools=BUILD_TOOLS,
                        memory=memory,
                        approval_gate=bash_gate if require_approval else None,
                        on_event=on_event,
                        engine=engine,
                    )

                    if fix_status == "completed":
                        if not _verify_build_changed(task.workspace):
                            await on_event({"type": "error", "content": "QA fix build completed but no files were modified."})
                        else:
                            combined_build_summary += f"\n\n---\n\nQA Fix:\n{fix_artifact}"

        if not qa_passed:
            final_status = "failed"
            error_msg = f"QA failed after {max_qa_cycles} cycles"
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

    # Auto-commit changes on the task branch when the task succeeds or goes to review
    if final_status in ("completed", "review") and branch_name:
        await _commit_task_changes(task.title, task.workspace)

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
                    "build_id": build_id,
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


def resolve_plan_approval(run_id: str, approved: bool) -> bool:
    """Signal a pending plan approval gate. Returns False if no gate is waiting."""
    event = _plan_approvals.get(run_id)
    if not event:
        return False
    _plan_results[run_id] = approved
    event.set()
    return True


async def abort_run(run_id: str) -> bool:
    """Abort a running agent. Returns True if it was running."""
    agent = active_agents.get(run_id)
    if agent:
        agent.abort()

    # Also terminate any claude-code or cursor subprocess
    from .agent.adapters.claude_code import abort_claude_code
    abort_claude_code(run_id)
    from .agent.adapters.cursor import abort_cursor
    abort_cursor(run_id)

    task_handle = active_runs.get(run_id)
    if task_handle and not task_handle.done():
        task_handle.cancel()
        return True
    return False


def set_pipeline_paused(paused: bool) -> None:
    global _pipeline_paused
    _pipeline_paused = paused


def is_pipeline_paused() -> bool:
    return _pipeline_paused or _pipeline_paused_by_window


def set_window_paused(paused: bool) -> None:
    global _pipeline_paused_by_window
    _pipeline_paused_by_window = paused


def is_window_paused() -> bool:
    return _pipeline_paused_by_window
