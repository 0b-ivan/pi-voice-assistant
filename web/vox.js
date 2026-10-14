// The vox terminal: everything that makes the page feel like calling Proximus
// over a crackling long-range vox-caster. Sounds are synthesized (Web Audio),
// nothing is downloaded. Effects can be switched off in the settings; the
// turn itself (app.js) works the same either way.
'use strict';

const Vox = (() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let ctx = null;
  let noise = null;        // 2 s of white noise, looped or sliced
  let master = null;
  let analyser = null;     // what Proximus says, for the scope and the needle
  let dry = null, wet = null;
  let bed = null;          // the hiss of the open channel
  let attached = false;
  let effects = true;
  let connected = false;
  let glitchTimer = null;
  let fxUntil = 0;         // performance.now() until which effect sounds play
  let crosstalkTimer = null;

  const canvases = [];
  const needle = { elements: [], value: 0 };
  let volume = 0.8;
  // Set by app.js: an AnalyserNode of the microphone while recording,
  // the turn state ('idle', 'listen', 'work', 'speak') and the link quality.
  const live = { mic: null, state: 'idle', speaking: false, link: 'unknown', latency: null, peak: 0 };

  function ensure() {
    if (!ctx) {
      const Context = window.AudioContext || window.webkitAudioContext;   // iOS < 14.5
      ctx = new Context();
      const length = ctx.sampleRate * 2;
      noise = ctx.createBuffer(1, length, ctx.sampleRate);
      const data = noise.getChannelData(0);
      for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
      master = ctx.createGain();
      master.gain.value = volume;
      master.connect(ctx.destination);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      analyser.connect(master);
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  // Proximus' voice through a vox band-pass with a little saturation:
  // a far-away transmitter. The dry path takes over when effects are off.
  function attach(element) {
    if (attached) return;
    ensure();
    attached = true;
    const source = ctx.createMediaElementSource(element);
    dry = ctx.createGain();
    wet = ctx.createGain();
    const low = ctx.createBiquadFilter();
    // Mild: the Servitor voice is already processed on the server; the radio
    // colour must not cost intelligibility.
    low.type = 'highpass'; low.frequency.value = 180;
    const high = ctx.createBiquadFilter();
    high.type = 'lowpass'; high.frequency.value = 5200;
    const presence = ctx.createBiquadFilter();
    presence.type = 'peaking'; presence.frequency.value = 2200; presence.gain.value = 2;
    const shaper = ctx.createWaveShaper();
    const curve = new Float32Array(1024);
    for (let i = 0; i < curve.length; i++) {
      const x = i / (curve.length - 1) * 2 - 1;
      curve[i] = Math.tanh(1.2 * x) / Math.tanh(1.2);
    }
    shaper.curve = curve;
    source.connect(dry);
    source.connect(low).connect(high).connect(presence).connect(shaper).connect(wet);
    dry.connect(analyser);
    wet.connect(analyser);
    setEffects(effects);
  }

  // Effect sounds go through the analyser: the scope shows them too.
  function audible(seconds, at = 0) {
    fxUntil = Math.max(fxUntil, performance.now() + (at + seconds) * 1000);
  }

  function burst(seconds, { gain = 0.2, freq = 1800, q = 1, at = 0, attack = 0.004, type = 'bandpass' } = {}) {
    if (!effects || !ctx) return;
    audible(seconds, at);
    const start = ctx.currentTime + at;
    const source = ctx.createBufferSource();
    source.buffer = noise;
    const filter = ctx.createBiquadFilter();
    filter.type = type; filter.frequency.value = freq; filter.Q.value = q;
    const envelope = ctx.createGain();
    envelope.gain.setValueAtTime(0, start);
    envelope.gain.linearRampToValueAtTime(gain, start + attack);
    envelope.gain.exponentialRampToValueAtTime(0.0001, start + seconds);
    source.connect(filter).connect(envelope).connect(analyser);
    source.start(start, Math.random() * 1.5, seconds + 0.05);
  }

  function tone(freq, seconds, at = 0, gain = 0.05, type = 'square') {
    if (!effects || !ctx) return;
    audible(seconds, at);
    const start = ctx.currentTime + at;
    const osc = ctx.createOscillator();
    osc.type = type; osc.frequency.value = freq;
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass'; filter.frequency.value = 2600;
    const envelope = ctx.createGain();
    envelope.gain.setValueAtTime(0, start);
    envelope.gain.linearRampToValueAtTime(gain, start + 0.004);
    envelope.gain.setValueAtTime(gain, start + seconds - 0.008);
    envelope.gain.linearRampToValueAtTime(0, start + seconds);
    osc.connect(filter).connect(envelope).connect(analyser);
    osc.start(start);
    osc.stop(start + seconds + 0.02);
  }

  const sounds = {
    // The press and release of the transmit key.
    click() { burst(0.025, { gain: 0.5, freq: 2600, q: 0.8, attack: 0.001 }); },
    // Squelch tail at the end of a transmission.
    squelch(at = 0) { burst(0.22, { gain: 0.16, freq: 1700, q: 0.6, at }); },
    // Carrier opens before Proximus speaks.
    open() { burst(0.09, { gain: 0.22, freq: 2100, q: 0.7 }); },
    // Binharic sign-off, like the Pi's acknowledgement: eight bits of cant.
    cant(at = 0) {
      for (let i = 0; i < 8; i++) tone(Math.random() < 0.5 ? 1150 : 1650, 0.038, at + i * 0.045, 0.035);
    },
    crackle(at = 0) {
      const count = 3 + Math.floor(Math.random() * 5);
      for (let i = 0; i < count; i++) {
        burst(0.015 + Math.random() * 0.05, { gain: 0.05 + Math.random() * 0.12,
          freq: 900 + Math.random() * 3000, q: 1.5, at: at + Math.random() * 0.45, attack: 0.001 });
      }
    },
    // Another channel bleeding in: speech-like but unintelligible. A buzzing
    // glottal source (sawtooth with a wandering pitch) through two formant
    // filters that jump from "vowel" to "vowel" in syllable rhythm, then the
    // narrow, distorted band of a distant transmitter with hiss on top.
    crosstalk(seconds = 1.4 + Math.random() * 2.2) {
      if (!effects || !ctx) return;
      audible(seconds + 0.3);
      const start = ctx.currentTime + 0.05;
      const end = start + seconds;
      const voice = ctx.createOscillator();
      voice.type = 'sawtooth';
      const base = 95 + Math.random() * 90;   // a different speaker each time
      for (let t = start; t < end; t += 0.09) {
        voice.frequency.linearRampToValueAtTime(base * (0.85 + Math.random() * 0.35), t);
      }
      const breath = ctx.createBufferSource();
      breath.buffer = noise; breath.loop = true;
      const breathLevel = ctx.createGain();
      breathLevel.gain.value = 0.25;
      const syllables = ctx.createGain();
      syllables.gain.setValueAtTime(0, start);
      const f1 = ctx.createBiquadFilter(), f2 = ctx.createBiquadFilter();
      f1.type = f2.type = 'bandpass'; f1.Q.value = 6; f2.Q.value = 9;
      let t = start;
      while (t < end - 0.08) {
        const length = 0.09 + Math.random() * 0.16;
        f1.frequency.setTargetAtTime(280 + Math.random() * 600, t, 0.02);
        f2.frequency.setTargetAtTime(900 + Math.random() * 1500, t, 0.02);
        syllables.gain.setTargetAtTime(0.6 + Math.random() * 0.4, t, 0.015);
        syllables.gain.setTargetAtTime(0.05, t + length, 0.02);
        t += length + (Math.random() < 0.18 ? 0.15 + Math.random() * 0.25 : 0.03);  // now and then a pause
      }
      syllables.gain.setTargetAtTime(0, end, 0.03);
      const band = ctx.createBiquadFilter();
      band.type = 'bandpass'; band.frequency.value = 1300; band.Q.value = 0.9;
      const shaper = ctx.createWaveShaper();
      const curve = new Float32Array(512);
      for (let i = 0; i < curve.length; i++) curve[i] = Math.tanh(4 * (i / 255.5 - 1));
      shaper.curve = curve;
      const level = ctx.createGain();
      level.gain.value = 0.045;
      // Fading in and out like a signal drifting across the band.
      const drift = ctx.createGain();
      drift.gain.setValueAtTime(0, start);
      drift.gain.linearRampToValueAtTime(1, start + 0.25);
      drift.gain.setValueAtTime(1, end - 0.3);
      drift.gain.linearRampToValueAtTime(0, end);
      voice.connect(syllables);
      breath.connect(breathLevel).connect(syllables);
      syllables.connect(f1); syllables.connect(f2);
      f1.connect(band); f2.connect(band);
      band.connect(shaper).connect(level).connect(drift).connect(analyser);
      voice.start(start); voice.stop(end + 0.1);
      breath.start(start, Math.random()); breath.stop(end + 0.1);
      burst(0.4, { gain: 0.05, freq: 1500, q: 0.5, at: 0 });     // hiss as it comes in
      sounds.crackle(seconds * Math.random());
      burst(0.25, { gain: 0.07, freq: 1700, q: 0.6, at: seconds + 0.05 });  // and squelch as it goes
    },
    // Searching the band: swept noise with a heterodyne whistle.
    tune(seconds = 1.8) {
      if (!effects || !ctx) return;
      audible(seconds);
      const start = ctx.currentTime;
      const source = ctx.createBufferSource();
      source.buffer = noise; source.loop = true;
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass'; filter.Q.value = 4;
      filter.frequency.setValueAtTime(380, start);
      filter.frequency.exponentialRampToValueAtTime(2900, start + seconds * 0.45);
      filter.frequency.exponentialRampToValueAtTime(700, start + seconds * 0.8);
      filter.frequency.exponentialRampToValueAtTime(1500, start + seconds);
      const envelope = ctx.createGain();
      envelope.gain.setValueAtTime(0, start);
      envelope.gain.linearRampToValueAtTime(0.18, start + 0.15);
      envelope.gain.setValueAtTime(0.18, start + seconds - 0.2);
      envelope.gain.linearRampToValueAtTime(0.0001, start + seconds);
      source.connect(filter).connect(envelope).connect(analyser);
      source.start(start); source.stop(start + seconds + 0.05);
      const whistle = ctx.createOscillator();
      whistle.type = 'sine';
      whistle.frequency.setValueAtTime(2400, start);
      whistle.frequency.exponentialRampToValueAtTime(420, start + seconds * 0.7);
      whistle.frequency.exponentialRampToValueAtTime(1000, start + seconds);
      const level = ctx.createGain();
      level.gain.setValueAtTime(0, start);
      level.gain.linearRampToValueAtTime(0.025, start + 0.3);
      level.gain.linearRampToValueAtTime(0, start + seconds);
      whistle.connect(level).connect(analyser);
      whistle.start(start); whistle.stop(start + seconds + 0.05);
    },
  };

  // The open channel hisses quietly; it ducks while the operator transmits.
  function hiss(on) {
    if (!ctx) return;
    if (on && effects && connected && !bed) {
      const source = ctx.createBufferSource();
      source.buffer = noise; source.loop = true;
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass'; filter.frequency.value = 1600; filter.Q.value = 0.5;
      const gain = ctx.createGain();
      gain.gain.value = 0;
      gain.gain.linearRampToValueAtTime(0.014, ctx.currentTime + 1.2);
      source.connect(filter).connect(gain).connect(master);
      source.start();
      bed = { source, gain };
    } else if (!on && bed) {
      const old = bed;
      bed = null;
      old.gain.gain.cancelScheduledValues(ctx.currentTime);
      old.gain.gain.setTargetAtTime(0, ctx.currentTime, 0.08);
      old.source.stop(ctx.currentTime + 0.5);
    }
  }

  function setEffects(on) {
    effects = on;
    document.body.dataset.effects = on ? 'on' : 'off';
    if (dry && wet) {
      // Mostly the clean voice, a share of the radio band on top.
      dry.gain.value = on ? 0.65 : 1;
      wet.gain.value = on ? 0.4 : 0;
    }
    hiss(on && connected);
    scheduleGlitch();
    scheduleCrosstalk();
  }

  // A foreign channel now and then while the line is open, never over the
  // operator or Proximus.
  function scheduleCrosstalk() {
    clearTimeout(crosstalkTimer);
    if (!effects || !connected) return;
    crosstalkTimer = setTimeout(() => {
      if (!live.mic && !live.speaking && live.state === 'idle') {
        sounds.crosstalk();
        if (api.onCrosstalk) api.onCrosstalk();
      }
      scheduleCrosstalk();
    }, 25000 + Math.random() * 45000);
  }

  // --- interference on the screen --------------------------------------------------

  function scheduleGlitch() {
    clearTimeout(glitchTimer);
    if (!effects || !connected) return;
    const busy = live.state === 'work' || live.state === 'speak';
    glitchTimer = setTimeout(() => {
      const screen = document.getElementById('chat');
      if (!reduced.matches && screen) {
        screen.classList.add('glitch');
        setTimeout(() => screen.classList.remove('glitch'), 120 + Math.random() * 260);
      }
      if (!live.mic) sounds.crackle();
      scheduleGlitch();
    }, (busy ? 2500 : 5000) + Math.random() * (busy ? 5000 : 10000));
  }

  // Grain for the screen: one small noise tile, made once.
  function grain() {
    const tile = document.createElement('canvas');
    tile.width = tile.height = 96;
    const g = tile.getContext('2d');
    const image = g.createImageData(96, 96);
    for (let i = 0; i < image.data.length; i += 4) {
      const v = Math.random() * 255;
      image.data[i] = image.data[i + 1] = image.data[i + 2] = v;
      image.data[i + 3] = 255;
    }
    g.putImageData(image, 0, 0);
    document.documentElement.style.setProperty('--grain', `url(${tile.toDataURL()})`);
  }

  // --- oscilloscope and needle --------------------------------------------------------

  const samples = new Uint8Array(1024);
  let last = 0;

  function levels(source, bars) {
    source.getByteTimeDomainData(samples);
    const step = Math.floor(samples.length / bars);
    const out = new Float32Array(bars);
    for (let b = 0; b < bars; b++) {
      let peak = 0;
      for (let i = b * step; i < (b + 1) * step; i++) peak = Math.max(peak, Math.abs(samples[i] - 128));
      out[b] = Math.min(1, peak / 128 * 2.4);
    }
    return out;
  }

  function synthetic(bars, time) {
    // No sound: a carrier line with static, busier while the server works.
    const out = new Float32Array(bars);
    const busy = live.state === 'work';
    for (let b = 0; b < bars; b++) {
      const centre = 1 - Math.abs(b / bars * 2 - 1);
      let value = 0.02 + Math.random() * (effects ? 0.05 : 0.01);
      if (busy) value += centre * (0.12 + 0.1 * Math.sin(time / 180 + b / 3)) * Math.random();
      if (effects && Math.random() < 0.004) value += Math.random() * 0.5;
      out[b] = Math.min(1, value);
    }
    return out;
  }

  function draw(time) {
    requestAnimationFrame(draw);
    if (document.hidden || time - last < 33) return;
    last = time;
    let source = null;
    if (live.mic) source = live.mic;
    else if (analyser && (live.speaking || time < fxUntil)) source = analyser;
    let peak = 0;
    for (const canvas of canvases) {
      if (!canvas.isConnected || !canvas.offsetParent) continue;
      const width = canvas.clientWidth, height = canvas.clientHeight;
      const scale = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(width * scale)) {
        canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
      }
      const g = canvas.getContext('2d');
      g.setTransform(scale, 0, 0, scale, 0, 0);
      g.clearRect(0, 0, width, height);
      const bars = Math.max(24, Math.floor(width / 5));
      const values = source ? levels(source, bars) : synthetic(bars, time);
      const color = getComputedStyle(canvas).color;
      g.fillStyle = color;
      const middle = height / 2;
      for (let b = 0; b < bars; b++) {
        const amplitude = Math.max(1, values[b] * (height / 2 - 2));
        const x = b * (width / bars);
        // Dashed bars like a phosphor scope: segments of 3 px.
        for (let y = 0; y < amplitude; y += 4) {
          g.globalAlpha = 0.95 - (y / amplitude) * 0.5;
          g.fillRect(x, middle - y - 2, 2, 2.5);
          g.fillRect(x, middle + y, 2, 2.5);
        }
        peak = Math.max(peak, values[b]);
      }
      g.globalAlpha = 1;
    }
    // Needle: the voice level when there is sound, else the signal strength.
    let target;
    if (live.mic) target = Math.min(1, live.peak * 1.4);
    else if (source) target = peak;
    else if (live.link === 'ok') {
      const latency = live.latency == null ? 150 : live.latency;
      target = Math.max(0.25, 0.8 - latency / 1000) + (effects ? (Math.random() - 0.5) * 0.06 : 0);
    } else if (live.link === 'loading') target = 0.2;
    else target = 0;
    needle.value += (target - needle.value) * 0.25;
    const angle = `rotate(${-85 + needle.value * 170}deg)`;   // S1 at the left end, S9 at the right
    for (const element of needle.elements) element.style.transform = angle;
  }

  const api = {
    onCrosstalk: null,   // app.js notes it in the journal
    live,
    sounds,
    ensure,
    attach,
    hiss,
    setEffects,
    get effects() { return effects; },
    get connected() { return connected; },
    connect() { connected = true; hiss(true); scheduleGlitch(); scheduleCrosstalk(); },
    disconnect() { connected = false; hiss(false); clearTimeout(glitchTimer); clearTimeout(crosstalkTimer); },
    stateChanged(mode) { live.state = mode; },
    // Everything Proximus and the terminal say goes through one volume control.
    setVolume(value) {
      volume = value;
      if (master) master.gain.setTargetAtTime(value, ctx.currentTime, 0.03);
    },
    start({ scopes, needles }) {
      canvases.push(...scopes);
      needle.elements = needles;
      grain();
      requestAnimationFrame(draw);
    },
  };
  return api;
})();
