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
    status: str = "pending"           # "pending" | "running" | "review" | "done" | "failed"
    depends_on: Optional[str] = None  # comma-separated task IDs
    model: str = "ollama/qwen2.5-coder:32b"
    workspace: str
    order: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Run(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str
    status: str = "running"           # "running" | "completed" | "failed" | "aborted"
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    summary: Optional[str] = None
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
    workspace: str
    depends_on: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    spec_path: Optional[str] = None
    mode: Optional[str] = None
    status: Optional[str] = None
    model: Optional[str] = None
    workspace: Optional[str] = None
    depends_on: Optional[str] = None


class TaskReorder(BaseModel):
    task_ids: list[str]


class Settings(BaseModel):
    workspace: str = ""
    default_model: str = "ollama/qwen2.5-coder:32b"
    anthropic_api_key: Optional[str] = None
    ollama_host: str = "http://localhost:11434"
    mcp_server_host: str = "http://localhost:8080"
    require_bash_approval: bool = False
    theme: str = "dark"
    memory_model: str = "llama3.2"
