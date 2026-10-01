export class Microphone {
  private constructor(
    private stream: MediaStream,
    private context: AudioContext,
    private node: AudioWorkletNode,
    private source: MediaStreamAudioSourceNode,
  ) {}

  static async open(
    rate: number,
    onPcm: (pcm: ArrayBuffer) => void,
  ): Promise<Microphone> {
    if (!navigator.mediaDevices?.getUserMedia)
      throw new Error("麦克风需要 localhost 或 HTTPS 安全上下文");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
      video: false,
    });
    const context = new AudioContext();
    try {
      await context.audioWorklet.addModule("/pcm-worklet.js");
      await context.resume();
      const node = new AudioWorkletNode(context, "pcm-capture", {
        processorOptions: { targetRate: rate },
        channelCount: 1,
        channelCountMode: "explicit",
      });
      const source = context.createMediaStreamSource(stream);
      const silent = context.createGain();
      silent.gain.value = 0;
      node.port.onmessage = ({ data }: MessageEvent<{ pcm?: ArrayBuffer }>) => {
        if (data.pcm) onPcm(data.pcm);
      };
      source.connect(node).connect(silent).connect(context.destination);
      return new Microphone(stream, context, node, source);
    } catch (error) {
      stream.getTracks().forEach((track) => track.stop());
      await context.close();
      throw error;
    }
  }

  async stop(flush = true): Promise<void> {
    this.source.disconnect();
    this.stream.getTracks().forEach((track) => track.stop());
    if (flush) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error("音频采集停止超时，请丢弃输入后重试")),
          2000,
        );
        const previous = this.node.port.onmessage;
        this.node.port.onmessage = (event: MessageEvent) => {
          if (event.data.stopped) {
            clearTimeout(timer);
            resolve();
          } else previous?.call(this.node.port, event);
        };
        this.node.port.postMessage("stop");
      }).finally(() => this.context.close());
    } else {
      this.node.port.onmessage = null;
      await this.context.close();
    }
  }
}

export class Playback {
  private context: AudioContext | null = null;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();

  async enable(): Promise<void> {
    this.context ??= new AudioContext();
    await this.context.resume();
  }

  play(base64: string): void {
    const context = this.context;
    if (!context || context.state !== "running")
      throw new Error("播放尚未启用，请点击「启用播放」后重试下一轮");
    const binary = atob(base64);
    if (!binary.length || binary.length % 2)
      throw new Error("收到无效 PCM 音频");
    if (
      Math.max(0, this.next - context.currentTime) +
        binary.length / (24000 * 2) >
      10
    ) {
      this.clear();
      throw new Error("播放积压超过 10 秒，已清空播放队列");
    }
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const view = new DataView(bytes.buffer);
    const buffer = context.createBuffer(1, bytes.length / 2, 24000);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++)
      samples[i] = view.getInt16(i * 2, true) / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.onended = () => this.sources.delete(source);
    this.sources.add(source);
    this.next = Math.max(this.next, context.currentTime + 0.02);
    source.start(this.next);
    this.next += buffer.duration;
  }

  clear(): void {
    for (const source of this.sources) source.stop();
    this.sources.clear();
    this.next = 0;
  }

  close(): void {
    this.clear();
    void this.context?.close();
    this.context = null;
  }
}
