"use strict";
const form = document.querySelector("#chat-form");
const prompt = document.querySelector("#prompt");
const messages = document.querySelector("#messages");
const status = document.querySelector("#status");
const send = document.querySelector("#send");

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
  // Release the blob when the user leaves the page.
  window.addEventListener("pagehide", () => URL.revokeObjectURL(url), {once: true});
}

async function checkHealth() {
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = prompt.value.trim();
  if (!text || send.disabled) return;
  prompt.value = "";
  addMessage("user", text);
  send.disabled = true;
  showStatus("◌ ANFRAGE WIRD VERARBEITET");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text}),
    });
    if (!response.ok) throw new Error("Server antwortet mit HTTP " + response.status);
    const events = (await response.text()).trim().split("\n").filter(Boolean).map(JSON.parse);
    const failure = events.find(e => e.event === "error");
    if (failure) throw new Error(failure.message || failure.code || "Verarbeitung fehlgeschlagen");
    const reply = events.find(e => e.event === "reply");
    if (!reply) throw new Error("Keine Antwort erhalten");
    const entry = addMessage("assistant", reply.text);
    const audio = events.find(e => e.event === "audio");
    if (audio) attachAudio(entry, audio);
    showStatus("● MASCHINENGEIST ONLINE", "ready");
  } catch (error) {
    addMessage("assistant", "FEHLER: " + error.message);
    showStatus("● ANFRAGE FEHLGESCHLAGEN", "bad");
  } finally {
    send.disabled = false;
    prompt.focus();
  }
});

prompt.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});
