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
