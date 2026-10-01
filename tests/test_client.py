from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.sync.server import serve

from aha import AsyncAudioHarness, AudioFormat, AudioHarness, Limits, UnknownEvent
from aha._common import PcmChunks, connection_url, encode_event, parse_event
from aha.errors import (
    ConnectionClosed,
    ConnectionLost,
    ConsumerBackpressure,
    HandshakeError,
    ProtocolError,
)


@contextmanager
def endpoint(mode="normal"):
    received = []
    requests = []
    disconnected = threading.Event()

    def handler(ws):
        requests.append(ws.request)
        try:
            if mode == "no_handshake":
                for _ in ws:
                    pass
                return
            ws.send(
                json.dumps(
                    {
                        "type": "session.created",
                        "event_id": "created",
                        "session": {"type": "transcription"},
                    }
                )
            )
            for raw in ws:
                event = json.loads(raw)
                received.append(event)
                if event["type"] == "session.update":
                    if mode == "reject":
                        ws.send(
                            json.dumps(
                                {
                                    "type": "error",
                                    "event_id": "err",
                                    "error": {
                                        "type": "invalid_request_error",
                                        "message": "bad config",
                                        "event_id": event["event_id"],
                                    },
                                }
                            )
                        )
                        continue
                    ws.send(
                        json.dumps(
                            {
                                "type": "session.updated",
                                "event_id": "updated",
                                "session": event["session"],
                            }
                        )
                    )
                    if mode == "flood":
                        for n in range(100):
                            ws.send(
                                json.dumps(
                                    {
                                        "type": "future.event",
                                        "event_id": str(n),
                                        "data": "x" * 100,
                                    }
                                )
                            )
                elif event["type"] == "input_audio_buffer.commit":
                    if mode == "disconnect":
                        ws.close()
                        return
                    if mode == "malformed":
                        ws.send("not-json")
                        continue
                    ws.send(
                        json.dumps(
                            {
                                "type": "error",
                                "event_id": "recoverable",
                                "error": {
                                    "type": "invalid_request_error",
                                    "message": "example",
                                },
                            }
                        )
                    )
                    ws.send(
                        json.dumps(
                            {
                                "type": "conversation.item.input_audio_transcription.completed",
                                "event_id": "text",
                                "item_id": "i",
                                "content_index": 0,
                                "transcript": "你好",
                                "usage": {"type": "duration", "seconds": 1},
                            }
                        )
                    )
                    time.sleep(0.02)
                    ws.send(
                        json.dumps(
                            {
                                "type": "x_aha.diarization.cluster.completed",
                                "event_id": "late",
                                "revision": 1,
                                "replaces_revision": None,
                                "full_snapshot": True,
                                "assignments": [],
                            }
                        )
                    )
                    ws.send(
                        json.dumps(
                            {
                                "type": "response.output_audio.delta",
                                "event_id": "audio",
                                "response_id": "r",
                                "item_id": "out",
                                "output_index": 0,
                                "content_index": 0,
                                "delta": "AAEC/w==",
                            }
                        )
                    )
                    ws.send(
                        json.dumps(
                            {
                                "type": "future.event",
                                "event_id": "last",
                                "detail": {"kept": True},
                            }
                        )
                    )
        except Exception:
            if not ws.close_code:
                raise
        finally:
            disconnected.set()

    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield (
                f"ws://127.0.0.1:{server.socket.getsockname()[1]}",
                received,
                requests,
                disconnected,
            )
        finally:
            server.shutdown()
            thread.join(2)
            assert not thread.is_alive()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_pcm_events_helpers_and_connection_lifecycle(asynchronous):
    pcm = bytes(range(256)) * 9 + b"\0\0"
    with endpoint() as (url, received, requests, disconnected):

        async def async_case():
            async with AsyncAudioHarness(
                base_url=url + "/v1?tenant=one", api_key="test", proxy=None
            ) as client:
                async with client.transcriptions.connect(
                    model="会议 模型", audio=AudioFormat.pcm16(16000)
                ) as connection:

                    async def chunks():
                        yield pcm[:1]
                        yield pcm[1:1500]
                        yield pcm[1500:]

                    await connection.send_audio(chunks())
                    await connection.input_audio_buffer.commit()
                    result = []
                    async for event in connection.events():
                        result.append(event)
                        if event.type == "future.event":
                            break
                    await connection.agent_backend.respond_approval(
                        "request", "approval", True
                    )
                    await connection.tools.submit_output("call", "done")
                    await connection.tools.respond_approval("approval", True)
                    await connection.response.cancel("r")
                with pytest.raises(ConnectionClosed):
                    await connection.send_audio(b"\0\0")
                return result

        def sync_case():
            with AudioHarness(
                base_url=url + "/v1?tenant=one", api_key="test", proxy=None
            ) as client:
                with client.transcriptions.connect(
                    model="会议 模型", audio=AudioFormat.pcm16(16000)
                ) as connection:
                    connection.send_audio([pcm[:1], pcm[1:1500], pcm[1500:]])
                    connection.input_audio_buffer.commit()
                    result = []
                    for event in connection.events():
                        result.append(event)
                        if event.type == "future.event":
                            break
                    connection.agent_backend.respond_approval(
                        "request", "approval", True
                    )
                    connection.tools.submit_output("call", "done")
                    connection.tools.respond_approval("approval", True)
                    connection.response.cancel("r")
                with pytest.raises(ConnectionClosed):
                    connection.send_audio(b"\0\0")
                return result

        result = asyncio.run(async_case()) if asynchronous else sync_case()
        assert disconnected.wait(2)
        assert [e.type for e in result][:2] == ["session.created", "session.updated"]
        assert [e.type for e in result][2:5] == [
            "error",
            "conversation.item.input_audio_transcription.completed",
            "x_aha.diarization.cluster.completed",
        ]
        assert result[-2].delta == b"\0\1\2\xff"
        assert isinstance(result[-1], UnknownEvent)
        assert result[-1].raw["detail"] == {"kept": True}
        audio = [
            base64.b64decode(e["audio"])
            for e in received
            if e["type"] == "input_audio_buffer.append"
        ]
        assert b"".join(audio) == pcm
        assert all(len(chunk) <= 640 for chunk in audio)
        assert sum(e["type"] == "input_audio_buffer.commit" for e in received) == 1
        assert (
            received[0]["session"]["audio"]["input"]["transcription"]["model"]
            == "会议 模型"
        )
        assert requests[0].headers["Authorization"] == "Bearer test"
        assert parse_qs(urlsplit(requests[0].path).query) == {
            "tenant": ["one"],
            "intent": ["transcription"],
            "x_aha": ["v1"],
        }


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "mode,error", [("disconnect", ConnectionLost), ("malformed", ProtocolError)]
)
def test_failures_do_not_reconnect(asynchronous, mode, error):
    with endpoint(mode) as (url, received, requests, disconnected):

        async def run():
            async with AsyncAudioHarness(base_url=url, proxy=None) as client:
                async with client.realtime.connect(model="agent") as connection:
                    await connection.input_audio_buffer.commit()
                    async for _ in connection.events():
                        pass

        with pytest.raises(error):
            if asynchronous:
                asyncio.run(run())
            else:
                with AudioHarness(base_url=url, proxy=None) as client:
                    with client.realtime.connect(model="agent") as connection:
                        connection.input_audio_buffer.commit()
                        list(connection.events())
        assert disconnected.wait(2)
        assert len(requests) == 1
        assert len(received) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("mode", ["reject", "no_handshake"])
