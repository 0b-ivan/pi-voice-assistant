// Servitor Proximus in the browser: the same server, the same turn as the Pi.
// Hold the button (or space), speak, release: 16 kHz PCM goes to POST /v1/turn
// and the NDJSON answer (server/servitor_server.py) is shown and played.
// Typed questions go to POST /v1/ask. Nothing here keeps state on the server.
// The look and sound of the vox terminal (call, hiss, scope, needle) live in vox.js.
'use strict';

const RATE = 16000;                 // PCM_RATE of the server
const MAX_SECONDS = 29;             // SERVITOR_MAX_AUDIO_SECONDS is 30 by default
const MIN_SECONDS = 0.3;            // protocol.MIN_TURN_SECONDS is 0.2
const STAGES = { recognize: 'ENTSCHLÜSSELE SIGNAL', think: 'MASCHINENGEIST DENKT',
                 synthesize: 'KODIERE ANTWORT', render: 'VOX-MODULATION' };
// Litanies of the Cult Mechanicus. The Litany of Ignition (White Dwarf 67) and
// the call and response from The Forges of Mars; Latin from the Mechanicus' High
// Gothic tradition; the rest written for Proximus.
const LITANY = {
  ignition: ['Die Seele des Maschinengottes umgibt dich.', 'Die Macht des Maschinengottes erfüllt dich.',
             'Der Hass des Maschinengottes treibt dich an.', 'Der Maschinengott schenkt dir Leben.'],
  hint: ['Der Maschinengeist lauscht.', 'Wissen ist heilig. Halte die Verbindung rein.',
         'Wissen ist Werkzeug. Setze es mit Bedacht ein.',
         'Wissen ist heilig. Stelle deine Frage.', 'Mit dem Segen des Omnissiah.',
         'Gesegnet seien Leitung und Signal.'],
};
// Murmured in the background between the log lines.
LITANY.journal = ['Ehre dem Omnissiah.', 'Die Lebenden schwinden. Aber die Maschine besteht.',
  'Deus ex machina.', 'Die Maschine besteht.', 'Gesegnet sei der Datenstrom.',
  'Möge Weihrauch die Schaltkreise schützen.', 'Litanei der unverfälschten Übertragung.',
  'Möge der Maschinengeist Ruhe finden.', 'Fehler seien benannt, nicht verschwiegen.',
  'Das Fleisch ist schwach. Der Stahl bleibt.', 'Öl für die Gelenke, Lobpreis für die Maschine.',
  'Die Seele des Maschinengottes umgibt dich.'];
const pick = (list) => list[Math.floor(Math.random() * list.length)];
const IDLE = 'KANAL OFFEN', SENDING = 'SENDE …', UPLINK = 'ÜBERTRAGUNG', INCOMING = 'EINGEHENDE ÜBERTRAGUNG';
const ERRORS = { no_speech: 'Keine Sprache erkannt.', too_short: 'Aufnahme zu kurz.',
                 too_large: 'Aufnahme zu lang.', llm: 'Sprachkern nicht erreichbar.',
                 stt: 'Spracherkennung gestört.', tts: 'Sprachsynthese gestört.',
                 dsp: 'Klangformung gestört.' };
const SETTINGS = ['persona', 'lore', 'voice', 'llm_mode'];
const DEFAULTS = { persona: 'servitor', lore: 'light', voice: 'servitor', llm_mode: 'auto' };

const $ = (id) => document.getElementById(id);
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) {
    try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, value); }
    catch { /* private window: settings last for this visit only */ }
  },
};

const state = {
  token: store.get('servitor.token') || '',   // optional: the server token instead of a password
  session: false,    // a password login's cookie is valid (GET /v1/session)
  toggle: store.get('servitor.toggle') === '1',
  effects: store.get('servitor.effects') !== 'off',
  volume: Math.min(10, Math.max(0, Number(store.get('servitor.volume') ?? 8) || 0)),
  recording: null,   // the running capture, see startRecording()
  request: null,     // AbortController of the running request
  story: null,       // state of a running long story (src/story.py)
};
for (const name of SETTINGS) state[name] = store.get(`servitor.${name}`) || DEFAULTS[name];

const FORMAT = new Audio().canPlayType('audio/ogg; codecs=opus') ? 'opus' : 'wav';

// --- display -------------------------------------------------------------------

function setStage(text, mode) {
  for (const id of ['stage', 'readout']) {
    $(id).textContent = text;
    $(id).dataset.state = mode;
  }
  $('chat').dataset.state = mode;
  // Red lamp: lit while transmitting, blinking while the server works.
  $('lamp-tx').dataset.on = mode === 'listen' ? 'yes' : mode === 'work' ? 'blink' : 'no';
  document.body.dataset.state = mode;   // the cogitator cog turns while the server works
  Vox.stateChanged(mode);
}

function idle() {
  if (state.recording || state.request) return;
  setStage(player.playing ? INCOMING : IDLE, player.playing ? 'speak' : 'idle');
  $('talk').disabled = false;
  $('cancel').hidden = !player.playing;
}

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function addLog(kind, text) {
  const item = document.createElement('li');
  item.className = kind;
  const body = document.createElement('span');
  body.textContent = text;
  item.append(body);
  const log = $('log');
  log.append(item);
  while (log.children.length > 100) log.firstElementChild.remove();
  log.scrollTop = log.scrollHeight;
  return item;
}

