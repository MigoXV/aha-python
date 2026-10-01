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

首版对接现有 `/v1/realtime` WebSocket 和 `x_aha=v1`。一条连接只有一路输入。
不提供文件上传、文件转写、Jobs、Artifacts、gRPC 客户端或多 Stream。

## 安装与分层

发布前使用构建出的 wheel：

```bash
poetry run python -m pip install /workspace/libs/aha-python/dist/aha_python-0.1.0-py3-none-any.whl
```

`aha.contracts` 提供共享的模型、验证器和编解码；导入它不会加载 `aha.client`。
为了保持现有服务端的类型继承和协议一致性，基础依赖保留 OpenAI 2.50 系列、Pydantic v2，
客户端使用 websockets 15。SDK 不依赖服务端源码、NumPy、音频设备、Torch 或模型运行时。
导入不会建立连接或启动线程。同步和异步客户端分别使用原生同步和异步 WebSocket。

## 实时转写

```python
import asyncio
from aha import AsyncAudioHarness, AudioFormat

async def run(pcm_chunks):
    async with AsyncAudioHarness(base_url="http://localhost:8000") as client:
        async with client.transcriptions.connect(
            model="meeting-transcribe",  # 服务端已发布的工作流名称
            audio=AudioFormat.pcm16(sample_rate=16000),
            session={"audio": {"input": {"turn_detection": None}}},
        ) as session:
            async def feed():
                await session.send_audio(pcm_chunks)
                await session.input_audio_buffer.commit()

            sender = asyncio.create_task(feed())
            try:
                async for event in session.events():
                    if event.type == "conversation.item.input_audio_transcription.completed":
                        print(event.transcript)
                    elif event.type == "error":
                        print(event.error.code)
                    # 由应用的停止条件结束消费；主转写完成不等于说话人结果已齐。
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
```

异步 `send_audio` 接受 bytes、同步或异步 bytes 迭代来源；同步版本接受 bytes 或同步来源。
来源必须为 PCM S16LE 单声道，支持 16/24 kHz，默认 24 kHz。SDK 合并碎块并按默认 20 ms 分块，
不做解码、重采样、静音填充或实时速度节流。来源取得数据的速度由应用控制。
来源跨块的半个样本会合并，最终仍有半个样本时报错，已发送音频不会自动撤销。
一次只运行一个 `send_audio`；不要同时调用低层 append 或在生产者尚未结束时 commit/clear。

`connect()` 等待 `session.created`、发送初始配置并等待 `session.updated` 后返回。
这两个事件仍然交付给用户。`session.update()` 等后续方法只发送命令，返回生成的客户端 event_id，
不表示服务端接受或执行完成；通过事件观察结果及关联错误。

同步用法相同，将 `AsyncAudioHarness` 换成 `AudioHarness`，使用 `with`、普通 `for`，不加 `await`。
连续发送和消费必须并行（同步使用调用方线程），避免输出积压。

## 语音智能体与控制

`client.realtime.connect(model="voice-agent")` 连接已发布智能体。两类会话均提供以下方法，
实际适用范围沿用服务端，纯转写不支持的 Agent 操作仍返回业务错误：

| 方法 | 作用 |
| --- | --- |
| `session.session.update(config)` | 更新现有协议支持的 Session 字段 |
| `session.input_audio_buffer.append/commit/clear` | 输入 bytes、提交、清空 |
| `session.response.create/cancel` | 创建或取消回复 |
| `session.conversation.item.create/retrieve/delete/truncate` | 会话条目操作及已播放位置截断 |
| `session.tools.submit_output(call_id, output)` | 提交客户端工具结果 |
| `session.tools.respond_approval(approval_request_id, approved)` | 显式回答 MCP 审批 |
| `session.agent_backend.respond_approval/respond_question/cancel` | 后台 Agent 审批、问答与取消 |
| `session.send(event)` | 发送现有契约支持的模型或字典，音频字段使用 bytes |

