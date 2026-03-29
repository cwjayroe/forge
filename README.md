# Forge

**Forge** is an AI coding agent orchestrator. It automates software development tasks by running AI agents through a structured **Plan → Validate → Build → Review → QA** pipeline, with a visual task board, real-time streaming output, dependency scheduling, and persistent memory.

Forge runs as a desktop app (Electron) or as a standalone backend + web frontend. Agents can use local models via [Ollama](https://ollama.com), cloud models via the [Anthropic API](https://www.anthropic.com), or CLI-based tools like Claude Code and Cursor.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Running Forge](#running-forge)
- [Configuration](#configuration)
- [Creating and Running Tasks](#creating-and-running-tasks)
- [The Pipeline](#the-pipeline)
- [Task Dependencies](#task-dependencies)
- [Agent Tools](#agent-tools)
- [Memory System](#memory-system)
- [Bash Approval](#bash-approval)
- [Supervised Mode](#supervised-mode)
- [REST API Reference](#rest-api-reference)
- [WebSocket Streaming](#websocket-streaming)
- [Data Storage](#data-storage)
- [Building for Distribution](#building-for-distribution)
- [Development](#development)

---

## Features

- **Five-phase pipeline** — agents plan (2-pass), validate the plan, build in parallel batches, review each batch, and QA the full result
- **Automatic QA retries** — failed QA sends feedback back into the build phase (up to N retries)
- **Batched parallel builds** — builder agents run concurrently within each phase batch, up to a configurable limit
- **DAG dependency scheduling** — tasks wait for their dependencies before starting; cycle detection prevents invalid graphs
- **Concurrency control** — run multiple tasks in parallel up to a configurable limit
- **Real-time WebSocket streaming** — watch every agent thought, tool call, and file change live
- **Persistent memory** — completed task summaries are stored and injected as context for future runs
- **Upstream context propagation** — dependent tasks automatically receive the plan artifacts of their dependencies
- **Test baseline capture** — pre-build test results are captured so QA can distinguish regressions from pre-existing failures
- **Bash approval gate** — optionally require human approval before the agent executes any shell command
- **Plan approval gate** — in supervised mode, the plan is presented for review before building begins
- **Supervised mode** — tasks land in a "Review" state instead of "Done", requiring manual sign-off
- **Multiple model providers** — Ollama (local), Anthropic Claude (cloud), Claude Code CLI, Cursor; mix per phase
- **Desktop app** — ships as an Electron app (AppImage, .deb, .dmg)

---

## Architecture

```
┌─────────────────────────────────────┐
│  Frontend  (React + Vite)           │
│  Task Board · Run View · Memory     │
│  Dependency Graph · Settings        │
└──────────────┬──────────────────────┘
               │  HTTP / WebSocket
               ▼
┌─────────────────────────────────────┐
│  FastAPI Backend                    │
│                                     │
│  Orchestrator ──► Agent Loop        │
│  Scheduler    ──► Model Adapters    │
│  Memory Client    (Ollama / Claude  │
│                    Claude Code /    │
│                    Cursor)          │
│                                     │
│  SQLite (SQLModel ORM)              │
└─────────────────────────────────────┘
               │
               ▼ (desktop only)
┌─────────────────────────────────────┐
│  Electron Shell                     │
│  Spawns backend · Manages window    │
└─────────────────────────────────────┘
```

### Key Components

| Component | File | Role |
|-----------|------|------|
| API server | `backend/main.py` | FastAPI app, all REST endpoints, WebSocket stream |
| Database models | `backend/models.py` | SQLModel ORM (Task, Run, RunPhase, RunEvent) + Pydantic schemas |
| Database / settings | `backend/database.py` | SQLite engine, settings file I/O |
| Orchestrator | `backend/orchestrator.py` | Runs the full pipeline, broadcasts events, manages approval gates |
| Scheduler | `backend/scheduler.py` | DAG dependency resolution, concurrency control |
| Agent loop | `backend/agent/loop.py` | LLM conversation loop, tool dispatch, abort handling |
| Agent tools | `backend/agent/tools.py` | File I/O, bash, search, memory — all sandboxed to workspace |
| Prompts | `backend/agent/prompts.py` | Phase-specific system prompts and allowed tool sets |
| Ollama adapter | `backend/agent/adapters/ollama.py` | OpenAI-compatible API adapter for local Ollama |
| Anthropic adapter | `backend/agent/adapters/anthropic.py` | Anthropic Claude API adapter |
| Claude Code adapter | `backend/agent/adapters/claude_code.py` | Claude Code CLI adapter |
| Cursor adapter | `backend/agent/adapters/cursor.py` | Cursor editor adapter |
| Memory client | `backend/memory.py` | Async wrapper around `memory-core`; falls back to JSON |
| Electron shell | `electron/main.js` | Desktop app lifecycle, spawns uvicorn |

For a deeper dive into the architecture, see [docs/architecture.md](docs/architecture.md).

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** and **npm**
- **Ollama** (optional) — required if using local models; install from [ollama.com](https://ollama.com)
- **Anthropic API key** (optional) — required if using `anthropic/` models
- `ripgrep` (`rg`) — optional but recommended for fast file search inside agent runs

---

## Installation

```bash
# Clone the repo
git clone <repo-url>
cd forge

# Install Python dependencies
pip install -r requirements.txt

# Install Node dependencies
npm install

# Install frontend dependencies
cd frontend && npm install && cd ..
```

---

## Running Forge

### Desktop app (Electron)

```bash
npm start
```

Electron starts, spawns the Python backend on `http://127.0.0.1:8000`, and opens the UI in a window. The backend process is managed automatically and shut down when you close the app.

### Development mode (backend + frontend separately)

**Terminal 1 — backend:**
```bash
npm run dev:backend
# or: uvicorn backend.main:app --reload
# Backend runs at http://localhost:8000
```

**Terminal 2 — frontend dev server:**
```bash
npm run dev:frontend
# Frontend runs at http://localhost:5173 (proxied to backend)
```

### Data directory

By default, Forge stores its SQLite database (`forge.db`) and settings file (`forge_settings.json`) in the current working directory. Override with the `FORGE_DATA_DIR` environment variable:

```bash
FORGE_DATA_DIR=/var/lib/forge uvicorn backend.main:app
```

---

## Configuration

Open **Settings** in the UI (keyboard shortcut: `s`) or `PUT /settings` to configure:

| Setting | Default | Description |
|---------|---------|-------------|
| `workspace` | `""` | Default workspace directory for new tasks |
| `default_model` | `ollama/qwen2.5-coder:latest` | Default model for the build phase |
| `default_plan_model` | _(uses default_model)_ | Model used for the plan phase |
| `default_qa_model` | _(uses default_model)_ | Model used for the QA phase |
| `max_concurrent_tasks` | `3` | Max tasks running in parallel |
| `max_concurrent_builders` | `3` | Max parallel builder agents per batch within a task |
| `anthropic_api_key` | _(empty)_ | API key for Anthropic Claude models |
| `ollama_host` | `http://localhost:11434` | URL of your Ollama server |
| `mcp_server_host` | `http://localhost:8080` | MCP server endpoint (for claude-code adapter) |
| `require_bash_approval` | `false` | Pause and ask before each shell command |
| `capture_test_baseline` | `true` | Run tests before build to capture a regression baseline |
| `theme` | `dark` | UI theme (`dark` or `light`) |
| `memory_model` | `llama3.2` | Ollama model used for memory embeddings |

### Model strings

Models are specified as `provider/model-name`. If no provider prefix is given, Ollama is assumed.

```
ollama/qwen2.5-coder:latest  # local Ollama
ollama/llama3.1:70b
anthropic/claude-opus-4-6    # Anthropic cloud
anthropic/claude-sonnet-4-6
claude-code/claude-sonnet-4-6  # Claude Code CLI
cursor-code/gpt-4o             # Cursor editor
```

Per-task overrides for plan and QA phases let you use a cheaper/faster model for those phases while using a more capable model for building.

---

## Creating and Running Tasks

### UI workflow

1. Press `n` or click **New Task** to open the task editor.
2. Fill in:
   - **Title** — short name for the task
   - **Description** — what the agent should do (be specific)
   - **Workspace** — absolute path to the directory the agent will work in
   - **Model** — build-phase model (e.g. `ollama/qwen2.5-coder:latest`)
   - **Plan model** / **QA model** — optional per-phase overrides
   - **Max retries** — how many build→QA cycles before giving up (default 3)
   - **Spec file** — optional path to a spec/requirements file to inject into prompts
   - **Dependencies** — other tasks that must complete before this one starts
   - **Mode** — `autonomous` (auto-complete) or `supervised` (requires plan approval and manual sign-off after QA)
3. Click **Save**.
4. Click **Start Pipeline** on the task board to begin execution.
5. Click any task card to open the **Run View** and watch the live output stream.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `n` | New task |
| `b` | Task board |
| `m` | Memory browser |
| `g` | Dependency graph |
| `s` | Settings |

---

## The Pipeline

Every task runs through five phases executed by separate agent invocations. See [docs/architecture.md](docs/architecture.md) for a detailed breakdown.

### 1. Plan

The planning agent performs a **two-pass analysis**:

- **Pass 1** — deep codebase exploration: reads files, traces imports, catalogs patterns, identifies high-risk files, and stores findings to memory.
- **Pass 2** — produces a structured, phased build plan with enriched per-task specs (interface contracts, preserve lists, test strategies, pattern references).

The plan artifact is passed to subsequent phases. If planning fails, the task fails immediately (no retries).

### 2. Validate

The plan validator checks the plan for internal consistency before any code is written:

- Dependency graph: all `depends_on` entries reference files that will exist by that phase
- Interface contract alignment: every `consumes` has a matching `produces` with compatible signatures
- Completeness: every requirement from the spec maps to at least one task
- File conflicts: no two tasks in the same phase modify the same file
- Preservation consistency: no task spec contradicts a `preserve` entry in another task

If validation fails (any issue found), the task fails without proceeding to build.

**In supervised mode**, the plan is presented to the user for approval after validation passes. The pipeline waits up to 10 minutes for a response via `POST /runs/{id}/plan/approve`.

### 3. Build

Builder agents execute the plan in **batches**. Tasks within the same plan phase run in parallel (up to `max_concurrent_builders` agents at once). Each builder receives:

- Its specific task spec (with interface contracts, preserve list, pattern references)
- The full architecture snapshot stored by the planner
- QA feedback from the previous attempt, if this is a retry

All file tools including `write_file` and `run_bash` are available. Builders must call `write_file` for every file they change.

### 4. Review

After each build batch completes, a **reviewer agent** checks the batch's output for correctness:

- Import validation: every local import resolves to an existing name
- Interface boundary check: imported names still exist with compatible signatures
- Regression check: modified files haven't broken callers; `preserve` lists are intact
- Spec compliance: required functions/classes exist with the right signatures
- Interface contract validation: every `produces` and `consumes` entry is satisfied
- Cross-file consistency: shared types and naming are consistent across the batch

If the reviewer finds issues (`VERDICT: FAIL`), the feedback is injected into the next build retry along with any QA feedback.

### 5. QA

The QA agent verifies the complete implementation against the plan and task requirements:

1. Confirms files were actually changed (`git diff --stat HEAD`)
2. Runs the project's test suite
3. Compares results against the pre-build baseline to distinguish regressions from pre-existing failures
4. Checks import validity for all new modules
5. Audits completeness against the plan

The QA agent's output must begin with `VERDICT: PASS` for the task to succeed.

### Retry loop

```
if capture_test_baseline: run tests to capture pre-build baseline

for attempt = 1 to max_retries:
    run build phase in batches
    for each batch:
        run build agents in parallel
        run reviewer agent
        if reviewer FAIL: record feedback
    run QA phase
    if QA output starts with "VERDICT: PASS":
        task succeeds
    else:
        save QA feedback (+ reviewer feedback), continue

if all retries exhausted → task fails
```

After a successful run the build summary is stored in memory for future tasks to reference.

---

## Task Dependencies

Tasks can declare dependencies on other tasks. The scheduler enforces:

- A task stays `pending` until all its dependencies reach `done`
- Cycle detection prevents invalid dependency graphs (validated at create/update time)
- Up to `max_concurrent_tasks` tasks run simultaneously

Dependency context is automatically injected: the plan artifacts and summaries from completed upstream tasks are included in the prompt of dependent tasks.

The **Dependency Graph** view (`g`) shows all tasks as a DAG for easy visualization.

---

## Agent Tools

All file system tools are sandboxed to the task's workspace directory. Attempts to read or write outside the workspace are rejected.

| Tool | Phases | Description |
|------|--------|-------------|
| `read_file` | plan, build, review, QA | Read a file relative to the workspace root |
| `write_file` | build | Write/overwrite a file; returns a unified diff |
| `list_files` | plan, build, review, QA | List files in a directory; respects `.gitignore` via `git ls-files` |
| `run_bash` | build, QA | Run a shell command in the workspace (30s timeout) |
| `search_files` | plan, validate, build, review, QA | Regex search across all files (uses `rg` or `grep`) |
| `search_memory` | plan, validate, build, review, QA | Retrieve relevant context from previous runs |
| `store_memory` | plan, build, QA | Persist information for future runs |

---

## Memory System

Forge maintains a persistent memory store. After each successful run, the build summary is stored with metadata (task title, run ID, task ID). The planner also stores its codebase analysis and architecture snapshot during Pass 1/2. Future agents can call `search_memory` to retrieve relevant past context.

The memory backend uses `memory-core` when available. If it is not installed or fails to initialize, Forge falls back to a local JSON file.

The **Memory Browser** (`m`) lets you search and delete stored memories.

---

## Bash Approval

When `require_bash_approval` is enabled, the agent pauses before every `run_bash` call and emits a `bash_approval_request` event. The Run View displays the pending command and prompts you to **Approve** or **Deny**. The approval gate times out after 5 minutes if no response is given.

This is useful when you want the agent to be able to run tests and build tools but you want visibility before it executes arbitrary shell commands.

---

## Supervised Mode

Set a task's mode to `supervised` for two additional checkpoints:

1. **Plan approval** — after the plan passes validation, execution pauses and a `plan_approval_request` event is emitted. You approve or deny via `POST /runs/{id}/plan/approve`. Timeout is 10 minutes.
2. **Post-QA sign-off** — after QA passes, the task moves to `review` status instead of `done`. The scheduler will not start dependent tasks until you manually approve the task.

---

## REST API Reference

The backend exposes a REST API at `http://localhost:8000`. Interactive docs are available at `/docs` (Swagger UI) and `/redoc`.

### Tasks

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/tasks` | List all tasks |
| `POST` | `/tasks` | Create a task |
| `PUT` | `/tasks/{id}` | Update a task |
| `DELETE` | `/tasks/{id}` | Delete a task and its runs |
| `POST` | `/tasks/reorder` | Reorder tasks (accepts `{task_ids: [...]}`) |
| `POST` | `/tasks/{id}/run` | Manually trigger a run for a specific task |

### Runs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/runs` | List all runs (optional `?task_id=` filter) |
| `GET` | `/runs/{id}` | Get a run with its phases and events |
| `GET` | `/runs/{id}/phases` | Get the phase records for a run |
| `POST` | `/runs/{id}/abort` | Abort a running task |
| `POST` | `/runs/{id}/bash/approve` | Approve a pending bash command (`{approved: true/false}`) |
| `POST` | `/runs/{id}/plan/approve` | Approve or deny a pending plan (`{approved: true/false}`) |

### Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/pipeline/start` | Start the pipeline (launch all ready tasks) |
| `POST` | `/pipeline/pause` | Pause the scheduler |
| `POST` | `/pipeline/resume` | Resume the scheduler |
| `GET` | `/pipeline/status` | Get task status counts and pipeline state |

### Memory

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/memory/list` | List all memory entries (optional `?project_id=`) |
| `GET` | `/memory/search?q=...` | Search memory by query (optional `?project_id=`) |
| `GET` | `/memory/projects` | List available memory projects |
| `DELETE` | `/memory/{id}` | Delete a memory entry (optional `?project_id=`) |

### Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/settings` | Get current settings |
| `PUT` | `/settings` | Update settings |

### Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/templates?path=...` | List `.md` template files in a directory |

---

## WebSocket Streaming

Connect to `ws://localhost:8000/runs/{run_id}/stream` to receive real-time events for a run.

Each message is a JSON object with a `type` field:

| Type | Payload | Description |
|------|---------|-------------|
| `phase_start` | `{phase, attempt}` | A pipeline phase has started |
| `phase_end` | `{phase, attempt, status}` | A pipeline phase has finished |
| `text` | `{content}` | Agent text output |
| `tool_call` | `{name, input}` | Agent is calling a tool |
| `tool_result` | `{name, output}` | Tool returned a result |
| `file_change` | `{path, diff}` | A file was written (includes unified diff) |
| `bash_approval_request` | `{command}` | Agent is waiting for bash approval |
| `plan_approval_request` | `{plan_summary}` | Supervised mode: plan is waiting for approval |
| `error` | `{content}` | An error occurred |
| `done` | `{status}` | Run finished; `status` is `completed`, `failed`, `aborted`, or `review` |
| `ping` | _(none)_ | Keepalive heartbeat (sent every 30s if idle) |

All events are also persisted to the `RunEvent` table in SQLite, so you can replay them after the fact via `GET /runs/{id}`.

---

## Data Storage

Forge uses SQLite. The schema has four tables:

- **`task`** — task definitions (title, description, model, workspace, status, dependencies, etc.)
- **`run`** — one row per execution attempt (status, current phase, summary, error, test_baseline, architecture_snapshot, build_id)
- **`runphase`** — one row per phase per attempt, per batch/task-index (plan/validate/build/review/QA status, artifact text)
- **`runevent`** — append-only log of every event emitted during a run

Settings are stored as JSON in `forge_settings.json` alongside the database.

### Task status lifecycle

```
pending → planning → building → qa → done
                              ↘ review  (supervised mode, awaiting sign-off)
         (any phase) → failed
```

The run's `current_phase` field tracks the active pipeline phase within a run: `plan`, `validate`, `build`, `review`, or `qa`.

---

## Building for Distribution

```bash
# Build the frontend static files
npm run build:frontend

# Build the Electron app
npm run build        # creates dist-electron/
npm run pack         # unpackaged directory only (faster, for testing)
```

Build targets (configured in `package.json`):

| Platform | Output |
|----------|--------|
| Linux | AppImage, .deb |
| macOS | .dmg |

The packaged app bundles the Electron shell, the built React frontend, and the Python backend. Python and pip must be available on the target machine to install `requirements.txt` dependencies.

---

## Development

### Running the end-to-end test

```bash
# Start the backend first
uvicorn backend.main:app --reload

# In another terminal
python test_run.py
```

`test_run.py` creates a task, starts a run, streams the WebSocket output, and prints the final result.

### Unit tests

```bash
pytest
```

### Project structure

```
forge/
├── backend/
│   ├── main.py           # FastAPI app + all endpoints
│   ├── models.py         # ORM tables + Pydantic schemas
│   ├── database.py       # SQLite engine + settings helpers
│   ├── orchestrator.py   # Plan→Validate→Build→Review→QA pipeline
│   ├── scheduler.py      # DAG scheduling + cycle detection
│   ├── memory.py         # memory-core client wrapper
│   └── agent/
│       ├── loop.py       # LLM conversation loop
│       ├── tools.py      # Tool implementations (sandboxed to workspace)
│       ├── prompts.py    # Phase-specific system prompts + tool sets
│       └── adapters/
│           ├── base.py       # Abstract ModelAdapter
│           ├── ollama.py     # Ollama (OpenAI-compatible) adapter
│           ├── anthropic.py  # Anthropic Claude adapter
│           ├── claude_code.py  # Claude Code CLI adapter
│           └── cursor.py     # Cursor editor adapter
├── frontend/
│   └── src/
│       ├── App.jsx           # Router + keyboard shortcuts
│       ├── api.js            # HTTP client
│       ├── TasksContext.jsx  # Global state
│       ├── hooks/
│       │   └── useWebSocket.js
│       └── components/
│           ├── TaskBoard.jsx       # Kanban board + pipeline controls
│           ├── RunView.jsx         # Live event stream + approval UIs
│           ├── TaskEditor.jsx      # Create/edit task form
│           ├── MemoryBrowser.jsx   # Search/delete memories
│           ├── DependencyGraph.jsx # DAG visualization
│           └── Settings.jsx        # Settings panel
├── electron/
│   ├── main.js       # Electron app lifecycle
│   └── preload.js    # Context isolation preload
├── docs/
│   └── architecture.md  # Detailed architecture reference
├── requirements.txt
├── package.json
└── test_run.py
```
