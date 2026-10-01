# aha-python

Audio Harness Python SDK，发行包名为 `aha-python`，导入名为 `aha`。

使用 Python 3.10–3.12，通过 Poetry 开发：

```bash
poetry env use python3.10
poetry install
poetry run aha version
poetry run pytest
poetry build
```

首版对接现有 Realtime WebSocket 和 `x_aha=v1`，共享协议与客户端分层。
不提供文件上传、文件转写或作业服务。
