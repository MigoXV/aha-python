from __future__ import annotations

import logging
import os
from typing import Annotated

import typer
import uvicorn
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)
load_dotenv()
app = typer.Typer()


@app.command()
def serve(
    host: Annotated[str, typer.Option(envvar="AHA_DEMO_HOST")] = "127.0.0.1",
    port: Annotated[int, typer.Option(envvar="AHA_DEMO_PORT", min=1, max=65535)] = 8090,
    base_url: Annotated[
        str, typer.Option(envvar="AHA_BASE_URL")
    ] = "http://127.0.0.1:8000",
    api_key: Annotated[str | None, typer.Option(envvar="AHA_API_KEY")] = None,
    model: Annotated[str, typer.Option(envvar="AHA_MODEL")] = "",
) -> None:
    os.environ["AHA_BASE_URL"] = base_url
    os.environ["AHA_MODEL"] = model
    if api_key is not None:
        os.environ["AHA_API_KEY"] = api_key
    logger.info("SDK demo: http://%s:%d", host, port)
    uvicorn.run(
        "aha_sdk_demo.web.app:app",
        host=host,
        port=port,
        ws_max_size=131072,
        ws_max_queue=8,
    )


if __name__ == "__main__":
    app()
