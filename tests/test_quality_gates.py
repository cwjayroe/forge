from backend.models import Task
from backend.orchestrator import _evaluate_quality_gates, _parse_quality_gate_rules


def test_parse_quality_gate_rules_handles_invalid_json():
    assert _parse_quality_gate_rules("not-json") == []
    assert _parse_quality_gate_rules('{"a": 1}') == []


def test_quality_gate_blocks_plan_to_build_when_supervised_required():
    task = Task(
        title="Auth patch",
        description="Harden backend/auth/token.py",
        workspace="/repo",
        mode="autonomous",
        max_retries=1,
    )
    rules = [
        {
            "name": "Auth requires supervision",
            "on_transition": "plan_to_build",
            "task_pattern": "backend/auth",
            "require_supervised": True,
            "min_retries": 2,
        }
    ]
    result = _evaluate_quality_gates("plan_to_build", task, rules, context={"plan_validation_pass": True})
    assert result["passed"] is False
    assert result["failures"][0]["rule_name"] == "Auth requires supervision"


def test_quality_gate_passes_when_rule_does_not_match_task():
    task = Task(
        title="Docs cleanup",
        description="Update README docs",
        workspace="/repo",
        mode="autonomous",
        max_retries=3,
    )
    rules = [
        {
            "name": "Only auth tasks",
            "on_transition": "plan_to_build",
            "task_pattern": "backend/auth",
            "require_supervised": True,
        }
    ]
    result = _evaluate_quality_gates("plan_to_build", task, rules)
    assert result["passed"] is True
    assert result["failures"] == []