// --- teleprinter: Proximus writes while he speaks -----------------------------------
// A reply waits on the screen with a blinking cursor. Each audio part types its
// text over the time the voice needs for it, starting when the sound starts.

const teletype = new Set();   // running jobs, finished at once on cancel

function addReply(text) {
  const item = addLog('servitor', '');
  const cursor = document.createElement('i');
  cursor.className = 'cursor';
  item.append(cursor);
  return { item, body: item.firstChild, cursor, text, queued: 0, typed: 0, closed: false };
}

function typeAlong(reply, text, ms) {
  const prefix = reply.body.textContent ? `${reply.body.textContent} ` : '';
  const log = $('log');
  const job = { finish: null };
  const finish = () => {
    teletype.delete(job);
    reply.body.textContent = prefix + text;
    reply.typed += 1;
    if (reply.closed && reply.typed >= reply.queued) reply.cursor.remove();
    log.scrollTop = log.scrollHeight;
  };
  job.finish = finish;
  if (!state.effects || reducedMotion.matches || ms <= 0) { finish(); return; }
  teletype.add(job);
  const started = performance.now();
  const frame = (now) => {
    if (!teletype.has(job)) return;   // finished early (cancel)
    const share = Math.min(1, (now - started) / ms);
    reply.body.textContent = prefix + text.slice(0, Math.ceil(text.length * share));
    log.scrollTop = log.scrollHeight;
    if (share < 1) requestAnimationFrame(frame); else finish();
  };
  requestAnimationFrame(frame);
}

function closeReply(reply) {
  if (!reply || reply.closed) return;
  reply.closed = true;
  // No voice came (error, cancelled synthesis): the text appears on its own.
  if (reply.queued === 0) { reply.queued = 1; typeAlong(reply, reply.text, Math.min(1500, reply.text.length * 18)); }
  else if (reply.typed >= reply.queued) reply.cursor.remove();
}

function finishTyping() {
  for (const job of [...teletype]) job.finish();
}

// --- playback: audio parts in order, one <audio> element -----------------------

const player = {
  element: new Audio(),
  queue: [],
  current: null,     // object URL of the part being played
  playing: false,
  waiters: [],
  unlocked: false,

  // iOS/Safari only allow play() from a gesture: play silence once on the first.
  unlock() {
    if (this.unlocked) return;
    this.unlocked = true;
    this.element.src = URL.createObjectURL(new Blob([wav(new Int16Array(160))], { type: 'audio/wav' }));
    this.element.play().catch(() => {});
  },

  // One part: its sound, and the text the teleprinter types while it plays.
  add(base64, mime, reply, text, ms) {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    if (reply) reply.queued += 1;
    this.queue.push({ url: URL.createObjectURL(new Blob([bytes], { type: mime })), reply, text, ms });
    if (!this.playing) this.next();
  },

  next() {
    if (this.current) URL.revokeObjectURL(this.current.url);
    const part = this.current = this.queue.shift() || null;
    if (!part) {
      if (this.playing) Vox.sounds.squelch();   // end of transmission
      this.playing = false;
      Vox.live.speaking = false;
      this.waiters.splice(0).forEach((resolve) => resolve());
      idle();
      return;
    }
    const opening = !this.playing;
    this.playing = true;
    Vox.live.speaking = true;
    if (!state.request && !state.recording) setStage(INCOMING, 'speak');
    $('cancel').hidden = false;
    this.element.src = part.url;
    // The carrier opens with a short burst before the voice comes through.
    if (opening) Vox.sounds.open();
    const write = (ms) => { if (part.reply && part.text) typeAlong(part.reply, part.text, ms); };
    setTimeout(() => {
      if (this.current !== part) return;
      this.element.play()
        .then(() => write(part.ms * 0.94))   // the line ends just before the voice
        .catch(() => { write(0); this.next(); });
    }, opening && state.effects ? 110 : 0);
  },

  stop() {
    // Whatever was still to be said appears at once: the transcript stays whole.
    for (const part of this.queue.splice(0)) {
      URL.revokeObjectURL(part.url);
      if (part.reply && part.text) typeAlong(part.reply, part.text, 0);
    }
    finishTyping();
    this.element.pause();
    this.next();
  },

  // Resolves once at most one part is left (a story asks for its next section then).
  low() {
    if (this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => {
      const check = () => (this.queue.length === 0 ? resolve() : setTimeout(check, 250));
      check();
    });
  },
};
player.element.addEventListener('ended', () => player.next());
player.element.addEventListener('error', () => { if (player.playing) player.next(); });

function wav(pcm) {
  const header = new DataView(new ArrayBuffer(44));
  const text = (offset, value) => [...value].forEach((c, i) => header.setUint8(offset + i, c.charCodeAt(0)));
  text(0, 'RIFF'); header.setUint32(4, 36 + pcm.byteLength, true); text(8, 'WAVE');
  text(12, 'fmt '); header.setUint32(16, 16, true); header.setUint16(20, 1, true);
  header.setUint16(22, 1, true); header.setUint32(24, RATE, true);
  header.setUint32(28, RATE * 2, true); header.setUint16(32, 2, true);
  header.setUint16(34, 16, true); text(36, 'data'); header.setUint32(40, pcm.byteLength, true);
  return new Blob([header, pcm]);
}

