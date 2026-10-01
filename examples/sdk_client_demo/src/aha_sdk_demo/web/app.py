from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, WebSocket

import aha

from .bridge import bridge

WEB_DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(*, dist: Path = WEB_DIST_DIR) -> FastAPI:
    application = FastAPI(title="AHA SDK 客户端 demo")
    base_url = os.getenv("AHA_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.getenv("AHA_API_KEY") or None
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in ("http", "https")
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError(
            "AHA_BASE_URL 应为不含凭据、query、fragment 的 HTTP(S) 服务地址"
        )

    @application.get("/api/config")
    async def config() -> dict[str, object]:
        return {
            "base_url": base_url,
            "model": os.getenv("AHA_MODEL", ""),
            "sdk_version": aha.__version__,
            "authenticated": bool(api_key),
        }

    @application.get("/api/models")
    async def models() -> dict[str, object]:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with httpx.AsyncClient(
                timeout=10, trust_env=False, verify=False
            ) as client:
                root = (
                    base_url.removesuffix("/realtime")
                    if base_url.endswith("/v1/realtime")
                    else base_url
                )
                models_url = (
                    f"{root}/models" if root.endswith("/v1") else f"{root}/v1/models"
                )
                response = await client.get(models_url, headers=headers)
                response.raise_for_status()
                data = response.json()["data"]
                return {"models": [item["id"] for item in data]}
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                502,
                f"上游返回 HTTP {exc.response.status_code}；请检查服务地址和访问凭据",
            ) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(502, "访问上游超时，请检查服务状态后重试") from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                502, "无法访问上游服务，请检查服务地址和网络后重试"
            ) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(
                502, "上游模型列表格式无效；请检查服务地址，或手动填写 model slug"
            ) from exc

    @application.websocket("/api/session")
    async def session(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin and urlsplit(origin).netloc != websocket.headers.get("host"):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await bridge(websocket, base_url, api_key)

    @application.api_route("/api", methods=["GET", "HEAD"], include_in_schema=False)
    @application.api_route(
        "/api/{path:path}", methods=["GET", "HEAD"], include_in_schema=False
    )
    async def unknown_api(path: str = "") -> None:
        raise HTTPException(404, "API route not found")

    application.frontend("/", directory=dist, fallback="index.html")
    return application


app = create_app()
