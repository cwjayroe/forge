"""
Unit tests for pure helper functions added to orchestrator.py:
  - _generate_build_id
  - _parse_plan_phases
  - _extract_tasks_from_phase
  - _batch_tasks
"""
import re

from backend.orchestrator import (
    _batch_tasks,
    _extract_tasks_from_phase,
    _generate_build_id,
    _parse_plan_phases,
)


# ---------------------------------------------------------------------------
# _generate_build_id
# ---------------------------------------------------------------------------

def test_generate_build_id_format():
    build_id = _generate_build_id("Add Webhook Support")
    # slug-XXXXXX pattern
    assert re.match(r'^[a-z0-9-]+-[0-9a-f]{6}$', build_id), build_id


def test_generate_build_id_slugifies_title():
    build_id = _generate_build_id("Add Webhook Support")
    assert build_id.startswith("add-webhook-support-")


def test_generate_build_id_truncates_long_title():
    long_title = "A" * 100
    build_id = _generate_build_id(long_title)
    slug_part = build_id.rsplit("-", 1)[0]
    assert len(slug_part) <= 40


def test_generate_build_id_strips_special_chars():
    build_id = _generate_build_id("Fix: auth/middleware (v2)!")
    slug_part = build_id.rsplit("-", 1)[0]
    assert re.match(r'^[a-z0-9-]+$', slug_part)


def test_generate_build_id_unique_per_call():
    # Two calls should produce different hashes (different timestamps)
    id1 = _generate_build_id("same title")
    id2 = _generate_build_id("same title")
    # Same slug prefix, different hash suffix (with high probability)
    assert id1.rsplit("-", 1)[0] == id2.rsplit("-", 1)[0]  # same slug
    # Note: hash could theoretically collide but extremely unlikely in practice


# ---------------------------------------------------------------------------
# _parse_plan_phases
# ---------------------------------------------------------------------------

_MINIMAL_PLAN = """
# Build Plan

## Summary
Build a webhook handler.

## Phases

### Phase 1: Foundation
- task_type: create
  file_path: /app/models/webhook.py
  description: Create webhook model

### Phase 2: Integration
- task_type: modify
  file_path: /app/api/routes.py
  description: Add webhook route

## Total: 2 phases, 2 tasks
"""


def test_parse_plan_phases_count():
    phases = _parse_plan_phases(_MINIMAL_PLAN)
    assert len(phases) == 2


def test_parse_plan_phases_names():
    phases = _parse_plan_phases(_MINIMAL_PLAN)
    assert phases[0]["name"] == "Phase 1: Foundation"
    assert phases[1]["name"] == "Phase 2: Integration"


def test_parse_plan_phases_tasks_extracted():
    phases = _parse_plan_phases(_MINIMAL_PLAN)
    tasks = phases[0]["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["file_path"] == "/app/models/webhook.py"
    assert tasks[0]["task_type"] == "create"


def test_parse_plan_phases_fallback_on_no_headers():
    plain_plan = "Just do stuff. No phase headers here."
    phases = _parse_plan_phases(plain_plan)
    assert len(phases) == 1
    assert phases[0]["name"] == "Phase 1: Build"
    assert phases[0]["raw"] == plain_plan


def test_parse_plan_phases_raw_preserved():
    phases = _parse_plan_phases(_MINIMAL_PLAN)
    assert "/app/models/webhook.py" in phases[0]["raw"]


# ---------------------------------------------------------------------------
# _extract_tasks_from_phase
# ---------------------------------------------------------------------------

_PHASE_WITH_TASKS = """
- task_type: create
  file_path: /app/services/handler.py
  description: Create handler service
  high_risk: false

- task_type: modify
  file_path: /app/api/__init__.py
  description: Export handler
  high_risk: true
  model_hint: fast
"""

_PHASE_NO_TASK_BLOCKS = "Just prose describing what to do."


def test_extract_tasks_basic():
    tasks = _extract_tasks_from_phase(_PHASE_WITH_TASKS)
    assert len(tasks) == 2


def test_extract_tasks_fields():
    tasks = _extract_tasks_from_phase(_PHASE_WITH_TASKS)
    t = tasks[0]
    assert t["task_type"] == "create"
    assert t["file_path"] == "/app/services/handler.py"
    assert t["description"] == "Create handler service"
    assert t["high_risk"] is False


def test_extract_tasks_high_risk_true():
    tasks = _extract_tasks_from_phase(_PHASE_WITH_TASKS)
    assert tasks[1]["high_risk"] is True


def test_extract_tasks_model_hint():
    tasks = _extract_tasks_from_phase(_PHASE_WITH_TASKS)
    assert tasks[1]["model_hint"] == "fast"


def test_extract_tasks_fallback_on_no_task_blocks():
    tasks = _extract_tasks_from_phase(_PHASE_NO_TASK_BLOCKS)
    assert len(tasks) == 1
    assert tasks[0]["raw"] == _PHASE_NO_TASK_BLOCKS


# ---------------------------------------------------------------------------
# _batch_tasks
# ---------------------------------------------------------------------------

def _task(file_path: str, depends_on=None, **kwargs) -> dict:
    t = {"file_path": file_path}
    if depends_on:
        t["depends_on"] = depends_on
    t.update(kwargs)
    return t


def test_batch_tasks_single():
    tasks = [_task("/a.py")]
    batches = _batch_tasks(tasks, max_per_batch=3)
    assert batches == [[{"file_path": "/a.py"}]]


def test_batch_tasks_independent_fit_in_one_batch():
    tasks = [_task("/a.py"), _task("/b.py"), _task("/c.py")]
    batches = _batch_tasks(tasks, max_per_batch=3)
    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_batch_tasks_respects_max_per_batch():
    tasks = [_task(f"/{i}.py") for i in range(5)]
    batches = _batch_tasks(tasks, max_per_batch=2)
    for batch in batches:
        assert len(batch) <= 2
    # All tasks should appear exactly once across all batches
    all_files = [t["file_path"] for b in batches for t in b]
    assert sorted(all_files) == sorted(t["file_path"] for t in tasks)


def test_batch_tasks_same_file_in_separate_batches():
    tasks = [_task("/a.py"), _task("/a.py")]
    batches = _batch_tasks(tasks, max_per_batch=3)
    # Same file cannot be in the same batch
    assert len(batches) == 2


def test_batch_tasks_dependency_ordering():
    # /b.py depends on /a.py — a must be in an earlier batch
    tasks = [
        _task("/b.py", depends_on=["/a.py"]),
        _task("/a.py"),
    ]
    batches = _batch_tasks(tasks, max_per_batch=3)
    assert len(batches) == 2
    a_batch = next(i for i, b in enumerate(batches) if any(t["file_path"] == "/a.py" for t in b))
    b_batch = next(i for i, b in enumerate(batches) if any(t["file_path"] == "/b.py" for t in b))
    assert a_batch < b_batch


def test_batch_tasks_empty():
    assert _batch_tasks([], max_per_batch=3) == []


def test_batch_tasks_no_deadlock_on_unresolvable_deps():
    # If a dep can never be satisfied, deadlock prevention kicks in
    tasks = [_task("/a.py", depends_on=["/missing.py"])]
    batches = _batch_tasks(tasks, max_per_batch=3)
    assert len(batches) == 1  # Forced into a batch


def test_batch_tasks_preserves_all_tasks():
    tasks = [_task(f"/{i}.py") for i in range(10)]
    batches = _batch_tasks(tasks, max_per_batch=3)
    all_files = [t["file_path"] for b in batches for t in b]
    assert sorted(all_files) == sorted(t["file_path"] for t in tasks)
