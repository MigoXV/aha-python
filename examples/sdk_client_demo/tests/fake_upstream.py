"""仅用于测试的上游：真实 WebSocket + SDK 编解码，没有产品端模拟回退。"""

from __future__ import annotations

import base64
import socket
import threading
import time
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect


def fake_app() -> FastAPI:
    app = FastAPI()
    app.state.received = []
    app.state.active = 0

    @app.get("/v1/models")
    async def models():
        return {
            "data": [
                {"id": "test-workflow"},
                {"id": "test-agent"},
                {"id": "approval-agent"},
            ]
        }

    @app.websocket("/v1/realtime")
    async def realtime(ws: WebSocket):
        await ws.accept()
        app.state.active += 1
        audio = bytearray()
        seq = 0
        model = ws.query_params.get("model")
        automatic_vad = False
        interim_sent = False
        input_item = "input-live"

        async def emit(kind: str, **fields):
            nonlocal seq
            seq += 1
            await ws.send_json({"type": kind, "event_id": f"event-{seq}", **fields})

        async def output():
            await emit(
                "response.created",
                response={
                    "id": "r",
                    "object": "realtime.response",
                    "status": "in_progress",
                    "output": [],
                },
            )
            await emit(
                "response.output_text.delta",
                response_id="r",
                item_id=f"assistant-{seq}",
                output_index=0,
                content_index=0,
                delta="你好，SDK 客户端连接正常。",
            )
            await emit(
                "response.output_audio.delta",
                response_id="r",
                item_id="assistant-audio",
                output_index=0,
                content_index=0,
                delta=base64.b64encode(b"\0\0" * 2400).decode(),
            )
            await emit(
                "response.done",
                response={
                    "id": "r",
                    "object": "realtime.response",
                    "status": "completed",
                    "output": [],
                },
            )

        try:
            await emit(
                "session.created",
                session={
                    "type": "realtime" if "agent" in (model or "") else "transcription"
                },
            )
            while True:
                event = await ws.receive_json()
                app.state.received.append(event)
                if event["type"] == "session.update":
                    automatic_vad = event["session"].get("audio", {}).get(
                        "input", {}
                    ).get("turn_detection") == {"type": "server_vad"}
                    model = model or event["session"].get("audio", {}).get(
                        "input", {}
                    ).get("transcription", {}).get("model")
                    if model == "reject":
                        await emit(
                            "error",
                            error={
                                "type": "invalid_request_error",
                                "code": "invalid_model",
                                "message": "模型未发布",
                            },
                        )
                    else:
                        await emit("session.updated", session=event["session"])
                elif event["type"] == "input_audio_buffer.append":
                    audio.extend(base64.b64decode(event["audio"]))
                    if automatic_vad and not interim_sent:
                        interim_sent = True
                        await emit(
                            "x_aha.input_audio_transcription.interim",
                            item_id=input_item,
                            content_index=0,
                            revision=1,
                            transcript="录音期间收到的临时转写。",
                            audio_start_ms=0,
                            audio_end_ms=20,
                        )
                elif event["type"] == "input_audio_buffer.commit":
                    assert len(audio) > 0
                    if interim_sent:
                        await emit(
                            "x_aha.input_audio_transcription.interim_cleared",
                            item_id=input_item,
                            reason="primary_transcript_started",
                        )
                    await emit(
                        "conversation.item.input_audio_transcription.completed",
                        item_id=input_item,
                        content_index=0,
                        transcript="麦克风音频已通过 SDK 提交。",
                        usage={"type": "duration", "seconds": 1},
                    )
                    audio.clear()
                    interim_sent = False
                elif event["type"] == "input_audio_buffer.clear":
                    audio.clear()
                    interim_sent = False
                elif event["type"] == "response.create":
                    if model == "approval-agent":
                        await emit(
                            "x_aha.agent_backend.approval.requested",
                            backend_id="test",
                            backend_session_id="s",
                            request_id="request-1",
                            approval_id="approval-1",
                            tool_name="read_test",
                            reason="测试显式审批",
                        )
                    elif model == "mcp-agent":
                        await emit(
                            "conversation.item.added",
                            previous_item_id=None,
                            item={
                                "id": "mcp-1",
                                "type": "mcp_approval_request",
                                "name": "read_test",
                                "server_label": "test",
                                "arguments": "{}",
                            },
                        )
                    elif model == "question-agent":
                        await emit(
                            "x_aha.agent_backend.question.requested",
                            backend_id="test",
                            backend_session_id="s",
                            request_id="question-1",
                            questions=[{"id": "q", "question": "测试问题"}],
                        )
                    else:
                        await output()
                elif event["type"] == "x_aha.agent_backend.question.respond":
                    await emit(
                        "x_aha.agent_backend.question.resolved",
                        backend_id="test",
                        backend_session_id="s",
                        request_id=event["request_id"],
                        outcome="answered",
                        resolution="handled",
                    )
                    await output()
                elif (
                    event["type"] == "conversation.item.create"
                    and event["item"].get("type") == "mcp_approval_response"
                ):
                    assert event["item"]["approval_request_id"] == "mcp-1"
                    await output()
                elif event["type"] == "x_aha.agent_backend.approval.respond":
                    await emit(
                        "x_aha.agent_backend.approval.resolved",
                        backend_id="test",
                        backend_session_id="s",
                        request_id=event["request_id"],
                        approval_id=event["approval_id"],
                        outcome="approved" if event["approved"] else "denied",
                        resolution="handled",
                    )
                    await output()
                elif event["type"] == "response.cancel":
                    await emit(
                        "response.done",
                        response={
                            "id": "r",
                            "object": "realtime.response",
                            "status": "cancelled",
                            "output": [],
                        },
                    )
        except WebSocketDisconnect:
            pass
        finally:
            app.state.active -= 1

    return app


@contextmanager
def running(app: FastAPI):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("测试上游未启动")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
