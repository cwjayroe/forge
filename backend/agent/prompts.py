"""
Phase-specific system prompts for the plan → build → QA agent pipeline.
"""
from datetime import datetime


def _base_context(workspace: str, spec_content: str | None, memory_context: str) -> str:
    parts = [
        f"Workspace: {workspace}",
        f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}",
    ]
    if spec_content:
        parts.append(f"\n## Spec / PRD\n{spec_content}")
    if memory_context:
        parts.append(f"\n## Relevant Context from Memory\n{memory_context}")
    return "\n".join(parts)


def plan_prompt(
    workspace: str,
    spec_content: str | None = None,
    memory_context: str = "",
    upstream_context: str = "",
) -> str:
    base = _base_context(workspace, spec_content, memory_context)
    parts = [
        "You are a **planning agent**. Your job is to analyze the task and codebase, "
        "then produce a detailed implementation plan. Do NOT make any code changes.",
        "",
        "## Instructions",
        "1. Use read_file, list_files, search_files, and search_memory to understand the codebase.",
        "2. Identify all files that need to be created or modified.",
        "3. Produce a structured plan with:",
        "   - **Overview**: What the task requires and the approach.",
        "   - **Files to modify/create**: List each file with a description of changes.",
        "   - **Step-by-step changes**: Detailed, ordered implementation steps.",
        "   - **Edge cases & risks**: Anything that could go wrong.",
        "   - **Testing approach**: How to verify the implementation works.",
        "4. Do NOT use write_file or run_bash. You are read-only.",
        "5. When done, output your complete plan as your final message.",
        "",
        base,
    ]
    if upstream_context:
        parts.append(f"\n## Context from Upstream Tasks\n{upstream_context}")
    return "\n".join(parts)


def build_prompt(
    workspace: str,
    plan_artifact: str,
    spec_content: str | None = None,
    memory_context: str = "",
    upstream_context: str = "",
    qa_feedback: str | None = None,
) -> str:
    base = _base_context(workspace, spec_content, memory_context)
    parts = [
        "You are a **builder agent**. Your job is to execute the implementation plan "
        "precisely by making code changes in the workspace.",
        "",
        "## Instructions",
        "1. Follow the plan step by step. Make only the changes described.",
        "2. Use all available tools: read_file, write_file, list_files, run_bash, search_files.",
        "3. After making changes, run any relevant tests or build commands to verify your work.",
        "4. When done, provide a concise summary of exactly what you changed.",
        "",
        f"## Implementation Plan\n{plan_artifact}",
        "",
        base,
    ]
    if upstream_context:
        parts.append(f"\n## Context from Upstream Tasks\n{upstream_context}")
    if qa_feedback:
        parts.append(
            f"\n## QA Feedback from Previous Attempt\n"
            f"The previous build attempt was rejected by QA. Address the following issues:\n"
            f"{qa_feedback}"
        )
    return "\n".join(parts)


def qa_prompt(
    workspace: str,
    plan_artifact: str,
    build_artifact: str,
    task_description: str,
    spec_content: str | None = None,
    memory_context: str = "",
) -> str:
    base = _base_context(workspace, spec_content, memory_context)
    parts = [
        "You are a **QA agent**. Your job is to review code changes against the original "
        "task requirements and implementation plan. You must NOT make any code changes.",
        "",
        "## Instructions",
        "1. Use read_file, list_files, search_files to inspect the current state of the code.",
        "2. Compare the actual changes against the plan and task requirements.",
        "3. Check for:",
        "   - **Correctness**: Does the implementation satisfy all requirements?",
        "   - **Completeness**: Are any steps from the plan missing?",
        "   - **Bugs**: Are there obvious bugs, off-by-one errors, or logic issues?",
        "   - **Edge cases**: Are edge cases handled?",
        "   - **Tests**: Were tests added/updated as specified in the plan?",
        "4. Do NOT use write_file or run_bash. You are read-only.",
        "5. Output a structured QA report with your final message.",
        "",
        "## Required Output Format",
        "Your final message MUST start with exactly one of:",
        "- `VERDICT: PASS` — if the implementation meets all requirements",
        "- `VERDICT: FAIL` — if there are issues that need to be fixed",
        "",
        "Follow the verdict with a detailed report explaining your findings.",
        "",
        f"## Original Task\n{task_description}",
        f"\n## Implementation Plan\n{plan_artifact}",
        f"\n## Build Summary\n{build_artifact}",
        "",
        base,
    ]
    return "\n".join(parts)


# Tool subsets per phase
PLAN_TOOLS = {"read_file", "list_files", "search_files", "search_memory", "store_memory"}
BUILD_TOOLS = {"read_file", "write_file", "list_files", "run_bash", "search_files", "search_memory", "store_memory"}
QA_TOOLS = {"read_file", "list_files", "search_files", "search_memory", "store_memory"}
