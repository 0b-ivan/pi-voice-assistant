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
  hint: ['Der Maschinengeist hört.', 'Mens sana in machina sano.',
         'Zu jedem Problem liegt eine Lösung in der Anwendung der Tech-Lore.',
         'Wissen ist heilig. Frage, und es wird dir gegeben.', 'Mit dem Segen des Omnissiah.',
         'Gesegnet sei die Röhre, gesegnet der Draht, gesegnet der Strom dazwischen.'],
};
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
  token: store.get('servitor.token') || '',
  toggle: store.get('servitor.toggle') === '1',
  effects: store.get('servitor.effects') !== 'off',
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

function addLog(kind, text, note) {
  const item = document.createElement('li');
  item.className = kind;
  const body = document.createElement('span');
  item.append(body);
  if (note) {
    const small = document.createElement('small');
    small.textContent = note;
    item.append(small);
  }
  const log = $('log');
  log.append(item);
  while (log.children.length > 100) log.firstElementChild.remove();
  if (kind === 'servitor' && state.effects && !reducedMotion.matches) typeOut(body, text, log);
  else body.textContent = text;
  log.scrollTop = log.scrollHeight;
  return item;
}

// Proximus' lines arrive like a teleprinter: at most about 1.6 s per line.
function typeOut(target, text, log) {
  const cursor = document.createElement('i');
  cursor.className = 'cursor';
  const step = Math.max(1, Math.ceil(text.length / 80));
  let shown = 0;
  target.after(cursor);
  const tick = () => {
    shown = Math.min(text.length, shown + step);
    target.textContent = text.slice(0, shown);
    log.scrollTop = log.scrollHeight;
    if (shown < text.length) setTimeout(tick, 18);
    else cursor.remove();
  };
  tick();
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

  add(base64, mime) {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    this.queue.push(URL.createObjectURL(new Blob([bytes], { type: mime })));
    if (!this.playing) this.next();
  },

  next() {
    if (this.current) URL.revokeObjectURL(this.current);
    const url = this.current = this.queue.shift() || null;
    if (!url) {
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
    this.element.src = url;
    // The carrier opens with a short burst before the voice comes through.
    if (opening) Vox.sounds.open();
    setTimeout(() => {
      if (this.current === url) this.element.play().catch(() => this.next());
    }, opening && state.effects ? 110 : 0);
  },

  stop() {
    this.queue.splice(0).forEach((url) => URL.revokeObjectURL(url));
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
      headers: { Authorization: `Bearer ${state.token}`, 'Content-Type': type,
                 'X-Servitor-Status': statusHeader() },
    });
    if (!response.ok) {
      const messages = { 401: 'Vox-Code abgelehnt.', 429: 'Zu viele Anfragen, kurz warten.',
                         503: 'Server beschäftigt oder lädt noch.', 413: 'Zu lang.' };
      addLog('error', messages[response.status] || `Serverfehler ${response.status}.`);
      if (response.status === 401) openSettings();
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
    if (error.name !== 'AbortError') addLog('error', `Verbindung abgerissen: ${error.message}`);
    state.story = null;
  } finally {
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
      return addLog('servitor', event.text, event.model);
    case 'audio':
      player.add(event.data, event.mime);
      break;
    case 'story':
      state.story = event.state && !event.state.done ? event.state : null;
      break;
    case 'stop':
      player.stop();
      break;
    case 'done': {
      const total = event.timings && event.timings.server_total;
      const note = reply && reply.querySelector('small');
      if (note && total != null) note.textContent += ` · ${total} s`;
      break;
    }
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
  if (!state.token) { openSettings(); return; }
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

$('ask').addEventListener('submit', (event) => {
  event.preventDefault();
  const text = $('question').value.trim();
  if (!text || state.request || state.recording || !Vox.connected) return;
  if (!state.token) { openSettings(); return; }
  player.unlock();
  Vox.ensure();
  player.stop();
  state.story = null;
  $('question').value = '';
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
$('settings').addEventListener('close', () => {
  if ($('settings').returnValue !== 'save') return;
  state.token = $('token').value.trim();
  store.set('servitor.token', $('remember').checked && state.token ? state.token : null);
  state.toggle = $('toggle').checked;
  store.set('servitor.toggle', state.toggle ? '1' : null);
  state.effects = $('effects').checked;
  store.set('servitor.effects', state.effects ? null : 'off');
  Vox.setEffects(state.effects);
  for (const name of SETTINGS) {
    state[name] = $(name).value;
    store.set(`servitor.${name}`, state[name]);
  }
  talkLabel();
});

function talkLabel() {
  talk.querySelector('.talk-main').textContent = state.toggle ? 'SENDETASTE · ANTIPPEN'
                                                              : 'SENDETASTE · HALTEN';
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
  pill.textContent = link === 'ok' ? `Signal ${latency} ms` : link === 'loading' ? 'Servitor erwacht' : 'Kein Träger';
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

async function authorize(token) {
  // GET /v1/status needs the token and does no work: a cheap check of the code.
  try {
    const response = await fetch('/v1/status', { cache: 'no-store',
                                                 headers: { Authorization: `Bearer ${token}` } });
    return response.ok ? 'ok' : response.status === 401 ? 'denied' : 'down';
  } catch {
    return 'down';
  }
}

async function dial(event) {
  event.preventDefault();
  const form = $('dial-form');
  const token = form.dataset.known === 'yes' ? state.token : $('dial-token').value.trim();
  if (token.length < 32) {
    form.dataset.known = 'no';
    dialLine('Vox-Code fehlt oder ist zu kurz.', 'bad');
    $('dial-token').focus();
    return;
  }
  // The click is the gesture browsers want before any sound may play.
  Vox.ensure();
  Vox.attach(player.element);
  player.unlock();
  $('dial-connect').disabled = true;
  $('dial-log').replaceChildren();
  const pace = state.effects && !reducedMotion.matches ? 1 : 0.15;
  Vox.sounds.click();
  dialLine('Läute die Große Glocke einmal. Weihrauch entzündet.');
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
    dialLine('+++ KEIN TRÄGERSIGNAL +++ Servitor nicht erreichbar. Klärung erforderlich.', 'bad');
    $('dial-connect').disabled = false;
    return;
  }
  Vox.sounds.crackle();
  dialLine(`Signal gefunden · Laufzeit ${Vox.live.latency} ms · Störungen hoch`);
  await wait(500 * pace);
  dialLine(LITANY.ignition[2]);
  dialLine('Prüfe Vox-Code …');
  const verdict = await authorize(token);
  if (verdict !== 'ok') {
    Vox.sounds.squelch();
    dialLine(verdict === 'denied' ? '+++ ZUGANG VERWEIGERT +++ Im Namen des Omnissiah, identifiziere dich.'
                                  : '+++ VERBINDUNG ABGERISSEN +++', 'bad');
    form.dataset.known = 'no';
    $('dial-connect').disabled = false;
    return;
  }
  if (form.dataset.known !== 'yes') {
    state.token = token;
    store.set('servitor.token', $('dial-remember').checked ? token : null);
  }
  Vox.sounds.cant();
  dialLine('Dignus est intrare. Vox-Code angenommen.', 'good');
  await wait(600 * pace);
  dialLine(link === 'loading' ? 'Der Maschinengeist erwacht noch. Geduld, Bediener.'
                              : LITANY.ignition[3], 'good');
  await wait(700 * pace);
  dialLine('LEBE! · VERBINDUNG STEHT.', 'good');
  await wait(400 * pace);
  connect();
}

function connect() {
  document.body.dataset.connected = 'yes';
  $('hint-litany').textContent = pick(LITANY.hint);
  $('dial-connect').disabled = false;
  Vox.connect();
  $('lamp-link').dataset.on = Vox.live.link === 'ok' ? 'yes' : 'no';
  addLog('system', `+++ Vox-Verbindung zu Servitor Proximus · ${new Date().toLocaleTimeString('de-DE')} +++`);
  idle();
}

function hangup() {
  cancel();
  Vox.sounds.squelch();
  Vox.disconnect();
  addLog('system', '+++ Die Lebenden schwinden. Aber die Maschine besteht. +++');
  document.body.dataset.connected = 'no';
  $('lamp-link').dataset.on = 'no';
  $('dial-form').dataset.known = state.token ? 'yes' : 'no';
  $('dial-log').replaceChildren();
  dialLine('Kanal 107 getrennt. Die Maschine besteht. Erneut anrufen?');
}

$('dial-form').addEventListener('submit', dial);
$('hangup').addEventListener('click', hangup);

Vox.setEffects(state.effects);
Vox.start({ scopes: [$('scope'), $('dial-scope')], needleElement: $('needle') });
$('dial-form').dataset.known = state.token ? 'yes' : 'no';
dialLine(state.token ? 'Vox-Code hinterlegt. Bereit zum Anruf.' : 'Vox-Code eingeben, dann anrufen.');
health();
setInterval(health, 15000);
talkLabel();