// --- server exchange -----------------------------------------------------------

function statusHeader() {
  // The fields sanitize_snapshot (src/system_status.py) knows. No 'memory',
  // 'devctl' or 'trip': the browser is a guest without memory core, calendar,
  // device commands or journeys. 'story': it plays multi-part answers.
  const snapshot = { server: 'ok', story: 'on' };
  for (const name of SETTINGS) snapshot[name] = state[name];
  return JSON.stringify(snapshot);
}

async function exchange(path, body, type) {
  const controller = new AbortController();
  state.request = controller;
  $('talk').disabled = true;
  $('cancel').hidden = false;
  setStage(UPLINK, 'work');
  let reply = null;
  try {
    const response = await fetch(`${path}?format=${FORMAT}`, {
      method: 'POST', body, signal: controller.signal, cache: 'no-store',
      headers: { ...authHeaders(), 'Content-Type': type,
                 'X-Servitor-Status': statusHeader() },
    });
    if (!response.ok) {
      const messages = { 401: 'Sitzung abgelaufen. Bitte neu anmelden.', 429: 'Zu viele Anfragen. Bitte kurz warten.',
                         503: 'Sprachkern beschäftigt oder noch nicht bereit.', 413: 'Aufnahme oder Nachricht zu lang.' };
      addLog('error', messages[response.status] || `Serverfehler ${response.status}.`);
      if (response.status === 401) { state.session = false; hangup(); }
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, newline).trim();
        pending = pending.slice(newline + 1);
        if (line) reply = handle(JSON.parse(line), reply);
      }
    }
  } catch (error) {
    if (error.name !== 'AbortError') addLog('error', 'VOX-Verbindung unterbrochen. Anfrage erneut senden.');
    state.story = null;
  } finally {
    closeReply(reply);
    if (state.request === controller) state.request = null;
    idle();
  }
  continueStory(controller);
}

function handle(event, reply) {
  switch (event.event) {
    case 'stage':
      setStage(STAGES[event.stage] || event.stage.toUpperCase(), 'work');
      break;
    case 'transcript':
      addLog('you', event.text);
      break;
    case 'reply':
      closeReply(reply);           // a story's previous section
      return addReply(event.text);  // which language model answered stays off screen
    case 'audio':
      // A part names its own text; a single answer speaks the whole reply.
      player.add(event.data, event.mime, reply, event.text || (reply && reply.text),
                 event.duration_ms || 0);
      break;
    case 'story':
      state.story = event.state && !event.state.done ? event.state : null;
      break;
    case 'stop':
      player.stop();
      break;
    case 'done':
      closeReply(reply);
      break;
    case 'error':
      addLog('error', ERRORS[event.code] || event.message || 'Fehler.');
      state.story = null;
      break;
    default:
      break;   // memory, session, mood, show, enroll ...: Pi matters
  }
  return reply;
}

async function continueStory(previous) {
  if (!state.story) return;
  const story = state.story;
  await player.low();
  // Cancelled, or a new request started meanwhile: the story ends here.
  if (state.story !== story || state.request || state.recording || previous.signal.aborted) return;
  state.story = null;
  exchange('/v1/story', JSON.stringify({ story }), 'application/json');
}

function cancel() {
  state.story = null;
  if (state.request) state.request.abort();
  if (state.recording) state.recording.discard = true;
  stopRecording();
  player.stop();
  idle();
}

// --- microphone ------------------------------------------------------------------

async function audioContext(stream) {
  // 16 kHz straight from the browser when it can; Firefox refuses a stream at
  // another rate, then the worklet decimates from the device rate itself.
  try {
    const context = new AudioContext({ sampleRate: RATE });
    return [context, context.createMediaStreamSource(stream)];
  } catch {
    const context = new AudioContext();
    return [context, context.createMediaStreamSource(stream)];
  }
}

async function startRecording() {
  if (state.recording || state.request || !Vox.connected) return;
  if (!state.token && !state.session) return;
  if (!navigator.mediaDevices || !window.AudioWorkletNode) {
    addLog('error', 'Mikrofon nur über HTTPS oder localhost verfügbar. Frage tippen geht immer.');
    return;
  }
  player.unlock();
  Vox.ensure();
  player.stop();
  state.story = null;
  const recording = { chunks: [], samples: 0, stop: false, discard: false };
  state.recording = recording;
  $('talk').classList.add('active');
  $('cancel').hidden = false;
  Vox.sounds.click();
  Vox.hiss(false);   // the operator's own channel is quiet while transmitting
  setStage(SENDING, 'listen');
  try {
    recording.stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    [recording.context, recording.source] = await audioContext(recording.stream);
    await recording.context.audioWorklet.addModule('/web/capture-worklet.js');
    recording.node = new AudioWorkletNode(recording.context, 'servitor-capture', {
      numberOfOutputs: 0, processorOptions: { targetRate: RATE } });
    recording.ended = new Promise((resolve) => {
      recording.node.port.onmessage = ({ data }) => {
        if (data === 'end') { resolve(); return; }
        recording.chunks.push(data.pcm);
        recording.samples += data.pcm.length;
        Vox.live.peak = data.peak;
        if (recording.samples >= MAX_SECONDS * RATE) stopRecording();
      };
    });
    recording.source.connect(recording.node);
    const analyser = recording.context.createAnalyser();
    analyser.fftSize = 1024;
    recording.source.connect(analyser);
    Vox.live.mic = analyser;
  } catch (error) {
    release(recording);
    state.recording = null;
    addLog('error', error.name === 'NotAllowedError' ? 'Mikrofon nicht freigegeben.'
                                                     : `Mikrofon: ${error.message}`);
    idle();
    return;
  }
  if (recording.stop) stopRecording();   // released while the browser was still asking
}

