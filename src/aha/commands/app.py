from __future__ import annotations

import logging
import typer
from aha import __version__

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)

@app.callback()
def main() -> None:
    """Audio Harness SDK 工具。"""

@app.command()
def version() -> None:
    """输出 SDK 版本。"""
    typer.echo(__version__)

if __name__ == "__main__":
    app()
