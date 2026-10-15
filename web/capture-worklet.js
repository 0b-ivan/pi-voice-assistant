// Microphone -> 16 kHz mono s16le, the format of POST /v1/turn (like the Pi).
// Box-filter decimation: each output sample averages the input it covers.
class Capture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.ratio = sampleRate / options.processorOptions.targetRate;
    this.sum = 0; this.count = 0; this.pos = 0; this.peak = 0;
    this.block = new Int16Array(1600);  // 100 ms
    this.fill = 0;
    this.active = true;
    this.port.onmessage = (event) => {
      if (event.data === 'stop') { this.flush(); this.active = false; this.port.postMessage('end'); }
    };
  }

  push(value) {
    const clipped = Math.max(-1, Math.min(1, value));
    this.block[this.fill++] = clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff;
    this.peak = Math.max(this.peak, Math.abs(clipped));
    if (this.fill === this.block.length) this.flush();
  }

  flush() {
    if (!this.fill) return;
    const pcm = this.block.slice(0, this.fill);
    this.port.postMessage({ pcm, peak: this.peak }, [pcm.buffer]);
    this.fill = 0; this.peak = 0;
  }

  process(inputs) {
    if (!this.active) return false;
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i++) {
        this.sum += channel[i]; this.count++; this.pos += 1;
        if (this.pos >= this.ratio) {
          this.pos -= this.ratio;
          this.push(this.sum / this.count);
          this.sum = 0; this.count = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('servitor-capture', Capture);