function release(recording) {
  if (recording.stream) recording.stream.getTracks().forEach((track) => track.stop());
  if (recording.context) recording.context.close().catch(() => {});
  Vox.live.mic = null;
  Vox.live.peak = 0;
  if (Vox.connected) Vox.hiss(true);
}

async function stopRecording() {
  const recording = state.recording;
  if (!recording) return;
  recording.stop = true;
  $('talk').classList.remove('active');
  if (!recording.node || recording.finishing) return;  // startRecording() comes back here
  recording.finishing = true;
  Vox.sounds.click();
  recording.node.port.postMessage('stop');
  await Promise.race([recording.ended, new Promise((resolve) => setTimeout(resolve, 500))]);
  release(recording);
  state.recording = null;
  if (recording.discard) { idle(); return; }
  if (recording.samples < MIN_SECONDS * RATE) {
    addLog('error', 'Aufnahme zu kurz: Taste halten, sprechen, loslassen.');
    idle();
    return;
  }
  const pcm = new Int16Array(recording.samples);
  let offset = 0;
  for (const chunk of recording.chunks) { pcm.set(chunk, offset); offset += chunk.length; }
  Vox.sounds.cant(0.05);   // binharic sign-off: over
  exchange('/v1/turn', pcm.buffer, 'application/octet-stream');
}

// --- controls --------------------------------------------------------------------

const talk = $('talk');
talk.addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return;
  event.preventDefault();
  if (state.toggle) {
    state.recording ? stopRecording() : startRecording();
    return;
  }
  talk.setPointerCapture(event.pointerId);
  startRecording();
});
for (const name of ['pointerup', 'pointercancel']) {
  talk.addEventListener(name, () => { if (!state.toggle) stopRecording(); });
}
talk.addEventListener('contextmenu', (event) => event.preventDefault());
talk.addEventListener('keydown', (event) => {   // Enter on the focused button: toggle
  if (event.key === 'Enter' && !event.repeat) {
    event.preventDefault();
    state.recording ? stopRecording() : startRecording();
  }
});

function typing(event) {
  return event.target.closest('input, select, textarea, dialog');
}
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && (state.recording || state.request || player.playing)) cancel();
  if (event.code !== 'Space' || event.repeat || typing(event)) return;
  event.preventDefault();
  if (state.toggle && state.recording) stopRecording(); else startRecording();
});
document.addEventListener('keyup', (event) => {
  if (event.code === 'Space' && !state.toggle && !typing(event)) stopRecording();
});

$('cancel').addEventListener('click', cancel);

function fitQuestion() {
  const field = $('question');
  field.style.height = 'auto';
  field.style.height = `${Math.max(62, Math.min(112, field.scrollHeight))}px`;
}

$('question').addEventListener('input', fitQuestion);
$('question').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  $('ask').requestSubmit();
});

$('ask').addEventListener('submit', (event) => {
  event.preventDefault();
  const text = $('question').value.trim();
  if (!text || state.request || state.recording || !Vox.connected) return;
  if (!state.token && !state.session) return;
  player.unlock();
  Vox.ensure();
  player.stop();
  state.story = null;
  $('question').value = '';
  fitQuestion();
  Vox.sounds.cant();
  exchange('/v1/ask', JSON.stringify({ text }), 'application/json');
});

// --- settings --------------------------------------------------------------------

function openSettings() {
  $('token').value = state.token;
  $('remember').checked = store.get('servitor.token') !== null;
  $('toggle').checked = state.toggle;
  $('effects').checked = state.effects;
  for (const name of SETTINGS) $(name).value = state[name];
  if (!$('settings').open) $('settings').showModal();
}

$('open-settings').addEventListener('click', openSettings);
$('logout').addEventListener('click', async () => {
  // Ends the session and forgets a stored token: the next call asks again.
  await fetch('/v1/logout', { method: 'POST', headers: authHeaders() }).catch(() => {});
  state.session = false;
  state.token = '';
  store.set('servitor.token', null);
  $('settings').close();
  hangup();
});
$('settings').addEventListener('close', () => {
  if ($('settings').returnValue !== 'save') return;
  state.token = $('token').value.trim();
  store.set('servitor.token', $('remember').checked && state.token ? state.token : null);
  setOption('toggle', $('toggle').checked);
  setOption('effects', $('effects').checked);
  for (const name of SETTINGS) setOption(name, $(name).value);
});

// --- hardware: rotary switches, toggles, volume (desktop panel, dial unit) --------
// Same values as the settings dialog; every change goes through setOption().

