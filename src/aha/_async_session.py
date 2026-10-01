from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Iterable
from typing import Any

from pydantic import BaseModel
from websockets.asyncio.client import ClientConnection

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


class AsyncRealtimeSession(Resources[Awaitable[str]]):
    """单连接、单事件消费者；close 不表示服务端处理完成。"""

    def __init__(
        self, websocket: ClientConnection, audio: AudioFormat, limits: Limits
    ) -> None:
        super().__init__(self.send)
        self._ws = websocket
        self.audio = audio
        self._limits = limits
        self._condition = asyncio.Condition()
        self._write_lock = asyncio.Lock()
        self._audio_lock = asyncio.Lock()
        self._queue: deque[tuple[BaseModel, int]] = deque()
        self._bytes = 0
        self._closing = False
        self._ended = False
        self._opening = True
        self._failure: AhaError | None = None
        self._signals: set[str] = set()
        self._consuming = False
        self.last_event_id: str | None = None
        self._reader: asyncio.Task[None] | None = None

    async def _start(self, config: dict[str, Any]) -> None:
        self._reader = asyncio.create_task(self._read(), name="aha-events")
        try:
            await self._wait_signal("session.created")
            await self.session.update(config)
            await self._wait_signal("session.updated")
            self._opening = False
        except BaseException:
            await self.close()
            raise

    async def _wait_signal(self, name: str) -> None:
        async def wait() -> None:
            async with self._condition:
                await self._condition.wait_for(
                    lambda: (
                        name in self._signals
                        or self._failure is not None
                        or self._ended
                    )
                )
                if self._failure is not None:
                    raise self._failure
                if name not in self._signals:
                    raise HandshakeError("连接在初始化完成前关闭")

        try:
            await asyncio.wait_for(wait(), self._limits.connect_timeout)
        except asyncio.TimeoutError as exc:
            raise HandshakeError(f"等待 {name} 超时") from exc

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                event = parse_event(raw)
                size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
                if size > self._limits.event_queue_bytes:
                    raise ConsumerBackpressure("单个事件超过事件队列字节预算")
                async with self._condition:
                    self.last_event_id = getattr(event, "event_id", self.last_event_id)
                    if event.type in ("session.created", "session.updated"):
                        self._signals.add(event.type)
                    if self._opening and event.type == "error":
                        self._failure = HandshakeError(
                            "服务端拒绝初始会话配置", event=event
                        )
                    self._condition.notify_all()
                    try:
                        await asyncio.wait_for(
                            self._condition.wait_for(
                                lambda: (
                                    self._closing
                                    or (
                                        len(self._queue)
                                        < self._limits.event_queue_count
                                        and self._bytes + size
                                        <= self._limits.event_queue_bytes
                                    )
                                )
                            ),
                            self._limits.consumer_timeout,
                        )
                    except asyncio.TimeoutError as exc:
                        raise ConsumerBackpressure(
                            "事件消费者持续积压，连接已中止"
                        ) from exc
                    if self._closing:
                        return
                    self._queue.append((event, size))
                    self._bytes += size
                    self._condition.notify_all()
            if not self._closing:
                raise ConnectionLost(
                    "服务端关闭连接，处理结果未知", last_event_id=self.last_event_id
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                self._failure = (
                    exc
                    if isinstance(exc, AhaError)
                    else ConnectionLost(
                        "Realtime 连接中断", last_event_id=self.last_event_id
                    )
                )
                await self._ws.close()
        finally:
            async with self._condition:
                self._ended = True
                self._condition.notify_all()

    def _check_open(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closing or self._ended:
            raise ConnectionClosed("Realtime 连接已关闭")

    async def send(self, event: BaseModel | dict[str, Any]) -> str:
        self._check_open()
        identifier, raw = encode_event(event, self._limits.max_message_bytes)

        async def write() -> None:
            async with self._write_lock:
                self._check_open()
                await self._ws.send(raw)

        try:
            await asyncio.wait_for(write(), self._limits.send_timeout)
        except asyncio.TimeoutError as exc:
            self._failure = SendTimeout("发送超时，连接已中止；事件是否接收未知")
            await self.close()
            raise self._failure from exc
        except asyncio.CancelledError:
            await self.close()
            raise
        except AhaError:
            raise
        except Exception as exc:
            self._failure = ConnectionLost(
                "发送失败，事件是否接收未知", last_event_id=self.last_event_id
            )
            await self.close()
            raise self._failure from exc
        return identifier

    async def send_audio(
        self, audio: bytes | AsyncIterable[bytes] | Iterable[bytes]
    ) -> None:
        self._check_open()
        if self._audio_lock.locked():
            raise RuntimeError("同一连接只允许一个 send_audio 生产者")
        async with self._audio_lock:
            chunks = PcmChunks(self.audio, self._limits.chunk_ms)
            if isinstance(audio, AsyncIterable):
                async for block in audio:
                    self._check_open()
                    for chunk in chunks.feed(block):
                        await self.input_audio_buffer.append(chunk)
            else:
                for block in (audio,) if isinstance(audio, bytes) else audio:
                    self._check_open()
                    for chunk in chunks.feed(block):
                        await self.input_audio_buffer.append(chunk)
            tail = chunks.finish()
            if tail:
                await self.input_audio_buffer.append(tail)

    async def events(self) -> AsyncIterator[BaseModel]:
        if self._consuming:
            raise RuntimeError("只允许一个事件消费迭代器")
        self._consuming = True
        try:
            while True:
                async with self._condition:
                    await self._condition.wait_for(
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
            self._consuming = False

    async def close(self) -> None:
        self._closing = True
        async with self._condition:
            self._condition.notify_all()
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        try:
            await asyncio.wait_for(self._ws.close(), self._limits.close_timeout)
        except asyncio.TimeoutError:
            self._ws.transport.abort()
