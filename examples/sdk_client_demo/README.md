# SDK 客户端工作台

独立浏览器 demo，通过 Python 桥接服务调用已安装的 `aha` SDK，连接现有 Audio Harness 服务。

## 安装与启动

需要 Python 3.10、Poetry、Node.js 22.12+（或 24）和 pnpm。前端使用 React、TypeScript、Vite；Python 后端统一托管 `src/web/dist`，标准运行只需一个 demo 进程。

从 SDK 仓库根目录执行：

```bash
poetry install
poetry build
cd examples/sdk_client_demo
poetry env use python3.10
poetry install
poetry run python -m pip install ../../dist/aha_python-0.1.0-py3-none-any.whl
pnpm --dir src/web install --frozen-lockfile
pnpm --dir src/web run build
poetry run aha-sdk-demo --base-url http://127.0.0.1:8000
```

打开 <http://127.0.0.1:8090>。上游服务端需要另外启动；demo 不启动引擎、不发布资源。若 SDK 有修改，重新 `poetry build`，再在 demo 环境中使用 `pip install --force-reinstall --no-deps` 安装新的 wheel。SDK 主包和 demo 使用各自的 Poetry 环境。

也可以复制 `.env.example` 为 `.env` 并修改配置；CLI 从当前目录读取 `.env`。`--host` / `--port` / `--model` 分别对应 `AHA_DEMO_HOST` / `AHA_DEMO_PORT` / `AHA_MODEL`，服务地址和密钥对应 `AHA_BASE_URL` / `AHA_API_KEY`。密钥只由 Python 持有并作为上游 Bearer header 使用，浏览器不接收密钥。

服务地址支持 HTTP(S) 根路径、`/v1` 或 `/v1/realtime`。麦克风需要 localhost 或 HTTPS；远程访问需由部署环境提供 HTTPS。默认 demo 监听 `127.0.0.1:8090`。前端缺少构建产物时后端会明确报错，需要先构建。

demo 的上游 HTTPS 模型列表请求和 WSS 实时连接均不校验证书及主机名，支持连接使用自签名证书的服务。

## 如何体验

1. 打开页面后自动读取服务端 `/v1/models`，服务地址旁显示上游检查状态、访问成功及模型数量，或访问失败。失败原因显示在模型输入框下方，可点击「重试访问」重新检查；「读取模型」用于刷新列表。也可直接输入已发布资源的 slug。列表不区分类型：转写模式填写工作流 slug，语音 Agent 模式填写 Agent slug。上游访问成功表示模型接口可访问，会话是否已连接由右侧会话状态单独显示。
2. 选择 16 / 24 kHz 输入采样率并连接；SDK 完成 `session.created → session.update → session.updated` 后界面显示「已连接」。
3. 开始录音，允许麦克风权限。浏览器通过 AudioWorklet 重采样为单声道 PCM S16LE，并按 20 ms 发送；Python 调用 `session.send_audio()`。转写模式启用上游 `server_vad`，录音期间持续处理，由已发布工作流决定断句边界。工作流返回的临时转写会即时显示，正式结果替换临时文本；是否有临时结果取决于工作流能力。电平来自实际采集信号。
4. 转写模式点击「停止录音」后调用 `input_audio_buffer.commit()` 收尾剩余音频，保持连接以继续接收尾段转写和说话人等后续事件；录音期间不需要点击提交。Agent 模式仍使用「停止并提交」，随后调用 `response.create()`。
5. Agent 模式可以发文本消息，体验 `conversation.item.create()` 和 `response.create()`；24 kHz TTS 交给浏览器播放器。取消响应同时清空本地播放队列，服务端取消结果以真实事件为准。
6. 工具审批必须明确允许或拒绝；Agent 问题显示原始问题，回答填写 `[{"id":"问题 id","selected":["选项"],"custom":null}]`，也可以用空 `selected` 和 `custom` 自由文本回答。
7. 在事件检查器选择事件、筛选类型，检查握手、临时转写、终态和 AHA 扩展。音频以字节数代替 Base64，单条显示上限 32 KiB。正文显示转写和 Agent 输出，其他事件保留在检查器。

「丢弃输入」仅清除尚未提交的输入；「清空正文」只清除本地正文；「清空播放」只操作浏览器播放器，不发送服务端播放缓冲确认。已发送时长是桥接调用 SDK 发送成功的累计字节换算，不是服务端处理确认。断开或关闭页签会释放麦克风、播放和 SDK 连接，**不表示服务端结果已排空**，也不自动重试或重放。已存在的正文保留到手动清空。

事件和正文分别保留最近 200 条；音频发送积压超过 1 MiB 时停止采集，播放积压超过 10 秒时清空播放并提示。慢消费会触发 SDK 超时并中止连接。转写模式使用上游服务端 VAD，Agent 模式使用手动提交；不提供文件、Job 或多输入流入口。

## 开发与验证

使用 VS Code 打开本目录并选择 demo 的 Poetry 解释器；后端 Debug 配置从 `.env` 读取配置，自动执行 `web: build` 后启动 `aha_sdk_demo.commands.app`。需要前端 HMR 时可使用 Vite，但正式体验入口始终是 Python 托管后的页面。

```bash
pnpm --dir src/web run lint
pnpm --dir src/web run build
pnpm --dir src/web run test
poetry run pytest
pnpm --dir src/web exec playwright install chromium
pnpm --dir src/web run test:browser
```

后端测试覆盖真实 WebSocket 传输、SDK 分块、转写、TTS、审批、问题回答、错误恢复、静态托管和连接释放。浏览器测试通过独立的测试上游与 Chromium 测试麦克风验证 UI → Python → 已安装 SDK 的链路；测试服务仅在 `tests/` 中，产品端没有模拟数据回退。浏览器测试使用本地 8091 端口，验证录音提交、断开时释放媒体、权限失败、文本与审批、问题回答和错误重连，并在两种主题的 320 / 768 / 1280 px 下进行 axe 检查、截图、缩放和 reduced-motion 验证。

测试上游和现有服务端运行时的测试引擎只能验证协议与交互；真实模型的准确率、延迟以及实体声卡的采集和播放质量，需要连接你的已发布资源进一步体验。

## 目录与职责

```text
src/aha_sdk_demo/commands/app.py   Typer 启动入口
src/aha_sdk_demo/web/app.py        配置、真实模型列表和静态托管
src/aha_sdk_demo/web/bridge.py     SDK 连接与浏览器双向转发
src/web/src/audio.ts              媒体采集生命周期和 PCM 播放
src/web/public/pcm-worklet.js      采样率转换和 20 ms PCM 分帧
src/web/src/events.ts             正文归并和事件检查视图
src/web/src/theme/                苍渊·白垣 Token 与按钮别名
tests/                           测试上游与 Python 集成检查
```

默认白垣，支持苍渊主题；MANAS 用于连续会话内容、轻量配置和辅助检查器的关系。控件和真实状态说明见 [DESIGN.md](DESIGN.md)。这些文件只属于 SDK demo，没有修改 Audio Harness 服务端的路由、执行或能力。
