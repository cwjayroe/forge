from backend.main import _build_failure_metadata, _categorize_failure
from backend.models import Run, RunEvent, RunPhase


def test_categorize_failure_test_failure():
    category, actions = _categorize_failure("pytest failed with assertion error")
    assert category == "test_failure"
    assert len(actions) >= 1


def test_categorize_failure_timeout():
    category, _ = _categorize_failure("process timed out after 120 seconds")
    assert category == "timeout"


def test_build_failure_metadata_includes_phase_and_recent_errors():
    run = Run(task_id="task-1", status="failed", error="Permission denied while running command")
    phases = [
        RunPhase(run_id=run.id, phase="build", status="completed"),
        RunPhase(run_id=run.id, phase="qa", status="failed", error="permission denied"),
    ]
    events = [
        RunEvent(run_id=run.id, type="text", content='{"content": "hello"}'),
        RunEvent(run_id=run.id, type="error", content='{"content": "permission denied in QA"}'),
    ]

    metadata = _build_failure_metadata(run, phases, events)

    assert metadata is not None
    assert metadata["category"] == "permission"
    assert metadata["phase"] == "qa"
    assert metadata["recent_errors"]
