"""
Forge FastAPI backend entry point.
Start with: uvicorn backend.main:app --reload
"""
import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from fastapi.staticfiles import StaticFiles

from .database import create_db_and_tables, engine, get_session, get_settings, save_settings
from .memory import MemoryClient
from pathlib import Path

from .models import (
    Run,
    RunEvent,
    RunPhase,
    Settings,
    Skill,
    SkillCreate,
    SkillUpdate,
    Task,
    TaskCreate,
    TaskReorder,
    TaskUpdate,
)
from .orchestrator import abort_run, deregister_ws_listener, is_pipeline_paused, is_window_paused, register_ws_listener, resolve_bash_approval, resolve_plan_approval, set_window_paused, start_run
from .scheduler import check_ready_tasks, get_pipeline_status, pause_pipeline, start_pipeline, start_task_with_dependencies, validate_dependencies

app = FastAPI(title="Forge", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global memory client (initialized on startup)
memory_client: MemoryClient = None  # type: ignore[assignment]


def _is_in_window(settings: dict) -> bool:
    """Return True if the current local time falls within the configured execution window."""
    from datetime import datetime as dt
    now = dt.now()
    today_dow = now.weekday()  # 0=Mon, 6=Sun

    allowed_days = [int(d) for d in settings.get("schedule_days", "0,1,2,3,4,5,6").split(",") if d.strip().isdigit()]
    if allowed_days and today_dow not in allowed_days:
        return False

    try:
        start_h, start_m = map(int, settings["schedule_window_start"].split(":"))
        end_h, end_m = map(int, settings["schedule_window_end"].split(":"))
    except (KeyError, ValueError):
        return True  # Malformed config — don't block execution

    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    now_minutes = now.hour * 60 + now.minute

    if start_minutes < end_minutes:
        # Same-day window e.g. 09:00–17:00
        return start_minutes <= now_minutes < end_minutes
    else:
        # Overnight window e.g. 22:00–06:00
        return now_minutes >= start_minutes or now_minutes < end_minutes


async def _window_checker_loop() -> None:
    """Background task: auto-pause/resume pipeline based on the configured schedule window."""
    while True:
        try:
            settings = get_settings()
            if settings.get("schedule_enabled", False):
                in_window = _is_in_window(settings)
                if not in_window and not is_window_paused():
                    set_window_paused(True)
                elif in_window and is_window_paused():
                    set_window_paused(False)
                    if not is_pipeline_paused():
                        await check_ready_tasks(engine)
        except Exception:
            pass
        await asyncio.sleep(60)


_BUILTIN_SKILLS = [
    {
        "slug": "write-tests",
        "name": "Write Tests",
        "icon": "🧪",
        "description": "Generate comprehensive test suites for existing code.",
        "prompt_addon": (
            "## Skill: Write Tests\n"
            "Your sole job is writing tests. Do NOT modify production code unless a trivial "
            "fix is strictly required for testability. Follow the project's existing test "
            "framework, fixtures, and naming conventions. Cover edge cases, error paths, "
            "and happy paths. Every new test must have a clear docstring."
        ),
        "template_description": "Write comprehensive tests for: ",
        "is_builtin": True,
    },
    {
        "slug": "security-audit",
        "name": "Security Audit",
        "icon": "🔒",
        "description": "Identify security vulnerabilities and produce a structured finding report.",
        "prompt_addon": (
            "## Skill: Security Audit\n"
            "Perform a security audit. Check for injection flaws, auth/authz gaps, insecure "
            "data handling, dependency vulnerabilities, and secrets in code. Produce a "
            "structured report with severity (Critical/High/Medium/Low), file:line, "
            "description, and recommended fix for each finding. "
            "Do NOT modify any files unless explicitly asked to remediate."
        ),
        "template_description": "Perform a security audit of: ",
        "is_builtin": True,
    },
    {
        "slug": "generate-docs",
        "name": "Generate Docs",
        "icon": "📝",
        "description": "Write docstrings, README sections, and API documentation.",
        "prompt_addon": (
            "## Skill: Generate Docs\n"
            "Your sole job is documentation. Write docstrings for all public functions, "
            "classes, and modules using the project's existing doc style. "
            "Do NOT change any logic, signatures, or behavior."
        ),
        "template_description": "Generate documentation for: ",
        "is_builtin": True,
    },
    {
        "slug": "fix-lint",
        "name": "Fix Lint",
        "icon": "✨",
        "description": "Fix all linting errors and enforce code style.",
        "prompt_addon": (
            "## Skill: Fix Lint\n"
            "Run the project's linter to discover all errors, then fix them. "
            "Do NOT change any logic, rename variables for non-style reasons, or refactor. "
            "Only touch lines flagged by the linter."
        ),
        "template_description": "Fix all linting issues in: ",
        "is_builtin": True,
    },
    {
        "slug": "refactor",
        "name": "Refactor",
        "icon": "♻️",
        "description": "Improve code structure and readability without changing behavior.",
        "prompt_addon": (
            "## Skill: Refactor\n"
            "CRITICAL: Do not change any external behavior, public API signatures, or test "
            "outcomes. Focus on removing duplication, improving naming, and simplifying logic. "
            "Run tests after every batch of changes to verify zero regressions."
        ),
        "template_description": "Refactor: ",
        "is_builtin": True,
    },
]


@app.on_event("startup")
async def on_startup():
    global memory_client
    create_db_and_tables()
    s = get_settings()
    memory_client = MemoryClient(
        ollama_host=s.get('ollama_host', 'http://localhost:11434'),
        memory_model=s.get('memory_model', 'llama3.2'),
    )
    # Seed built-in skills (idempotent by slug)
    with Session(engine) as session:
        for data in _BUILTIN_SKILLS:
            if not session.exec(select(Skill).where(Skill.slug == data["slug"])).first():
                session.add(Skill(**data))
        session.commit()
    asyncio.create_task(_window_checker_loop())


# ===========================================================================
# Skills
# ===========================================================================

@app.get("/skills")
def list_skills(session: Session = Depends(get_session)):
    return session.exec(
        select(Skill).order_by(Skill.is_builtin.desc(), Skill.name)
    ).all()


@app.post("/skills", status_code=201)
def create_skill(payload: SkillCreate, session: Session = Depends(get_session)):
    # Guard against duplicate slugs
    existing = session.exec(select(Skill).where(Skill.slug == payload.slug)).first()
    if existing:
        raise HTTPException(400, f"Skill with slug '{payload.slug}' already exists")
    skill = Skill(**payload.model_dump())
    session.add(skill)
    session.commit()
    session.refresh(skill)
    return skill


@app.put("/skills/{skill_id}")
def update_skill(skill_id: str, payload: SkillUpdate, session: Session = Depends(get_session)):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(skill, k, v)
    session.add(skill)
    session.commit()
    session.refresh(skill)
    return skill


@app.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, session: Session = Depends(get_session)):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")
    if skill.is_builtin:
        raise HTTPException(400, "Cannot delete built-in skills")
    session.delete(skill)
    session.commit()