服务端保留工具执行和审批决策权。SDK 不自动批准、重做工具或把取消当成副作用回滚。
TTS `response.output_audio.delta` 的 `delta` 是解码后的 PCM bytes；输出格式沿用服务端（24 kHz mono）。
应用负责播放器缓冲、清空和实际播放水位。取消使用 `response.cancel()`，同步已听内容使用
`conversation.item.truncate()`；现有 WS 不支持服务端播放缓冲 clear，SDK 不模拟该确认。

参考 [文本与 TTS 示例](examples/voice.py) 和 [内存 PCM 示例](examples/pcm_stream.py)：

```bash
AHA_BASE_URL=http://localhost:8000 AHA_MODEL=voice-agent poetry run python examples/voice.py
```

交互式客户端体验测试见 [SDK 客户端工作台](examples/sdk_client_demo/README.md)。
这是独立的 Poetry + React demo，浏览器采集麦克风，Python 桥接服务使用已安装的 `aha` wheel；
支持实时转写、文本对话、TTS 播放、审批和事件检查，不增加 SDK 主包的 Web 依赖。

审批回复示例（只在业务已作出明确决定后发送）：

```python
await session.agent_backend.respond_approval(
    request_id=event.request_id,
    approval_id=event.approval_id,
    approved=False,
)
```

## 结束、错误和资源边界

- `send_audio` 来源耗尽只表示字节已发送，不自动 commit/close。自动 VAD 和手动 commit 完全沿用服务端。
- `close()`／上下文退出释放连接、读取任务或线程，不保证待处理结果交付，不提供 `finish/drained`。
  当前 WS 没有全分支排空屏障，静默超时也不能证明处理完成。主转写之后仍可能有说话人结果。
- 一个会话只允许一个活动事件迭代器；事件不按文本内容去重，也不自动合并或改写 revision。
  已知事件的新增可选字段可从 `model_extra` 读取（严格 AHA 类型的 `model_dump()` 不包含它们），未知类型以 `UnknownEvent` 交付并保留 `.raw`。
- 服务端 `error` 是可恢复业务事件，消费者可继续读取。无效事件抛 `ProtocolError`，断线抛
  `ConnectionLost`（含 `last_event_id` 和 `outcome_unknown=True`）。不自动重连、重试或重放。
- 来源迭代和用户消费任务由调用方拥有，必须在自己的 `finally` 中取消／回收；SDK 拥有连接读取任务。

`Limits` 可配置连接、发送、关闭、消费者等待和队列预算：默认分别为 10、30、10、30 秒，
事件队列最多 256 条且原始事件累计不超过 8 MiB，单条网络消息最多 1 MiB，PCM 分块 20 ms。
WebSocket 内部缓冲另有 16 帧上限；事件反序列化对象也占内存，字节预算不是进程 RSS 上限。
消费者超时抛 `ConsumerBackpressure` 并中止连接，已入队事件交付后暴露错误；不默默丢弃它们。
这些是 SDK 本地预算，现有服务端没有输入确认／额度协商，不能据此声称服务端已接收或完成推理。

```python
from aha import AsyncAudioHarness, Limits
client = AsyncAudioHarness(
    base_url="https://audio.example/v1",
    api_key="由应用安全取得的凭证",
    limits=Limits(event_queue_count=128, consumer_timeout=5),
)
```

`base_url` 接受 HTTP(S)/WS(S)、根路径、`/v1` 或完整 `/v1/realtime`。
`api_key` 作为 Bearer header 发送，不能与自定义 Authorization 同时指定；应用当前由部署网关鉴权。
可传入 `headers`、`ssl_context` 和 `proxy`（默认尊重环境代理，`None` 禁用）；TLS 默认验证证书。
同一异步客户端及其会话只在创建和使用它们的事件循环内操作。

## 契约与验证

```bash
poetry run aha schema --direction client
poetry run aha schema --direction server
poetry run pytest
poetry build
```

Schema 是现有线协议（JSON 中音频为 Base64）；Python `send()` 和事件视图使用 bytes。
服务端应使用 `aha.contracts` 的原有严格适配器，客户端宽容解析不会改变服务端验证规则。
测试使用本地 WebSocket 服务，不依赖模型或外部服务。
