"use strict";
// Runs on the browser's audio render thread. No speech recognition or
// business logic lives here: the shared Servitor server receives PCM16.
class ProximusCapture extends AudioWorkletProcessor {
  process(inputs, outputs) {
    const channels = inputs[0];
    if (channels && channels.length && channels[0].length) {
      const frames = channels[0].length;
      const mono = new Float32Array(frames);
      for (let i = 0; i < frames; i++) {
        let sum = 0;
        for (const channel of channels) sum += channel[i];
        mono[i] = sum / channels.length;
      }
      this.port.postMessage(mono, [mono.buffer]);
    }
    // No microphone monitoring: all output channels remain zero.
    for (const output of outputs) for (const channel of output) channel.fill(0);
    return true;
  }
}
registerProcessor("proximus-capture", ProximusCapture);
