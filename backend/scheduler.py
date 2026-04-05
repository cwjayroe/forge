"""
DAG scheduler: dependency-aware task scheduling with concurrency control.

Called after each task completion to start tasks whose dependencies are met.
Provides cycle detection for task creation/update validation.
"""
import asyncio
from collections import defaultdict
from datetime import datetime

from sqlmodel import Session, select

from .models import Run, Task


def detect_cycles(tasks: list[Task]) -> list[str]:
    """
    Detect dependency cycles using DFS.

    Args:
        tasks: All tasks to check.

    Returns:
        List of task IDs involved in a cycle (empty if no cycles).
    """
    task_map = {t.id: t for t in tasks}
    adj: dict[str, list[str]] = defaultdict(list)

    for t in tasks:
        if t.depends_on:
            for dep_id in t.depends_on.split(","):
                dep_id = dep_id.strip()
                if dep_id and dep_id in task_map:
                    adj[dep_id].append(t.id)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {t.id: WHITE for t in tasks}
    cycle_nodes: list[str] = []

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if color.get(neighbor) == GRAY:
                cycle_nodes.append(neighbor)
                return True
            if color.get(neighbor) == WHITE:
                if dfs(neighbor):
                    cycle_nodes.append(node)
                    return True
        color[node] = BLACK
        return False

    for task_id in task_map:
        if color[task_id] == WHITE:
            if dfs(task_id):
                break

    return cycle_nodes


def validate_dependencies(task_id: str, depends_on: str | None, all_tasks: list[Task]) -> str | None:
    """
    Validate that setting depends_on for a task won't create a cycle.

    Returns an error message if invalid, None if OK.
    """
    if not depends_on:
        return None

    dep_ids = [d.strip() for d in depends_on.split(",") if d.strip()]
    task_map = {t.id: t for t in all_tasks}

    # Check that all referenced task IDs exist
    for dep_id in dep_ids:
        if dep_id not in task_map:
            return f"Dependency task not found: {dep_id}"

    # Self-dependency
    if task_id in dep_ids:
        return "A task cannot depend on itself"

    # Simulate the update and check for cycles
    simulated = []
    for t in all_tasks:
        if t.id == task_id:
            # Create a copy with the new depends_on
            fake = Task(
                id=t.id, title=t.title, description=t.description,
                workspace=t.workspace, depends_on=depends_on,
                status=t.status, model=t.model,
            )
            simulated.append(fake)
        else:
            simulated.append(t)

    cycles = detect_cycles(simulated)
    if cycles:
        return f"Dependency cycle detected involving tasks: {', '.join(set(cycles))}"

    return None


async def check_ready_tasks(engine) -> list[str]:
    """
    Check for pending tasks whose dependencies are all satisfied and start them.
    Respects the max_concurrent_tasks setting.

    Returns list of task IDs that were started.
    """
    from .orchestrator import active_runs, is_pipeline_paused, start_run
    from .database import get_settings

    if is_pipeline_paused():
        return []

    settings = get_settings()
    max_concurrent = settings.get("max_concurrent_tasks", 3)

    started: list[str] = []

    with Session(engine) as session:
        # Count currently running tasks
        running_count = len(active_runs)
        if running_count >= max_concurrent:
            return []

        slots = max_concurrent - running_count

        # Find all pending tasks
        pending_tasks = session.exec(
            select(Task)
            .where(Task.status == "pending")
            .order_by(Task.order, Task.created_at)
        ).all()

        for task in pending_tasks:
            if slots <= 0:
                break

            # Check if all dependencies are satisfied
            if task.depends_on:
                dep_ids = [d.strip() for d in task.depends_on.split(",") if d.strip()]
                if dep_ids:
                    deps = session.exec(
                        select(Task).where(Task.id.in_(dep_ids))
                    ).all()
                    dep_map = {d.id: d for d in deps}

                    # All deps must be "done"
                    all_done = all(
                        dep_map.get(did) and dep_map[did].status == "done"
                        for did in dep_ids
                    )
                    if not all_done:
                        continue

            # Per-task schedule gate
            if task.scheduled_for is not None:
                if datetime.utcnow() < task.scheduled_for:
                    continue

            # Start this task
            run = Run(task_id=task.id)
            session.add(run)
            task.status = "running"
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(run)
            session.refresh(task)

            await start_run(run.id, task, engine)
            started.append(task.id)
            slots -= 1

    return started


