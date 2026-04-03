# Forge Feature Extension Plan (Usability + Functionality)

This plan is based on the current architecture and UX surface in this repository (FastAPI + SQLModel backend orchestration, React task board/run view frontend, and skills/memory abstractions).

## 1) Immediate Usability Wins (High Impact, Low/Medium Effort)

### 1.1 Task templates and presets
**Problem:** Creating high-quality tasks repeatedly is manual and error-prone.

**Proposed feature:** Add first-class task templates that prefill title, description structure, model trio, retry count, mode, and optional dependency patterns.

**Scope:**
- Backend: new `TaskTemplate` model + CRUD endpoints.
- Frontend: “Create from template” in `TaskEditor` and quick template picker on board.
- Include built-in templates for bugfix, test-writing, refactor, docs, security audit.

**Why now:** Existing tasks already have rich configuration; templates would immediately reduce setup friction and improve consistency.

---

### 1.2 Global search + filters across tasks/runs/events
**Problem:** At scale, users cannot quickly find “the run that failed in QA yesterday” or “tasks touching file X”.

**Proposed feature:** Search bar + filter chips (status, model/provider, date range, workspace, phase, failure-only).

**Scope:**
- Backend: paginated query endpoints with filter params for tasks/runs/events.
- Frontend: searchable list and saved filter presets.
- Add sort options: newest, oldest, duration, failure rate.

**Why now:** Core data exists but discoverability is limited.

---

### 1.3 Rich failure summaries and actionable retry UX
**Problem:** Failed runs are visible, but guidance for “what next” is limited.

**Proposed feature:** Structured failure cards with root-cause category (test failure, lint, missing dep, permission, timeout), suggested next actions, and one-click retry strategies (same config, increase retries, switch QA model, supervised mode).

**Scope:**
- Backend: store parsed failure metadata on run completion.
- Frontend: failure panel in `RunView` + retry modal.

**Why now:** Improves confidence and lowers operator burden.

---

## 2) Core Functionality Expansions (High Impact, Medium Effort)

### 2.1 Pipeline policy engine (quality gates)
**Problem:** Current phases are fixed, but release-quality teams need policy controls.

**Proposed feature:** Add configurable quality gates before transitioning phase/state.

**Examples:**
- Require test coverage delta >= threshold.
- Require no high-severity security findings.
- Require explicit review approval for tasks matching patterns (e.g., `backend/auth/*`).

**Scope:**
- Backend: policy definitions in settings + gate evaluator.
- Orchestrator integration at phase transitions.
- Frontend: policy editor and gate result audit trail.

---

### 2.2 Multi-workspace projects + reusable context packs
**Problem:** Tasks currently assume a single workspace path per task, making monorepo/multi-repo work clunky.

**Proposed feature:** “Project” abstraction that can include multiple repositories/workspaces and shared context packs (architecture notes, coding standards, API contracts).

**Scope:**
- Backend: `Project` model and task->project linkage.
- Agent prompt assembly: inject project-level context + workspace-specific constraints.
- Frontend: project switcher, project settings page.

---

### 2.3 Native GitHub/GitLab provider integration
**Problem:** Branch creation/commit exists, but remote collaboration flow is incomplete for teams.

**Proposed feature:** Connect provider accounts for automatic push, PR/MR creation, reviewer assignment, label/rulesets, and status-sync back into Forge.

**Scope:**
- Backend: OAuth/token config, provider adapter layer, webhook ingestion.
- Frontend: repo/link settings, PR status badges in task cards and run view.

---

## 3) Reliability + Scale Improvements (Medium/High Impact, Medium/High Effort)

### 3.1 Durable job queue and resumable orchestration
**Problem:** In-memory run state and WS listener bookkeeping are vulnerable to process restarts.

**Proposed feature:** Durable queue with persisted run state machine and resumable execution semantics.

**Scope:**
- Move active run tracking from in-process globals to durable store.
- Worker model for execution with lease/heartbeat.
- Recovery flow: resume, mark unknown state, or safe abort.

---