const POSITIONS = {
  persona: [['servitor', 'SERVITOR'], ['mensch', 'BILLY']],
  lore: [['off', 'AUS'], ['light', 'LEICHT'], ['full', 'VOLL']],
  voice: [['servitor', 'SERVITOR'], ['natural', 'NATÜRLICH']],
  llm_mode: [['auto', 'AUTO'], ['free', 'FREI'], ['local', 'LOKAL']],
};

function setOption(name, value) {
  if (SETTINGS.includes(name)) {
    state[name] = value;
    store.set(`servitor.${name}`, value);
  } else if (name === 'effects') {
    state.effects = value;
    store.set('servitor.effects', value ? null : 'off');
    Vox.setEffects(value);
  } else if (name === 'toggle') {
    state.toggle = value;
    store.set('servitor.toggle', value ? '1' : null);
    talkLabel();
  } else if (name === 'volume') {
    state.volume = Math.min(10, Math.max(0, value));
    store.set('servitor.volume', String(state.volume));
    Vox.setVolume(state.volume / 10);
  }
  renderHardware();
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function buildHardware() {
  for (const box of document.querySelectorAll('.hw-rotary')) {
    const name = box.dataset.name;
    const strip = element('div', 'hw-strip');
    for (const [value, label] of POSITIONS[name]) {
      const button = element('button', 'hw-position', label);
      button.type = 'button';
      button.dataset.value = value;
      button.addEventListener('click', () => { Vox.sounds.click(); setOption(name, value); });
      strip.append(button);
    }
    const knob = element('button', 'hw-knob');
    knob.type = 'button';
    knob.append(element('span', 'hw-knob-cap'));
    const step = (delta) => {
      const values = POSITIONS[name].map(([value]) => value);
      const next = values[(values.indexOf(state[name]) + delta + values.length) % values.length];
      Vox.sounds.click();
      setOption(name, next);
    };
    knob.addEventListener('click', () => step(1));
    knob.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowUp') { event.preventDefault(); step(1); }
      if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') { event.preventDefault(); step(-1); }
    });
    box.append(strip, knob, element('span', 'hw-label', box.dataset.label));
  }
  for (const box of document.querySelectorAll('.hw-toggle')) {
    const name = box.dataset.name;
    const button = element('button', 'hw-switch');
    button.type = 'button';
    button.setAttribute('role', 'switch');
    button.append(element('span', 'hw-lever'));
    button.addEventListener('click', () => { Vox.sounds.click(); setOption(name, !state[name]); });
    box.append(element('span', 'hw-mark', box.dataset.on), button,
               element('span', 'hw-mark', box.dataset.off), element('span', 'hw-label', box.dataset.label));
  }
  for (const box of document.querySelectorAll('.hw-volume')) {
    const down = element('button', 'hw-arrow', '▼');
    const up = element('button', 'hw-arrow', '▲');
    down.type = up.type = 'button';
    down.setAttribute('aria-label', 'leiser');
    up.setAttribute('aria-label', 'lauter');
    const bar = element('span', 'hw-bar');
    for (let i = 0; i < 10; i++) bar.append(element('i'));
    down.addEventListener('click', () => { setOption('volume', state.volume - 1); Vox.sounds.cant(); });
    up.addEventListener('click', () => { setOption('volume', state.volume + 1); Vox.sounds.cant(); });
    box.append(down, bar, up, element('span', 'hw-label', box.dataset.label));
  }
}

function renderHardware() {
  for (const box of document.querySelectorAll('.hw-rotary')) {
    const name = box.dataset.name;
    const values = POSITIONS[name];
    const index = Math.max(0, values.findIndex(([value]) => value === state[name]));
    // Positions spread over the upper arc, like the pointer of a selector switch.
    const angle = values.length === 1 ? 0 : -50 + index * (100 / (values.length - 1));
    box.querySelector('.hw-knob-cap').style.transform = `rotate(${angle}deg)`;
    for (const button of box.querySelectorAll('.hw-position')) {
      button.setAttribute('aria-pressed', String(button.dataset.value === state[name]));
    }
    box.querySelector('.hw-knob').setAttribute('aria-label', `${box.dataset.label}: ${values[index][1]}, weiterschalten`);
  }
  for (const box of document.querySelectorAll('.hw-toggle')) {
    const on = Boolean(state[box.dataset.name]);
    const button = box.querySelector('.hw-switch');
    button.setAttribute('aria-checked', String(on));
    button.setAttribute('aria-label', `${box.dataset.label}: ${on ? box.dataset.on : box.dataset.off}`);
  }
  for (const box of document.querySelectorAll('.hw-volume')) {
    [...box.querySelectorAll('.hw-bar i')].forEach((led, i) => { led.dataset.on = String(i < state.volume); });
  }
}

function talkLabel() {
  talk.querySelector('.talk-main').textContent = state.toggle ? 'SENDETASTE · ANTIPPEN'
                                                              : 'SENDETASTE · HALTEN';
}

// --- the journal behind the chat: live server log, litanies, interference -----------
// The service's own log lines (GET /v1/journal) run dimly behind the transcript.
// With effects on, litanies and noospheric interference are mixed in.

const journal = { seq: 0, instance: '', timers: [], murmurTimer: null, host: '', controller: null, pending: [], flowTimer: null };

