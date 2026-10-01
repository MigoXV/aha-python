from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar
from uuid import uuid4

R = TypeVar("R")


class Resource(Generic[R]):
    def __init__(self, send: Callable[[dict[str, Any]], R]) -> None:
        self._send = send


class SessionResource(Resource[R]):
    def update(self, session: dict[str, Any]) -> R:
        return self._send({"type": "session.update", "session": session})


class InputAudioBuffer(Resource[R]):
    def append(self, audio: bytes) -> R:
        if not isinstance(audio, bytes) or len(audio) % 2:
            raise ValueError("audio 必须为完整 PCM S16LE bytes")
        return self._send({"type": "input_audio_buffer.append", "audio": audio})

    def commit(self) -> R:
        return self._send({"type": "input_audio_buffer.commit"})

    def clear(self) -> R:
        return self._send({"type": "input_audio_buffer.clear"})


class ResponseResource(Resource[R]):
    def create(self, response: dict[str, Any] | None = None) -> R:
        return self._send(
            {
                "type": "response.create",
                **({"response": response} if response is not None else {}),
            }
        )

    def cancel(self, response_id: str | None = None) -> R:
        return self._send(
            {
                "type": "response.cancel",
                **({"response_id": response_id} if response_id is not None else {}),
            }
        )


class ConversationItem(Resource[R]):
    def create(self, item: dict[str, Any], previous_item_id: str | None = None) -> R:
        return self._send(
            {
                "type": "conversation.item.create",
                "item": item,
                **(
                    {"previous_item_id": previous_item_id}
                    if previous_item_id is not None
                    else {}
                ),
            }
        )

    def retrieve(self, item_id: str) -> R:
        return self._send({"type": "conversation.item.retrieve", "item_id": item_id})

    def delete(self, item_id: str) -> R:
        return self._send({"type": "conversation.item.delete", "item_id": item_id})

    def truncate(self, item_id: str, audio_end_ms: int, content_index: int = 0) -> R:
        return self._send(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "audio_end_ms": audio_end_ms,
                "content_index": content_index,
            }
        )


class Conversation(Resource[R]):
    def __init__(self, send: Callable[[dict[str, Any]], R]) -> None:
        super().__init__(send)
        self.item = ConversationItem(send)


class Tools(Resource[R]):
    def submit_output(self, call_id: str, output: str) -> R:
        return self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )

    def respond_approval(self, approval_request_id: str, approved: bool) -> R:
        return self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "mcp_approval_response",
                    "id": f"item_{uuid4().hex}",
                    "approval_request_id": approval_request_id,
                    "approve": approved,
                },
            }
        )


class AgentBackend(Resource[R]):
    def respond_approval(self, request_id: str, approval_id: str, approved: bool) -> R:
        return self._send(
            {
                "type": "x_aha.agent_backend.approval.respond",
                "request_id": request_id,
                "approval_id": approval_id,
                "approved": approved,
            }
        )

    def respond_question(self, request_id: str, answers: list[dict[str, Any]]) -> R:
        return self._send(
            {
                "type": "x_aha.agent_backend.question.respond",
                "request_id": request_id,
                "answers": answers,
            }
        )

    def cancel(self) -> R:
        return self._send({"type": "x_aha.agent_backend.cancel"})


class Resources(Generic[R]):
    def __init__(self, send: Callable[[dict[str, Any]], R]) -> None:
        self.session = SessionResource(send)
        self.input_audio_buffer = InputAudioBuffer(send)
        self.response = ResponseResource(send)
        self.conversation = Conversation(send)
        self.tools = Tools(send)
        self.agent_backend = AgentBackend(send)
