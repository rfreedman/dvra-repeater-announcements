const enginesEl = document.getElementById("engines");
const voiceEl = document.getElementById("voice");
const textEl = document.getElementById("text");
const countEl = document.getElementById("count");
const statusEl = document.getElementById("status");
const speakBtn = document.getElementById("speak");
const stopBtn = document.getElementById("stop");

const SAMPLE = "The booth is live. This audio never touches disk — it is synthesized, streamed, and played entirely from memory.";

let catalog = null;
let engineId = "piper";
let player = null;
let abort = null;

textEl.value = SAMPLE;
updateCount();

textEl.addEventListener("input", updateCount);
speakBtn.addEventListener("click", speak);
stopBtn.addEventListener("click", stop);
voiceEl.addEventListener("change", persist);

loadVoices().catch((err) => setStatus(err.message, "error"));

function updateCount() {
  countEl.textContent = `${textEl.value.length} / ${textEl.maxLength}`;
}

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = kind;
}

async function loadVoices() {
  const res = await fetch("/api/voices");
  if (!res.ok) throw new Error("Could not load voices");
  catalog = await res.json();
  const saved = readPrefs();
  engineId = saved.engine || catalog.default_engine;
  renderEngines();
  renderVoices(saved.voice);
}

function renderEngines() {
  enginesEl.replaceChildren();
  for (const engine of catalog.engines) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `engine${engine.id === engineId ? " active" : ""}`;
    btn.innerHTML = `
      <span class="name">${engine.name}</span>
      <span class="hint">${engine.blurb}</span>
      <span class="pill">${engine.ready ? "Model ready" : "Downloads on first use"}</span>
    `;
    btn.addEventListener("click", () => {
      engineId = engine.id;
      renderEngines();
      renderVoices();
      persist();
    });
    enginesEl.appendChild(btn);
  }
}

function renderVoices(preferred) {
  const engine = catalog.engines.find((item) => item.id === engineId);
  const selected = preferred || engine.default_voice;
  voiceEl.replaceChildren();
  for (const voice of engine.voices) {
    const option = document.createElement("option");
    option.value = voice.id;
    const bits = [voice.name, voice.gender, voice.locale, voice.quality].filter(Boolean);
    option.textContent = bits.join(" · ");
    option.selected = voice.id === selected;
    voiceEl.appendChild(option);
  }
  if (![...voiceEl.options].some((opt) => opt.selected) && voiceEl.options.length) {
    voiceEl.options[0].selected = true;
  }
}

function persist() {
  localStorage.setItem(
    "booth-prefs",
    JSON.stringify({ engine: engineId, voice: voiceEl.value }),
  );
}

function readPrefs() {
  try {
    return JSON.parse(localStorage.getItem("booth-prefs") || "{}");
  } catch {
    return {};
  }
}

async function speak() {
  const text = textEl.value.trim();
  if (!text) {
    setStatus("Type something first.", "error");
    return;
  }
  persist();
  stop();
  abort = new AbortController();
  player = new PcmPlayer();
  speakBtn.disabled = true;
  stopBtn.disabled = false;
  setStatus("Preparing voice…");
  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        engine: engineId,
        voice: voiceEl.value,
      }),
      signal: abort.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = await res.json();
        detail = payload.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    const sampleRate = Number(res.headers.get("X-Sample-Rate") || "22050");
    setStatus("Streaming…", "playing");
    await player.play(res.body, sampleRate, abort.signal);
    if (!abort.signal.aborted) setStatus("Done");
  } catch (err) {
    if (err.name === "AbortError") {
      setStatus("Stopped");
    } else {
      setStatus(err.message || "Playback failed", "error");
    }
  } finally {
    speakBtn.disabled = false;
    stopBtn.disabled = true;
  }
}

function stop() {
  if (abort) abort.abort();
  if (player) player.stop();
  abort = null;
  player = null;
}

class PcmPlayer {
  constructor() {
    this.ctx = null;
    this.sources = [];
    this.stopped = false;
  }

  async play(stream, sampleRate, signal) {
    this.ctx = new AudioContext({ sampleRate });
    await this.ctx.resume();
    const reader = stream.getReader();
    let leftover = new Uint8Array(0);
    let nextTime = this.ctx.currentTime;
    while (!this.stopped && !signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      const combined = new Uint8Array(leftover.length + value.length);
      combined.set(leftover);
      combined.set(value, leftover.length);
      const usable = combined.byteLength - (combined.byteLength % 2);
      leftover = combined.slice(usable);
      if (usable === 0) continue;
      const int16 = new Int16Array(combined.buffer, combined.byteOffset, usable / 2);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i += 1) float32[i] = int16[i] / 32768;
      const buffer = this.ctx.createBuffer(1, float32.length, sampleRate);
      buffer.copyToChannel(float32, 0);
      const source = this.ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(this.ctx.destination);
      const startAt = Math.max(this.ctx.currentTime + 0.03, nextTime);
      source.start(startAt);
      nextTime = startAt + buffer.duration;
      this.sources.push(source);
    }
    const remaining = nextTime - this.ctx.currentTime;
    if (remaining > 0 && !this.stopped) {
      await new Promise((resolve) => setTimeout(resolve, remaining * 1000));
    }
  }

  stop() {
    this.stopped = true;
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources = [];
    if (this.ctx) this.ctx.close();
  }
}