def test_handshake_failure_releases_connection(asynchronous, mode):
    with endpoint(mode) as (url, _, _, disconnected):
        limits = Limits(connect_timeout=0.2)

        async def run():
            async with AsyncAudioHarness(
                base_url=url, limits=limits, proxy=None
            ) as client:
                async with client.transcriptions.connect(model="m"):
                    pytest.fail("must not connect")

        with pytest.raises(HandshakeError):
            if asynchronous:
                asyncio.run(run())
            else:
                with AudioHarness(base_url=url, limits=limits, proxy=None) as client:
                    with client.transcriptions.connect(model="m"):
                        pytest.fail("must not connect")
        assert disconnected.wait(2)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_slow_consumer_is_bounded(asynchronous):
    with endpoint("flood") as (url, _, _, disconnected):
        limits = Limits(event_queue_count=3, consumer_timeout=0.05)

        async def run():
            async with AsyncAudioHarness(
                base_url=url, limits=limits, proxy=None
            ) as client:
                async with client.transcriptions.connect(model="m") as connection:
                    await asyncio.sleep(0.2)
                    async for _ in connection.events():
                        pass

        with pytest.raises(ConsumerBackpressure):
            if asynchronous:
                asyncio.run(run())
            else:
                with AudioHarness(base_url=url, limits=limits, proxy=None) as client:
                    with client.transcriptions.connect(model="m") as connection:
                        time.sleep(0.2)
                        list(connection.events())
        assert disconnected.wait(2)


