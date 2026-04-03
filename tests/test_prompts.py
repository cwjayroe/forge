"""
Unit tests for the new and updated prompt functions in agent/prompts.py.

Covers:
  - validator_prompt() — new function
  - reviewer_prompt() — new function
  - plan_prompt() — rewritten with 2-pass instructions
  - build_prompt() — new task_spec, architecture_snapshot, review_feedback params
  - qa_prompt() — new test_baseline param
  - Tool set constants: QA_TOOLS now includes run_bash; VALIDATOR_TOOLS and REVIEWER_TOOLS defined
"""
from backend.agent.prompts import (
    BUILD_TOOLS,
    PLAN_TOOLS,
    QA_TOOLS,
    REVIEWER_TOOLS,
    VALIDATOR_TOOLS,
    build_prompt,
    plan_prompt,
    qa_prompt,
    reviewer_prompt,
    validator_prompt,
)


# ---------------------------------------------------------------------------
# Tool set constants
# ---------------------------------------------------------------------------

def test_qa_tools_includes_run_bash():
    assert "run_bash" in QA_TOOLS


def test_plan_tools_no_run_bash():
    assert "run_bash" not in PLAN_TOOLS


def test_validator_tools_read_only():
    assert "write_file" not in VALIDATOR_TOOLS
    assert "run_bash" not in VALIDATOR_TOOLS
    assert "read_file" in VALIDATOR_TOOLS


def test_reviewer_tools_read_only():
    assert "write_file" not in REVIEWER_TOOLS
    assert "run_bash" not in REVIEWER_TOOLS
    assert "read_file" in REVIEWER_TOOLS


def test_build_tools_has_write_and_bash():
    assert "write_file" in BUILD_TOOLS
    assert "run_bash" in BUILD_TOOLS


# ---------------------------------------------------------------------------
# validator_prompt
# ---------------------------------------------------------------------------

def test_validator_prompt_returns_string():
    result = validator_prompt(workspace="/app", plan_artifact="# Plan\n## Phase 1")
    assert isinstance(result, str)
    assert len(result) > 100


def test_validator_prompt_contains_verdict_format():
    result = validator_prompt(workspace="/app", plan_artifact="plan here")
    assert "VERDICT: PASS" in result
    assert "VERDICT: FAIL" in result


def test_validator_prompt_embeds_plan():
    plan = "## Phase 1: Foundation\n- task_type: create"
    result = validator_prompt(workspace="/app", plan_artifact=plan)
    assert plan in result


def test_validator_prompt_includes_checks():
    result = validator_prompt(workspace="/app", plan_artifact="plan")
    assert "Dependency graph" in result or "dependency graph" in result.lower()
    assert "Interface contract" in result or "interface contract" in result.lower()
    assert "Completeness" in result or "completeness" in result.lower()


def test_validator_prompt_read_only_constraint():
    result = validator_prompt(workspace="/app", plan_artifact="plan")
    assert "write_file" in result or "read-only" in result.lower()


def test_validator_prompt_with_spec_content():
    result = validator_prompt(
        workspace="/app",
        plan_artifact="plan",
        spec_content="# Spec\nAdd webhooks.",
    )
    assert "Add webhooks." in result


# ---------------------------------------------------------------------------
# reviewer_prompt
# ---------------------------------------------------------------------------

def test_reviewer_prompt_returns_string():
    result = reviewer_prompt(
        workspace="/app",
        plan_artifact="# Plan",
        touched_files=["/app/handler.py"],
        batch_results="File: /app/handler.py\nAction: created",
    )
    assert isinstance(result, str)
    assert len(result) > 100


def test_reviewer_prompt_contains_verdict_format():
    result = reviewer_prompt(
        workspace="/app",
        plan_artifact="plan",
        touched_files=["/app/a.py"],
        batch_results="result",
    )
    assert "VERDICT: PASS" in result
    assert "VERDICT: FAIL" in result


def test_reviewer_prompt_lists_touched_files():
    result = reviewer_prompt(
        workspace="/app",
        plan_artifact="plan",
        touched_files=["/app/handler.py", "/app/models.py"],
        batch_results="results",
    )
    assert "/app/handler.py" in result
    assert "/app/models.py" in result


def test_reviewer_prompt_embeds_batch_results():
    batch = "File: /app/a.py\nAction: created\nLint: clean"
    result = reviewer_prompt(
        workspace="/app",
        plan_artifact="plan",
        touched_files=["/app/a.py"],
        batch_results=batch,
    )
    assert batch in result


def test_reviewer_prompt_with_architecture_snapshot():
    result = reviewer_prompt(
        workspace="/app",
        plan_artifact="plan",
        touched_files=["/app/a.py"],
        batch_results="result",
        architecture_snapshot="## Architecture\nKey files: ...",
    )
    assert "Architecture" in result


def test_reviewer_prompt_interface_contract_check():
    result = reviewer_prompt(
        workspace="/app",
        plan_artifact="plan",
        touched_files=["/app/a.py"],
        batch_results="result",
    )
    assert "interface_contract" in result or "interface contract" in result.lower()


