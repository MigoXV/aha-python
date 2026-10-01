from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient


def receive(ws, kind, *, action=None, event_type=None):
    for _ in range(30):
        value = ws.receive_json()
        if (
            value["kind"] == kind
            and (action is None or value.get("action") == action)
            and (event_type is None or value.get("event", {}).get("type") == event_type)
        ):
            return value
    pytest.fail("未收到预期事件")


def connect(ws, *, mode="transcription", model="test-workflow", rate=16000):
    ws.send_json(
        {"action": "connect", "model": model, "mode": mode, "sample_rate": rate}
    )
    assert receive(ws, "status")["status"] == "connecting"
    assert receive(ws, "status")["status"] == "ready"


@pytest.mark.parametrize("rate", [16000, 24000])
def test_pcm_commit_events_and_cleanup(app, rate):
    application, upstream = app
    pcm = b"\x01\x00" * (rate // 5)
    with TestClient(application).websocket_connect("/api/session") as ws:
        connect(ws, rate=rate)
        ws.send_bytes(pcm)
        sent = None
        interim = None
        for _ in range(30):
            value = ws.receive_json()
            if value["kind"] == "sent" and value.get("action") == "audio":
                sent = value
            if value.get("event", {}).get("type") == (
                "x_aha.input_audio_transcription.interim"
            ):
                interim = value
            if sent is not None and interim is not None:
                break
        assert sent is not None and sent["bytes"] == len(pcm)
        assert interim is not None
        assert interim["event"]["transcript"] == "录音期间收到的临时转写。"
        assert not any(
            event["type"] == "input_audio_buffer.commit"
            for event in upstream.state.received
        )
        assert upstream.state.received[0]["session"]["audio"]["input"][
            "turn_detection"
        ] == {"type": "server_vad"}
        ws.send_json({"action": "commit"})
        event = receive(
            ws,
            "event",
            event_type="conversation.item.input_audio_transcription.completed",
        )
        assert event["event"]["transcript"] == "麦克风音频已通过 SDK 提交。"
    chunks = [
        base64.b64decode(event["audio"])
        for event in upstream.state.received
        if event["type"] == "input_audio_buffer.append"
    ]
    assert b"".join(chunks) == pcm
    assert all(len(chunk) == rate // 50 * 2 for chunk in chunks)


def test_voice_text_audio_and_explicit_approval(app):
    application, upstream = app
    with TestClient(application).websocket_connect("/api/session") as ws:
        connect(ws, mode="realtime", model="approval-agent")
        assert upstream.state.received[0]["session"]["audio"]["input"][
            "turn_detection"
        ] is None
        ws.send_json({"action": "text", "text": "你好"})
        receive(ws, "event", event_type="x_aha.agent_backend.approval.requested")
        assert not [
            item
            for item in upstream.state.received
            if item["type"] == "x_aha.agent_backend.approval.respond"
        ]
        ws.send_json(
            {
                "action": "approval",
                "request_id": "request-1",
                "approval_id": "approval-1",
                "approved": False,
            }
        )
        receive(ws, "event", event_type="x_aha.agent_backend.approval.resolved")
        audio = receive(ws, "event", event_type="response.output_audio.delta")
        assert base64.b64decode(audio["event"]["delta"]) == b"\0\0" * 2400
        receive(ws, "event", event_type="response.done")
        ws.send_json({"action": "cancel"})
        receive(ws, "sent", action="cancel")
    approval = next(
        item
        for item in upstream.state.received
        if item["type"] == "x_aha.agent_backend.approval.respond"
    )
    assert approval["approved"] is False


def test_invalid_command_recovers_and_config_hides_key(app):
    application, _ = app
    client = TestClient(application)
    assert "test-secret" not in client.get("/api/config").text
    assert len(client.get("/api/models").json()["models"]) == 3
    with client.websocket_connect("/api/session") as ws:
        connect(ws)
        ws.send_json({"action": "commit"})
        assert receive(ws, "error")["fatal"] is False
        ws.send_json({"action": "clear"})
        receive(ws, "sent", action="clear")


def test_handshake_rejection_is_visible(app):
    application, _ = app
    with TestClient(application).websocket_connect("/api/session") as ws:
        ws.send_json(
            {
                "action": "connect",
                "mode": "transcription",
                "model": "reject",
                "sample_rate": 16000,
            }
        )
        error = receive(ws, "error")
        assert error["fatal"] is True
        assert error["event"]["error"]["message"] == "模型未发布"


def test_static_routes_and_missing_build(app, tmp_path):
    application, _ = app
    client = TestClient(application)
    assert client.get("/").status_code == 200
    assert (
        client.get("/session/deep-link", headers={"Accept": "text/html"}).text
        == client.get("/").text
    )
    assert (
        client.get("/pcm-worklet.js")
        .headers["content-type"]
        .startswith("text/javascript")
    )
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "AHA SDK 客户端 demo"
    for accept in ("application/json", "text/html"):
        for path in ("/api", "/api/missing"):
            for method in ("get", "head"):
                assert (
                    getattr(client, method)(
                        path, headers={"Accept": accept}
                    ).status_code
                    == 404
                )
    from aha_sdk_demo.web.app import create_app

    with pytest.raises(RuntimeError, match="does not exist"):
        create_app(dist=tmp_path / "missing")


def test_cross_origin_websocket_rejected(app):
    application, _ = app
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with TestClient(application).websocket_connect(
            "/api/session", headers={"Origin": "https://unrelated.example"}
        ):
            pass
