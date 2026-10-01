from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .contracts import AHA_REALTIME_SERVER_EVENT_ADAPTER, CLIENT_EVENTS, SERVER_EVENTS
from .contracts.json_codec import _decode_model, _encode
from .errors import ProtocolError


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: Literal[16000, 24000] = 24000
    channels: Literal[1] = 1

    def __post_init__(self) -> None:
        if (
            type(self.sample_rate) is not int
            or self.sample_rate not in (16000, 24000)
            or type(self.channels) is not int
            or self.channels != 1
        ):
            raise ValueError("只支持 16/24 kHz 单声道 PCM S16LE")

    @classmethod
    def pcm16(
        cls, sample_rate: Literal[16000, 24000] = 24000, channels: Literal[1] = 1
    ) -> AudioFormat:
        return cls(sample_rate, channels)

    def wire(self) -> dict[str, Any]:
        return {"type": "audio/pcm", "rate": self.sample_rate}


@dataclass(frozen=True)
class Limits:
    connect_timeout: float = 10
    send_timeout: float = 30
    close_timeout: float = 10
    consumer_timeout: float = 30
    event_queue_count: int = 256
    event_queue_bytes: int = 8 * 1024 * 1024
    max_message_bytes: int = 1024 * 1024
    chunk_ms: int = 20

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} 必须为有限正数")
        for name in (
            "event_queue_count",
            "event_queue_bytes",
            "max_message_bytes",
            "chunk_ms",
        ):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"{name} 必须为整数")
        if self.chunk_ms > 100:
            raise ValueError("chunk_ms 不能超过 100")


class UnknownEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    raw: dict[str, Any] = Field(exclude=True)


@cache
def known_event_types() -> frozenset[str]:
    schema = AHA_REALTIME_SERVER_EVENT_ADAPTER.json_schema()
    result: set[str] = set()

    def visit(value: dict[str, Any]) -> None:
        if "$ref" in value:
            visit(schema["$defs"][value["$ref"].rsplit("/", 1)[-1]])
            return
        kind = value.get("properties", {}).get("type", {})
        if "const" in kind:
            result.add(kind["const"])
        result.update(kind.get("enum", []))
        for variant in value.get("anyOf", value.get("oneOf", [])):
            visit(variant)

    visit(schema)
    return frozenset(result)


def parse_event(raw: str | bytes) -> BaseModel:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise ValueError("事件必须包含字符串 type")
        if payload["type"] not in known_event_types():
            return UnknownEvent.model_validate({**payload, "raw": payload})
        wire = AHA_REALTIME_SERVER_EVENT_ADAPTER.validate_python(payload, extra="allow")
        # Keep server validation unchanged. The SDK adds decoded PCM to its view.
        return SERVER_EVENTS.validate_python(_decode_model(wire), extra="allow")
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ProtocolError("服务端事件格式无效") from exc


def encode_event(event: BaseModel | dict[str, Any], limit: int) -> tuple[str, str]:
    payload = (
        event.model_dump(mode="python", exclude_unset=True)
        if isinstance(event, BaseModel)
        else dict(event)
    )
    identifier = payload.get("event_id") or f"evt_{uuid4().hex}"
    payload["event_id"] = identifier
    parsed = CLIENT_EVENTS.validate_python(payload)
    value = parsed.model_dump(mode="python", exclude_unset=True)
    raw = json.dumps(
        _encode(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    if len(raw.encode("utf-8")) > limit:
        raise ValueError("事件超过 max_message_bytes")
    return identifier, raw


def connection_url(
    base_url: str, model: str, transcription: bool, extensions: bool, diagnostics: bool
) -> str:
    url = urlsplit(base_url)
    if (
        url.scheme not in ("http", "https", "ws", "wss")
        or not url.netloc
        or url.fragment
        or url.username
        or url.password
    ):
        raise ValueError("base_url 必须是无内嵌凭证的 HTTP(S) 或 WS(S) 地址")
    if not model.strip():
        raise ValueError("model 不能为空")
    path = url.path.rstrip("/")
    if not path.endswith("/v1/realtime"):
        path += "/realtime" if path.endswith("/v1") else "/v1/realtime"
    query = dict(parse_qsl(url.query))
    for key in ("intent", "model", "x_aha", "diagnostics"):
        query.pop(key, None)
    query.update({"intent": "transcription"} if transcription else {"model": model})
    if extensions:
        query["x_aha"] = "v1"
    if diagnostics:
        query["diagnostics"] = "true"
    scheme = {"http": "ws", "https": "wss"}.get(url.scheme, url.scheme)
    return urlunsplit((scheme, url.netloc, path, urlencode(query), ""))


def initial_config(
    model: str, transcription: bool, audio: AudioFormat, session: dict[str, Any] | None
) -> dict[str, Any]:
    import copy

    value = copy.deepcopy(session or {})
    expected = "transcription" if transcription else "realtime"
    if value.get("type", expected) != expected:
        raise ValueError("session.type 与连接模式不一致")
    value["type"] = expected
    input_config = value.setdefault("audio", {}).setdefault("input", {})
    if "format" in input_config and input_config["format"] != audio.wire():
        raise ValueError("session 音频格式与 audio 不一致")
    input_config["format"] = audio.wire()
    if transcription:
        recognition = input_config.setdefault("transcription", {})
        if recognition.get("model", model) != model:
            raise ValueError("session 转写模型与 model 不一致")
        recognition["model"] = model
    return value


class PcmChunks:
    """每次只保留一个输出块；来源块可以跨 PCM 样本边界。"""

    def __init__(self, audio: AudioFormat, chunk_ms: int) -> None:
        self.size = audio.sample_rate * chunk_ms // 1000 * 2
        self.pending = bytearray()

    def feed(self, block: bytes):
        if not isinstance(block, bytes):
            raise TypeError("音频来源必须产生 bytes")
        view = memoryview(block)
        offset = 0
        while offset < len(view):
            take = min(self.size - len(self.pending), len(view) - offset)
            self.pending.extend(view[offset : offset + take])
            offset += take
            if len(self.pending) == self.size:
                yield bytes(self.pending)
                self.pending.clear()

    def finish(self) -> bytes:
        if len(self.pending) % 2:
            raise ValueError("PCM S16LE 来源结束时存在不完整样本")
        return bytes(self.pending)
