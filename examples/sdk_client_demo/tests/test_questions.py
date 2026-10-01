from fastapi.testclient import TestClient
from test_demo import connect, receive


def test_question_answer_validation_and_sdk_send(app):
    application, upstream = app
    with TestClient(application).websocket_connect("/api/session") as ws:
        connect(ws, mode="realtime", model="question-agent")
        ws.send_json({"action": "text", "text": "请提问"})
        receive(ws, "event", event_type="x_aha.agent_backend.question.requested")
        ws.send_json(
            {
                "action": "question",
                "request_id": "question-1",
                "answers": [{"id": "q", "answer": "是"}],
            }
        )
        error = receive(ws, "error")
        assert error["fatal"] is False
        assert "selected" in error["message"]
        ws.send_json(
            {
                "action": "question",
                "request_id": "question-1",
                "answers": [{"id": "q", "selected": [], "custom": "是"}],
            }
        )
        receive(ws, "event", event_type="x_aha.agent_backend.question.resolved")
        receive(ws, "event", event_type="response.done")
    answer = next(
        item
        for item in upstream.state.received
        if item["type"] == "x_aha.agent_backend.question.respond"
    )
    assert answer["answers"] == [{"id": "q", "selected": [], "custom": "是"}]