async def start_task_with_dependencies(task_id: str, engine) -> dict:
    """
    Start a specific task and all its pending ancestors (transitive deps) that are ready.

    Pending deps whose own deps are not yet done are "queued" — they will auto-start
    via check_ready_tasks() as upstream tasks complete.

    Returns:
        {
            "started": [{"task_id": ..., "run_id": ...}, ...],
            "queued":  [task_id, ...]
        }
    """
    from .orchestrator import active_runs, start_run
    from .database import get_settings

    settings = get_settings()
    max_concurrent = settings.get("max_concurrent_tasks", 3)

    started: list[dict] = []
    queued: list[str] = []

    with Session(engine) as session:
        all_tasks = session.exec(select(Task)).all()
        task_map = {t.id: t for t in all_tasks}

        if task_id not in task_map:
            return {"started": [], "queued": []}

        # Collect all transitive dependency IDs of the target task (including itself)
        def collect_chain(tid: str, visited: set) -> None:
            if tid in visited:
                return
            visited.add(tid)
            task = task_map.get(tid)
            if task and task.depends_on:
                for dep_id in task.depends_on.split(","):
                    dep_id = dep_id.strip()
                    if dep_id:
                        collect_chain(dep_id, visited)

        chain_ids: set = set()
        collect_chain(task_id, chain_ids)

        # Only consider pending tasks in the chain (skip done/running/failed)
        pending_in_chain = [
            task_map[tid] for tid in chain_ids
            if tid in task_map and task_map[tid].status == "pending"
        ]

        # Sort by order then created_at for deterministic start order
        pending_in_chain.sort(key=lambda t: (t.order, t.created_at))

        running_count = len(active_runs)
        slots = max(0, max_concurrent - running_count)

        for task in pending_in_chain:
            # A task is "ready" if all its own deps are done
            all_deps_done = True
            if task.depends_on:
                dep_ids = [d.strip() for d in task.depends_on.split(",") if d.strip()]
                for dep_id in dep_ids:
                    dep_task = task_map.get(dep_id)
                    if not dep_task or dep_task.status != "done":
                        all_deps_done = False
                        break

            if all_deps_done:
                if slots > 0:
                    run = Run(task_id=task.id)
                    session.add(run)
                    task.status = "running"
                    task.updated_at = datetime.utcnow()
                    session.add(task)
                    session.commit()
                    session.refresh(run)
                    session.refresh(task)
                    await start_run(run.id, task, engine)
                    started.append({"task_id": task.id, "run_id": run.id})
                    slots -= 1
                else:
                    queued.append(task.id)
            else:
                queued.append(task.id)

    return {"started": started, "queued": queued}


async def start_pipeline(engine) -> list[str]:
    """
    Start all root tasks (no unmet dependencies) up to the concurrency limit.
    This is the entry point for kicking off a full pipeline.

    Returns list of task IDs that were started.
    """
    from .orchestrator import active_runs, is_pipeline_paused, set_pipeline_paused
    from .database import get_settings

    # Unpause if paused
    if is_pipeline_paused():
        set_pipeline_paused(False)

    return await check_ready_tasks(engine)


async def pause_pipeline() -> None:
    """Pause the pipeline scheduler — no new tasks will be auto-started."""
    from .orchestrator import set_pipeline_paused
    set_pipeline_paused(True)


def get_pipeline_status(engine) -> dict:
    """Get a summary of all task statuses."""
    with Session(engine) as session:
        tasks = session.exec(select(Task).order_by(Task.order, Task.created_at)).all()

    status_counts: dict[str, int] = {}
    task_summaries = []
    for t in tasks:
        status_counts[t.status] = status_counts.get(t.status, 0) + 1
        task_summaries.append({
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "depends_on": t.depends_on,
        })

    from .orchestrator import is_pipeline_paused, is_window_paused
    from .database import get_settings
    paused = is_pipeline_paused()
    window_paused = is_window_paused()
    settings = get_settings()
    if window_paused:
        paused_reason = "schedule"
    elif paused:
        paused_reason = "manual"
    else:
        paused_reason = None
    return {
        "paused": paused,
        "paused_reason": paused_reason,
        "schedule_active": settings.get("schedule_enabled", False),
        "status_counts": status_counts,
        "tasks": task_summaries,
    }
