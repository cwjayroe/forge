"""
Agent tool implementations.
All file-system tools are scoped to the workspace path for security.
"""
import asyncio
import difflib
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory import MemoryClient


# ---------------------------------------------------------------------------
# Security helper
# ---------------------------------------------------------------------------

def _safe_path(path: str, workspace: str) -> Path:
    """
    Resolve a path relative to workspace. Raises ValueError if it escapes workspace.
    """
    ws = Path(workspace).resolve()
    # Allow absolute paths that are inside the workspace, or relative paths
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ws / candidate
    resolved = candidate.resolve()
    if not str(resolved).startswith(str(ws)):
        raise ValueError(f"Path '{path}' is outside the workspace '{workspace}'")
    return resolved


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def read_file(path: str, workspace: str) -> str:
    target = _safe_path(path, workspace)
    if not target.exists():
        return f"Error: file not found: {path}"
    if not target.is_file():
        return f"Error: '{path}' is not a file"
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading file: {e}"


async def write_file(path: str, content: str, workspace: str) -> str:
    target = _safe_path(path, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)

    old_lines: list[str] = []
    if target.exists():
        try:
            old_lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError:
            pass

    target.write_text(content, encoding="utf-8")
    new_lines = content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    ))
    if diff:
        return "".join(diff)
    return f"File written: {path} (no changes)"


async def list_files(path: str, workspace: str) -> str:
    target = _safe_path(path, workspace)
    if not target.exists():
        return f"Error: path not found: {path}"
    if not target.is_dir():
        return f"Error: '{path}' is not a directory"

    # Try git ls-files first to respect .gitignore
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
            return json.dumps(files)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: recursive glob, skip hidden and common noise dirs
    SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}
    files = []
    for p in sorted(target.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file():
            try:
                rel = str(p.relative_to(target))
                files.append(rel)
            except ValueError:
                pass
    return json.dumps(files)


async def run_bash(command: str, workspace: str) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return "Error: command timed out after 30 seconds"

        output = stdout.decode("utf-8", errors="replace")
        rc = proc.returncode
        if rc != 0:
            return f"Exit code {rc}:\n{output}"
        return output or "(no output)"
    except Exception as e:
        return f"Error running command: {e}"


async def search_files(pattern: str, workspace: str) -> str:
    ws = Path(workspace).resolve()

    # Try ripgrep first
    try:
        proc = await asyncio.create_subprocess_exec(
            "rg", "--line-number", "--no-heading", pattern, str(ws),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        output = stdout.decode("utf-8", errors="replace").strip()
        if output:
            return output
        return "(no matches)"
    except (FileNotFoundError, asyncio.TimeoutError):
        pass

    # Fallback: grep
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-rn", pattern, str(ws),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        output = stdout.decode("utf-8", errors="replace").strip()
        return output or "(no matches)"
    except (FileNotFoundError, asyncio.TimeoutError):
        return "Error: neither ripgrep nor grep is available"


async def search_memory(query: str, memory: "MemoryClient") -> str:
    results = await memory.search(query)
    if not results:
        return "(no relevant memories found)"
    return json.dumps(results, indent=2, default=str)


async def store_memory(content: str, memory: "MemoryClient", metadata: dict | None = None) -> str:
    memory_id = await memory.store(content, metadata or {})
    return f"Stored memory with id: {memory_id}"


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file, relative to workspace root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file in the workspace. Returns a unified diff of the changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file, relative to workspace root"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory within the workspace. Respects .gitignore.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to workspace root. Use '.' for workspace root."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_bash",
        "description": "Execute a shell command in the workspace directory. Timeout is 30 seconds.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for a pattern across all files in the workspace using ripgrep or grep.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex or literal pattern to search for"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_memory",
        "description": "Search the memory store for relevant context about previous work.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to find relevant memories"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "store_memory",
        "description": "Persist important information to the memory store for future runs.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to store in memory"},
                "metadata": {"type": "object", "description": "Optional metadata tags"},
            },
            "required": ["content"],
        },
    },
]