function journalLine(text, kind) {
  const list = $('journal');
  const item = document.createElement('li');
  item.textContent = text;
  if (kind) item.className = kind;
  const ambient = ['litany', 'noise', 'crosstalk'].includes(kind);
  item.dataset.kind = ambient ? 'ambient' : 'log';
  list.prepend(item);
  // Grow the new row in the layout, so existing rows move continuously too.
  // This is independent of the optional sound/interference effects.
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches && !document.hidden) {
    const height = item.getBoundingClientRect().height;
    item.animate([
      { height: '0px', marginTop: '-3px', opacity: 0 },
      { height: `${height}px`, marginTop: '0px', opacity: 1 },
    ], { duration: 240, easing: 'linear' });
  }
  // Effects keep their cadence, but cannot evict the system log or fill the
  // entire visible tube when the machines are quiet.
  const entries = [...list.querySelectorAll(`[data-kind="${item.dataset.kind}"]`)];
  entries.slice(ambient ? 3 : 60).forEach((entry) => {
    entry.getAnimations().forEach((animation) => animation.cancel());
    entry.remove();
  });
}

// Polls arrive in batches. Feed them into the display one row at a time,
// keeping the waiting queue bounded during busy periods.
function flowJournal() {
  journal.flowTimer = null;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  do {
    const next = journal.pending.shift();
    if (!next) return;
    journalLine(next.text, next.kind);
  } while (reduced);
  if (journal.pending.length) journal.flowTimer = setTimeout(flowJournal, 160);
}

function journalStatus(message) {
  const list = $('journal');
  let item = list.querySelector('[data-kind="status"]');
  if (!message) { item?.remove(); return; }
  if (!item) {
    item = document.createElement('li');
    item.dataset.kind = 'status';
    item.className = 'warn';
    list.prepend(item);
  }
  item.textContent = message;
}

