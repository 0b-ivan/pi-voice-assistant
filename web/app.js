"use strict";
const form = document.querySelector("#chat-form");
const prompt = document.querySelector("#prompt");
const messages = document.querySelector("#messages");
const status = document.querySelector("#status");
const send = document.querySelector("#send");
const mic = document.querySelector("#microphone");
const recordingHint = document.querySelector("#recording-hint");
const AUDIO_RATE = 16000;
const MAX_RECORD_SECONDS = 28;
let recording = null;
let starting = false;
let busy = false;

function showStatus(label, kind = "") {
  status.textContent = label;
  status.className = "status " + kind;
}

function addMessage(role, text) {
  const entry = document.createElement("div");
  entry.className = "entry " + role;
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = role === "user" ? "BEDIENER" : "PROXIMUS";
  const body = document.createElement("p");
  body.textContent = text;
  entry.append(label, body);
  messages.append(entry);
  messages.scrollTop = messages.scrollHeight;
  return entry;
}

function attachAudio(entry, event) {
  if (event.format !== "wav" || !event.data) return;
  const binary = atob(event.data);
  const buffer = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) buffer[i] = binary.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([buffer], {type: "audio/wav"}));
  const play = document.createElement("button");
  play.type = "button";
  play.className = "play";
  play.textContent = "▶ SERVITOR-STIMME";
  const player = new Audio(url);
  player.addEventListener("ended", () => play.textContent = "▶ ERNEUT ABSPIELEN");
  play.addEventListener("click", async () => {
    player.currentTime = 0;
    try { await player.play(); } catch { play.textContent = "Audio nicht verfügbar"; }
  });
  entry.append(play);
  window.addEventListener("pagehide", () => URL.revokeObjectURL(url), {once: true});
}

function setBusy(active) {
  busy = active;
  send.disabled = active || Boolean(recording) || starting;
  mic.disabled = active || starting;
  prompt.disabled = active || Boolean(recording) || starting;
}

async function checkHealth() {
  if (busy || recording || starting) return;
  try {
    const response = await fetch("/api/health", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok || !data.ready) throw new Error("Server lädt");
    showStatus("● MASCHINENGEIST ONLINE", "ready");
  } catch {
    showStatus("● VERBINDUNG NICHT BEREIT", "bad");
  }
}
checkHealth();
setInterval(checkHealth, 30000);

function renderEvents(events, voice) {
  if (voice) {
    const transcript = events.find(e => e.event === "transcript");
    if (transcript && transcript.text) addMessage("user", transcript.text);
  }
  const failure = events.find(e => e.event === "error");
  if (failure) throw new Error(failure.message || failure.code || "Verarbeitung fehlgeschlagen");
  if (events.some(e => e.event === "stop")) {
    addMessage("assistant", "Abbruch bestätigt.");
    return;
  }
  const reply = events.find(e => e.event === "reply");
  if (!reply) throw new Error("Keine Antwort erhalten");
  const entry = addMessage("assistant", reply.text);
  const audio = events.find(e => e.event === "audio");
  if (audio) attachAudio(entry, audio);
}

async function requestTurn(url, body, contentType, voice = false) {
  setBusy(true);
  showStatus("◌ ANFRAGE WIRD VERARBEITET");
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": contentType},
      body,
    });
    if (!response.ok) {
      throw new Error("Server antwortet mit HTTP " + response.status);
    }
    const events = (await response.text()).trim().split("\n").filter(Boolean).map(JSON.parse);
    renderEvents(events, voice);
    showStatus("● MASCHINENGEIST ONLINE", "ready");
  } catch (error) {
    addMessage("assistant", "FEHLER: " + error.message);
    showStatus("● ANFRAGE FEHLGESCHLAGEN", "bad");
  } finally {
    setBusy(false);
    if (!voice) prompt.focus();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = prompt.value.trim();
  if (!text || busy || starting || recording) return;
  prompt.value = "";
  addMessage("user", text);
  await requestTurn("/api/chat", JSON.stringify({text}), "application/json");
});

prompt.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function resetMicrophoneControls() {
  mic.classList.remove("recording");
  mic.setAttribute("aria-pressed", "false");
  mic.textContent = "◉ SPRACHE AUFNEHMEN";
  recordingHint.textContent = "Mikrofon starten, sprechen, erneut drücken zum Senden (max. 28 s).";
}

