from __future__ import annotations

import socket
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator
from typing import Any

from pydantic import BaseModel
from websockets.sync.client import ClientConnection

from ._common import AudioFormat, Limits, PcmChunks, encode_event, parse_event
from ._resources import Resources
from .errors import (
    AhaError,
    ConnectionClosed,
    ConnectionLost,
    ConsumerBackpressure,
    HandshakeError,
    SendTimeout,
)


class RealtimeSession(Resources[str]):
    """原生同步 WebSocket 会话，不创建 asyncio 事件循环。"""

    def __init__(
        self, websocket: ClientConnection, audio: AudioFormat, limits: Limits
    ) -> None:
        super().__init__(self.send)
        self._ws = websocket
        self.audio = audio
        self._limits = limits
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._audio_lock = threading.Lock()
        self._queue: deque[tuple[BaseModel, int]] = deque()
        self._bytes = 0
        self._closing = False
        self._ended = False
        self._opening = True
        self._failure: AhaError | None = None
        self._signals: set[str] = set()
        self._consuming = False
        self.last_event_id: str | None = None
        self._reader: threading.Thread | None = None

    def _start(self, config: dict[str, Any]) -> None:
        self._reader = threading.Thread(
            target=self._read, name="aha-events", daemon=True
        )
        self._reader.start()
        try:
            self._wait_signal("session.created")
            self.session.update(config)
            self._wait_signal("session.updated")
            self._opening = False
        except BaseException:
            self.close()
            raise

    def _wait_signal(self, name: str) -> None:
        with self._condition:
            ready = self._condition.wait_for(
                lambda: (
                    name in self._signals or self._failure is not None or self._ended
                ),
                self._limits.connect_timeout,
            )
            if self._failure is not None:
                raise self._failure
            if not ready or name not in self._signals:
                raise HandshakeError(f"等待 {name} 失败或超时")

    def _interrupt_socket(self) -> None:
        # shutdown wakes a blocked native send without taking websockets' protocol lock.
        try:
            self._ws.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _read(self) -> None:
        try:
            for raw in self._ws:
                event = parse_event(raw)
                size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
                if size > self._limits.event_queue_bytes:
                    raise ConsumerBackpressure("单个事件超过事件队列字节预算")
                with self._condition:
                    self.last_event_id = getattr(event, "event_id", self.last_event_id)
                    if event.type in ("session.created", "session.updated"):
                        self._signals.add(event.type)
                    if self._opening and event.type == "error":
                        self._failure = HandshakeError(
                            "服务端拒绝初始会话配置", event=event
                        )
                    self._condition.notify_all()
                    ready = self._condition.wait_for(
                        lambda: (
                            self._closing
                            or (
                                len(self._queue) < self._limits.event_queue_count
                                and self._bytes + size <= self._limits.event_queue_bytes
                            )
                        ),
                        self._limits.consumer_timeout,
                    )
                    if not ready:
                        raise ConsumerBackpressure("事件消费者持续积压，连接已中止")
                    if self._closing:
                        return
                    self._queue.append((event, size))
                    self._bytes += size
                    self._condition.notify_all()
            if not self._closing:
                raise ConnectionLost(
                    "服务端关闭连接，处理结果未知", last_event_id=self.last_event_id
                )
        except Exception as exc:
            if not self._closing:
                self._failure = (
                    exc
                    if isinstance(exc, AhaError)
                    else ConnectionLost(
                        "Realtime 连接中断", last_event_id=self.last_event_id
                    )
                )
                self._interrupt_socket()
        finally:
            with self._condition:
                self._ended = True
                self._condition.notify_all()

    def _check_open(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closing or self._ended:
            raise ConnectionClosed("Realtime 连接已关闭")

    def send(self, event: BaseModel | dict[str, Any]) -> str:
        self._check_open()
        identifier, raw = encode_event(event, self._limits.max_message_bytes)
        deadline = time.monotonic() + self._limits.send_timeout
        if not self._write_lock.acquire(timeout=self._limits.send_timeout):
            raise SendTimeout("等待发送锁超时；本事件未发送")
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            self._interrupt_socket()

        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        try:
            self._check_open()
            timer.start()
            self._ws.send(raw)
        except AhaError:
            raise
        except Exception as exc:
            self._failure = (
                SendTimeout("发送超时，事件是否接收未知")
                if timed_out.is_set()
                else ConnectionLost(
                    "发送失败，事件是否接收未知", last_event_id=self.last_event_id
                )
            )
            raise self._failure from exc
        finally:
            timer.cancel()
            if timer.ident is not None:
                timer.join()
            self._write_lock.release()
        if timed_out.is_set():
            self._failure = SendTimeout("发送超时，事件是否接收未知")
            raise self._failure
        return identifier

    def send_audio(self, audio: bytes | Iterable[bytes]) -> None:
        self._check_open()
        if not self._audio_lock.acquire(blocking=False):
            raise RuntimeError("同一连接只允许一个 send_audio 生产者")
        try:
            chunks = PcmChunks(self.audio, self._limits.chunk_ms)
            for block in (audio,) if isinstance(audio, bytes) else audio:
                self._check_open()
                for chunk in chunks.feed(block):
                    self.input_audio_buffer.append(chunk)
            tail = chunks.finish()
            if tail:
                self.input_audio_buffer.append(tail)
        finally:
            self._audio_lock.release()

    def events(self) -> Iterator[BaseModel]:
        with self._condition:
            if self._consuming:
                raise RuntimeError("只允许一个事件消费迭代器")
            self._consuming = True
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: bool(self._queue) or self._ended or self._closing
                    )
                    if not self._queue:
                        if self._failure is not None:
                            raise self._failure
                        return
                    event, size = self._queue.popleft()
                    self._bytes -= size
                    self._condition.notify_all()
                yield event
        finally:
            with self._condition:
                self._consuming = False

    def close(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        # The timer also bounds close when another thread is inside native send.
        timer = threading.Timer(self._limits.close_timeout, self._interrupt_socket)
        timer.daemon = True
        timer.start()
        try:
            self._ws.close()
        finally:
            timer.cancel()
            timer.join()
            if (
                self._reader is not None
                and self._reader is not threading.current_thread()
            ):
                self._reader.join(self._limits.close_timeout)