# ---------------------------------------------------------------------------
# plan_prompt — rewritten with 2-pass analysis
# ---------------------------------------------------------------------------

def test_plan_prompt_mentions_two_passes():
    result = plan_prompt(workspace="/app", spec_content="build X")
    assert "Pass 1" in result or "pass 1" in result.lower()
    assert "Pass 2" in result or "pass 2" in result.lower()


def test_plan_prompt_requests_enriched_specs():
    result = plan_prompt(workspace="/app")
    assert "interface_contract" in result
    assert "pattern_reference" in result
    assert "test_strategy" in result


def test_plan_prompt_requires_structured_output():
    result = plan_prompt(workspace="/app")
    # Should ask for phased output
    assert "Phase" in result


def test_plan_prompt_no_write_file():
    result = plan_prompt(workspace="/app")
    # Should instruct planner to be read-only
    assert "write_file" in result or "read-only" in result.lower()


def test_plan_prompt_includes_workspace():
    result = plan_prompt(workspace="/my/workspace")
    assert "/my/workspace" in result


def test_plan_prompt_includes_spec_content():
    result = plan_prompt(workspace="/app", spec_content="## Feature\nAdd login.")
    assert "Add login." in result


def test_plan_prompt_includes_upstream_context():
    result = plan_prompt(workspace="/app", upstream_context="### Task: Auth\nSummary: done")
    assert "Upstream" in result
    assert "Auth" in result


def test_plan_prompt_includes_project_context():
    result = plan_prompt(workspace="/app", project_context="## Project\nName: Forge")
    assert "Project Context" in result
    assert "Name: Forge" in result


# ---------------------------------------------------------------------------
# build_prompt — new parameters
# ---------------------------------------------------------------------------

def test_build_prompt_includes_task_spec():
    spec = "- task_type: create\n  file_path: /app/a.py"
    result = build_prompt(workspace="/app", plan_artifact="plan", task_spec=spec)
    assert spec in result


def test_build_prompt_includes_architecture_snapshot():
    snap = "## Architecture\nDir structure: ..."
    result = build_prompt(workspace="/app", plan_artifact="plan", architecture_snapshot=snap)
    assert snap in result


def test_build_prompt_includes_review_feedback():
    feedback = "VERDICT: FAIL\nImport issue in handler.py"
    result = build_prompt(workspace="/app", plan_artifact="plan", review_feedback=feedback)
    assert feedback in result


def test_build_prompt_review_feedback_section_header():
    result = build_prompt(
        workspace="/app",
        plan_artifact="plan",
        review_feedback="some issues",
    )
    assert "Review Feedback" in result


def test_build_prompt_qa_feedback_still_works():
    result = build_prompt(
        workspace="/app",
        plan_artifact="plan",
        qa_feedback="QA found regressions",
    )
    assert "QA Feedback" in result
    assert "QA found regressions" in result


def test_build_prompt_enriched_fields_mentioned():
    result = build_prompt(workspace="/app", plan_artifact="plan")
    assert "interface_contract" in result
    assert "preserve" in result
    assert "pattern_reference" in result


def test_build_prompt_includes_project_context():
    result = build_prompt(workspace="/app", plan_artifact="plan", project_context="Use coding standard X")
    assert "Project Context" in result
    assert "coding standard X" in result


# ---------------------------------------------------------------------------
# qa_prompt — test_baseline parameter
# ---------------------------------------------------------------------------

def test_qa_prompt_with_baseline():
    baseline = "Pre-build test baseline:\nTotal: 10, Passed: 10, Failed: 0"
    result = qa_prompt(
        workspace="/app",
        plan_artifact="plan",
        build_artifact="build summary",
        task_description="Add webhooks",
        test_baseline=baseline,
    )
    assert baseline in result


def test_qa_prompt_without_baseline_shows_warning():
    result = qa_prompt(
        workspace="/app",
        plan_artifact="plan",
        build_artifact="build",
        task_description="task",
        test_baseline=None,
    )
    assert "No pre-existing test baseline" in result


def test_qa_prompt_verdict_format():
    result = qa_prompt(
        workspace="/app",
        plan_artifact="plan",
        build_artifact="build",
        task_description="task",
    )
    assert "VERDICT: PASS" in result
    assert "VERDICT: FAIL" in result


def test_qa_prompt_mentions_failure_attribution():
    result = qa_prompt(
        workspace="/app",
        plan_artifact="plan",
        build_artifact="build",
        task_description="task",
    )
    assert "Regression" in result or "regression" in result.lower()
    assert "Pre-existing" in result or "pre-existing" in result.lower()


def test_qa_prompt_mentions_run_bash_or_test_command():
    result = qa_prompt(
        workspace="/app",
        plan_artifact="plan",
        build_artifact="build",
        task_description="task",
    )
    assert "pytest" in result or "run_bash" in result


def test_qa_prompt_includes_all_required_context():
    result = qa_prompt(
        workspace="/app",
        plan_artifact="my plan",
        build_artifact="my build",
        task_description="my task",
    )
    assert "my plan" in result
    assert "my build" in result
    assert "my task" in result
