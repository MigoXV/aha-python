"""Audio Harness Python SDK。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import AudioFormat, Limits, UnknownEvent
    from .client import AsyncAudioHarness, AudioHarness

__version__ = "0.1.0"

__all__ = ["AudioHarness", "AsyncAudioHarness", "AudioFormat", "Limits", "UnknownEvent"]


def __getattr__(name: str):
    # Importing aha.contracts must not load the SDK transport implementation.
    if name in ("AudioHarness", "AsyncAudioHarness"):
        from . import client

        return getattr(client, name)
    if name in ("AudioFormat", "Limits", "UnknownEvent"):
        from . import _common

        return getattr(_common, name)
    raise AttributeError(name)
