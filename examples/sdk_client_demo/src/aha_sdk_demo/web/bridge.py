"""浏览器只负责采集和播放；所有 Realtime 操作都调用 aha 的公开 API。"""

from __future__ import annotations

import asyncio
import base64
import logging
import ssl
from typing import Any, Literal

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aha import AsyncAudioHarness, AudioFormat, Limits
from aha.contracts.protocol.aha_v1 import AhaAgentBackendQuestionAnswer
from aha.errors import AhaError

logger = logging.getLogger(__name__)


class OpenSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["connect"]
    model: str = Field(min_length=1, max_length=256)
    mode: Literal["transcription", "realtime"]
    sample_rate: Literal[16000, 24000]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["text", "commit", "clear", "cancel", "approval", "question"]
    text: str = Field(default="", max_length=16000)
    request_id: str = Field(default="", max_length=256)
    approval_id: str = Field(default="", max_length=256)
    approved: bool = False
    backend: bool = True
    answers: list[AhaAgentBackendQuestionAnswer] = Field(
        default_factory=list, max_length=32
    )


def browser_event(event: BaseModel) -> dict[str, Any]:
    def encode(value: Any) -> Any:
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        if isinstance(value, dict):
            return {key: encode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [encode(item) for item in value]
        return value

    payload = getattr(event, "raw", None) or {
        **event.model_dump(mode="python"),
        **(event.model_extra or {}),
    }
    return encode(payload)


async def bridge(websocket: WebSocket, base_url: str, api_key: str | None) -> None:
    write_lock = asyncio.Lock()
    tasks: list[asyncio.Task[None]] = []

    async def emit(payload: dict[str, Any]) -> None:
        async with write_lock:
            await asyncio.wait_for(websocket.send_json(payload), timeout=10)

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        if len(raw) > 32768:
            raise ValueError("连接配置过大")
        config = OpenSession.model_validate_json(raw)
        if not config.model.strip():
            raise ValueError("请输入已发布资源的 model slug")
        await emit({"kind": "status", "status": "connecting"})
        ssl_context = None
        if base_url.startswith("https://"):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        async with AsyncAudioHarness(
            base_url=base_url,
            api_key=api_key,
            ssl_context=ssl_context,
            proxy=None,
            limits=Limits(send_timeout=10, consumer_timeout=10, close_timeout=3),
        ) as client:
            resource = (
                client.transcriptions
                if config.mode == "transcription"
                else client.realtime
            )
            async with resource.connect(
                model=config.model,
                audio=AudioFormat.pcm16(config.sample_rate),
                session={
                    "audio": {
                        "input": {
                            "turn_detection": (
                                {"type": "server_vad"}
                                if config.mode == "transcription"
                                else None
                            )
                        }
                    }
                },
            ) as session:
                await emit({"kind": "status", "status": "ready"})

                async def events() -> None:
                    async for event in session.events():
                        await emit({"kind": "event", "event": browser_event(event)})

                async def commands() -> None:
                    audio_bytes = 0
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        audio = message.get("bytes")
                        if audio is not None:
                            if not audio or len(audio) > 131072 or len(audio) % 2:
                                raise ValueError("音频应为不超过 128 KiB 的 PCM S16LE")
                            await session.send_audio(audio)
                            audio_bytes += len(audio)
                            await emit(
                                {"kind": "sent", "action": "audio", "bytes": len(audio)}
                            )
                            continue
                        raw_command = message.get("text", "")
                        try:
                            if len(raw_command) > 32768:
                                raise ValueError("指令过大")
                            command = Command.model_validate_json(raw_command)
                            if command.action == "text":
                                if (
                                    config.mode != "realtime"
                                    or not command.text.strip()
                                ):
                                    raise ValueError(
                                        "文本对话需要语音 Agent 模式和非空文本"
                                    )
                                await session.conversation.item.create(
                                    {
                                        "type": "message",
                                        "role": "user",
                                        "content": [
                                            {"type": "input_text", "text": command.text}
                                        ],
                                    }
                                )
                                await session.response.create()
                            elif command.action == "commit":
                                if audio_bytes == 0:
                                    raise ValueError("没有可提交的音频")
                                await session.input_audio_buffer.commit()
                                audio_bytes = 0
                                if config.mode == "realtime":
                                    await session.response.create()
                            elif command.action == "clear":
                                await session.input_audio_buffer.clear()
                                audio_bytes = 0
                            elif command.action == "cancel":
                                await session.response.cancel()
                            elif command.action == "approval":
                                if not command.approval_id or (
                                    command.backend and not command.request_id
                                ):
                                    raise ValueError("审批标识缺失")
                                if command.backend:
                                    await session.agent_backend.respond_approval(
                                        command.request_id,
                                        command.approval_id,
                                        command.approved,
                                    )
                                else:
                                    await session.tools.respond_approval(
                                        command.approval_id, command.approved
                                    )
                            elif command.action == "question":
                                if not command.request_id:
                                    raise ValueError("问题请求标识缺失")
                                await session.agent_backend.respond_question(
                                    command.request_id,
                                    [answer.model_dump() for answer in command.answers],
                                )
                            await emit({"kind": "sent", "action": command.action})
                        except (ValidationError, ValueError) as exc:
                            message = str(exc)
                            if isinstance(exc, ValidationError):
                                message = "指令参数无效：" + "; ".join(
                                    f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                                    for error in exc.errors(
                                        include_url=False, include_input=False
                                    )[:3]
                                )
                            await emit(
                                {
                                    "kind": "error",
                                    "fatal": False,
                                    "message": message[:800],
                                }
                            )

                tasks = [asyncio.create_task(events()), asyncio.create_task(commands())]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                # Stop both pumps before leaving the SDK context; no orphan reader or producer.
                for task in tasks:
                    if task not in done:
                        task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        raise result
    except WebSocketDisconnect:
        pass
    except (AhaError, ValueError, asyncio.TimeoutError) as exc:
        payload: dict[str, Any] = {
            "kind": "error",
            "fatal": True,
            "message": str(exc) or "连接或消费超时",
        }
        if getattr(exc, "event", None) is not None:
            payload["event"] = browser_event(exc.event)
        try:
            await emit(payload)
        except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
            pass
    except Exception:
        logger.exception("SDK demo bridge failed")
        try:
            await emit(
                {
                    "kind": "error",
                    "fatal": True,
                    "message": "桥接服务异常，请查看终端日志后重新连接",
                }
            )
        except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
            pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close()
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
