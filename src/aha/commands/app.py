from __future__ import annotations

import logging

import typer

from aha import __version__

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Audio Harness SDK 工具。"""


@app.command()
def version() -> None:
    """输出 SDK 版本。"""
    typer.echo(__version__)


@app.command()
def schema(direction: str = typer.Option("server", help="client 或 server")) -> None:
    """输出当前线协议的 JSON Schema。"""
    import json

    from aha.contracts import (
        AHA_REALTIME_CLIENT_EVENT_ADAPTER,
        AHA_REALTIME_SERVER_EVENT_ADAPTER,
    )

    adapters = {
        "client": AHA_REALTIME_CLIENT_EVENT_ADAPTER,
        "server": AHA_REALTIME_SERVER_EVENT_ADAPTER,
    }
    if direction not in adapters:
        raise typer.BadParameter("必须为 client 或 server")
    typer.echo(
        json.dumps(adapters[direction].json_schema(), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    app()