async function startRecording() {
  if (busy || starting || recording) return;
  starting = true;
  setBusy(false);
  mic.disabled = true;
  recordingHint.textContent = "Mikrofon-Berechtigung wird angefordert …";
  let stream = null;
  let context = null;
  try {
    if (!window.isSecureContext) throw new Error("Mikrofon erfordert HTTPS oder localhost");
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
      throw new Error("AudioWorklet-Mikrofon wird von diesem Browser nicht unterstützt");
    }
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true},
    });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("Web Audio wird nicht unterstützt");
    context = new AudioContextClass({latencyHint: "interactive"});
    await context.audioWorklet.addModule("/recorder-worklet.js");
    const source = context.createMediaStreamSource(stream);
    const processor = new AudioWorkletNode(context, "proximus-capture", {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
    });
    const muted = context.createGain();
    muted.gain.value = 0; // never feed live microphone sound to the speakers
    const chunks = [];
    let frames = 0;
    processor.port.onmessage = event => {
      chunks.push(event.data);
      frames += event.data.length;
    };
    source.connect(processor);
    processor.connect(muted);
    muted.connect(context.destination);
    await context.resume();
    recording = {
      stream, context, source, processor, muted, chunks,
      get frames() { return frames; },
    };
    recording.timer = setTimeout(stopRecording, MAX_RECORD_SECONDS * 1000);
    mic.classList.add("recording");
    mic.setAttribute("aria-pressed", "true");
    mic.textContent = "■ AUFNAHME SENDEN";
    recordingHint.textContent = "Aufnahme läuft. Zum Beenden und Senden erneut drücken.";
    showStatus("● MIKROFON AKTIV", "bad");
  } catch (error) {
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (context) await context.close().catch(() => {});
    addMessage("assistant", "MIKROFON: " + error.message);
    resetMicrophoneControls();
    showStatus("● MIKROFON NICHT BEREIT", "bad");
  } finally {
    starting = false;
    setBusy(false);
  }
}

// Convert the captured native-rate samples with the browser's resampler,
// then encode explicitly as little-endian 16-bit mono PCM (the Pi protocol).
async function toPcm16(capture) {
  if (!capture.frames) throw new Error("Keine Aufnahme erfasst");
  const nativeRate = capture.context.sampleRate;
  const buffer = capture.context.createBuffer(1, capture.frames, nativeRate);
  const mono = buffer.getChannelData(0);
  let offset = 0;
  for (const chunk of capture.chunks) {
    mono.set(chunk, offset);
    offset += chunk.length;
  }
  const outFrames = Math.floor(capture.frames * AUDIO_RATE / nativeRate);
  if (outFrames < AUDIO_RATE * 0.2) throw new Error("Aufnahme zu kurz");
  const offline = new OfflineAudioContext(1, outFrames, AUDIO_RATE);
  const source = offline.createBufferSource();
  source.buffer = buffer;
  source.connect(offline.destination);
  source.start();
  const resampled = (await offline.startRendering()).getChannelData(0);
  const bytes = new ArrayBuffer(resampled.length * 2);
  const view = new DataView(bytes);
  for (let i = 0; i < resampled.length; i++) {
    const sample = Math.max(-1, Math.min(1, resampled[i]));
    view.setInt16(i * 2, sample < 0 ? sample * 32768 : sample * 32767, true);
  }
  return bytes;
}

async function stopRecording() {
  if (!recording || busy) return;
  const capture = recording;
  recording = null;
  clearTimeout(capture.timer);
  mic.disabled = true;
  send.disabled = true;
  resetMicrophoneControls();
  recordingHint.textContent = "Audio wird für Proximus vorbereitet …";
  showStatus("◌ AUDIO WIRD VORBEREITET");
  try {
    capture.stream.getTracks().forEach(track => track.stop());
    capture.processor.port.onmessage = null;
    capture.source.disconnect();
    capture.processor.disconnect();
    capture.muted.disconnect();
    const pcm = await toPcm16(capture);
    await requestTurn("/api/voice", pcm, "application/octet-stream", true);
  } catch (error) {
    addMessage("assistant", "MIKROFON: " + error.message);
    showStatus("● AUFNAHME FEHLGESCHLAGEN", "bad");
  } finally {
    await capture.context.close().catch(() => {});
    recordingHint.textContent = "Mikrofon starten, sprechen, erneut drücken zum Senden (max. 28 s).";
    setBusy(false);
  }
}

mic.addEventListener("click", () => recording ? stopRecording() : startRecording());

window.addEventListener("pagehide", () => {
  if (recording) {
    clearTimeout(recording.timer);
    recording.stream.getTracks().forEach(track => track.stop());
    recording.context.close().catch(() => {});
    recording = null;
  }
});
