# Forge Architecture

This document provides a detailed reference for Forge's internal architecture — how the pipeline works, how components fit together, and how data flows through the system.

---

## Overview

Forge is a full-stack application with three layers:

1. **FastAPI backend** — orchestrates agent pipelines, exposes REST + WebSocket APIs
2. **React frontend** — visual task board, live run view, settings, memory browser
3. **Electron shell** (optional) — desktop app that spawns and manages the backend process

```
┌────────────────────────────────────────────────────────┐
│  Electron Shell (optional)                             │
│  electron/main.js  ──►  spawns uvicorn backend        │
│  electron/preload.js    manages window lifecycle       │
└──────────────────────────────┬─────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────┐
│  React Frontend  (frontend/src/)                       │
│                                                        │
│  App.jsx           Router, keyboard shortcuts          │
│  TasksContext.jsx  Global state provider               │
│  api.js            HTTP client wrapper                 │
│  useWebSocket.js   WebSocket hook                      │
│                                                        │
│  TaskBoard.jsx     Kanban + pipeline controls          │
│  RunView.jsx       Live stream + approval UIs          │
│  TaskEditor.jsx    Create/edit form                    │
│  MemoryBrowser.jsx Memory search/delete                │
│  DependencyGraph.jsx  DAG visualization                │
│  Settings.jsx      Config panel                        │
└──────────────────────────────┬─────────────────────────┘
                               │  HTTP REST / WebSocket
┌──────────────────────────────▼─────────────────────────┐
│  FastAPI Backend  (backend/)                           │
│                                                        │
│  main.py        All routes, WebSocket endpoint         │
│  scheduler.py   DAG resolution, concurrency            │
│  orchestrator.py  Pipeline execution engine            │
│  memory.py      memory-core wrapper (+ JSON fallback)  │
│  database.py    SQLite engine, settings I/O            │
│  models.py      SQLModel ORM + Pydantic schemas        │
│                                                        │
│  agent/loop.py      LLM conversation loop              │
│  agent/tools.py     Tool implementations               │
│  agent/prompts.py   Phase prompts + tool sets          │
│  agent/adapters/    Model provider adapters            │
└────────────────────────────────────────────────────────┘
                               │
           ┌───────────────────┤
           │                   │
    ┌──────▼──────┐    ┌───────▼────────┐
    │  SQLite DB  │    │  memory-core   │
    │  forge.db   │    │  (or JSON)     │
    └─────────────┘    └────────────────┘
```

---

## Pipeline

Every task runs through five sequential phases. Each phase is a separate agent invocation with its own system prompt and tool set.

```
Task created (pending)
       │
       ▼
  ┌─────────┐
  │  PLAN   │  2-pass: explore codebase → produce enriched build plan
  └────┬────┘
       │ plan_artifact
       ▼
  ┌──────────┐
  │ VALIDATE │  check plan for internal consistency
  └────┬─────┘
       │ (supervised mode: plan_approval_request → wait for human)
       ▼
  ┌───────────────────────────────────────────┐
  │  BUILD (batched, parallel per plan phase) │
  │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │
  │  │ builder  │ │ builder  │ │ builder  │  │  ← max_concurrent_builders
  │  └────┬─────┘ └────┬─────┘ └────┬─────┘  │
  │       └────────────┴────────────┘         │
  │                    │ batch complete        │
  │              ┌─────▼──────┐               │
  │              │  REVIEWER  │               │  ← per-batch correctness check
  │              └─────┬──────┘               │
  └────────────────────┼──────────────────────┘
                       │ (repeat for each plan phase batch)
                       ▼
                  ┌──────┐
                  │  QA  │  run tests, check coverage, validate imports
                  └──┬───┘
                     │
          ┌──────────┴──────────┐
          │ VERDICT: PASS       │ VERDICT: FAIL (+ feedback)
          ▼                     ▼
     task done/review     retry build (up to max_retries)
```

### Phase Details

