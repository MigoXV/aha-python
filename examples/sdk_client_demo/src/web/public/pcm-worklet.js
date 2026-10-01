// AudioWorklet runs independently of rendering. The area accumulator is carried
// across callbacks so resampling and 20 ms framing do not lose boundary samples.
class PcmCapture extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this.ratio = sampleRate / options.processorOptions.targetRate
    this.frame = new Int16Array(options.processorOptions.targetRate / 50)
    this.index = 0
    this.weight = 0
    this.sum = 0
    this.active = true
    this.port.onmessage = ({ data }) => {
      if (data === 'stop') {
        this.active = false
        this.emit()
        this.port.postMessage({ stopped: true })
      }
    }
  }
  emit() {
    if (!this.index) return
    const bytes = new ArrayBuffer(this.index * 2)
    const view = new DataView(bytes)
    for (let i = 0; i < this.index; i++) view.setInt16(i * 2, this.frame[i], true)
    this.port.postMessage({ pcm: bytes }, [bytes])
    this.index = 0
  }
  process(inputs) {
    const input = inputs[0]?.[0]
    if (!this.active) return false
    if (!input) return true
    for (const value of input) {
      let remaining = 1
      while (remaining > 1e-9) {
        const take = Math.min(remaining, this.ratio - this.weight)
        this.sum += value * take
        this.weight += take
        remaining -= take
        if (this.weight >= this.ratio - 1e-9) {
          const sample = Math.max(-1, Math.min(1, this.sum / this.ratio))
          this.frame[this.index++] = Math.round(sample * (sample < 0 ? 32768 : 32767))
          this.sum = 0
          this.weight = 0
          if (this.index === this.frame.length) this.emit()
        }
      }
    }
    return true
  }
}
registerProcessor('pcm-capture', PcmCapture)