### 3.2 Observability: metrics, traces, and run analytics dashboard
**Problem:** Limited operational visibility into throughput, failure modes, model performance, and cost.

**Proposed feature:** Add structured metrics and dashboards.

**KPIs:**
- Mean run duration by phase/model.
- Success rate and retry rate.
- Queue wait time, concurrency saturation.
- Cost/token estimates by provider.

**Scope:**
- Backend instrumentation + optional OpenTelemetry export.
- Frontend “Analytics” page with trend charts and drill-down.

---

### 3.3 Long-run streaming resilience
**Problem:** Long runs can lose UI continuity on reconnect.

**Proposed feature:** Cursor-based event streaming and replay with idempotent event IDs.

**Scope:**
- Backend WS protocol enhancement with `last_event_id` resume.
- Frontend reconnect strategy and gap-repair fetch.

---

## 4) Collaboration + Governance (Medium Impact, Medium Effort)

### 4.1 User accounts, RBAC, and audit logs
**Problem:** Current setup is single-tenant/operator oriented.

**Proposed feature:** Multi-user support with role-based permissions and immutable audit trails.

**Roles:** Admin, Maintainer, Operator, Reviewer, Viewer.

**Scope:**
- AuthN/AuthZ framework.
- Endpoint-level authorization checks.
- Activity timeline for task changes, approvals, and pipeline controls.

---

### 4.2 Approval workflow improvements
**Problem:** Approval exists for bash and plan, but approvals are minimal and not SLA-oriented.

**Proposed feature:** Assignable approvals with timeout/escalation rules and context-rich diff/test snapshots.

**Scope:**
- Approval requests become first-class records.
- Notification hooks (Slack/Discord/webhook) include deep links and decision metadata.

---

### 4.3 Shared knowledge and memory curation
**Problem:** Memory exists, but curation/quality controls are limited.

**Proposed feature:** Memory pinning, tagging, decay/retention policies, and confidence scoring.

**Scope:**
- Memory UI enhancements + moderation tools.
- Better retrieval controls per phase and per skill.

---

## 5) Recommended Implementation Roadmap

## Phase A (2–4 weeks): “Operator Productivity”
1. Task templates/presets.
2. Search + filters for tasks/runs.
3. Failure summary + guided retry UX.

**Expected outcome:** faster task authoring, easier debugging, lower cognitive load.

## Phase B (4–8 weeks): “Team-Ready Pipelines”
1. Policy engine + quality gates.
2. Provider integrations (GitHub/GitLab) with PR status backflow.
3. Approval workflow improvements (assign/timeout/escalate).

**Expected outcome:** stronger governance, smoother dev team adoption.

## Phase C (6–12 weeks): “Scale + Enterprise Reliability”
1. Durable/resumable orchestration.
2. Observability dashboard + OTel.
3. RBAC and audit logs.

**Expected outcome:** production-grade reliability and compliance posture.

## 6) Backlog Candidates (Nice-to-Have)

- Cost-aware model routing (auto-select cheapest model that meets policy).
- Experiment mode (A/B run same task across models/prompts and compare outcomes).
- Plugin marketplace for custom skills and tools.
- Natural-language query interface over run history (“show flaky tasks in last 7 days”).
- Mobile-friendly run monitoring UI.

## 7) Suggested Next Action

Start with **Phase A** and implement **task templates** first; it has the best effort-to-value ratio and creates a reusable foundation for policy-driven and team-oriented workflows.

---

## 8) Phase 1 Execution Plan (Implemented)

Phase 1 maps to **Phase A.1: Task templates and presets** and is intentionally scoped to deliver immediate value without blocking future Phase A.2/A.3 work.

### 8.1 Functional goals
- Add first-class, DB-backed task templates.
- Ship built-in presets for: bugfix, test-writing, refactor, docs, security audit.
- Support “create from template” in the task editor.
- Add a quick template picker on the board for fast task creation.

### 8.2 Delivery slices
1. **Data model + API**
   - Add `TaskTemplate` SQLModel table with configuration fields mirroring task creation.
   - Add CRUD endpoints (`/task-templates`) and uniqueness guard on `slug`.
   - Seed built-in templates at backend startup (idempotent by slug).