@app.get("/skills/discover")
def discover_cli_skills(workspace: str = ""):
    """
    Scan standard locations for Claude Code skill/command files.
    Returns list of {slug, name, description, slash_command, path}.
    """
    search_dirs = []
    home = Path.home()
    for sub in ("skills", "commands"):
        search_dirs.append(home / ".claude" / sub)
        if workspace:
            ws = Path(workspace).expanduser().resolve()
            search_dirs.append(ws / ".claude" / sub)

    def parse_frontmatter(content: str, fallback_name: str):
        name, description = fallback_name, ""
        if content.startswith("---"):
            for line in content.split("\n")[1:]:
                if line.strip() == "---":
                    break
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
        return name, description

    results, seen = [], set()
    for d in search_dirs:
        if not d.exists():
            continue
        # Subdirectory pattern: skills/name/SKILL.md
        try:
            subdirs = sorted(d.iterdir())
        except OSError:
            continue
        for subdir in subdirs:
            if not subdir.is_dir():
                continue
            skill_md = subdir / "SKILL.md"
            if not skill_md.exists():
                continue
            slug = subdir.name
            if slug in seen:
                continue
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")
                name, desc = parse_frontmatter(content, slug)
                seen.add(slug)
                results.append({
                    "slug": slug,
                    "name": name,
                    "description": desc,
                    "slash_command": f"/{slug}",
                    "path": str(skill_md),
                })
            except OSError:
                continue
        # Flat .md file pattern: commands/name.md
        try:
            md_files = sorted(d.glob("*.md"))
        except OSError:
            continue
        for md_file in md_files:
            slug = md_file.stem
            if slug in seen:
                continue
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                name, desc = parse_frontmatter(content, slug)
                seen.add(slug)
                results.append({
                    "slug": slug,
                    "name": name,
                    "description": desc,
                    "slash_command": f"/{slug}",
                    "path": str(md_file),
                })
            except OSError:
                continue
    return results


