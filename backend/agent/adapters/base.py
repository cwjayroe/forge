from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Response:
    text: Optional[str]
    stop_reason: str          # "end_turn" | "tool_use"
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelAdapter(ABC):
    @abstractmethod
    async def complete(self, messages: list[dict], tools: list[dict]) -> Response:
        """
        Send messages to the model and return a Response.

        Args:
            messages: OpenAI-style message list
            tools: List of tool definitions in OpenAI function-calling format

        Returns:
            Response with text, stop_reason, and any tool_calls
        """
        ...
