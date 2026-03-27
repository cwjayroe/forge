"""
Core agent loop. Model-agnostic; driven by a ModelAdapter.
"""
import asyncio
import json
from datetime import datetime
from typing import Callable, Optional

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


class AgentAbortedError(Exception):
    pass


class Agent:
    def __init__(
        self,
        model: ModelAdapter,
        workspace: str,
        memory: MemoryClient,
    ):
        self.model = model
        self.workspace = workspace
        self.memory = memory
        self._abort = asyncio.Event()

    def abort(self) -> None:
        self._abort.set()

    async def run(
        self,
        task_description: str,
        spec_content: Optional[str],
        on_event: Callable,
    ) -> str:
        """
        Run the agent loop for a task.

        Args:
            task_description: The task to perform
            spec_content: Optional spec/PRD file content to include in context
            on_event: Async callback called with each event dict

        Returns:
            Summary string (last text response from the model)
        """
        # 1. Pull relevant memory context
        memory_results = await self.memory.search(task_description[:100])
        memory_context = ""
        if memory_results:
            memory_context = "\n\nRelevant context from memory:\n" + json.dumps(memory_results, indent=2, default=str)

        # 2. Build system prompt
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

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_description},
        ]

        summary = ""
        iterations = 0

        while iterations < MAX_ITERATIONS:
            if self._abort.is_set():
                raise AgentAbortedError("Agent was aborted")

            iterations += 1
            response = await self.model.complete(messages, TOOL_DEFINITIONS)

            # Emit text event
            if response.text:
                summary = response.text
                await on_event({"type": "text", "content": response.text})

            if response.stop_reason == "end_turn":
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
