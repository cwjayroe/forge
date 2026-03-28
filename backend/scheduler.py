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

    from .orchestrator import is_pipeline_paused
    return {
        "paused": is_pipeline_paused(),
        "status_counts": status_counts,
        "tasks": task_summaries,
    }
