import json
from typing import Optional

import httpx

from .base import ModelAdapter, Response, ToolCall


class OllamaAdapter(ModelAdapter):
    """
    Adapter for Ollama's OpenAI-compatible /api/chat endpoint.
    Default model: qwen2.5-coder:32b
    """

    def __init__(
        self,
        model: str = "qwen2.5-coder:32b",
        host: str = "http://localhost:11434",
        timeout: float = 120.0,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert tool definitions to Ollama's expected format (OpenAI-compatible)."""
        converted = []
        for t in tools:
            converted.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return converted

    async def complete(self, messages: list[dict], tools: list[dict]) -> Response:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = self._convert_tools(tools)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.host}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.host}. "
                "Make sure Ollama is running: `ollama serve`"
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama returned HTTP {e.response.status_code}: {e.response.text}")

        message = data.get("message", {})
        finish_reason = data.get("done_reason", "stop")

        text: Optional[str] = message.get("content") or None
        tool_calls: list[ToolCall] = []

        raw_tool_calls = message.get("tool_calls") or []
        for i, tc in enumerate(raw_tool_calls):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{i}"),
                name=fn.get("name", ""),
                input=args,
            ))

        if tool_calls:
            stop_reason = "tool_use"
        elif finish_reason in ("stop", "length", "eos"):
            stop_reason = "end_turn"
        else:
            stop_reason = "end_turn"

        return Response(text=text, stop_reason=stop_reason, tool_calls=tool_calls)
