import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, MouseEvent } from "react";
import { Microphone, Playback } from "./audio";
import Choice from "./Choice";
import { inspectEvent, updateEntries } from "./events";
import type { Entry, Event, Log } from "./events";

type Status = "idle" | "connecting" | "ready" | "failed";
type Config = {
  base_url: string;
  model: string;
  sdk_version: string;
  authenticated: boolean;
};
type Request = {
  key: string;
  kind: "approval" | "question";
  event: Event;
  pending?: boolean;
};
type Envelope = {
  kind: string;
  status?: Status;
  fatal?: boolean;
  message?: string;
  event?: Event;
  action?: string;
  bytes?: number;
};
const statusLabels: Record<Status, string> = {
  idle: "未连接",
  connecting: "连接中",
  ready: "已连接",
  failed: "连接中断",
};
const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

export default function App() {
  const [theme, setTheme] = useState<"vallum" | "abyssus">("vallum");
  const [config, setConfig] = useState<Config | null>(null);
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelsBusy, setModelsBusy] = useState(false);
  const [modelError, setModelError] = useState("");
  const [upstreamStatus, setUpstreamStatus] = useState<
    "checking" | "ready" | "failed"
  >("checking");
  const [mode, setMode] = useState<"transcription" | "realtime">(
    "transcription",
  );
  const [rate, setRate] = useState(16000);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [micState, setMicState] = useState<
    "off" | "opening" | "on" | "stopping"
  >("off");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [logs, setLogs] = useState<Log[]>([]);
  const [selected, setSelected] = useState<Log | null>(null);
  const [query, setQuery] = useState("");
  const [text, setText] = useState("");
  const [response, setResponse] = useState("");
  const [inputStatus, setInputStatus] = useState("等待输入");
  const [transcriptionStatus, setTranscriptionStatus] = useState("等待转写事件");
  const [requests, setRequests] = useState<Request[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [sentBytes, setSentBytes] = useState(0);
  const [eventCount, setEventCount] = useState(0);
  const [level, setLevel] = useState(0);
  const [playEnabled, setPlayEnabled] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const microphone = useRef<Microphone | null>(null);
  const generation = useRef(0);
  const sequence = useRef(0);
  const lastMeter = useRef(0);
  const player = useRef(new Playback());
  const pendingText = useRef("");
  const playbackAllowed = useRef(false);
  const transcriptEnd = useRef<HTMLDivElement | null>(null);
  const connected = status === "ready";
  const locked = connected || status === "connecting";
  const recording = micState === "on";
  const busyResponse = ["发送中", "生成中", "正在取消"].includes(response);

  const loadModels = useCallback(async (signal?: AbortSignal) => {
    setModelsBusy(true);
    setModelError("");
    setUpstreamStatus("checking");
    try {
      const result = await fetch("/api/models", { signal });
      const value = await result.json();
      if (!result.ok) throw new Error(value.detail ?? "模型列表读取失败");
      if (signal?.aborted) return;
      setModels(value.models);
      setUpstreamStatus("ready");
      if (!value.models.length)
        setModelError("服务端尚无已发布模型，请先发布工作流或语音 Agent");
    } catch (error) {
      if (signal?.aborted) return;
      setModels([]);
      setUpstreamStatus("failed");
      setModelError(errorMessage(error));
    } finally {
      if (!signal?.aborted) setModelsBusy(false);
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    fetch("/api/config", { signal: controller.signal })
      .then(async (result) => {
        if (!result.ok)
          throw new Error("配置加载失败，请检查 demo 后端并刷新页面");
        const value: Config = await result.json();
        if (active) {
          setConfig(value);
          setModel(value.model);
          await loadModels(controller.signal);
        }
      })
      .catch((error) => {
        if (active) {
          setError(errorMessage(error));
          setModelError("配置加载失败，暂时无法检查上游服务");
          setUpstreamStatus("failed");
        }
      });
    const playback = player.current;
    const lifecycle = generation;
    return () => {
      active = false;
      controller.abort();
      lifecycle.current++;
      socket.current?.close();
      void microphone.current?.stop(false);
      playback.close();
    };
  }, [loadModels]);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [entries]);

  function releaseMedia() {
    generation.current++;
    const mic = microphone.current;
    microphone.current = null;
    void mic?.stop(false).catch(() => undefined);
    setMicState("off");
    setLevel(0);
    player.current.clear();
  }

  function disconnect(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    const ws = socket.current;
    socket.current = null;
    ws?.close();
    releaseMedia();
    setStatus("idle");
    setResponse("");
    setRequests([]);
    setInputStatus("连接已关闭；关闭连接不表示结果已排空");
  }

  function send(command: Record<string, unknown>) {
    const ws = socket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !connected) {
      setError("连接尚未就绪，请先连接");
      return false;
    }
    ws.send(JSON.stringify(command));
    return true;
  }

  async function enablePlayback() {
    try {
      await player.current.enable();
      playbackAllowed.current = true;
      setPlayEnabled(true);
    } catch (error) {
      setError(errorMessage(error));
    }
  }

  function consume(event: Event) {
    const seq = ++sequence.current;
    setEventCount((value) => value + 1);
    setLogs((value) =>
      [
        ...value,
        {
          seq,
          time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
          type: event.type,
          detail: inspectEvent(event),
        },
      ].slice(-200),
    );
    setEntries((value) => updateEntries(value, event));
    if (event.type === "x_aha.input_audio_transcription.interim")
      setTranscriptionStatus("正在更新临时转写");
    if (event.type === "x_aha.input_audio_transcription.interim_cleared")
      setTranscriptionStatus("等待正式转写");
    if (event.type === "conversation.item.input_audio_transcription.delta")
      setTranscriptionStatus("正在接收转写");
    if (event.type === "conversation.item.input_audio_transcription.completed")
      setTranscriptionStatus("已收到完整转写");
    if (event.type === "conversation.item.input_audio_transcription.failed") {
      setTranscriptionStatus("转写失败");
      const detail = event.error as { message?: string } | undefined;
      setError(detail?.message ?? "转写失败，请检查工作流和引擎后重试");
    }
    if (event.type === "response.created") setResponse("生成中");
    if (event.type === "response.done") {
      const value = event.response as { status?: string };
      setResponse(`响应 ${value?.status ?? "已结束"}`);
    }
    if (event.type === "error") {
      const value = event.error as { message?: string; code?: string };
      setError(
        `${value?.code ?? "服务端错误"}：${value?.message ?? "请检查会话配置"}`,
      );
      setResponse("");
      setRequests((value) =>
        value.map((request) => ({ ...request, pending: false })),
      );
    }
    if (
      event.type === "response.output_audio.delta" &&
      typeof event.delta === "string" &&
      playbackAllowed.current
    ) {
      try {
        player.current.play(event.delta);
      } catch (error) {
        setError(errorMessage(error));
        playbackAllowed.current = false;
        setPlayEnabled(false);
      }
    }
    if (
      event.type === "x_aha.agent_backend.approval.requested" ||
      event.type === "x_aha.agent_backend.question.requested"
    ) {
      const key = `${event.request_id}:${event.approval_id ?? "question"}`;
      const request: Request = {
        key,
        kind: event.type.includes(".approval.") ? "approval" : "question",
        event,
      };
      setRequests((value) =>
        [...value.filter((item) => item.key !== key), request].slice(-32),
      );
    }
    if (
      event.type.endsWith(".approval.resolved") ||
      event.type.endsWith(".question.resolved")
    ) {
      setRequests((value) =>
        value.filter(
          (item) =>
            item.event.request_id !== event.request_id ||
            (event.approval_id !== undefined &&
              item.event.approval_id !== event.approval_id),
        ),
      );
    }
    const item = event.item as { type?: string; id?: string } | undefined;
    if (
      [
        "conversation.item.created",
        "conversation.item.added",
        "conversation.item.done",
        "response.output_item.added",
      ].includes(event.type) &&
      item?.type === "mcp_approval_request" &&
      item.id
    ) {
      const request: Request = {
        key: item.id,
        kind: "approval",
        event: { ...event, approval_id: item.id, backend: false },
      };
      setRequests((value) =>
        [...value.filter((item) => item.key !== request.key), request].slice(
          -32,
        ),
      );
    }
  }

  function connect(event: FormEvent) {
    event.preventDefault();
    if (locked || !model.trim() || !config) return;
    setError("");
    setStatus("connecting");
    setResponse("");
    setInputStatus("等待输入");
    setTranscriptionStatus("等待转写事件");
    setRequests([]);
    setAnswers({});
    setSentBytes(0);
    setEventCount(0);
    setLogs([]);
    setSelected(null);
    sequence.current = 0;
    if (mode === "realtime") void enablePlayback();
    const ws = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/session`,
    );
    socket.current = ws;
    ws.onopen = () =>
      ws.send(
        JSON.stringify({
          action: "connect",
          model: model.trim(),
          mode,
          sample_rate: rate,
        }),
      );
    ws.onmessage = ({ data }: MessageEvent<string>) => {
      if (socket.current !== ws) return;
      try {
        const value: Envelope = JSON.parse(data);
        if (value.kind === "status" && value.status) setStatus(value.status);
        if (value.kind === "event" && value.event) consume(value.event);
        if (value.kind === "error") {
          setError(value.message ?? "请求失败");
          setResponse("");
          setRequests((value) =>
            value.map((request) => ({ ...request, pending: false })),
          );
          if (value.event) consume(value.event);
          if (value.fatal) {
            setStatus("failed");
            releaseMedia();
            ws.close();
          }
        }
        if (value.kind === "sent" && value.action === "audio")
          setSentBytes((count) => count + (value.bytes ?? 0));
        if (value.kind === "sent" && value.action === "commit")
          setInputStatus(mode === "transcription"
            ? "录音已停止，等待尾段转写"
            : "音频已提交，等待服务端事件");
        if (value.kind === "sent" && value.action === "text") {
          const message = pendingText.current;
          const entry: Entry = {
            id: `local:${Date.now()}`,
            role: "user",
            text: message,
            complete: true,
          };
          if (message) setEntries((entries) => [...entries, entry].slice(-200));
          pendingText.current = "";
        }
      } catch (error) {
        setError(errorMessage(error));
        setStatus("failed");
        releaseMedia();
        ws.close();
      }
    };
    ws.onerror = () => {
      if (socket.current === ws)
        setError("无法连接 demo 桥接服务，请确认后端运行后重新连接");
    };
    ws.onclose = () => {
      if (socket.current !== ws) return;
      socket.current = null;
      releaseMedia();
      setStatus("failed");
      setRequests([]);
      setResponse("");
      setError(
        (value) => value || "连接已中断，结果可能尚未处理完；请重新连接",
      );
    };
  }

  async function startMic() {
    setError("");
    setMicState("opening");
    const token = ++generation.current;
    const ws = socket.current;
    const audioRate = rate;
    try {
      const mic = await Microphone.open(audioRate, (pcm) => {
        if (generation.current !== token || ws?.readyState !== WebSocket.OPEN)
          return;
        if (ws.bufferedAmount + pcm.byteLength > 1024 * 1024) {
          releaseMedia();
          setError(
            "音频发送积压超过 1 MiB，采集已停止；丢弃输入后重试或重新连接",
          );
          return;
        }
        ws.send(pcm);
        if (performance.now() - lastMeter.current > 100) {
          lastMeter.current = performance.now();
          const view = new DataView(pcm);
          let total = 0;
          for (let i = 0; i < pcm.byteLength; i += 2)
            total += (view.getInt16(i, true) / 32768) ** 2;
          setLevel(
            Math.min(100, Math.sqrt(total / (pcm.byteLength / 2)) * 300),
          );
        }
      });
      if (token !== generation.current) {
        await mic.stop(false);
        return;
      }
      microphone.current = mic;
      setMicState("on");
      setInputStatus("正在采集麦克风");
    } catch (error) {
      if (token === generation.current) {
        setError(`麦克风：${errorMessage(error)}。检查浏览器权限后重试`);
        setMicState("off");
      }
    }
  }

  async function stopMic(commit: boolean) {
    const mic = microphone.current;
    microphone.current = null;
    setMicState("stopping");
    try {
      await mic?.stop(commit);
      setMicState("off");
      setLevel(0);
      if (commit) {
        if (send({ action: "commit" })) {
          setInputStatus("正在提交音频");
          if (mode === "realtime") setResponse("发送中");
        }
      } else {
        generation.current++;
        send({ action: "clear" });
        setInputStatus("已丢弃未提交输入");
      }
    } catch (error) {
      setError(errorMessage(error));
      setMicState("off");
    }
  }

  function sendText(event: FormEvent) {
    event.preventDefault();
    if (!text.trim() || busyResponse) return;
    setError("");
    pendingText.current = text.trim();
    if (send({ action: "text", text: text.trim() })) {
      setText("");
      setResponse("发送中");
    }
  }

  function respond(request: Request, approved?: boolean) {
    try {
      const command =
        request.kind === "approval"
          ? {
              action: "approval",
              request_id: request.event.request_id ?? "",
              approval_id: request.event.approval_id,
              approved,
              backend: request.event.backend !== false,
            }
          : {
              action: "question",
              request_id: request.event.request_id,
              answers: JSON.parse(answers[request.key] ?? "") as unknown,
            };
      if (request.kind === "question" && !Array.isArray(command.answers))
        throw new Error("回答必须是 JSON 数组，请按服务端问题结构填写");
      if (send(command)) {
        if (request.event.backend === false)
          setRequests((value) =>
            value.filter((item) => item.key !== request.key),
          );
        else
          setRequests((value) =>
            value.map((item) =>
              item.key === request.key ? { ...item, pending: true } : item,
            ),
          );
      }
    } catch (error) {
      setError(errorMessage(error));
    }
  }

  const visibleLogs = logs.filter((log) => log.type.includes(query.trim()));
  return (
    <>
      <a className="skip" href="#workspace">
        跳到会话内容
      </a>
      <header className="app-header">
        <a href="/" className="brand" aria-label="AHA SDK 客户端工作台首页">
          <span className="brand-mark" aria-hidden="true">
            a
          </span>
          <strong>aha</strong>
          <span className="header-divider" />
          <span>SDK 客户端工作台</span>
        </a>
        <div className="header-actions">
          <span className="version">
            Python SDK {config?.sdk_version ?? "—"}
          </span>
          <button
            className="quiet"
            onClick={() => setTheme(theme === "vallum" ? "abyssus" : "vallum")}
            aria-label={theme === "vallum" ? "切换苍渊主题" : "切换白垣主题"}
          >
            {theme === "vallum" ? "◐ 苍渊" : "◑ 白垣"}
          </button>
        </div>
      </header>
      <main>
        <section className="connection" aria-labelledby="connection-title">
          <div className="section-heading">
            <div>
              <h1 id="connection-title">连接与交互</h1>
              <p>选择已发布的模型，开始一次真实客户端会话。</p>
            </div>
            <div className={`connection-status ${status}`} role="status">
              <span aria-hidden="true" className="status-dot" />
              {statusLabels[status]}
            </div>
          </div>
          <div className="endpoint">
            <span>服务地址</span>
            <code>{config?.base_url ?? "正在读取配置…"}</code>
            <span
              className={`connection-status upstream-status ${upstreamStatus}`}
              role="status"
              aria-label="上游访问状态"
            >
              <span aria-hidden="true" className="status-dot" />
              {upstreamStatus === "checking"
                ? "正在检查上游…"
                : upstreamStatus === "ready"
                  ? `上游访问成功 · ${models.length} 个已发布模型`
                  : "上游访问失败"}
            </span>
            <span>
              {config?.authenticated ? "服务端已配置凭据" : "未配置 API Key"}
            </span>
          </div>
          <form onSubmit={connect} className="connection-form">
            <div className="field model-field">
              <label id="model-label" htmlFor="model">Model slug</label>
              <div className="model-input">
                <Choice
                  id="model"
                  value={model}
                  options={models.map((value) => ({ value, label: value }))}
                  onChange={setModel}
                  disabled={locked}
                  editable
                  placeholder="已发布的工作流或 Agent slug"
                  describedBy="model-help"
                />
                <button
                  type="button"
                  className="quiet"
                  disabled={locked || modelsBusy || !config}
                  onClick={() => void loadModels()}
                >
                  {modelsBusy
                    ? "读取中"
                    : upstreamStatus === "failed"
                      ? "重试访问"
                      : "读取模型"}
                </button>
              </div>
            </div>
            <div className="field mode-field">
              <label id="mode-label" htmlFor="mode">交互模式</label>
              <Choice
                id="mode"
                disabled={locked}
                value={mode}
                onChange={(value) => setMode(value as typeof mode)}
                options={[
                  { value: "transcription", label: "实时转写" },
                  { value: "realtime", label: "语音 Agent" },
                ]}
              />
            </div>
            <div className="field rate-field">
              <label id="rate-label" htmlFor="rate">输入采样率</label>
              <Choice
                id="rate"
                disabled={locked}
                value={String(rate)}
                onChange={(value) => setRate(Number(value))}
                options={[
                  { value: "16000", label: "16 kHz" },
                  { value: "24000", label: "24 kHz" },
                ]}
              />
            </div>
            {locked ? (
              <button
                type="button"
                className="primary connect-button"
                onClick={disconnect}
              >
                断开连接
              </button>
            ) : (
              <button
                className="primary connect-button"
                disabled={!config || !model.trim()}
              >
                连接会话 <span aria-hidden="true">↗</span>
              </button>
            )}
          </form>
          <p id="model-help" className="field-help">
            {modelError ||
              "转写模式使用工作流 slug；语音 Agent 模式使用 Agent slug。连接后配置锁定。"}
          </p>
        </section>
        {error && (
          <div className="error-banner" role="alert">
            <strong>操作未完成</strong>
            <span>{error}</span>
            <button className="quiet" onClick={() => setError("")}>
              收起提示
            </button>
          </div>
        )}
        <div className="workspace-grid" id="workspace">
          <section className="session" aria-labelledby="session-title">
            <div className="region-heading">
              <h2 id="session-title">
                {mode === "transcription" ? "实时转写" : "语音对话"}
              </h2>
              {mode === "transcription" && (
                <span className="transcription-status" role="status">
                  {transcriptionStatus}
                </span>
              )}
              <button
                className="quiet"
                disabled={!entries.length}
                onClick={() => setEntries([])}
              >
                清空正文
              </button>
            </div>
            <div className="transcript" tabIndex={0} aria-label="会话正文">
              {!entries.length && (
                <div className="empty-state">
                  <span className="empty-symbol" aria-hidden="true">
                    〰
                  </span>
                  <h3>让声音成为内容</h3>
                  <p>
                    连接会话后，开启麦克风并说话。
                    <br />
                    {mode === "transcription"
                      ? "录音期间自动断句并显示转写；临时结果取决于工作流能力。"
                      : "停止并提交后，在这里查看服务端的真实结果。"}
                  </p>
                  <ol>
                    <li>填写已发布的 model</li>
                    <li>连接并允许麦克风权限</li>
                    <li>{mode === "transcription"
                      ? "持续录音，观察转写；停止时收尾"
                      : "录音、提交，观察事件与结果"}</li>
                  </ol>
                </div>
              )}
              {entries.map((entry) => (
                <article
                  key={entry.id}
                  className={`transcript-entry ${entry.role}`}
                >
                  <div className="entry-meta">
                    <strong>{entry.role === "user" ? "你" : "Agent"}</strong>
                    <span>{entry.complete
                      ? "完整文本"
                      : entry.interim ? "临时转写" : "正在接收"}</span>
                  </div>
                  <p>{entry.text || "…"}</p>
                </article>
              ))}
              <div ref={transcriptEnd} />
            </div>
            {!!requests.length && (
              <section className="requests" aria-label="待处理请求">
                {requests.map((request) => (
                  <div key={request.key} className="request">
                    <h3>
                      {request.kind === "approval"
                        ? "工具需要你的审批"
                        : "Agent 需要补充信息"}
                    </h3>
                    <pre>
                      {JSON.stringify(
                        request.kind === "question"
                          ? request.event.questions
                          : request.event,
                        null,
                        2,
                      )}
                    </pre>
                    {request.kind === "approval" ? (
                      <div className="actions">
                        <button
                          className="primary"
                          disabled={!connected || request.pending}
                          onClick={() => respond(request, true)}
                        >
                          允许
                        </button>
                        <button
                          disabled={!connected || request.pending}
                          onClick={() => respond(request, false)}
                        >
                          拒绝
                        </button>
                      </div>
                    ) : (
                      <>
                        <label htmlFor={`answer-${request.key}`}>
                          回答 JSON 数组
                        </label>
                        <textarea
                          id={`answer-${request.key}`}
                          value={answers[request.key] ?? ""}
                          onChange={(event) =>
                            setAnswers((value) => ({
                              ...value,
                              [request.key]: event.target.value,
                            }))
                          }
                          placeholder='[{"id":"问题 id","selected":[],"custom":"回答"}]'
                        />
                        <button
                          disabled={!connected || request.pending}
                          onClick={() => respond(request)}
                        >
                          提交回答
                        </button>
                      </>
                    )}
                    {request.pending && (
                      <p role="status">已发送，等待服务端处理结果</p>
                    )}
                  </div>
                ))}
              </section>
            )}
            <div className="input-dock">
              <div className="input-status">
                <span role="status">{inputStatus}</span>
                <span
                  className="meter"
                  role="meter"
                  aria-label="麦克风实时电平"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(level)}
                >
                  <i style={{ width: `${level}%` }} />
                </span>
                <span className="numeric">
                  {(sentBytes / (rate * 2)).toFixed(1)} s 已发送
                </span>
              </div>
              <div className="actions audio-actions">
                <button
                  className="primary"
                  disabled={
                    !connected ||
                    micState === "opening" ||
                    micState === "stopping" ||
                    (!recording && busyResponse)
                  }
                  onClick={() => void (recording ? stopMic(true) : startMic())}
                >
                  {recording
                    ? mode === "transcription" ? "■ 停止录音" : "■ 停止并提交"
                    : micState === "opening"
                      ? "请求麦克风…"
                      : micState === "stopping"
                        ? "正在停止…"
                        : "● 开始录音"}
                </button>
                <button
                  disabled={
                    !connected ||
                    micState === "opening" ||
                    micState === "stopping"
                  }
                  onClick={() =>
                    recording
                      ? void stopMic(false)
                      : (send({ action: "clear" }),
                        setInputStatus("已丢弃未提交输入"))
                  }
                >
                  丢弃输入
                </button>
                {mode === "realtime" && (
                  <button
                    disabled={!connected}
                    onClick={() => {
                      player.current.clear();
                      send({ action: "cancel" });
                    }}
                  >
                    取消响应
                  </button>
                )}
              </div>
              {mode === "realtime" && (
                <>
                  <form className="text-form" onSubmit={sendText}>
                    <label className="sr-only" htmlFor="message">
                      文本消息
                    </label>
                    <textarea
                      id="message"
                      rows={2}
                      placeholder="也可以输入文本，测试一轮 Agent 响应…"
                      value={text}
                      disabled={!connected}
                      onChange={(event) => setText(event.target.value)}
                      maxLength={16000}
                    />
                    <button
                      className="primary"
                      disabled={!connected || !text.trim() || busyResponse}
                    >
                      发送
                    </button>
                  </form>
                  <div className="playback">
                    <span role="status">{response || "等待 Agent 响应"}</span>
                    <button
                      className="quiet"
                      onClick={() => {
                        if (playEnabled) {
                          playbackAllowed.current = false;
                          player.current.clear();
                          setPlayEnabled(false);
                        } else void enablePlayback();
                      }}
                    >
                      {playEnabled ? "关闭语音播放" : "启用语音播放"}
                    </button>
                    <button
                      className="quiet"
                      onClick={() => player.current.clear()}
                    >
                      清空播放
                    </button>
                  </div>
                </>
              )}
              <p className="field-help">
                {mode === "transcription"
                  ? "录音期间由服务端自动断句；停止录音会收尾剩余音频。"
                  : "手动提交一轮音频；停止录音后连接仍保持。"}
                断开连接不表示服务端结果已排空。
              </p>
            </div>
          </section>
          <details className="inspector" open>
            <summary>
              <span>事件检查器</span>
              <span className="numeric">{eventCount} 个事件</span>
            </summary>
            <div className="inspector-body">
              <label className="sr-only" htmlFor="filter">
                按事件类型筛选
              </label>
              <input
                id="filter"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="筛选事件类型…"
              />
              <p className="field-help">保留最近 200 条 · 音频以字节数展示</p>
              <div className="event-list" aria-label="事件列表">
                {!logs.length && (
                  <div className="event-empty">
                    连接后可查看握手、转写与响应事件。
                    <code>
                      session.created
                      <br />
                      session.updated
                      <br />…
                    </code>
                  </div>
                )}
                {logs.length > 0 && !visibleLogs.length && (
                  <p className="field-help">没有匹配的事件，清除筛选后重试。</p>
                )}
                {visibleLogs.map((log) => (
                  <button
                    key={log.seq}
                    className={`event-row ${selected?.seq === log.seq ? "selected" : ""}`}
                    aria-pressed={selected?.seq === log.seq}
                    onClick={() => setSelected(log)}
                  >
                    <span>
                      <span className="event-number">
                        {String(log.seq).padStart(3, "0")}
                      </span>
                      <time>{log.time}</time>
                    </span>
                    <code>{log.type}</code>
                  </button>
                ))}
              </div>
              <div className="event-detail">
                <div className="region-heading">
                  <h3>事件内容</h3>
                  {selected && <span>#{selected.seq}</span>}
                </div>
                <pre tabIndex={0}>
                  {selected?.detail ?? "选择一条事件，检查 SDK 接收到的内容。"}
                </pre>
              </div>
            </div>
          </details>
        </div>
        <footer>
          <span>aha · 原生 Python SDK</span>
          <span>单连接 · PCM S16LE · 输出 24 kHz</span>
        </footer>
      </main>
    </>
  );
}
