import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
from pydantic import BaseModel


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# DB tables
# ---------------------------------------------------------------------------

class Skill(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str                              # "Write Tests"
    slug: str                              # "write-tests" (unique, used for seeding)
    icon: str = "🛠️"                       # emoji shown in UI
    description: str = ""                 # user-facing one-liner
    prompt_addon: Optional[str] = None    # appended to build system prompt (Ollama/Anthropic)
    claude_code_skill: Optional[str] = None  # e.g. "/write-tests" — replaces /feature-plan-and-build
    cursor_skill: Optional[str] = None        # same for Cursor CLI
    template_description: Optional[str] = None  # pre-fills task description on select
    is_builtin: bool = False
    created_at: datetime = Field(default_factory=_now)


class Task(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    title: str
    description: str
    spec_path: Optional[str] = None
    mode: str = "autonomous"          # "autonomous" | "supervised"
    status: str = "pending"           # "pending" | "planning" | "building" | "qa" | "review" | "done" | "failed"
    depends_on: Optional[str] = None  # comma-separated task IDs
    model: str = "ollama/qwen2.5-coder:latest"          # build model (ollama/*, anthropic/*, claude-code/*, cursor-code/*)
    plan_model: Optional[str] = None                   # planning phase model (falls back to model)
    qa_model: Optional[str] = None                     # QA phase model (falls back to model)
    max_retries: int = 3                               # max build→QA retry cycles
    workspace: str
    order: int = 0
    branch_name: Optional[str] = None                 # git branch created for this task
    skill_id: Optional[str] = None                    # FK to Skill.id
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Run(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str
    build_id: Optional[str] = None    # slugified title + timestamp hash for memory key schema
    status: str = "running"           # "running" | "completed" | "failed" | "aborted"
    current_phase: Optional[str] = None  # "plan" | "validate" | "build" | "review" | "qa"
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    test_baseline: Optional[str] = None  # pre-build test results for QA regression attribution
    architecture_snapshot: Optional[str] = None  # stored by planner, used by builders/reviewers
    branch_name: Optional[str] = None  # git branch created for this run


class RunPhase(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str
    phase: str                        # "plan" | "validate" | "build" | "review" | "qa"
    attempt: int = 1                  # which attempt (for build/qa retries)
    batch: Optional[int] = None       # batch number within a phase (for parallel builds)
    task_index: Optional[int] = None  # task index within a batch (for per-task tracking)
    status: str = "running"           # "running" | "completed" | "failed"
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    artifact: Optional[str] = None    # phase output (plan doc, QA report, build summary)
    error: Optional[str] = None


class RunEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str
    type: str                         # "text" | "tool_call" | "tool_result" | "file_change" | "error"
    content: str                      # JSON blob
    timestamp: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    title: str
    description: str
    spec_path: Optional[str] = None
    mode: str = "autonomous"
    model: str = "ollama/qwen2.5-coder:latest"
    plan_model: Optional[str] = None
    qa_model: Optional[str] = None
    max_retries: int = 3
    workspace: str
    depends_on: Optional[str] = None
    skill_id: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    spec_path: Optional[str] = None
    mode: Optional[str] = None
    status: Optional[str] = None
    model: Optional[str] = None
    plan_model: Optional[str] = None
    qa_model: Optional[str] = None
    max_retries: Optional[int] = None
    workspace: Optional[str] = None
    depends_on: Optional[str] = None
    skill_id: Optional[str] = None


class SkillCreate(BaseModel):
    name: str
    slug: str
    icon: str = "🛠️"
    description: str = ""
    prompt_addon: Optional[str] = None
    claude_code_skill: Optional[str] = None
    cursor_skill: Optional[str] = None
    template_description: Optional[str] = None


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    prompt_addon: Optional[str] = None
    claude_code_skill: Optional[str] = None
    cursor_skill: Optional[str] = None
    template_description: Optional[str] = None


class TaskReorder(BaseModel):
    task_ids: list[str]


class Settings(BaseModel):
    workspace: str = ""
    default_model: str = "ollama/qwen2.5-coder:latest"  # ollama/*, anthropic/*, claude-code/*
    default_plan_model: Optional[str] = None
    default_qa_model: Optional[str] = None
    max_concurrent_tasks: int = 3
    max_concurrent_builders: int = 3          # max parallel builder agents per batch
    anthropic_api_key: Optional[str] = None
    ollama_host: str = "http://localhost:11434"
    mcp_server_host: str = "http://localhost:8080"
    require_bash_approval: bool = False
    capture_test_baseline: bool = True        # run tests before build to capture baseline
    theme: str = "dark"
    memory_model: str = "llama3.2"
    slack_webhook_url: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    generic_webhook_url: Optional[str] = None
    notify_on_complete: bool = True
    notify_on_failure: bool = True
    notify_on_approval: bool = False
    schedule_enabled: bool = False
    schedule_window_start: str = "22:00"   # 24h HH:MM, local server time
    schedule_window_end: str = "06:00"     # 24h HH:MM — can cross midnight
    schedule_days: str = "0,1,2,3,4,5,6"  # comma-separated 0=Mon…6=Sun
