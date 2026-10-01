import json

import pytest
from pydantic import ValidationError

from aha._common import encode_event, parse_event
from aha._resources import Resources
from aha.contracts import AHA_REALTIME_SERVER_EVENT_ADAPTER
from aha.contracts.json_codec import decode_client_event, encode_server_event


@pytest.mark.parametrize(
    "value", [{}, {"turn_detection": None}, {"turn_detection": {"type": "server_vad"}}]
)
def test_client_json_preserves_presence(value):
    event = {
        "type": "session.update",
        "event_id": "cfg",
        "session": {"type": "transcription", "audio": {"input": value}},
    }
    _, wire = encode_event(event, 65536)
    decoded = decode_client_event(json.loads(wire)).model_dump(
        mode="python", exclude_unset=True
    )
    assert decoded == event


def test_nested_pcm_and_final_cluster_null():
    event = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_audio", "audio": b"\0\xff"}],
        },
    }
    _, wire = encode_event(event, 65536)
    assert decode_client_event(json.loads(wire)).item.content[0].audio == b"\0\xff"
    cluster = {
        "type": "x_aha.diarization.cluster.completed",
        "event_id": "e",
        "revision": 2,
        "replaces_revision": None,
        "full_snapshot": True,
        "assignments": [],
    }
    assert encode_server_event(parse_event(json.dumps(cluster))) == cluster


def test_client_preserves_optional_extensions_but_server_stays_strict():
    event = {
        "type": "x_aha.vad.progress",
        "event_id": "e",
        "processed_audio_ms": 10,
        "future_field": {"v": 1},
    }
    assert parse_event(json.dumps(event)).model_extra["future_field"] == {"v": 1}
    with pytest.raises(ValidationError):
        AHA_REALTIME_SERVER_EVENT_ADAPTER.validate_python(event)


def test_resource_commands_match_current_contract():
    events = []

    def send(event):
        identifier, raw = encode_event(event, 65536)
        events.append(json.loads(raw))
        return identifier

    resources = Resources(send)
    resources.session.update({"type": "realtime"})
    resources.input_audio_buffer.append(b"\0\0")
    resources.input_audio_buffer.clear()
    resources.input_audio_buffer.commit()
    resources.conversation.item.create(
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }
    )
    resources.conversation.item.retrieve("i")
    resources.conversation.item.delete("i")
    resources.conversation.item.truncate("i", 100)
    resources.response.create()
    resources.response.cancel("r")
    resources.tools.submit_output("c", "ok")
    resources.tools.respond_approval("a", True)
    resources.agent_backend.respond_approval("r", "a", False)
    resources.agent_backend.respond_question(
        "r", [{"id": "q", "selected": ["yes"], "custom": None}]
    )
    resources.agent_backend.cancel()
    assert len({event["event_id"] for event in events}) == len(events)
    for event in events:
        decode_client_event(event)