#### Plan Phase

**Goal:** Produce a structured, enriched implementation plan.

**Two-pass approach:**

*Pass 1 — Deep Exploration*
- Search existing memory for prior architecture decisions
- Read and trace all entry-point files, their imports, and their callers
- Catalog patterns (error handling, logging, config access, test structure)
- Identify high-risk files (imported by 5+ others)
- Map gaps (missing types, modules, config, fixtures)
- Store codebase analysis to memory (`{"phase": "codebase-analysis", "type": "architecture"}`)

*Pass 2 — Specification*
- Store architecture snapshot to memory (`{"phase": "architecture-snapshot", "type": "architecture"}`)
- Decompose implementation into ordered phases with tasks that can run in parallel
- For each task, produce an **enriched spec** including:
  - `task_type` (create/modify), `file_path`, `high_risk`
  - `existing_api` (modify tasks): current public API
  - `preserve`: signatures/behaviors that must not change
  - `spec`: concrete implementation requirements
  - `interface_contract`: what this task produces and consumes across task boundaries
  - `pattern_reference`: actual code from the codebase to follow
  - `test_strategy`: test file, scenarios, mocking, fixtures

**Allowed tools:** `read_file`, `list_files`, `search_files`, `search_memory`, `store_memory`

**Output:** Structured plan document (stored as `RunPhase.artifact`)

---

#### Validate Phase

**Goal:** Catch plan errors before any code is written.

**Checks:**
1. **Dependency graph** — every `depends_on` file will exist by that phase or already exists
2. **Interface contract alignment** — every `consumes` has a matching `produces` with compatible signatures; producing task is in an earlier phase
3. **Completeness** — every spec/task requirement maps to a plan task; no scope creep
4. **File conflicts** — no two tasks in the same phase modify the same file
5. **Preservation consistency** — no task spec contradicts another task's `preserve` entries
6. **Test coverage** — every non-trivial task has a `test_strategy`

**Verdict:** `VERDICT: PASS` or `VERDICT: FAIL` (any single issue = FAIL, task stops)

**Allowed tools:** `read_file`, `list_files`, `search_files`, `search_memory`

---

#### Build Phase

**Goal:** Execute the plan by writing files.

The plan is decomposed into **batches** (corresponding to plan phases). Within each batch, up to `max_concurrent_builders` builder agents run in parallel. Each builder receives:
- Its assigned task spec (with enriched context from the planner)
- The full architecture snapshot
- Review feedback from the previous batch attempt (if any)
- QA feedback from the previous full retry (if any)

**Critical requirement:** Builders must call `write_file` for every file they change. Text-only descriptions without `write_file` calls are treated as failures.

**Allowed tools:** `read_file`, `write_file`, `list_files`, `run_bash`, `search_files`, `search_memory`, `store_memory`

---

#### Review Phase

**Goal:** Validate each build batch before proceeding to the next or to QA.

A reviewer agent checks the batch's touched files:
1. **Import validation** — every local import resolves to an existing exported name
2. **Interface boundary check** — imported names still exist with compatible signatures; new parameters have defaults
3. **Regression check** — modified files haven't broken callers; `preserve` entries are intact; existing test assertions still reference valid names
4. **Spec compliance** — required functions/classes exist with correct signatures and behavior
5. **Interface contract validation** — every `produces`/`consumes` entry is satisfied; shared types are consistent
6. **Cross-file consistency** — naming and types are consistent across the batch

**Verdict:** `VERDICT: PASS` (proceed) or `VERDICT: FAIL` (feedback injected into next retry)

**Allowed tools:** `read_file`, `list_files`, `search_files`, `search_memory`

---

#### QA Phase

**Goal:** End-to-end quality assurance on the completed build.

