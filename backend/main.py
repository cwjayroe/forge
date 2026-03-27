"""
Forge FastAPI backend entry point.
Start with: uvicorn backend.main:app --reload
"""
import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .database import create_db_and_tables, engine, get_session, get_settings, save_settings
from .memory import MemoryClient
from .models import (
    Run,
    RunEvent,
    Settings,
    Task,
    TaskCreate,
    TaskReorder,
    TaskUpdate,
)
from .orchestrator import abort_run, deregister_ws_listener, register_ws_listener, start_run

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


@app.on_event("startup")
async def on_startup():
    global memory_client
    create_db_and_tables()
    memory_client = MemoryClient()


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
        workspace=payload.workspace,
        depends_on=payload.depends_on,
    )
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
    for field, value in payload.model_dump(exclude_unset=True).items():
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

    # Delete associated runs and events
    runs = session.exec(select(Run).where(Run.task_id == task_id)).all()
    for run in runs:
        events = session.exec(select(RunEvent).where(RunEvent.run_id == run.id)).all()
        for event in events:
            session.delete(event)
        session.delete(run)

    session.delete(task)
    session.commit()


@app.post("/tasks/{task_id}/run", status_code=201)
async def run_task(task_id: str, session: Session = Depends(get_session)):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status == "running":
        raise HTTPException(status_code=409, detail="Task is already running")

    # Create run record
    run = Run(task_id=task_id)
    session.add(run)

    # Update task status
    task.status = "running"
    task.updated_at = datetime.utcnow()
    session.add(task)

    session.commit()
    session.refresh(run)

    # Start agent in background
    await start_run(run.id, task, engine)

    return run


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
    return {
        **run.model_dump(),
        "events": [
            {**e.model_dump(), "content": json.loads(e.content)}
            for e in events
        ],
    }


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
# Memory
# ===========================================================================

@app.get("/memory/search")
async def search_memory(q: str):
    results = await memory_client.search(q)
    return results


@app.get("/memory/list")
async def list_memory():
    return await memory_client.list_all()


@app.delete("/memory/{memory_id}", status_code=204)
async def delete_memory(memory_id: str):
    deleted = await memory_client.delete(memory_id)
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
