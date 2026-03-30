"""
Claude Code CLI adapter.

Delegates the entire task to a single `claude` CLI invocation using the
/feature-plan-and-build skill, which handles planning, building, and QA
internally.  Forge becomes a pure orchestration & streaming layer.
"""
import asyncio
import json
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# MCP config for the memory server
# ---------------------------------------------------------------------------

MCP_CONFIG = json.dumps({
    "mcpServers": {
        "memory": {
            "command": "/Users/willjayroe/Desktop/repos/memories/.env/bin/python3.11",
            "args": ["/Users/willjayroe/Desktop/repos/memories/mcp_server.py"],
        }
    }
})

# ---------------------------------------------------------------------------
# Active subprocess tracking (for abort)
# ---------------------------------------------------------------------------

_active_processes: dict[str, asyncio.subprocess.Process] = {}


def abort_claude_code(run_id: str) -> None:
    """Terminate the claude subprocess for a run, if running."""
    proc = _active_processes.pop(run_id, None)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------------------
# Stream-JSON event parsing
# ---------------------------------------------------------------------------

async def _parse_and_emit(line: str, on_event: Callable) -> Optional[str]:
    """
    Parse one stream-json line from claude CLI and emit forge events.

    Returns the final result text if this is a result event, else None.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    event_type = event.get("type")

    if event_type == "assistant":
        message = event.get("message", {})
        for block in message.get("content", []):
            block_type = block.get("type")

            if block_type == "text":
                await on_event({"type": "text", "content": block["text"]})

            elif block_type == "tool_use":
                await on_event({
                    "type": "tool_call",
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                })

    elif event_type == "user":
        message = event.get("message", {})
        for block in message.get("content", []):
            block_type = block.get("type")

            if block_type == "tool_result":
                content = block.get("content", "")
                if isinstance(content, str) and len(content) > 2000:
                    content = content[:2000] + "\n... (truncated)"
                await on_event({
                    "type": "tool_result",
                    "name": block.get("tool_use_id", ""),
                    "result": content,
                })

    elif event_type == "result":
        result_text = event.get("result", "")
        cost = event.get("total_cost_usd")
        num_turns = event.get("num_turns")
        if cost is not None or num_turns is not None:
            await on_event({
                "type": "text",
                "content": f"[Claude Code] Completed in {num_turns} turn(s), cost: ${cost:.4f}",
            })
        return result_text

    return None


# ---------------------------------------------------------------------------
# Main execution — single pass via /feature-plan-and-build
# ---------------------------------------------------------------------------

async def run_claude_code_task(
    run_id: str,
    model_name: str,
    workspace: str,
    task_description: str,
    on_event: Optional[Callable] = None,
    skill_slash_command: Optional[str] = None,
) -> tuple[str, str]:
    """
    Execute a full task by shelling out to the `claude` CLI with the
    /feature-plan-and-build skill.

    The skill handles its own planning, building, and QA — forge only needs
    to stream events and capture the final result.

    Args:
        run_id: Forge run ID (used for abort tracking).
        model_name: Model short name (e.g. "opus", "sonnet").
        workspace: Working directory for the subprocess.
        task_description: The full task/feature description.
        on_event: Async callback for streaming events.

    Returns:
        (status, artifact) — "completed"/"failed" and the output text.
    """
    if on_event is None:
        async def on_event(e):
            pass

    # Build the prompt: invoke the skill with the task description.
    # "autonomous" / "just build it" keywords tell the skill to skip the
    # interactive approval gate.
    slash_cmd = skill_slash_command or "/feature-plan-and-build"
    prompt = (
        f"{slash_cmd} autonomous\n\n"
        f"{task_description}"
    )

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model_name,
        "--permission-mode", "bypassPermissions",
        "--mcp-config", MCP_CONFIG,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )
        _active_processes[run_id] = proc

        artifact = ""

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            result_text = await _parse_and_emit(line_str, on_event)
            if result_text is not None:
                artifact = result_text

        await proc.wait()

        if proc.returncode != 0:
            stderr_output = ""
            if proc.stderr:
                stderr_bytes = await proc.stderr.read()
                stderr_output = stderr_bytes.decode("utf-8", errors="replace").strip()

            error_msg = f"claude CLI exited with code {proc.returncode}"
            if stderr_output:
                error_msg += f": {stderr_output[:500]}"

            await on_event({"type": "error", "content": error_msg})
            return "failed", error_msg

        if not artifact:
            artifact = "(no output)"

        return "completed", artifact

    except FileNotFoundError:
        error_msg = (
            "claude CLI not found. Install Claude Code: "
            "https://docs.anthropic.com/en/docs/claude-code"
        )
        await on_event({"type": "error", "content": error_msg})
        return "failed", error_msg

    except Exception as e:
        error_msg = f"Claude Code execution error: {e}"
        await on_event({"type": "error", "content": error_msg})
        return "failed", error_msg

    finally:
        _active_processes.pop(run_id, None)