1. **File change verification** — runs `git diff --stat HEAD` and `git ls-files --others --exclude-standard`; fails immediately if no files were changed
2. **Test suite execution** — runs `python -m pytest -q` (or project test command), captures pass/fail/error counts
3. **Failure attribution** — compares against the pre-build baseline to classify: regressions (blockers), new failures (blockers), pre-existing failures (informational), fixed tests (positive)
4. **Coverage check** — runs with `--cov` if available; flags new files with 0% coverage
5. **Import validation** — verifies every new module is importable
6. **Completeness audit** — cross-references plan against what was built

**Verdict:** `VERDICT: PASS` or `VERDICT: FAIL` (feedback injected into next build retry)

**Allowed tools:** `read_file`, `list_files`, `search_files`, `run_bash`, `search_memory`, `store_memory`

---

## Model Adapters

All model adapters implement the abstract `ModelAdapter` base class (`backend/agent/adapters/base.py`), which provides a unified interface for the agent loop.

| Adapter | File | Provider String | Notes |
|---------|------|-----------------|-------|
| Ollama | `ollama.py` | `ollama/<model>` or bare `<model>` | OpenAI-compatible API; default provider |
| Anthropic | `anthropic.py` | `anthropic/<model>` | Requires `anthropic_api_key` in settings |
| Claude Code | `claude_code.py` | `claude-code/<model>` | Claude Code CLI; uses `mcp_server_host` |
| Cursor | `cursor.py` | `cursor-code/<model>` | Cursor editor integration |

The orchestrator selects the adapter in `_make_adapter()` by parsing the `provider/model-name` string.

Per-task model overrides:
- `task.model` — build phase
- `task.plan_model` — plan + validate phases (falls back to `task.model`)
- `task.qa_model` — QA phase (falls back to `task.model`)

---

## Agent Loop

`backend/agent/loop.py` implements the core LLM conversation loop used by all phases:

1. Send the system prompt + initial user message to the model
2. Stream the response; dispatch any tool calls
3. Append tool results to the conversation
4. Repeat up to 50 iterations (configurable)
5. Return the final text output as the phase artifact

The loop handles:
- **Abort signals** — checks `AgentAbortedError` at each iteration; propagates through the pipeline
- **Bash approval gates** — when `require_bash_approval` is set, emits `bash_approval_request` and blocks until `POST /runs/{id}/bash/approve` resolves the gate (5-minute timeout)
- **Tool sandboxing** — all file tools call `_safe_path()` to validate paths are within the workspace

---

## Scheduler

`backend/scheduler.py` manages which tasks are ready to run and how many can run concurrently.

**DAG logic:**
- A task is "ready" when all its `depends_on` tasks have `status == "done"`
- Cycle detection runs at task create/update time using DFS
- Up to `max_concurrent_tasks` tasks may run simultaneously

**Pipeline controls:**
- `POST /pipeline/start` — finds all ready tasks and launches them
- `POST /pipeline/pause` — sets a flag that prevents new tasks from starting
- `POST /pipeline/resume` — clears the flag and calls start

---

## Memory System

`backend/memory.py` is an async wrapper around the `memory-core` package.

**Usage across phases:**
- **Plan phase** — stores codebase analysis and architecture snapshot; searches for prior decisions
- **Build phase** — can store and retrieve context between builder agents
- **QA phase** — can store summaries for future runs
- **After successful run** — build summary stored automatically by the orchestrator

**Fallback:** If `memory-core` is not installed or initialization fails, Forge falls back to a local JSON file for persistence.

**Projects:** Memory is organized into projects (accessible via `GET /memory/projects`). The memory browser UI lets you filter, search, and delete entries by project.

---

## Data Model

### Task

Represents a unit of work. Fields:

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID string | Primary key |
| `title` | string | Short display name |
| `description` | string | What the agent should build |
| `spec_path` | string? | Path to a spec/PRD file |
| `mode` | string | `autonomous` or `supervised` |
| `status` | string | See lifecycle below |
| `depends_on` | string? | Comma-separated task IDs |
| `model` | string | Build phase model |
| `plan_model` | string? | Plan phase model override |
| `qa_model` | string? | QA phase model override |
| `max_retries` | int | Max build→QA cycles (default 3) |
| `workspace` | string | Absolute path to working directory |
| `order` | int | Board display order |

