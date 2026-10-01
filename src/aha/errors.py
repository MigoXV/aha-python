"""客户端本地错误；可恢复的服务端 error 仍通过事件流交付。"""


class AhaError(Exception):
    """SDK 错误基类。"""


class ConnectionLost(AhaError):
    def __init__(self, message: str, *, last_event_id: str | None = None) -> None:
        super().__init__(message)
        self.last_event_id = last_event_id
        self.outcome_unknown = True


class ConnectionClosed(AhaError):
    pass


class ProtocolError(AhaError):
    pass


class ConsumerBackpressure(AhaError):
    pass


class SendTimeout(AhaError):
    pass


class HandshakeError(AhaError):
    def __init__(self, message: str, *, event: object = None) -> None:
        super().__init__(message)
        self.event = event
