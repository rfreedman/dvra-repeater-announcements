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
let session = 0;

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
  cancelInFlight();
  const mySession = session;
  const controller = new AbortController();
  abort = controller;
  const currentPlayer = new PcmPlayer();
  player = currentPlayer;
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
      signal: controller.signal,
    });
    if (mySession !== session) return;
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
    await currentPlayer.play(res.body, sampleRate, controller.signal);
    if (mySession !== session) return;
    if (!controller.signal.aborted) setStatus("Done");
  } catch (err) {
    if (mySession !== session) return;
    if (err.name === "AbortError" || controller.signal.aborted) {
      setStatus("Stopped");
    } else {
      setStatus(err.message || "Playback failed", "error");
    }
  } finally {
    if (mySession !== session) return;
    speakBtn.disabled = false;
    stopBtn.disabled = true;
  }
}

function stop() {
  const hadPlayback = Boolean(abort || player);
  cancelInFlight();
  speakBtn.disabled = false;
  stopBtn.disabled = true;
  if (hadPlayback) setStatus("Stopped");
}

function cancelInFlight() {
  session += 1;
  if (abort) {
    abort.abort();
    abort = null;
  }
  if (player) {
    player.stop();
    player = null;
  }
}

class PcmPlayer {
  constructor() {
    this.ctx = null;
    this.sources = [];
    this.stopped = false;
    this.reader = null;
  }

  async play(stream, sampleRate, signal) {
    if (!stream) return;
    this.ctx = new AudioContext({ sampleRate });
    const reader = stream.getReader();
    this.reader = reader;
    const onAbort = () => {
      this.stop();
    };
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) {
      onAbort();
      return;
    }
    try {
      await this.ctx.resume();
      if (this.stopped || signal.aborted) return;
      let leftover = new Uint8Array(0);
      let nextTime = this.ctx.currentTime;
      while (!this.stopped && !signal.aborted) {
        const { done, value } = await reader.read();
        if (this.stopped || signal.aborted || done) break;
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
      if (this.stopped || signal.aborted) return;
      const remaining = nextTime - this.ctx.currentTime;
      if (remaining > 0) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, remaining * 1000);
          const finishEarly = () => {
            clearTimeout(timer);
            resolve();
          };
          signal.addEventListener("abort", finishEarly, { once: true });
        });
      }
    } catch (err) {
      if (this.stopped || signal.aborted || err.name === "AbortError" || err.name === "InvalidStateError") {
        return;
      }
      throw err;
    } finally {
      signal.removeEventListener("abort", onAbort);
      this._cancelReader();
    }
  }

  stop() {
    this.stopped = true;
    this._cancelReader();
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources = [];
    if (this.ctx) {
      this.ctx.close().catch(() => {});
      this.ctx = null;
    }
  }

  _cancelReader() {
    if (!this.reader) return;
    const reader = this.reader;
    this.reader = null;
    reader.cancel().catch(() => {});
  }
}
