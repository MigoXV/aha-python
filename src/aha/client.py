from __future__ import annotations

import asyncio
import ssl
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Generic, TypeVar

from websockets.asyncio.client import connect as async_connect
from websockets.sync.client import connect as sync_connect

from ._async_session import AsyncRealtimeSession
from ._common import AudioFormat, Limits, connection_url, initial_config
from ._sync_session import RealtimeSession
from .errors import ConnectionClosed, HandshakeError

C = TypeVar("C")


class _ConnectResource(Generic[C]):
    def __init__(self, connect: Callable[..., C], transcription: bool) -> None:
        self._connect = connect
        self._transcription = transcription

    def connect(
        self,
        *,
        model: str,
        audio: AudioFormat | None = None,
        session: dict[str, Any] | None = None,
        extensions: bool = True,
        diagnostics: bool = False,
    ) -> C:
        """配置完成后返回连接；初始 session 事件仍会交付 events()。"""
        return self._connect(
            model,
            self._transcription,
            audio or AudioFormat(),
            session,
            extensions,
            diagnostics,
        )


class _Client:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        proxy: str | bool | None = True,
        limits: Limits | None = None,
    ) -> None:
        self.base_url = base_url
        self.limits = limits or Limits()
        self._headers = dict(headers or {})
        if api_key is not None:
            if any(key.lower() == "authorization" for key in self._headers):
                raise ValueError("api_key 与 Authorization header 不能同时提供")
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._ssl = ssl_context
        self._proxy = proxy
        self._closed = False

    def _check_open(self) -> None:
        if self._closed:
            raise ConnectionClosed("客户端已关闭")

    def _options(self, url: str) -> dict[str, Any]:
        options = dict(
            additional_headers=self._headers,
            proxy=self._proxy,
            open_timeout=self.limits.connect_timeout,
            close_timeout=self.limits.close_timeout,
            max_size=self.limits.max_message_bytes,
            max_queue=16,
        )
        if self._ssl is not None:
            if not url.startswith("wss://"):
                raise ValueError("ssl_context 只能用于 WSS")
            options["ssl"] = self._ssl
        return options


class AudioHarness(_Client):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        proxy: str | bool | None = True,
        limits: Limits | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            headers=headers,
            ssl_context=ssl_context,
            proxy=proxy,
            limits=limits,
        )
        self.transcriptions = _ConnectResource(self._connect, True)
        self.realtime = _ConnectResource(self._connect, False)
        self._sessions: set[RealtimeSession] = set()
        self._lock = threading.Lock()

    @contextmanager
    def _connect(
        self,
        model: str,
        transcription: bool,
        audio: AudioFormat,
        session: dict[str, Any] | None,
        extensions: bool,
        diagnostics: bool,
    ) -> Iterator[RealtimeSession]:
        self._check_open()
        url = connection_url(
            self.base_url, model, transcription, extensions, diagnostics
        )
        config = initial_config(model, transcription, audio, session)
        options = self._options(url)
        try:
            ws = sync_connect(url, **options)
        except Exception as exc:
            raise HandshakeError("WebSocket 建连失败") from exc
        connection = RealtimeSession(ws, audio, self.limits)
        try:
            with self._lock:
                self._check_open()
                self._sessions.add(connection)
            connection._start(config)
            self._check_open()
            yield connection
        finally:
            connection.close()
            with self._lock:
                self._sessions.discard(connection)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sessions = tuple(self._sessions)
        for session in sessions:
            session.close()

    def __enter__(self) -> AudioHarness:
        self._check_open()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


class AsyncAudioHarness(_Client):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        proxy: str | bool | None = True,
        limits: Limits | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            headers=headers,
            ssl_context=ssl_context,
            proxy=proxy,
            limits=limits,
        )
        self.transcriptions = _ConnectResource(self._connect, True)
        self.realtime = _ConnectResource(self._connect, False)
        self._sessions: set[AsyncRealtimeSession] = set()

    @asynccontextmanager
    async def _connect(
        self,
        model: str,
        transcription: bool,
        audio: AudioFormat,
        session: dict[str, Any] | None,
        extensions: bool,
        diagnostics: bool,
    ) -> AsyncIterator[AsyncRealtimeSession]:
        self._check_open()
        url = connection_url(
            self.base_url, model, transcription, extensions, diagnostics
        )
        config = initial_config(model, transcription, audio, session)
        options = self._options(url)
        try:
            ws = await async_connect(url, **options)
        except Exception as exc:
            raise HandshakeError("WebSocket 建连失败") from exc
        connection = AsyncRealtimeSession(ws, audio, self.limits)
        try:
            self._check_open()
            self._sessions.add(connection)
            await connection._start(config)
            self._check_open()
            yield connection
        finally:
            await connection.close()
            self._sessions.discard(connection)

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(*(session.close() for session in tuple(self._sessions)))

    async def __aenter__(self) -> AsyncAudioHarness:
        self._check_open()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.close()