**Status lifecycle:**
```
pending → planning → building → qa → done
                              ↘ review  (supervised mode)
         (any phase) → failed
```

### Run

One row per execution attempt for a task.

| Field | Notes |
|-------|-------|
| `id` | UUID string |
| `task_id` | Foreign key to Task |
| `build_id` | Slugified title + timestamp hash (used as memory key) |
| `status` | `running`, `completed`, `failed`, `aborted` |
| `current_phase` | Active pipeline phase: `plan`, `validate`, `build`, `review`, `qa` |
| `test_baseline` | Pre-build test output (captured if `capture_test_baseline` is true) |
| `architecture_snapshot` | Stored by planner; passed to builders and reviewer |
| `summary` | Final build summary (on success) |
| `error` | Error message (on failure) |

### RunPhase

One row per phase per attempt, per batch/task-index.

| Field | Notes |
|-------|-------|
| `run_id` | Foreign key to Run |
| `phase` | `plan`, `validate`, `build`, `review`, `qa` |
| `attempt` | Retry number (starts at 1) |
| `batch` | Batch number within a phase (for parallel builds) |
| `task_index` | Builder index within a batch |
| `status` | `running`, `completed`, `failed` |
| `artifact` | Phase output text (plan doc, QA report, build summary, etc.) |

### RunEvent

Append-only event log. Every WebSocket event is also persisted here.

| Field | Notes |
|-------|-------|
| `run_id` | Foreign key to Run |
| `type` | Event type (see WebSocket event table) |
| `content` | JSON blob |
| `timestamp` | UTC datetime |

---

## Security & Sandboxing

### Workspace isolation

All file tools in `backend/agent/tools.py` call `_safe_path()` before any file operation. This function:
1. Resolves the path relative to the workspace root
2. Resolves symlinks
3. Verifies the resolved path is still inside the workspace directory

Attempts to escape with `../` or absolute paths outside the workspace raise a `PermissionError` that is returned to the agent as a tool error.

### Tool restrictions by phase

Each phase receives a specific set of allowed tools. The agent loop will reject calls to tools not in the allowed set.

| Phase | Allowed Tools |
|-------|--------------|
| Plan | `read_file`, `list_files`, `search_files`, `search_memory`, `store_memory` |
| Validate | `read_file`, `list_files`, `search_files`, `search_memory` |
| Build | `read_file`, `write_file`, `list_files`, `run_bash`, `search_files`, `search_memory`, `store_memory` |
| Review | `read_file`, `list_files`, `search_files`, `search_memory` |
| QA | `read_file`, `list_files`, `search_files`, `run_bash`, `search_memory`, `store_memory` |

### Approval gates

**Bash approval** (`require_bash_approval = true`): The agent loop emits a `bash_approval_request` event and blocks on an `asyncio.Event`. The gate resolves when `POST /runs/{id}/bash/approve` is called. Timeout: 5 minutes.

**Plan approval** (supervised mode): The orchestrator emits a `plan_approval_request` event after plan validation passes and blocks on an `asyncio.Event`. The gate resolves when `POST /runs/{id}/plan/approve` is called. Timeout: 10 minutes.

---

## Event Broadcasting

The orchestrator uses a fan-out queue system for real-time streaming:

1. On WebSocket connect, `register_ws_listener(run_id)` creates a per-connection `asyncio.Queue`
2. `_broadcast(run_id, event)` puts the event into all registered queues for that run
3. The WebSocket endpoint dequeues and sends events; a 30-second keepalive ping is sent if no events arrive
4. `deregister_ws_listener` removes the queue on disconnect

All events are also persisted to `RunEvent` by the orchestrator's `on_event` callback, enabling replay via `GET /runs/{id}`.