# ===========================================================================
# Tasks
# ===========================================================================

@app.get("/tasks")
def list_tasks(session: Session = Depends(get_session)):
    tasks = session.exec(select(Task).order_by(Task.order, Task.created_at)).all()
    return tasks


@app.post("/tasks", status_code=201)
def create_task(payload: TaskCreate, session: Session = Depends(get_session)):
    task = Task(
        title=payload.title,
        description=payload.description,
        spec_path=payload.spec_path,
        mode=payload.mode,
        model=payload.model,
        plan_model=payload.plan_model,
        qa_model=payload.qa_model,
        max_retries=payload.max_retries,
        workspace=payload.workspace,
        depends_on=payload.depends_on,
    )

    # Validate dependencies won't create cycles
    if payload.depends_on:
        all_tasks = session.exec(select(Task)).all()
        all_tasks_plus = list(all_tasks) + [task]
        err = validate_dependencies(task.id, payload.depends_on, all_tasks_plus)
        if err:
            raise HTTPException(status_code=400, detail=err)

    session.add(task)
    session.commit()
    session.refresh(task)
    return task


# NOTE: /tasks/reorder must be defined BEFORE /tasks/{id}/run to avoid
# FastAPI treating "reorder" as a task ID.
@app.post("/tasks/reorder")
def reorder_tasks(payload: TaskReorder, session: Session = Depends(get_session)):
    for i, task_id in enumerate(payload.task_ids):
        task = session.get(Task, task_id)
        if task:
            task.order = i
            task.updated_at = datetime.utcnow()
            session.add(task)
    session.commit()
    return {"ok": True}


@app.put("/tasks/{task_id}")
def update_task(task_id: str, payload: TaskUpdate, session: Session = Depends(get_session)):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Validate dependency changes won't create cycles
    update_data = payload.model_dump(exclude_unset=True)
    if "depends_on" in update_data and update_data["depends_on"] is not None:
        all_tasks = session.exec(select(Task)).all()
        err = validate_dependencies(task_id, update_data["depends_on"], list(all_tasks))
        if err:
            raise HTTPException(status_code=400, detail=err)

    for field, value in update_data.items():
        setattr(task, field, value)
    task.updated_at = datetime.utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, session: Session = Depends(get_session)):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Delete associated runs, phases, and events
    runs = session.exec(select(Run).where(Run.task_id == task_id)).all()
    for run in runs:
        events = session.exec(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        for event in events:
            session.delete(event)
        phases = session.exec(select(RunPhase).where(RunPhase.run_id == run.id)).all()
        for phase in phases:
            session.delete(phase)
        session.delete(run)

    session.delete(task)
    session.commit()


@app.post("/tasks/{task_id}/run", status_code=201)
async def run_task(task_id: str, session: Session = Depends(get_session)):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in ("running", "planning", "building", "qa"):
        raise HTTPException(status_code=409, detail="Task is already running")

    # Start the task and any pending dependencies in the chain
    return await start_task_with_dependencies(task_id, engine)


# ===========================================================================
# Runs
# ===========================================================================

@app.get("/runs")
def list_runs(task_id: Optional[str] = None, session: Session = Depends(get_session)):
    query = select(Run)
    if task_id:
        query = query.where(Run.task_id == task_id)
    return session.exec(query.order_by(Run.started_at.desc())).all()


@app.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    events = session.exec(
        select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)
    ).all()
    phases = session.exec(
        select(RunPhase).where(RunPhase.run_id == run_id).order_by(RunPhase.started_at)
    ).all()
    return {
        **run.model_dump(),
        "events": [
            {**e.model_dump(), "content": json.loads(e.content)}
            for e in events
        ],
        "phases": [p.model_dump() for p in phases],
    }


class BashApproval(BaseModel):
    approved: bool


@app.post("/runs/{run_id}/bash/approve")
async def approve_bash(run_id: str, payload: BashApproval):
    ok = resolve_bash_approval(run_id, payload.approved)
    if not ok:
        raise HTTPException(status_code=404, detail="No pending bash approval for this run")
    return {"ok": True}