2. **Task creation integration**
   - Extend editor load path to apply template defaults (title, description, mode, model trio, retries, dependencies).
   - Preserve existing support for file-based markdown templates.

3. **Board usability**
   - Add top-level “Use Template” quick picker next to “New Task”.
   - Open editor prefilled from selected template for final review before save.

### 8.3 Out-of-scope for this phase
- Template management UI (create/edit/delete screens).
- Template sharing/import-export across environments.
- Advanced dependency macros/pattern expansion.

### 8.4 Success criteria
- Operators can create new tasks from built-in templates in <= 2 clicks from board.
- API consumers can list/create/update/delete templates.
- Existing task creation/edit flows continue to work unchanged when no template is chosen.

---

## 9) Phase 2 Execution Plan (Implemented)

Phase 2 maps to **Phase A.2: Global search + filters across tasks/runs/events** and is scoped to improve discoverability while preserving existing task/run workflows.

### 9.1 Functional goals
- Add backend search endpoints with filter/pagination controls for tasks, runs, and run events.
- Support common operator filters: free-text, status, mode/phase, date range, failure-only, workspace.
- Add board-level search/filter controls with lightweight saved presets for repeated queries.
- Keep existing `/tasks` and `/runs` APIs backward-compatible for current UI consumers.

### 9.2 Delivery slices
1. **Search APIs**
   - Add `/tasks/search` endpoint with text, status, mode, workspace, date range, sort, and pagination.
   - Add `/runs/search` endpoint with task join metadata plus status/phase/workspace/date filters.
   - Add `/run-events/search` endpoint with run/task joins, type/date filters, and text query over event payloads.
   - Add consistent validation for ISO-8601 datetimes and limit/offset guardrails.

2. **Frontend board discoverability**
   - Add search bar + filter chips/dropdowns in `TaskBoard` (status, mode, sort).
   - Add “Save preset” and quick preset re-apply powered by localStorage.
   - Keep DnD reorder behavior safe with filtered board views.

3. **Client API utilities**
   - Add frontend API wrappers for task/run/event search endpoints.
   - Add query-string builder helper for consistent parameter encoding.

### 9.3 Out-of-scope for this phase
- Dedicated global “search page” spanning tasks, runs, and events in one unified UI.
- Server-side persisted filter presets shared across users/environments.
- Advanced sorting (duration/failure-rate aggregations) and full-text indexing optimization.

### 9.4 Success criteria
- Operators can narrow board tasks by text/status/mode in seconds.
- API consumers can query paginated task/run/event search results with composable filters.
- Existing board, run view, and task creation behavior remain intact with no migration needed.

---

## 10) Phase 3 Execution Plan (Implemented)

Phase 3 maps to **Phase A.3: Rich failure summaries and actionable retry UX** and is scoped to make failed runs easier to triage and recover from directly in the run detail view.

### 10.1 Functional goals
- Add backend failure analysis metadata for failed runs (category, phase, root message, suggested actions).
- Surface structured failure cards in `RunView` instead of raw errors only.
- Add one-click retry actions:
  - retry same config,
  - retry with increased retries,
  - retry with QA model switch,
  - retry in supervised mode.

### 10.2 Delivery slices
1. **Failure analysis helpers**
   - Add backend helpers to classify failures (test failure, missing dependency, permission, timeout, unknown).
   - Build run-level failure metadata using run error, failed phase, and recent error events.

2. **Run API response enrichment**
   - Extend `/runs/{run_id}` to include `failure_metadata` when a run fails.
   - Extend `/runs/search` items with lightweight `failure_category` for list-level filtering/diagnostics.

3. **RunView actionable recovery UX**
   - Render a failure summary panel for failed runs.
   - Show category, failing phase, concise error message, and suggested next actions.
   - Add one-click retry strategy buttons wired to task update + re-run flows.

### 10.3 Out-of-scope for this phase
- Persisting failure classifications as first-class DB columns.
- Historical failure analytics aggregation/dashboard widgets.
- Advanced retry policies (exponential backoff, automatic model routing).

