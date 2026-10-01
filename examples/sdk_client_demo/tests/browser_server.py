"""浏览器自动化专用入口，生产 CLI 不使用此服务。"""

import os

import uvicorn
from fake_upstream import fake_app, running

if __name__ == "__main__":
    with running(fake_app()) as url:
        os.environ["AHA_BASE_URL"] = url
        os.environ.pop("AHA_API_KEY", None)
        uvicorn.run(
            "aha_sdk_demo.web.app:app", host="127.0.0.1", port=8091, log_level="error"
        )
