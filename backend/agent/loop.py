"""
Core agent loop. Model-agnostic; driven by a ModelAdapter.
"""
import asyncio
import json
import re
from datetime import datetime
from typing import Awaitable, Callable, Optional

from .adapters.base import ModelAdapter, ToolCall
from .tools import (
    TOOL_DEFINITIONS,
    list_files,
    read_file,
    run_bash,
    search_files,
    search_memory,
    store_memory,
    write_file,
)

from ..memory import MemoryClient

MAX_ITERATIONS = 50

_WRITE_PATTERNS = re.compile(
    r'(?:File:\s*\S+.*Action:\s*creat|'
    r'write_file\s*\(|'
    r'I (?:will |would |have |\'ll )(?:create|write|save|add)\s+(?:the |a )?file|'
    r'creating (?:the |a )?file\s|'
    r'```\s*\n\s*# (?:File|Path):\s)',
    re.IGNORECASE,
)

# Matches JSON blocks in code fences
_CODE_FENCE_RE = re.compile(r'```(?:json)?\s*\n(.*?)\n\s*```', re.DOTALL)

# Tool names we know about — used to validate extracted calls
_KNOWN_TOOLS = {"read_file", "write_file", "list_files", "run_bash", "search_files", "search_memory", "store_memory"}


def _extract_tool_calls_from_text(text: str) -> list[ToolCall]:
    """Extract tool calls that models embed as JSON in their text output.

    Some local models (e.g. Ollama qwen2.5-coder) output tool invocations as
    JSON code blocks instead of using the structured tool-calling API. This
    function parses those and returns them as ToolCall objects so the agent
    loop can execute them.
    """
    calls: list[ToolCall] = []

    # First try code-fenced JSON blocks
    candidates = [m.group(1).strip() for m in _CODE_FENCE_RE.finditer(text)]

    # Also try bare JSON objects containing "name" and "arguments"
    if not candidates:
        # Simple brace-matching: find top-level { ... } containing "name"
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == '{' and depth == 0:
                start = i
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])
                    start = None

    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name", "")
        args = data.get("arguments", {})
        if name not in _KNOWN_TOOLS:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                continue
        if not isinstance(args, dict):
            continue
        calls.append(ToolCall(id=f"text_call_{len(calls)}", name=name, input=args))
    return calls


def _text_describes_write(text: str) -> bool:
    """Detect if the model described a file write action in text without calling the tool."""
    return bool(_WRITE_PATTERNS.search(text))


class AgentAbortedError(Exception):
    pass