async def test_single_consumer_and_cancelled_context():
    with endpoint() as (url, _, _, disconnected):
        async with AsyncAudioHarness(base_url=url, proxy=None) as client:
            with pytest.raises(asyncio.CancelledError):
                async with client.transcriptions.connect(model="m") as session:
                    iterator = session.events()
                    await anext(iterator)
                    with pytest.raises(RuntimeError):
                        await anext(session.events())
                    await iterator.aclose()
                    raise asyncio.CancelledError
        assert disconnected.wait(2)
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "aha-events"]


def test_url_format_validation_and_pcm_tail():
    assert (
        connection_url("https://example.test/prefix/v1", "agent x", False, False, False)
        == "wss://example.test/prefix/v1/realtime?model=agent+x"
    )
    with pytest.raises(ValueError):
        AudioFormat.pcm16(48000)
    with pytest.raises(ValueError):
        Limits(send_timeout=float("inf"))
    framer = PcmChunks(AudioFormat.pcm16(16000), 20)
    list(framer.feed(b"x"))
    with pytest.raises(ValueError):
        framer.finish()
    with pytest.raises(ValueError):
        encode_event({"type": "input_audio_buffer.append", "audio": b"x" * 100}, 10)
    with pytest.raises(ProtocolError):
        parse_event('{"type":"x_aha.vad.progress","processed_audio_ms":-1}')


def test_contract_import_does_not_load_client():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import aha.contracts, sys; assert 'aha.client' not in sys.modules; assert 'audio_harness' not in sys.modules",
        ],
        check=True,
    )


async def test_async_send_timeout_closes_transport():
    from aha._async_session import AsyncRealtimeSession
    from aha.errors import SendTimeout

    class Socket:
        closed = False

        async def send(self, raw):
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    socket = Socket()
    session = AsyncRealtimeSession(socket, AudioFormat(), Limits(send_timeout=0.02))
    with pytest.raises(SendTimeout):
        await session.input_audio_buffer.commit()
    assert socket.closed


def test_sync_send_timeout_interrupts_blocked_native_send():
    from aha._sync_session import RealtimeSession
    from aha.errors import SendTimeout

    interrupted = threading.Event()

    class Socket:
        def shutdown(self, how):
            interrupted.set()

    class Connection:
        socket = Socket()

        def send(self, raw):
            assert interrupted.wait(2)
            raise OSError("interrupted")

    session = RealtimeSession(Connection(), AudioFormat(), Limits(send_timeout=0.02))
    with pytest.raises(SendTimeout):
        session.input_audio_buffer.commit()
    assert interrupted.is_set()


def test_sync_single_consumer_and_client_close():
    with endpoint() as (url, _, _, disconnected):
        client = AudioHarness(base_url=url, proxy=None)
        with client.transcriptions.connect(model="m") as session:
            events = session.events()
            next(events)
            with pytest.raises(RuntimeError):
                next(session.events())
            events.close()
            client.close()
            client.close()
            assert session._reader is not None and not session._reader.is_alive()
        assert disconnected.wait(2)
        with pytest.raises(ConnectionClosed):
            with client.transcriptions.connect(model="m"):
                pytest.fail("closed client")
