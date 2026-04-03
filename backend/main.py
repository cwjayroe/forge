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
from sqlalchemy import func, or_

from fastapi.staticfiles import StaticFiles

from .database import create_db_and_tables, engine, get_session, get_settings, save_settings
from .memory import MemoryClient
from pathlib import Path

from .models import (
    ContextPack,
    ContextPackCreate,
    ContextPackUpdate,
    Project,
    ProjectCreate,
    ProjectUpdate,
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
    TaskTemplate,
    TaskTemplateCreate,
    TaskTemplateUpdate,
    TaskUpdate,
)
from .orchestrator import abort_run, deregister_ws_listener, is_pipeline_paused, is_window_paused, register_ws_listener, resolve_bash_approval, resolve_plan_approval, set_window_paused, start_run
from .providers import (
    ProviderIntegrationError,
    create_change_request,
    get_change_request_status,
)
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


def _parse_csv_values(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def _parse_iso_datetime(raw: Optional[str], field_name: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(400, f"Invalid {field_name}; expected ISO-8601 datetime") from exc


def _parse_json_content(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _parse_workspaces(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _project_payload(project: Project) -> dict:
    return {**project.model_dump(), "workspaces": _parse_workspaces(project.workspaces)}


def _find_latest_change_request_event(events: list[RunEvent]) -> Optional[dict]:
    for event in reversed(events):
        if event.type != "provider_change_request_created":
            continue
        payload = _parse_json_content(event.content)
        if isinstance(payload, dict):
            return payload
    return None


def _categorize_failure(text: str) -> tuple[str, list[str]]:
    haystack = (text or "").lower()
    rules: list[tuple[str, tuple[str, ...], list[str]]] = [
        (
            "test_failure",
            ("assert", "failed", "pytest", "test", "regression"),
            [
                "Inspect failing tests and stack traces for the first deterministic failure.",
                "Add or update regression coverage before retrying.",
                "Retry with the same configuration after addressing the failing test path.",
            ],
        ),
        (
            "missing_dependency",
            ("module not found", "no module named", "importerror", "command not found", "dependency"),
            [
                "Install or pin the missing dependency in project manifests.",
                "Re-run setup/bootstrap commands before retrying the task.",
                "Retry with an increased retry budget if installs are flaky in CI.",
            ],
        ),
        (
            "permission",
            ("permission denied", "not permitted", "operation not permitted", "eacces", "approval"),
            [
                "Review sandbox/approval settings for blocked commands.",
                "Switch to supervised mode if approvals are expected during this run.",
                "Retry once required permissions are granted.",
            ],
        ),
        (
            "timeout",
            ("timeout", "timed out", "deadline exceeded"),
            [
                "Split the task scope into smaller batches to reduce long-running steps.",
                "Increase retry budget to allow another execution attempt.",
                "Retry with a lighter QA model if validation latency is the bottleneck.",
            ],
        ),
    ]
    for category, markers, actions in rules:
        if any(marker in haystack for marker in markers):
            return category, actions
    return (
        "unknown",
        [
            "Open the event log and inspect the latest error details.",
            "Retry with supervised mode to monitor and guide agent actions.",
            "Try a QA model switch if failures repeat with the same settings.",
        ],
    )


def _build_failure_metadata(run: Run, phases: list[RunPhase], events: list[RunEvent]) -> Optional[dict]:
    if run.status != "failed":
        return None

    last_failed_phase = next((p for p in reversed(phases) if p.status == "failed"), None)
    recent_errors: list[str] = []
    for event in reversed(events):
        if event.type != "error":
            continue
        try:
            payload = json.loads(event.content)
        except Exception:
            payload = event.content
        if isinstance(payload, dict):
            recent_errors.append(str(payload.get("content") or payload))
        else:
            recent_errors.append(str(payload))
        if len(recent_errors) >= 3:
            break

    root_message = run.error or (last_failed_phase.error if last_failed_phase else "") or "\n".join(recent_errors)
    category, suggested_actions = _categorize_failure(root_message)
    return {
        "category": category,
        "phase": last_failed_phase.phase if last_failed_phase else run.current_phase,
        "message": (root_message or "Run failed without a detailed error message.")[:1000],
        "suggested_actions": suggested_actions,
        "recent_errors": recent_errors,
    }


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

_BUILTIN_TASK_TEMPLATES = [
    {
        "slug": "bugfix",
        "name": "Bugfix",
        "title_template": "Fix: <bug summary>",
        "description_template": (
            "## Goal\n"
            "Fix the reported bug and confirm behavior is corrected.\n\n"
            "## Context\n"
            "- Symptom:\n"
            "- Suspected area:\n"
            "- Repro steps:\n\n"
            "## Acceptance Criteria\n"
            "- [ ] Root cause identified\n"
            "- [ ] Fix implemented\n"
            "- [ ] Regression coverage added/updated\n"
        ),
        "mode": "autonomous",
        "model": "claude-code/sonnet",
        "max_retries": 3,
        "is_builtin": True,
    },
    {
        "slug": "test-writing",
        "name": "Test Writing",
        "title_template": "Add tests: <target area>",
        "description_template": (
            "## Goal\n"
            "Add focused tests that validate expected behavior and edge cases.\n\n"
            "## Scope\n"
            "- Target modules/files:\n"
            "- Existing gaps:\n\n"
            "## Acceptance Criteria\n"
            "- [ ] New tests are deterministic\n"
            "- [ ] Edge cases covered\n"
            "- [ ] Existing suite remains green\n"
        ),
        "mode": "autonomous",
        "model": "claude-code/sonnet",
        "max_retries": 2,
        "is_builtin": True,
    },
    {
        "slug": "refactor",
        "name": "Refactor",
        "title_template": "Refactor: <module/component>",
        "description_template": (
            "## Goal\n"
            "Improve code structure/readability without changing behavior.\n\n"
            "## Constraints\n"
            "- Preserve public APIs\n"
            "- Avoid behavior changes\n\n"
            "## Acceptance Criteria\n"
            "- [ ] Duplication reduced\n"
            "- [ ] Complexity reduced\n"
            "- [ ] Tests confirm no regressions\n"
        ),
        "mode": "supervised",
        "model": "claude-code/sonnet",
        "max_retries": 2,
        "is_builtin": True,
    },
    {
        "slug": "docs",
        "name": "Docs",
        "title_template": "Docs: <topic>",
        "description_template": (
            "## Goal\n"
            "Improve documentation clarity and completeness.\n\n"
            "## Targets\n"
            "- README sections:\n"
            "- API docs/docstrings:\n"
            "- Developer guidance:\n\n"
            "## Acceptance Criteria\n"
            "- [ ] Docs reflect current behavior\n"
            "- [ ] Examples are accurate\n"
            "- [ ] Links/paths validated\n"
        ),
        "mode": "autonomous",
        "model": "claude-code/haiku",
        "max_retries": 1,
        "is_builtin": True,
    },
    {
        "slug": "security-audit",
        "name": "Security Audit",
        "title_template": "Security audit: <area>",
        "description_template": (
            "## Goal\n"
            "Audit selected scope for security weaknesses and produce findings.\n\n"
            "## Scope\n"
            "- In-scope paths/services:\n"
            "- Out-of-scope areas:\n\n"
            "## Deliverable\n"
            "Structured report with severity, location, impact, and remediation guidance.\n"
        ),
        "mode": "supervised",
        "model": "claude-code/opus",
        "max_retries": 1,
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
        for data in _BUILTIN_TASK_TEMPLATES:
            if not session.exec(select(TaskTemplate).where(TaskTemplate.slug == data["slug"])).first():
                session.add(TaskTemplate(**data))
        session.commit()
    asyncio.create_task(_window_checker_loop())


# ===========================================================================
# Projects + Context Packs
# ===========================================================================

@app.get("/projects")
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    return [_project_payload(project) for project in projects]


@app.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)):
    if session.exec(select(Project).where(Project.slug == payload.slug)).first():
        raise HTTPException(status_code=409, detail="Project slug already exists")
    project = Project(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        workspaces=json.dumps(payload.workspaces),
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return _project_payload(project)


@app.put("/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    update_data = payload.model_dump(exclude_unset=True)
    if "workspaces" in update_data:
        update_data["workspaces"] = json.dumps(update_data["workspaces"] or [])
    for field, value in update_data.items():
        setattr(project, field, value)
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return _project_payload(project)


@app.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    context_packs = session.exec(select(ContextPack).where(ContextPack.project_id == project_id)).all()
    for pack in context_packs:
        session.delete(pack)
    project_tasks = session.exec(select(Task).where(Task.project_id == project_id)).all()
    for task in project_tasks:
        task.project_id = None
        session.add(task)
    session.delete(project)
    session.commit()


@app.get("/projects/{project_id}/context-packs")
def list_context_packs(project_id: str, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return session.exec(
        select(ContextPack).where(ContextPack.project_id == project_id).order_by(ContextPack.created_at.desc())
    ).all()


@app.post("/projects/{project_id}/context-packs", status_code=201)
def create_context_pack(project_id: str, payload: ContextPackCreate, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    pack = ContextPack(
        project_id=project_id,
        name=payload.name,
        content=payload.content,
        workspace_hint=payload.workspace_hint,
    )
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack


@app.put("/projects/{project_id}/context-packs/{pack_id}")
def update_context_pack(
    project_id: str,
    pack_id: str,
    payload: ContextPackUpdate,
    session: Session = Depends(get_session),
):
    pack = session.get(ContextPack, pack_id)
    if not pack or pack.project_id != project_id:
        raise HTTPException(status_code=404, detail="Context pack not found")
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(pack, field, value)
    pack.updated_at = datetime.utcnow()
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack


@app.delete("/projects/{project_id}/context-packs/{pack_id}", status_code=204)
def delete_context_pack(project_id: str, pack_id: str, session: Session = Depends(get_session)):
    pack = session.get(ContextPack, pack_id)
    if not pack or pack.project_id != project_id:
        raise HTTPException(status_code=404, detail="Context pack not found")
    session.delete(pack)
    session.commit()


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
# Task templates
# ===========================================================================

@app.get("/task-templates")
def list_task_templates(session: Session = Depends(get_session)):
    return session.exec(
        select(TaskTemplate).order_by(TaskTemplate.is_builtin.desc(), TaskTemplate.name)
    ).all()


@app.post("/task-templates", status_code=201)
def create_task_template(payload: TaskTemplateCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(TaskTemplate).where(TaskTemplate.slug == payload.slug)).first()
    if existing:
        raise HTTPException(400, f"Task template with slug '{payload.slug}' already exists")
    tpl = TaskTemplate(**payload.model_dump())
    session.add(tpl)
    session.commit()
    session.refresh(tpl)
    return tpl


@app.put("/task-templates/{template_id}")
def update_task_template(template_id: str, payload: TaskTemplateUpdate, session: Session = Depends(get_session)):
    tpl = session.get(TaskTemplate, template_id)
    if not tpl:
        raise HTTPException(404, "Task template not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(tpl, k, v)
    tpl.updated_at = datetime.utcnow()
    session.add(tpl)
    session.commit()
    session.refresh(tpl)
    return tpl


@app.delete("/task-templates/{template_id}", status_code=204)
def delete_task_template(template_id: str, session: Session = Depends(get_session)):
    tpl = session.get(TaskTemplate, template_id)
    if not tpl:
        raise HTTPException(404, "Task template not found")
    if tpl.is_builtin:
        raise HTTPException(400, "Cannot delete built-in task template")
    session.delete(tpl)
    session.commit()


# ===========================================================================
# Tasks
# ===========================================================================

@app.get("/tasks")
def list_tasks(session: Session = Depends(get_session)):
    tasks = session.exec(select(Task).order_by(Task.order, Task.created_at)).all()
    return tasks


@app.get("/tasks/search")
def search_tasks(
    q: Optional[str] = None,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    workspace: Optional[str] = None,
    failure_only: bool = False,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    statuses = _parse_csv_values(status)
    modes = _parse_csv_values(mode)
    created_after_dt = _parse_iso_datetime(created_after, "created_after")
    created_before_dt = _parse_iso_datetime(created_before, "created_before")

    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(400, "offset must be >= 0")

    query = select(Task)
    count_query = select(func.count()).select_from(Task)

    if q:
        pattern = f"%{q}%"
        condition = or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
        query = query.where(condition)
        count_query = count_query.where(condition)
    if statuses:
        condition = Task.status.in_(statuses)
        query = query.where(condition)
        count_query = count_query.where(condition)
    if modes:
        condition = Task.mode.in_(modes)
        query = query.where(condition)
        count_query = count_query.where(condition)
    if workspace:
        condition = Task.workspace == workspace
        query = query.where(condition)
        count_query = count_query.where(condition)
    if failure_only:
        condition = Task.status == "failed"
        query = query.where(condition)
        count_query = count_query.where(condition)
    if created_after_dt:
        condition = Task.created_at >= created_after_dt
        query = query.where(condition)
        count_query = count_query.where(condition)
    if created_before_dt:
        condition = Task.created_at <= created_before_dt
        query = query.where(condition)
        count_query = count_query.where(condition)

    if sort == "oldest":
        query = query.order_by(Task.created_at.asc())
    else:
        query = query.order_by(Task.created_at.desc())

    items = session.exec(query.offset(offset).limit(limit)).all()
    total = session.exec(count_query).one()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.post("/tasks", status_code=201)
def create_task(payload: TaskCreate, session: Session = Depends(get_session)):
    if payload.project_id:
        project = session.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        workspaces = _parse_workspaces(project.workspaces)
        if workspaces and payload.workspace not in workspaces:
            raise HTTPException(status_code=400, detail="Workspace is not part of selected project")

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
        project_id=payload.project_id,
        depends_on=payload.depends_on,
        skill_id=payload.skill_id,
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
    next_project_id = update_data.get("project_id", task.project_id)
    next_workspace = update_data.get("workspace", task.workspace)
    if next_project_id:
        project = session.get(Project, next_project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        workspaces = _parse_workspaces(project.workspaces)
        if workspaces and next_workspace not in workspaces:
            raise HTTPException(status_code=400, detail="Workspace is not part of selected project")
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


@app.get("/runs/search")
def search_runs(
    q: Optional[str] = None,
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    phase: Optional[str] = None,
    workspace: Optional[str] = None,
    failure_only: bool = False,
    started_after: Optional[str] = None,
    started_before: Optional[str] = None,
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    statuses = _parse_csv_values(status)
    phases = _parse_csv_values(phase)
    started_after_dt = _parse_iso_datetime(started_after, "started_after")
    started_before_dt = _parse_iso_datetime(started_before, "started_before")

    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(400, "offset must be >= 0")

    query = select(Run, Task).join(Task, Task.id == Run.task_id)
    count_query = select(func.count()).select_from(Run).join(Task, Task.id == Run.task_id)

    if q:
        pattern = f"%{q}%"
        condition = or_(
            Run.summary.ilike(pattern),
            Run.error.ilike(pattern),
            Task.title.ilike(pattern),
            Task.description.ilike(pattern),
        )
        query = query.where(condition)
        count_query = count_query.where(condition)
    if task_id:
        condition = Run.task_id == task_id
        query = query.where(condition)
        count_query = count_query.where(condition)
    if statuses:
        condition = Run.status.in_(statuses)
        query = query.where(condition)
        count_query = count_query.where(condition)
    if phases:
        condition = Run.current_phase.in_(phases)
        query = query.where(condition)
        count_query = count_query.where(condition)
    if workspace:
        condition = Task.workspace == workspace
        query = query.where(condition)
        count_query = count_query.where(condition)
    if failure_only:
        condition = Run.status == "failed"
        query = query.where(condition)
        count_query = count_query.where(condition)
    if started_after_dt:
        condition = Run.started_at >= started_after_dt
        query = query.where(condition)
        count_query = count_query.where(condition)
    if started_before_dt:
        condition = Run.started_at <= started_before_dt
        query = query.where(condition)
        count_query = count_query.where(condition)

    if sort == "oldest":
        query = query.order_by(Run.started_at.asc())
    else:
        query = query.order_by(Run.started_at.desc())

    rows = session.exec(query.offset(offset).limit(limit)).all()
    items = []
    for run, task in rows:
        failure_category = None
        if run.status == "failed":
            failure_category, _ = _categorize_failure(run.error or "")
        items.append({**run.model_dump(), "task": task.model_dump(), "failure_category": failure_category})
    total = session.exec(count_query).one()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


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
    failure_metadata = _build_failure_metadata(run, phases, events)
    return {
        **run.model_dump(),
        "failure_metadata": failure_metadata,
        "events": [
            {**e.model_dump(), "content": _parse_json_content(e.content)}
            for e in events
        ],
        "phases": [p.model_dump() for p in phases],
    }


class CreateProviderChangeRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    base_branch: Optional[str] = None
    labels: Optional[list[str]] = None


@app.post("/runs/{run_id}/provider/change-request")
async def create_provider_change_request(
    run_id: str,
    payload: CreateProviderChangeRequest,
    session: Session = Depends(get_session),
):
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    task = session.get(Task, run.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not run.branch_name:
        raise HTTPException(status_code=409, detail="Run has no branch to open a PR/MR from")

    settings = get_settings()
    if not settings.get("provider_integration_enabled", False):
        raise HTTPException(status_code=409, detail="Provider integration is disabled in settings")

    labels = payload.labels
    if labels is None:
        labels = _parse_csv_values(settings.get("provider_default_labels"))

    try:
        created = await create_change_request(
            provider_type=settings.get("provider_type", "github"),
            api_base_url=settings.get("provider_api_base_url"),
            token=settings.get("provider_token") or "",
            repo_slug=settings.get("provider_repo") or "",
            head_branch=run.branch_name,
            base_branch=payload.base_branch or settings.get("provider_default_base_branch", "main"),
            title=payload.title or task.title,
            description=payload.description or task.description,
            labels=labels,
        )
    except ProviderIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    event_payload = {
        "type": "provider_change_request_created",
        "provider": created["provider"],
        "number": created["number"],
        "url": created["url"],
        "state": created["state"],
    }
    session.add(
        RunEvent(
            run_id=run_id,
            type="provider_change_request_created",
            content=json.dumps(event_payload),
        )
    )
    session.commit()
    return event_payload


@app.get("/runs/{run_id}/provider/change-request/status")
async def get_provider_change_request_status(run_id: str, session: Session = Depends(get_session)):
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    settings = get_settings()
    if not settings.get("provider_integration_enabled", False):
        raise HTTPException(status_code=409, detail="Provider integration is disabled in settings")

    events = session.exec(
        select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)
    ).all()
    latest_event = _find_latest_change_request_event(events)
    if not latest_event:
        raise HTTPException(status_code=404, detail="No provider change request linked to this run")
    try:
        status_payload = await get_change_request_status(
            provider_type=settings.get("provider_type", "github"),
            api_base_url=settings.get("provider_api_base_url"),
            token=settings.get("provider_token") or "",
            repo_slug=settings.get("provider_repo") or "",
            number=int(latest_event["number"]),
        )
    except ProviderIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return status_payload


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


@app.get("/run-events/search")
def search_run_events(
    q: Optional[str] = None,
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
    event_type: Optional[str] = None,
    started_after: Optional[str] = None,
    started_before: Optional[str] = None,
    sort: str = "newest",
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    event_types = _parse_csv_values(event_type)
    started_after_dt = _parse_iso_datetime(started_after, "started_after")
    started_before_dt = _parse_iso_datetime(started_before, "started_before")

    if limit < 1 or limit > 300:
        raise HTTPException(400, "limit must be between 1 and 300")
    if offset < 0:
        raise HTTPException(400, "offset must be >= 0")

    query = (
        select(RunEvent, Run, Task)
        .join(Run, Run.id == RunEvent.run_id)
        .join(Task, Task.id == Run.task_id)
    )
    count_query = (
        select(func.count())
        .select_from(RunEvent)
        .join(Run, Run.id == RunEvent.run_id)
        .join(Task, Task.id == Run.task_id)
    )

    if q:
        pattern = f"%{q}%"
        condition = RunEvent.content.ilike(pattern)
        query = query.where(condition)
        count_query = count_query.where(condition)
    if run_id:
        condition = RunEvent.run_id == run_id
        query = query.where(condition)
        count_query = count_query.where(condition)
    if task_id:
        condition = Run.task_id == task_id
        query = query.where(condition)
        count_query = count_query.where(condition)
    if event_types:
        condition = RunEvent.type.in_(event_types)
        query = query.where(condition)
        count_query = count_query.where(condition)
    if started_after_dt:
        condition = RunEvent.timestamp >= started_after_dt
        query = query.where(condition)
        count_query = count_query.where(condition)
    if started_before_dt:
        condition = RunEvent.timestamp <= started_before_dt
        query = query.where(condition)
        count_query = count_query.where(condition)

    if sort == "oldest":
        query = query.order_by(RunEvent.timestamp.asc())
    else:
        query = query.order_by(RunEvent.timestamp.desc())

    rows = session.exec(query.offset(offset).limit(limit)).all()
    items = [
        {
            **event.model_dump(),
            "run_status": run.status,
            "task_id": task.id,
            "task_title": task.title,
        }
        for event, run, task in rows
    ]
    total = session.exec(count_query).one()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


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
    try:
        parsed_rules = json.loads(payload.quality_gate_rules or "[]")
        if not isinstance(parsed_rules, list):
            raise ValueError("quality_gate_rules must decode to a JSON array")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid quality_gate_rules JSON: {exc}") from exc
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