class PlanApproval(BaseModel):
    approved: bool


@app.post("/runs/{run_id}/plan/approve")
async def approve_plan(run_id: str, payload: PlanApproval):
    ok = resolve_plan_approval(run_id, payload.approved)
    if not ok:
        raise HTTPException(status_code=404, detail="No pending plan approval for this run")
    return {"ok": True}


@app.post("/runs/{run_id}/abort")
async def abort_run_endpoint(run_id: str, session: Session = Depends(get_session)):
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "running":
        raise HTTPException(status_code=409, detail="Run is not currently running")

    was_running = await abort_run(run_id)

    if was_running:
        run.status = "aborted"
        run.completed_at = datetime.utcnow()
        session.add(run)
        session.commit()

    return {"ok": True, "was_running": was_running}


# ===========================================================================
# Run Phases
# ===========================================================================

@app.get("/runs/{run_id}/phases")
def list_run_phases(run_id: str, session: Session = Depends(get_session)):
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    phases = session.exec(
        select(RunPhase)
        .where(RunPhase.run_id == run_id)
        .order_by(RunPhase.started_at)
    ).all()
    return [p.model_dump() for p in phases]


# ===========================================================================
# Pipeline
# ===========================================================================

@app.post("/pipeline/start")
async def start_pipeline_endpoint():
    started = await start_pipeline(engine)
    return {"ok": True, "started_task_ids": started}


@app.post("/pipeline/pause")
async def pause_pipeline_endpoint():
    await pause_pipeline()
    return {"ok": True, "paused": True}


@app.post("/pipeline/resume")
async def resume_pipeline_endpoint():
    started = await start_pipeline(engine)
    return {"ok": True, "paused": False, "started_task_ids": started}


@app.get("/pipeline/status")
def pipeline_status_endpoint():
    return get_pipeline_status(engine)


# ===========================================================================
# Memory
# ===========================================================================

@app.get("/memory/projects")
def list_memory_projects():
    return memory_client.list_projects()


@app.get("/memory/search")
async def search_memory(q: str, project_id: Optional[str] = None):
    results = await memory_client.search(q, project_id=project_id)
    return results


@app.get("/memory/list")
async def list_memory(project_id: Optional[str] = None):
    return await memory_client.list_all(project_id=project_id)


@app.get("/memory/stats")
async def memory_stats(project_id: Optional[str] = None):
    return await memory_client.get_stats(project_id=project_id)


class MemoryCreate(BaseModel):
    content: str
    metadata: Optional[dict] = None
    project_id: Optional[str] = None


@app.post("/memory", status_code=201)
async def create_memory(payload: MemoryCreate):
    meta = {**(payload.metadata or {}), **({"project_id": payload.project_id} if payload.project_id else {})}
    memory_id = await memory_client.store(payload.content, meta)
    return {"id": memory_id}


@app.delete("/memory/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, project_id: Optional[str] = None):
    deleted = await memory_client.delete(memory_id, project_id=project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory entry not found")


# ===========================================================================
# Settings
# ===========================================================================

@app.get("/settings")
def get_settings_endpoint():
    data = get_settings()
    defaults = Settings().model_dump()
    merged = {**defaults, **data}
    return merged


@app.put("/settings")
def update_settings(payload: Settings):
    save_settings(payload.model_dump())
    return payload


# ===========================================================================
# Templates
# ===========================================================================

@app.get("/templates")
def list_templates(path: str = ""):
    """List .md files in the given directory and return title + content."""
    if not path:
        return []
    dir_path = Path(path).expanduser().resolve()
    if not dir_path.is_dir():
        return []
    results = []
    for md_file in sorted(dir_path.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            title = md_file.stem
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            results.append({
                "name": md_file.name,
                "path": str(md_file),
                "title": title,
                "content": content,
            })
        except OSError:
            continue
    return results


# ===========================================================================
# WebSocket stream
# ===========================================================================

@app.websocket("/runs/{run_id}/stream")
async def stream_run(websocket: WebSocket, run_id: str):
    await websocket.accept()

    queue = register_ws_listener(run_id)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a ping to keep connection alive
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
                continue

            try:
                await websocket.send_json(event)
            except Exception:
                break

            # Close after terminal events
            if event.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        deregister_ws_listener(run_id, queue)


# ===========================================================================
# Static frontend (MUST be last — after all API routes)
# ===========================================================================

_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