### 10.4 Success criteria
- Operators can identify likely root-cause category from the run view in under 10 seconds.
- Failed runs expose clear, actionable next steps without scanning the full event feed.
- Operators can launch a guided retry strategy in one click from the failure panel.

---

## 11) Phase 4 Execution Plan (Implemented)

Phase 4 maps to **Phase B.1: Pipeline policy engine (quality gates)** and is scoped to add configurable transition gates without changing the existing task/run data model.

### 11.1 Functional goals
- Add configurable quality gate rules in settings.
- Evaluate gates at key transitions:
  - `plan_to_build`
  - `qa_to_done`
- Emit auditable gate results into run events for operator visibility.
- Block transition when active gate rules fail and return clear failure reasons.

### 11.2 Delivery slices
1. **Settings and validation**
   - Add `quality_gates_enabled` and `quality_gate_rules` to persisted settings.
   - Validate `quality_gate_rules` as JSON array in `/settings` updates.

2. **Gate evaluator in orchestrator**
   - Parse rules safely (invalid JSON falls back to empty list at runtime).
   - Evaluate rules against task context and transition context.
   - Supported rule fields:
     - `name`
     - `enabled`
     - `on_transition` (`plan_to_build`, `qa_to_done`)
     - `task_pattern` (regex over title/description/spec path/workspace)
     - `min_retries`
     - `require_supervised`
     - `require_plan_validation_pass`
     - `require_qa_pass`

3. **UI configuration surface**
   - Add settings UI section for quality gates.
   - Add enable toggle and JSON rule editor with examples/documented fields.

4. **Test coverage**
   - Add unit tests for rule parsing and transition gate evaluation behavior.

### 11.3 Out-of-scope for this phase
- Dedicated policy CRUD tables and versioned policy history.
- Coverage/security scanner integrations for dynamic thresholds.
- Per-team/project scoped policy sets and RBAC enforcement.

### 11.4 Success criteria
- Operators can define and save quality gate rules from settings.
- Runs emit `quality_gate_result` events for evaluated transitions.
- Runs fail early with explicit reasons when configured gate requirements are not met.

---

## 12) Phase 5 Execution Plan (Implemented)

Phase 5 maps to **Phase B.2: Native GitHub/GitLab provider integration** and is scoped to provide an operator-ready PR/MR flow (create + status sync) using existing run branch metadata.

### 12.1 Functional goals
- Add configurable provider integration settings for GitHub/GitLab in Forge settings.
- Allow operators to create a PR/MR from a completed run directly in `RunView`.
- Support automatic PR/MR creation after successful runs when enabled.
- Provide PR/MR status sync endpoint for badge/link backflow into the run UI.

### 12.2 Delivery slices
1. **Provider adapter layer**
   - Add `backend/providers.py` with a provider-agnostic API for:
     - create change request (GitHub PR / GitLab MR),
     - fetch change request status.
   - Normalize payloads to a shared shape (`provider`, `number`, `url`, `state`).

2. **Backend API + run event linkage**
   - Add run-scoped endpoints:
     - `POST /runs/{run_id}/provider/change-request`
     - `GET /runs/{run_id}/provider/change-request/status`
   - Store `provider_change_request_created` run events so linkage stays auditable and queryable.
   - Emit `provider_change_request_created` / `provider_change_request_error` events during optional auto-create path.

3. **Settings + run UX**
   - Add settings controls for provider type, repo slug, token, API base URL, default base branch, default labels, and auto-create toggle.
   - Add `RunView` actions:
     - “Create PR/MR” button when run has a branch and no linked change request.
     - “Open PR/MR” link once created.

### 12.3 Out-of-scope for this phase
- OAuth account linking and token exchange flows.
- Reviewer assignment and branch protection/ruleset automation.
- Webhook ingestion for fully push-based status updates.

### 12.4 Success criteria
- Operators can create a PR/MR from a run in one click from `RunView`.
- Runs can surface current PR/MR status via provider API lookup.
- Successful runs can optionally auto-create a PR/MR when integration is enabled.