// '21:48:16 kernel  Out of memory: …' – time, unit, message; the service's
// JSON events read as 'READY load_seconds=9.84'.
function describe(entry) {
  const time = new Date(entry.ts * 1000).toLocaleTimeString('de-DE');
  // Forwarded lines (Proxmox host, Pi) name their machine; CT 107's own do not.
  const host = entry.host && entry.host !== journal.host ? `${entry.host} ▸ ` : '';
  const unit = entry.unit ? `${entry.unit} ` : '';
  let text = entry.text;
  try {
    const data = JSON.parse(text);
    if (data && data.event === 'processing') {
      const steps = { stt_finalize: 'Sprache erkennen', speaker: 'Stimme prüfen',
        weather: 'Wetterdaten abrufen (Dienst/Cache)', intent: 'Feste Antwort erzeugen',
        rephrase: 'Formulierung prüfen', llm: 'Sprachmodell anfragen',
        synthesis: 'Stimme erzeugen · Piper', render: 'Funkfilter anwenden',
        encode: 'Audio kodieren', journey_pi: 'Fahrtauskunft an Pi weitergeben', recognize: 'Sprache erkennen', think: 'Antwort vorbereiten',
        synthesize: 'Stimme erzeugen', status: 'Systemstatus', network: 'Netzwerkstatus' };
      const states = { start: 'gestartet', done: 'fertig', failed: 'fehlgeschlagen' };
      const duration = typeof data.ms === 'number' ? ` · ${data.ms} ms` : '';
      let detail = '';
      if (data.step === 'turn') detail = `Anfrage ${states[data.state] || ''}`
        + (data.channel ? ` · ${{ text: 'Texteingabe', voice: 'Sprachkanal', speak: 'Sprachausgabe', story: 'Geschichte' }[data.channel] || data.channel}` : '');
      else if (data.step === 'sources') detail = `Quelle ${data.archive}: ${data.ids?.length
        ? `${data.ids.join(', ')} · in den Kontext übernommen` : 'keine Archivtreffer'}`;
      else if (data.step === 'provider') detail = `LLM-Dienst ${data.name} ${states[data.state] || ''}`
        + (data.model ? ` · ${data.model}` : '')
        + (Number.isInteger(data.total_tokens) ? ` · ${data.total_tokens} Token` : '');
      else if (data.step === 'fallback') detail = 'LLM: Wechsel auf lokalen Sprachkern';
      else if (data.step === 'answer') detail = `Antwortpfad: ${data.model}`;
      else if (data.step === 'route') detail = `Regel erkannt: ${steps[data.name] || data.name}`;
      else detail = `${steps[data.name] || data.name || data.step} ${states[data.state] || ''}`;
      return `${time} VOX ${data.turn} ▸ ${detail}${duration}${data.error ? ` · ${data.error}` : ''}`;
    }
    if (data && data.event === 'request') {   // who talked to the server
      return `${time} ${String(data.client).toUpperCase()} ▸ ${data.method} ${data.path} → ${data.status}`;
    }
    if (data && typeof data === 'object' && data.event) {
      const rest = Object.entries(data).filter(([key]) => key !== 'event')
        .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : value}`);
      text = `${String(data.event).toUpperCase()} ${rest.join(' ')}`;
    }
  } catch { /* a plain line */ }
  return `${time} ${host}${unit}${text}`;
}

function severity(entry) {
  // syslog priority: 0-3 error, 4 warning; the service's own failures too.
  if (entry.prio <= 3) return 'error';
  if (entry.prio === 4 || /"event": ?"[a-z_]*(error|failed|fallback)/i.test(entry.text)) return 'warn';
  return '';
}

async function pollJournal() {
  if (!Vox.connected || document.hidden || journal.controller) return;
  const controller = new AbortController();
  journal.controller = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`/v1/journal?after=${journal.seq}&instance=${journal.instance}`, {
      cache: 'no-store', headers: authHeaders(), signal: controller.signal });
    if (journal.controller !== controller) return;
    if (!response.ok) {
      journalStatus(response.status === 401
        ? 'Journal: Anmeldung abgelaufen – bitte neu anmelden.'
        : `Journal nicht erreichbar (HTTP ${response.status}).`);
      return;
    }
    const data = await response.json();
    if (journal.controller !== controller) return;
    journal.host = data.host || journal.host;
    for (const entry of data.lines) {
      const pi = /"event": ?"request", ?"client": ?"pi"/.test(entry.text);
      journal.pending.push({ text: describe(entry), kind: severity(entry) || (pi ? 'traffic' : '') });
    }
    journal.pending = journal.pending.slice(-120);
    if (journal.flowTimer === null) flowJournal();
    journal.seq = data.seq;
    journal.instance = data.instance || '';
    journalStatus(data.source?.error ? `Journal gestört: ${data.source.error}`
      : data.source?.mode === 'own' ? 'Journal: nur Meldungen des Sprachdienstes verfügbar.' : '');
  } catch {
    if (journal.controller === controller) journalStatus('Journal-Verbindung unterbrochen. Neuer Versuch läuft.');
  } finally {
    clearTimeout(timeout);
    if (journal.controller === controller) journal.controller = null;
  }
}

function interference() {
  return pick([
    '░▒▓ NOOSPHÄRE-STÖRUNG ▓▒░',
    '··· SIGNAL VERZERRT ···',
    '▚▚ TRÄGERDRIFT ▚▚',
    '▒▒ FUNKRAUSCHEN ▒▒',
  ]);
}

Vox.onCrosstalk = () => {
  journalLine('▒▒ FREMDKANAL ▒▒ unverständlich', 'crosstalk');
};

function startJournal() {
  stopJournal();
  pollJournal();
  journal.timers.push(setInterval(pollJournal, 2500));
  const murmur = () => {
    journal.murmurTimer = setTimeout(() => {
      if (state.effects) {
        if (Math.random() < 0.55) journalLine(`+++ ${pick(LITANY.journal)} +++`, 'litany');
        else journalLine(interference(), 'noise');
      }
      murmur();
    }, 4000 + Math.random() * 8000);
  };
  murmur();
}

function stopJournal() {
  journal.timers.splice(0).forEach((timer) => { clearInterval(timer); clearTimeout(timer); });
  clearTimeout(journal.murmurTimer);
  journal.controller?.abort();
  journal.controller = null;
  clearTimeout(journal.flowTimer);
  journal.flowTimer = null;
  journal.pending = [];
  $('journal').getAnimations({ subtree: true }).forEach((animation) => animation.finish());
}

// --- server state: the link lamp, the signal needle --------------------------------

async function health() {
  const pill = $('link');
  const started = performance.now();
  let link = 'down';
  try {
    const response = await fetch('/health', { cache: 'no-store' });
    const data = await response.json();
    link = data.ready ? 'ok' : 'loading';
  } catch { /* no carrier */ }
  const latency = Math.round(performance.now() - started);
  Object.assign(Vox.live, { link, latency });
  pill.dataset.state = link;
  pill.textContent = link === 'ok' ? `Signal ${latency} ms` : link === 'loading' ? 'Sprachkern startet' : 'Kein Träger';
  $('lamp-link').dataset.on = link === 'ok' && Vox.connected ? 'yes' : 'no';
  return link;
}

// --- the call: entering the page dials Proximus -----------------------------------

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function dialLine(text, kind) {
  const item = document.createElement('li');
  item.textContent = text;
  if (kind) item.className = kind;
  const log = $('dial-log');
  log.append(item);
  while (log.children.length > 7) log.firstElementChild.remove();
}

// Password login: a session cookie (POST /v1/login). The server token still
// works in the same field; every request names the web client in a header,
// without it the server ignores the cookie.
function authHeaders() {
  const headers = { 'X-Servitor-Client': 'web' };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return headers;
}

async function checkToken(token) {
  // GET /v1/status needs the token and does no work: a cheap check of the code.
  try {
    const response = await fetch('/v1/status', { cache: 'no-store',
                                                 headers: { Authorization: `Bearer ${token}` } });
    return response.ok ? 'ok' : response.status === 401 ? 'denied' : 'down';
  } catch {
    return 'down';
  }
}

async function checkSession() {
  try {
    const response = await fetch('/v1/session', { cache: 'no-store', headers: authHeaders() });
    return response.ok ? await response.json() : { ok: false };
  } catch {
    return { ok: false };
  }
}

async function login(secret, remember) {
  let status = 0;
  try {
    const response = await fetch('/v1/login', {
      method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: secret, remember }) });
    status = response.status;
  } catch {
    return 'down';
  }
  if (status === 200) { state.session = true; state.token = ''; return 'ok'; }
  if (status === 429) return 'locked';
  // A long secret may be the server token instead of a password.
  if ((status === 401 || status === 503) && secret.length >= 32) {
    const verdict = await checkToken(secret);
    if (verdict === 'ok') {
      state.token = secret;
      store.set('servitor.token', remember ? secret : null);
    }
    return verdict;
  }
  return status === 503 ? 'unset' : status === 401 ? 'denied' : 'down';
}

async function signedIn() {
  if (state.token) return checkToken(state.token);
  state.session = (await checkSession()).ok;
  return state.session ? 'ok' : 'denied';
}

async function dial(event) {
  event.preventDefault();
  const form = $('dial-form');
  const known = form.dataset.known === 'yes';
  const secret = known ? '' : $('dial-token').value;
  if (!known && !secret) {
    dialLine('Kennwort eingeben.', 'bad');
    $('dial-token').focus();
    return;
  }
  // The click is the gesture browsers want before any sound may play. Sound
  // trouble (old iOS, blocked audio) must never keep the operator out.
  try {
    Vox.ensure();
    Vox.attach(player.element);
    player.unlock();
  } catch (error) {
    console.warn('Vox-Ton nicht verfügbar:', error);
  }
  $('dial-connect').disabled = true;
  $('dial-log').replaceChildren();
  const pace = state.effects && !reducedMotion.matches ? 1 : 0.15;
  Vox.sounds.click();
  dialLine('Litanei der Zündung beginnt.');
  await wait(700 * pace);
  dialLine(LITANY.ignition[0]);
  await wait(650 * pace);
  dialLine(LITANY.ignition[1]);
  await wait(500 * pace);
  dialLine('Suche Trägersignal auf Kanal 107 …');
  Vox.sounds.tune(1.8);
  const [link] = await Promise.all([health(), wait(1800 * pace)]);
  if (link === 'down') {
    Vox.sounds.crackle();
    dialLine('+++ KEIN TRÄGERSIGNAL +++ Proximus antwortet nicht. Verbindung prüfen.', 'bad');
    $('dial-connect').disabled = false;
    return;
  }
  Vox.sounds.crackle();
  dialLine(`Server erreichbar · Antwortzeit ${Vox.live.latency} ms`);
  await wait(500 * pace);
  dialLine(LITANY.ignition[2]);
  dialLine('Prüfe Kennung …');
  const verdict = known ? await signedIn() : await login(secret, $('dial-remember').checked);
  if (verdict !== 'ok') {
    Vox.sounds.squelch();
    const why = {
      denied: '+++ ZUGANG VERWEIGERT +++ Kennwort oder Token ungültig.',
      locked: '+++ GESPERRT +++ Zu viele Fehlversuche. 15 Minuten warten.',
      unset: '+++ ANMELDUNG NICHT EINGERICHTET +++ Server-Konfiguration prüfen.',
    };
    dialLine(why[verdict] || '+++ VERBINDUNG ABGERISSEN +++', 'bad');
    form.dataset.known = 'no';
    $('dial-token').value = '';
    $('dial-connect').disabled = false;
    return;
  }
  $('dial-token').value = '';
  Vox.sounds.cant();
  dialLine('Kennung angenommen. Einlass in den Vox-Kanal gewährt.', 'good');
  await wait(600 * pace);
  dialLine(link === 'loading' ? 'Sprachkern startet noch. Verbindung steht.'
                              : LITANY.ignition[3], 'good');
  await wait(700 * pace);
  dialLine('LEBE! · VERBINDUNG HERGESTELLT.', 'good');
  await wait(400 * pace);
  connect();
}

function connect() {
  document.body.dataset.connected = 'yes';
  $('hint-litany').textContent = pick(LITANY.hint);
  Vox.connect();
  startJournal();
  $('dial-connect').disabled = false;
  $('lamp-link').dataset.on = Vox.live.link === 'ok' ? 'yes' : 'no';
  addLog('system', `+++ Vox-Verbindung zu Servitor Proximus · ${new Date().toLocaleTimeString('de-DE')} +++`);
  idle();
}

function hangup() {
  cancel();
  Vox.sounds.squelch();
  Vox.disconnect();
  stopJournal();
  addLog('system', '+++ Die Lebenden schwinden. Aber die Maschine besteht. +++');
  document.body.dataset.connected = 'no';
  $('lamp-link').dataset.on = 'no';
  $('dial-form').dataset.known = state.token || state.session ? 'yes' : 'no';
  $('dial-log').replaceChildren();
  dialLine('Verbindung getrennt. Erneut verbinden?');
}

$('dial-form').addEventListener('submit', dial);
$('hangup').addEventListener('click', hangup);

Vox.setEffects(state.effects);
Vox.setVolume(state.volume / 10);
Vox.start({ scopes: [$('scope'), $('dial-scope')], needles: [...document.querySelectorAll('.gauge-needle')] });
buildHardware();
renderHardware();
$('dial-form').dataset.known = state.token ? 'yes' : 'no';
dialLine(state.token ? 'Kennung hinterlegt. Bereit zum Anruf.' : 'Kennwort eingeben, dann anrufen.');
checkSession().then(({ ok }) => {
  if (!ok || state.token) return;
  state.session = true;
  $('dial-form').dataset.known = 'yes';
  $('dial-log').replaceChildren();
  dialLine('Angemeldet. Bereit zum Anruf.');
});
health();
setInterval(health, 15000);
talkLabel();