class Agent:
    def __init__(
        self,
        model: ModelAdapter,
        workspace: str,
        memory: MemoryClient,
        approval_gate: Optional[Callable[[str], Awaitable[bool]]] = None,
        allowed_tools: Optional[set[str]] = None,
    ):
        self.model = model
        self.workspace = workspace
        self.memory = memory
        self._approval_gate = approval_gate
        self._allowed_tools = allowed_tools  # None means all tools allowed
        self._abort = asyncio.Event()

    def abort(self) -> None:
        self._abort.set()

    def _get_tool_definitions(self) -> list[dict]:
        """Return tool definitions filtered by allowed_tools."""
        if self._allowed_tools is None:
            return TOOL_DEFINITIONS
        return [t for t in TOOL_DEFINITIONS if t["name"] in self._allowed_tools]

    async def run(
        self,
        task_description: str,
        on_event: Callable,
        system_prompt: Optional[str] = None,
        spec_content: Optional[str] = None,
    ) -> str:
        """
        Run the agent loop for a task.

        Args:
            task_description: The task/user message to send to the model
            on_event: Async callback called with each event dict
            system_prompt: Pre-built system prompt (used by phase orchestrator).
                           If None, builds a default prompt (legacy behavior).
            spec_content: Optional spec/PRD file content (only used if system_prompt is None)

        Returns:
            Summary string (last text response from the model)
        """
        if system_prompt is None:
            # Legacy behavior: build a default system prompt
            memory_results = await self.memory.search(task_description[:100])
            memory_context = ""
            if memory_results:
                memory_context = "\n\nRelevant context from memory:\n" + json.dumps(memory_results, indent=2, default=str)

            system_parts = [
                f"You are an expert software engineer working in the workspace at: {self.workspace}",
                f"Today's date: {datetime.utcnow().strftime('%Y-%m-%d')}",
                "Use the provided tools to read, write, and explore the codebase to complete the task.",
                "When you are done, call store_memory with a concise summary of what you did.",
                "Be precise and make only the changes needed to complete the task.",
            ]
            if spec_content:
                system_parts.append(f"\n\nSpec / PRD:\n{spec_content}")
            if memory_context:
                system_parts.append(memory_context)

            system_prompt = "\n".join(system_parts)

        tool_defs = self._get_tool_definitions()

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_description},
        ]

        summary = ""
        iterations = 0
        write_file_called = False

        while iterations < MAX_ITERATIONS:
            if self._abort.is_set():
                raise AgentAbortedError("Agent was aborted")

            iterations += 1
            response = await self.model.complete(messages, tool_defs)

            # Emit text event
            if response.text:
                summary = response.text
                await on_event({"type": "text", "content": response.text})

            if response.stop_reason == "end_turn":
                # Try to extract tool calls embedded as JSON in text output.
                # Some local models (e.g. Ollama) output tool calls as text
                # instead of using the structured tool-calling API.
                if response.text:
                    text_tool_calls = _extract_tool_calls_from_text(response.text)
                    if text_tool_calls:
                        await on_event({
                            "type": "text",
                            "content": f"Extracted {len(text_tool_calls)} tool call(s) from text output (model did not use structured tool calling).",
                        })
                        # Build a synthetic assistant message with tool_calls
                        assistant_msg = {
                            "role": "assistant",
                            "content": response.text,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.name,
                                        "arguments": json.dumps(tc.input),
                                    },
                                }
                                for tc in text_tool_calls
                            ],
                        }
                        messages.append(assistant_msg)
                        for tc in text_tool_calls:
                            if self._abort.is_set():
                                raise AgentAbortedError("Agent was aborted")
                            if tc.name == "write_file":
                                write_file_called = True
                            await on_event({
                                "type": "tool_call",
                                "name": tc.name,
                                "input": tc.input,
                            })
                            result = await self._execute_tool(tc)
                            await on_event({
                                "type": "tool_result",
                                "name": tc.name,
                                "result": result,
                            })
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            })
                        continue
                break

            if response.stop_reason == "tool_use" and response.tool_calls:
                # Build assistant message with tool_calls for the message history
                assistant_msg: dict = {"role": "assistant"}
                if response.text:
                    assistant_msg["content"] = response.text
                else:
                    assistant_msg["content"] = None
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages.append(assistant_msg)

                for tool_call in response.tool_calls:
                    if self._abort.is_set():
                        raise AgentAbortedError("Agent was aborted")

                    if tool_call.name == "write_file":
                        write_file_called = True

                    await on_event({
                        "type": "tool_call",
                        "name": tool_call.name,
                        "input": tool_call.input,
                    })

                    result = await self._execute_tool(tool_call)

                    await on_event({
                        "type": "tool_result",
                        "name": tool_call.name,
                        "result": result,
                    })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })
            else:
                # Unexpected stop reason — treat as done
                break

        if iterations >= MAX_ITERATIONS:
            await on_event({"type": "error", "content": "Max iterations reached"})

        return summary

    async def _execute_tool(self, tool_call: ToolCall) -> str:
        name = tool_call.name
        args = tool_call.input

        try:
            if name == "read_file":
                return await read_file(args["path"], self.workspace)
            elif name == "write_file":
                result = await write_file(args["path"], args["content"], self.workspace)
                return result
            elif name == "list_files":
                return await list_files(args.get("path", "."), self.workspace)
            elif name == "run_bash":
                if self._approval_gate:
                    approved = await self._approval_gate(args["command"])
                    if not approved:
                        return "Command rejected by user."
                return await run_bash(args["command"], self.workspace)
            elif name == "search_files":
                return await search_files(args["pattern"], self.workspace)
            elif name == "search_memory":
                return await search_memory(args["query"], self.memory)
            elif name == "store_memory":
                return await store_memory(
                    args["content"],
                    self.memory,
                    args.get("metadata"),
                )
            else:
                return f"Error: unknown tool '{name}'"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Tool error ({name}): {e}"
