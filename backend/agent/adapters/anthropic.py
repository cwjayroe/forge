"""
Anthropic Claude adapter (optional cloud fallback).
Requires ANTHROPIC_API_KEY environment variable.
"""
import json
import os
from typing import Optional

import httpx

from .base import ModelAdapter, Response, ToolCall

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter(ModelAdapter):
    """
    Adapter for the Anthropic Messages API.
    Requires ANTHROPIC_API_KEY in environment.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5",
        api_key: Optional[str] = None,
        max_tokens: int = 8096,
        timeout: float = 120.0,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        converted = []
        for t in tools:
            converted.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
            })
        return converted

    def _convert_messages(self, messages: list[dict]) -> tuple[Optional[str], list[dict]]:
        """Split out system message and convert tool results to Anthropic format."""
        system: Optional[str] = None
        converted: list[dict] = []

        for m in messages:
            role = m.get("role")
            if role == "system":
                system = m.get("content", "")
                continue
            if role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }],
                })
                continue
            if role == "assistant" and "tool_calls" in m:
                content_blocks = []
                if m.get("content"):
                    content_blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"],
                    })
                converted.append({"role": "assistant", "content": content_blocks})
                continue
            converted.append(m)

        return system, converted

    async def complete(self, messages: list[dict], tools: list[dict]) -> Response:
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")

        system, converted_messages = self._convert_messages(messages)

        payload: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": converted_messages,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._convert_tools(tools)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(ANTHROPIC_API_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Anthropic API error {e.response.status_code}: {e.response.text}")

        text: Optional[str] = None
        tool_calls: list[ToolCall] = []

        for block in data.get("content", []):
            if block["type"] == "text":
                text = block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append(ToolCall(
                    id=block["id"],
                    name=block["name"],
                    input=block["input"],
                ))

        stop_reason_raw = data.get("stop_reason", "end_turn")
        stop_reason = "tool_use" if stop_reason_raw == "tool_use" else "end_turn"

        return Response(text=text, stop_reason=stop_reason, tool_calls=tool_calls)
