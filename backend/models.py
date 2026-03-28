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

class Task(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    title: str
    description: str
    spec_path: Optional[str] = None
    mode: str = "autonomous"          # "autonomous" | "supervised"
    status: str = "pending"           # "pending" | "planning" | "building" | "qa" | "review" | "done" | "failed"
    depends_on: Optional[str] = None  # comma-separated task IDs
    model: str = "ollama/qwen2.5-coder:32b"          # build model
    plan_model: Optional[str] = None                   # planning phase model (falls back to model)
    qa_model: Optional[str] = None                     # QA phase model (falls back to model)
    max_retries: int = 3                               # max build→QA retry cycles
    workspace: str
    order: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Run(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str
    status: str = "running"           # "running" | "completed" | "failed" | "aborted"
    current_phase: Optional[str] = None  # "plan" | "build" | "qa"
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    summary: Optional[str] = None
    error: Optional[str] = None


class RunPhase(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str
    phase: str                        # "plan" | "build" | "qa"
    attempt: int = 1                  # which attempt (for build/qa retries)
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
    model: str = "ollama/qwen2.5-coder:32b"
    plan_model: Optional[str] = None
    qa_model: Optional[str] = None
    max_retries: int = 3
    workspace: str
    depends_on: Optional[str] = None


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


class TaskReorder(BaseModel):
    task_ids: list[str]


class Settings(BaseModel):
    workspace: str = ""
    default_model: str = "ollama/qwen2.5-coder:32b"
    default_plan_model: Optional[str] = None
    default_qa_model: Optional[str] = None
    max_concurrent_tasks: int = 3
    anthropic_api_key: Optional[str] = None
    ollama_host: str = "http://localhost:11434"
    mcp_server_host: str = "http://localhost:8080"
    require_bash_approval: bool = False
    theme: str = "dark"
    memory_model: str = "llama3.2"
