const PREFS_KEY = "announcements-prefs";
// Timer-driven refreshes send this so they do not reset the server's idle logout.
// The server checks only that the header is present (BACKGROUND_POLL_HEADER in app/auth.py).
const BACKGROUND_HEADERS = { "X-Background-Poll": "1" };

// Poll / countdown (frontend). REST interval aligns with home status fallback when SSE is idle.
const HOME_REST_POLL_MS = 15_000;
const NEWSLINE_PROGRESS_POLL_MS = 1_000;
const STATS_REST_POLL_MS = 15_000;
const UPCOMING_SOON_MS = 15_000;
const COUNTDOWN_TICK_MS = 1_000;

// Mirrors app/config.py DEFAULT_SLOT_HALF_WINDOW_MINUTES until /api/schedules settings load.
const DEFAULT_SLOT_HALF_WINDOW_MINUTES = 10;

// Fallback when /api/voices has not loaded; app/config.py MAX_BUSY_GIVE_UP_SECONDS (600 s).
const MANUAL_TRIGGER_FETCH_TIMEOUT_MS = 600_000;

// Local Newsline preview: short beeps at simulated PTT key/unkey.
const PTT_ENGAGE_HZ = 800;
const PTT_RELEASE_HZ = 700;
const PTT_TONE_SECONDS = 0.2;

/**
 * @typedef {Object} VoiceInfo
 * @property {string} id
 * @property {string} [name]
 * @property {string} [gender]
 * @property {string} [locale]
 * @property {string} [quality]
 */

/**
 * @typedef {Object} VoiceCatalog
 * @property {string|null} [default_voice]
 * @property {VoiceInfo[]} voices
 * @property {number} [speed]
 * @property {number} [sentence_pause]
 * @property {number} [busy_retry_seconds]
 * @property {number} [busy_give_up_seconds]
 * @property {number} [max_busy_give_up_seconds]
 * @property {number} [ptt_lead_seconds]
 */

/**
 * @typedef {Object} MetaLabel
 * @property {string} id
 * @property {string} label
 */

/**
 * @typedef {Object} ScheduleMeta
 * @property {Object.<string, number>} default_priority
 * @property {MetaLabel[]} days
 * @property {MetaLabel[]} months
 */

/**
 * @typedef {Object} HomeUpcoming
 * @property {string} [status]
 * @property {string|null} [schedule_id]
 * @property {number|null} [story_index]
 * @property {number|null} [story_total]
 * @property {string} [at]
 * @property {string} [slot]
 * @property {string} [piece_label]
 * @property {string} [note]
 * @property {string} [phase]
 * @property {number} [clear_seconds]
 * @property {number} [fetchedAt]
 */

/**
 * @typedef {Object} NewslineStatus
 * @property {boolean} [playing]
 * @property {boolean} [ready]
 * @property {boolean} [in_progress]
 * @property {string|null} [outcome]
 * @property {string|number|null} [report_number]
 * @property {string|null} [release_friday]
 * @property {string|null} [published_on]
 * @property {string|null} [last_check_at]
 * @property {string|number|null} [working_report]
 * @property {string} [phase]
 * @property {string} [error]
 * @property {string} [step]
 * @property {number} [elapsed_seconds]
 */

/**
 * @typedef {Object} AnnouncementItem
 * @property {string} [id]
 * @property {string} [name]
 * @property {string} [kind]
 * @property {string} [text]
 * @property {boolean} [use_phoneme_dictionary]
 * @property {boolean} [too_long]
 * @property {number|null} [play_seconds]
 */

/**
 * @typedef {Object} ClockCell
 * @property {string} [slot]
 * @property {string} [label]
 * @property {string} [source]
 * @property {string|null} [fire_at]
 * @property {string|null} [slot_at]
 * @property {string|null} [schedule_id]
 * @property {string} [warning]
 */

/**
 * @typedef {Object} ScheduleRow
 * @property {string} [schedule_id]
 * @property {string} [name]
 * @property {string|null} [announcement_id]
 * @property {string|null} [announcement_name]
 * @property {string} [summary]
 * @property {boolean} [enabled]
 * @property {string} [warning]
 * @property {string|null} [last_run_at]
 * @property {string|null} [next_run_at]
 */

// Day/month labels and template priorities come from GET /api/meta (app/models.py).
/** @type {ScheduleMeta|null} */
let scheduleMeta = null;

const TEMPLATES = {
  baseline: { kind: "baseline", offset_minutes: 0 },
  weekly: { kind: "weekly", offset_minutes: 0 },
  monthly: {
    kind: "monthly",
    occurrence: 1,
    offset_minutes: 0,
    skip_months: [],
  },
  range: { kind: "range", offset_minutes: 0 },
  monthly_range: {
    kind: "monthly_range",
    occurrence: 1,
    days_before: 7,
    offset_minutes: 0,
  },
  silence: { kind: "weekly", offset_minutes: 0, announcement_id: null },
  emergency: { kind: "emergency", offset_minutes: 0 },
};

const voiceEl = document.getElementById("voice");
const speedEl = document.getElementById("speed");
const pauseEl = document.getElementById("pause");
const textEl = document.getElementById("text");
const nameEl = document.getElementById("name");

/** @type {VoiceCatalog|null} */
let catalog = null;
let player = null;
let abort = null;
let editorPlaying = false;
let editorPlaySeconds = null;
let session = 0;
let pollTimer = null;
let newslinePollTimer = null;
/** @type {NewslineStatus|null} */
let lastNewsline = null;
let newslinePlaying = false;
let statsTimer = null;
let countdownTimer = null;
let homeSseWanted = false;
let homeSseAbort = null;
let homeSseConnected = false;
let statsSseWanted = false;
let statsSseAbort = null;
let statsSseConnected = false;
/** @type {HomeUpcoming|null} */
let upcoming = null;
let clockSkewMs = 0;
let homeBusy = false;
let statsBusy = false;
let ledFailed = false;
let ledBlinkTimer = null;
let ledBlinkUntil = 0;
let defaultScript = "";
/** @type {Array.<AnnouncementItem>} */
let announcements = [];
/** @type {Array.<ScheduleRow>} */
let schedules = [];
let settings = {
  slot_half_window_minutes: DEFAULT_SLOT_HALF_WINDOW_MINUTES,
  baseline_randomize: false,
};
let triggerNowEnabled = false;
let triggerNowBusy = false;
let pendingManualTriggers = [];
let editingAnnouncementId = null;
/** @type {Array.<{spelling: string, ipa: string, alternate: string}>} */
let phonemeEntries = [];
let phonemeEditIndex = -1;
let phonemeMatchTimer = 0;
let editingScheduleId = null;
let currentUser = null;
let authReady = false;
let readOnly = false;
let editorBaseline = null;
/** Play length shown when the editor was opened; restored by Reset. */
let editorBaselinePlaySeconds = null;
/** `announcements-prefs` when the editor was opened, or null if unset. */
let editorBaselinePrefs = null;
let editorKey = "";
let ignoreHashChange = false;
let boot = Promise.resolve();
function generationFromPage() {
  const src = document.getElementById("station-logo")?.getAttribute("src") || "";
  const match = /[?&]v=(\d+)/.exec(src);
  if (!match) return null;
  const generation = Number(match[1]);
  return Number.isFinite(generation) ? generation : null;
}

// Start from the generation baked into this document. A cached API response
// can still carry an older header; treating that as the baseline reloads forever.
let seenConfigGeneration = generationFromPage();

const nativeFetch = window.fetch.bind(window);

function paintLed() {
  const el = document.getElementById("next-run-led");
  if (!el) return;
  const off = Date.now() < ledBlinkUntil;
  el.classList.toggle("is-off", off);
  el.classList.toggle("is-green", !ledFailed);
  el.classList.toggle("is-red", ledFailed);
  const label = ledFailed ? "Last request failed" : "Server connected";
  el.setAttribute("aria-label", label);
  el.setAttribute("title", label);
}

function blinkLed() {
  ledBlinkUntil = Date.now() + 90;
  paintLed();
  clearTimeout(ledBlinkTimer);
  ledBlinkTimer = setTimeout(paintLed, 90);
}

window.fetch = async function trackedFetch(input, init) {
  blinkLed();
  const url = typeof input === "string" ? input : input?.url || "";
  const opts = { ...(init || {}) };
  if (url.startsWith("/api/") || url.startsWith("/")) {
    opts.credentials = opts.credentials || "same-origin";
  }
  try {
    const res = await nativeFetch(input, opts);
    noteConfigGeneration(res);
    ledFailed = !res.ok && res.status !== 401;
    paintLed();
    if (
      res.status === 401 &&
      url.startsWith("/api/") &&
      !url.startsWith("/api/auth/") &&
      authReady
    ) {
      currentUser = null;
      readOnly = false;
      updateAuthChrome();
      if (location.hash !== "#/login" && location.hash !== "#/setup") {
        goToHash("#/login");
      }
    }
    return res;
  } catch (err) {
    ledFailed = true;
    paintLed();
    throw err;
  }
};

function isAdmin() {
  return currentUser?.role === "admin";
}

function setError(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  if (message) {
    el.hidden = false;
    el.textContent = message;
  } else {
    el.hidden = true;
    el.textContent = "";
  }
}

function updateAuthChrome() {
  const loggedIn = Boolean(currentUser);
  readOnly = loggedIn && currentUser.role === "readonly";
  document.getElementById("nav-home").classList.toggle("hidden", !loggedIn);
  document.getElementById("nav-help").classList.toggle("hidden", !loggedIn);
  document.getElementById("nav-phonemes").classList.toggle("hidden", !loggedIn);
  document.getElementById("nav-admin").classList.toggle("hidden", !isAdmin());
  document.getElementById("nav-logout").classList.toggle("hidden", !loggedIn);
  document.getElementById("system-stats").classList.toggle("hidden", !loggedIn);
  const userLabel = document.getElementById("nav-user");
  userLabel.classList.toggle("hidden", !loggedIn);
  userLabel.textContent = loggedIn ? currentUser.username : "";
  document.getElementById("new-announcement").classList.toggle("hidden", readOnly);
  document.getElementById("new-schedule").classList.toggle("hidden", readOnly);
  document.getElementById("save-announcement").classList.toggle("hidden", readOnly);
  document.getElementById("save-schedule").classList.toggle("hidden", readOnly);
  const shuffle = document.getElementById("baseline-randomize");
  shuffle.disabled = readOnly;
  shuffle.closest("label")?.classList.toggle("hidden", readOnly);
  const newslineCheck = document.getElementById("newsline-check");
  if (newslineCheck) newslineCheck.classList.toggle("hidden", readOnly);
  syncNewslineCheckButton();
  applyEditorReadOnly();
}

function announcementEditorControls() {
  return [
    nameEl,
    voiceEl,
    speedEl,
    pauseEl,
    textEl,
    document.getElementById("busy-retry"),
    document.getElementById("busy-giveup"),
    document.getElementById("reset"),
    document.getElementById("announcement-kind"),
    document.getElementById("news-opening"),
    document.getElementById("news-station"),
    document.getElementById("news-closing"),
    document.getElementById("news-resume"),
    document.getElementById("news-gap"),
    document.getElementById("newsline-reset"),
    document.getElementById("use-phoneme-dictionary"),
  ];
}

function scheduleEditorControls() {
  return [
    document.getElementById("sched-name"),
    document.getElementById("sched-kind"),
    document.getElementById("sched-announcement"),
    document.getElementById("sched-offset"),
    document.getElementById("monthly-occurrence"),
    document.getElementById("range-start"),
    document.getElementById("range-event-date"),
    document.getElementById("days-before"),
    document.getElementById("range-event-slot"),
    document.getElementById("range-all-slots"),
    document.getElementById("sched-priority"),
    document.getElementById("sched-enabled"),
  ];
}

function applyEditorReadOnly() {
  const lockAnnouncement = readOnly || editorPlaying;
  for (const el of announcementEditorControls()) {
    if (!el) continue;
    el.disabled = lockAnnouncement;
  }
  setPlaybackButtons(editorPlaying);
  for (const el of scheduleEditorControls()) {
    if (!el) continue;
    el.disabled = readOnly;
  }
  for (const input of document.querySelectorAll(
    "#weekly-days input, #monthly-weekday input, #monthly-skip input, #slot-chips input"
  )) {
    input.disabled = readOnly;
  }
  syncEditorSave();
}

async function refreshAuth() {
  const res = await fetch("/api/auth/status");
  const payload = await res.json();
  if (payload.needs_setup) {
    currentUser = null;
    updateAuthChrome();
    return { needsSetup: true, authenticated: false };
  }
  if (payload.authenticated && payload.user) {
    currentUser = payload.user;
    updateAuthChrome();
    return { needsSetup: false, authenticated: true };
  }
  currentUser = null;
  updateAuthChrome();
  return { needsSetup: false, authenticated: false };
}

async function submitSetup() {
  setError("setup-error", "");
  const username = document.getElementById("setup-username").value.trim();
  const password = document.getElementById("setup-password").value;
  const password2 = document.getElementById("setup-password2").value;
  if (!username) {
    setError("setup-error", "Username is required");
    return;
  }
  if (password !== password2) {
    setError("setup-error", "Passwords do not match");
    return;
  }
  const res = await fetch("/api/auth/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    setError("setup-error", typeof payload.detail === "string" ? payload.detail : "Could not create admin");
    return;
  }
  currentUser = payload.user;
  updateAuthChrome();
  goToHash("#/");
}

async function submitLogin() {
  setError("login-error", "");
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    setError("login-error", typeof payload.detail === "string" ? payload.detail : "Could not log in");
    return;
  }
  currentUser = payload.user;
  document.getElementById("login-password").value = "";
  updateAuthChrome();
  goToHash("#/");
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  currentUser = null;
  updateAuthChrome();
  goToHash("#/login");
}

function resetUserForm() {
  document.getElementById("user-edit-id").value = "";
  document.getElementById("user-form-title").textContent = "Add user";
  document.getElementById("user-username").value = "";
  document.getElementById("user-password").value = "";
  document.getElementById("user-role").value = "readonly";
  document.getElementById("user-cancel").classList.add("hidden");
  document.getElementById("user-password-hint").textContent =
    "At least 8 characters.";
  setError("user-error", "");
}

function editUserRow(user) {
  document.getElementById("user-edit-id").value = user.id;
  document.getElementById("user-form-title").textContent = `Edit ${user.username}`;
  document.getElementById("user-username").value = user.username;
  document.getElementById("user-password").value = "";
  document.getElementById("user-role").value = user.role;
  document.getElementById("user-cancel").classList.remove("hidden");
  document.getElementById("user-password-hint").textContent =
    "At least 8 characters. Leave blank to keep the current password.";
  setError("user-error", "");
}

async function refreshUsers() {
  const res = await fetch("/api/users");
  if (!res.ok) throw new Error("Could not load users");
  const payload = await res.json();
  const body = document.getElementById("users-rows");
  body.replaceChildren();
  for (const user of payload.users || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(user.username)}</td>
      <td>${escapeHtml(user.role === "admin" ? "Admin" : "Read-Only")}</td>
      <td class="row-actions"></td>
    `;
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "ghost";
    edit.textContent = "Edit";
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      editUserRow(user);
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "Delete";
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!window.confirm(`Delete user “${user.username}”?`)) return;
      const delRes = await fetch(`/api/users/${user.id}`, { method: "DELETE" });
      if (!delRes.ok) {
        let detail = delRes.statusText;
        try {
          detail = (await delRes.json()).detail || detail;
        } catch {
          /* ignore */
        }
        window.alert(typeof detail === "string" ? detail : "Could not delete");
        return;
      }
      if (currentUser && currentUser.id === user.id) {
        await logout();
        return;
      }
      resetUserForm();
      await refreshUsers();
    });
    tr.querySelector(".row-actions").append(edit, del);
    body.append(tr);
  }
}

async function saveUser() {
  setError("user-error", "");
  const id = document.getElementById("user-edit-id").value;
  const username = document.getElementById("user-username").value.trim();
  const password = document.getElementById("user-password").value;
  const role = document.getElementById("user-role").value;
  if (!username) {
    setError("user-error", "Username is required");
    return;
  }
  if (!id && !password) {
    setError("user-error", "Password is required");
    return;
  }
  const body = { username, role };
  if (!id || password) body.password = password;
  const res = await fetch(id ? `/api/users/${id}` : "/api/users", {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    setError("user-error", typeof payload.detail === "string" ? payload.detail : "Could not save user");
    return;
  }
  if (currentUser && payload.id === currentUser.id) {
    currentUser = { ...currentUser, username: payload.username, role: payload.role };
    updateAuthChrome();
  }
  resetUserForm();
  await refreshUsers();
}

function showScreen(id) {
  for (const el of document.querySelectorAll(".screen")) {
    el.classList.toggle("active", el.id === id);
  }
}

function isBootScreenActive() {
  const el = document.getElementById("view-boot");
  return Boolean(el && el.classList.contains("active"));
}

async function fetchServerReady() {
  try {
    const res = await nativeFetch("/api/ready", { cache: "no-store", credentials: "same-origin" });
    if (!res.ok) return false;
    const body = await res.json();
    return Boolean(body.ready);
  } catch {
    return false;
  }
}

function showBoot(message = "Starting server, please wait…") {
  const panel = document.querySelector(".boot-panel");
  const msg = document.getElementById("boot-message");
  const err = document.getElementById("boot-error");
  const retryWrap = document.getElementById("boot-retry-wrap");
  if (msg) msg.textContent = message;
  if (err) {
    err.hidden = true;
    err.textContent = "";
  }
  if (retryWrap) retryWrap.classList.add("hidden");
  if (panel) panel.classList.remove("has-error");
  showScreen("view-boot");
}

function yieldToPaint() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

async function waitForServerReady() {
  const msg = document.getElementById("boot-message");
  while (true) {
    try {
      const res = await nativeFetch("/api/ready", { cache: "no-store", credentials: "same-origin" });
      if (res.ok) {
        const body = await res.json();
        if (body.ready) return;
      }
    } catch {
      /* connection refused or reset while uvicorn binds */
    }
    if (msg) msg.textContent = "Starting server, please wait…";
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

function showBootError(message) {
  const panel = document.querySelector(".boot-panel");
  const msg = document.getElementById("boot-message");
  const err = document.getElementById("boot-error");
  const retryWrap = document.getElementById("boot-retry-wrap");
  if (msg) msg.textContent = "Could not load the app";
  if (err) {
    err.hidden = false;
    err.textContent = message;
  }
  if (retryWrap) retryWrap.classList.remove("hidden");
  if (panel) panel.classList.add("has-error");
  showScreen("view-boot");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function slotStamps() {
  const stamps = [];
  for (let hour = 0; hour < 24; hour += 1) {
    stamps.push(`${String(hour).padStart(2, "0")}:00`);
    stamps.push(`${String(hour).padStart(2, "0")}:30`);
  }
  return stamps;
}

function formatWhen(iso) {
  if (!iso) return "Never";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "—";
  const month = dt.toLocaleString("en-US", { month: "short" });
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  return `${month} ${dt.getDate()}, ${hh}:${mm}`;
}

function formatCountdown(ms) {
  if (ms <= 0) return "0:00";
  const total = Math.ceil(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function goToHash(hash) {
  const current = location.hash || "#/";
  if (current === hash || (hash === "#/" && (current === "" || current === "#"))) {
    route().catch(() => {});
    return;
  }
  location.hash = hash;
}

function confirmDiscardEdits() {
  return window.confirm("You have unsaved changes. If you leave, those changes will be lost.");
}

function formSnapshot() {
  if (document.getElementById("view-announcement").classList.contains("active")) {
    return JSON.stringify({
      kind: document.getElementById("announcement-kind").value,
      name: nameEl.value,
      text: textEl.value,
      voice: voiceEl.value,
      speed: speedEl.value,
      pause: pauseEl.value,
      busyRetry: document.getElementById("busy-retry").value,
      busyGiveup: document.getElementById("busy-giveup").value,
      newsOpening: document.getElementById("news-opening").value,
      newsStation: document.getElementById("news-station").value,
      newsClosing: document.getElementById("news-closing").value,
      newsResume: document.getElementById("news-resume").value,
      newsGap: document.getElementById("news-gap").value,
      usePhonemeDictionary: document.getElementById("use-phoneme-dictionary").checked,
    });
  }
  if (document.getElementById("view-schedule").classList.contains("active")) {
    const payload = readSchedulePayload();
    // Compare the name as typed. readSchedulePayload trims it for the request.
    payload.name = document.getElementById("sched-name").value;
    return JSON.stringify(payload);
  }
  return "";
}

function isEditorDirty() {
  return editorBaseline !== null && formSnapshot() !== editorBaseline;
}

function syncEditorSave() {
  const announcementSave = document.getElementById("save-announcement");
  if (announcementSave) {
    announcementSave.disabled = readOnly || editorPlaying || !isEditorDirty();
  }
  const scheduleSave = document.getElementById("save-schedule");
  if (scheduleSave) scheduleSave.disabled = readOnly || !isEditorDirty();
  for (const id of ["reset", "newsline-reset"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = readOnly || editorPlaying || !isEditorDirty();
  }
}

function markEditorClean() {
  editorBaseline = formSnapshot();
  editorBaselinePlaySeconds = editorPlaySeconds;
  editorBaselinePrefs = localStorage.getItem(PREFS_KEY);
  syncEditorSave();
}

function clearEditorGuard() {
  editorBaseline = null;
  editorBaselinePlaySeconds = null;
  editorBaselinePrefs = null;
  syncEditorSave();
}

function resetAnnouncementEdits() {
  if (readOnly || editorPlaying || !isEditorDirty() || editorBaseline == null) return;
  const saved = JSON.parse(editorBaseline);
  document.getElementById("announcement-kind").value = saved.kind;
  nameEl.value = saved.name;
  textEl.value = saved.text;
  speedEl.value = saved.speed;
  pauseEl.value = saved.pause;
  document.getElementById("busy-retry").value = saved.busyRetry;
  document.getElementById("busy-giveup").value = saved.busyGiveup;
  fillSpokenSelect("news-opening", saved.newsOpening, { optional: true });
  fillSpokenSelect("news-station", saved.newsStation);
  fillSpokenSelect("news-closing", saved.newsClosing, { optional: true });
  fillSpokenSelect("news-resume", saved.newsResume, { optional: true });
  document.getElementById("news-gap").value = saved.newsGap;
  document.getElementById("use-phoneme-dictionary").checked = saved.usePhonemeDictionary !== false;
  if (catalog) renderVoices(saved.voice);
  editorPlaySeconds = editorBaselinePlaySeconds;
  if (editorBaselinePrefs == null) localStorage.removeItem(PREFS_KEY);
  else localStorage.setItem(PREFS_KEY, editorBaselinePrefs);
  syncAnnouncementKind();
  updateCount();
  updateDeliveryLabels();
  showError("announcement-error", "");
  showKnownPlayLength();
  syncEditorSave();
  schedulePhonemeMatches();
}

function primaryPendingManual() {
  return pendingManualTriggers.length ? pendingManualTriggers[0] : null;
}

function pendingManualForSchedule(scheduleId) {
  return pendingManualTriggers.find((item) => item.schedule_id === scheduleId) || null;
}

function triggerNowLabelForSchedule(scheduleId) {
  const pending = pendingManualForSchedule(scheduleId);
  if (upcoming?.status === "running" && upcoming?.schedule_id === scheduleId) {
    return "Playing";
  }
  if (pending) {
    if (pending.phase === "clear_window") {
      const remain = interpolatedClearRemaining(pending);
      if (remain != null && remain <= 0) {
        return "Starting…";
      }
    }
    return pending.in_progress ? "Waiting…" : "Queued…";
  }
  return "Trigger now";
}

function pendingElapsedSeconds(pending) {
  return (Date.now() + clockSkewMs - (pending?.fetchedAt || 0)) / 1000;
}

function interpolatedClearRemaining(pending) {
  if (pending?.phase !== "clear_window" || pending.clear_remaining_seconds == null) return null;
  return Math.max(0, pending.clear_remaining_seconds - pendingElapsedSeconds(pending));
}

function interpolatedGiveUpRemaining(pending) {
  if (pending?.give_up_remaining_seconds == null) return null;
  return Math.max(0, pending.give_up_remaining_seconds - pendingElapsedSeconds(pending));
}

function renderManualTriggerDropNotice(notice) {
  const box = document.getElementById("manual-trigger-drop");
  const text = document.getElementById("manual-trigger-drop-text");
  if (!box || !text) return;
  if (!notice) {
    box.classList.add("hidden");
    text.textContent = "";
    return;
  }
  const name = notice.announcement_name || "Announcement";
  const detail =
    notice.detail ||
    "The channel did not stay clear long enough before the give-up timer expired.";
  text.textContent = `Manual trigger cancelled: “${name}” was not played. ${detail}`;
  box.classList.remove("hidden");
}

async function dismissManualTriggerDrop() {
  try {
    await fetch("/api/manual-trigger-drop/dismiss", { method: "POST" });
  } catch {
    /* ignore */
  }
  renderManualTriggerDropNotice(null);
}

function scheduleNameFor(row) {
  if (row?.schedule_name) return row.schedule_name;
  if (!row?.schedule_id) return "";
  const found = schedules.find((item) => item.schedule_id === row.schedule_id);
  return found?.name || "";
}

function fillUpcomingLine(lineId, label, value) {
  const line = document.getElementById(lineId);
  if (!line) return;
  const labelEl = document.createElement("span");
  labelEl.className = "next-run-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "next-run-value";
  valueEl.textContent = value;
  line.replaceChildren(labelEl, document.createTextNode(" "), valueEl);
}

function fillUpcomingIdentity(row) {
  fillUpcomingLine("next-run-schedule-line", "Schedule:", scheduleNameFor(row) || "—");
  fillUpcomingLine(
    "next-run-announcement-line",
    "Announcement:",
    row?.announcement_name || "Untitled",
  );
}

function setUpcomingNote(text) {
  const note = document.getElementById("next-run-note");
  if (!note) return;
  note.hidden = !text;
  note.textContent = text || "";
}

function newslineProgressNote(row) {
  const parts = [];
  if (row?.piece_label) parts.push(segmentDisplayName(row.piece_label));
  if (row?.story_index != null && row?.story_total != null) {
    parts.push(`news item ${row.story_index} of ${row.story_total}`);
  }
  if (parts.length) return parts.join(" · ");
  return row?.note || "";
}

function renderSkipNotice(notice) {
  const box = document.getElementById("skip-notice");
  const text = document.getElementById("skip-notice-text");
  if (!box || !text) return;
  if (!notice || !notice.detail) {
    box.classList.add("hidden");
    text.textContent = "";
    return;
  }
  text.textContent = notice.detail;
  box.classList.remove("hidden");
}

function renderUpcoming() {
  const box = document.getElementById("next-run");
  if (!box) return;
  const kicker = box.querySelector(".next-run-kicker");
  const running = upcoming?.status === "running";
  const pending = !running ? primaryPendingManual() : null;
  if (!upcoming && !pending) {
    box.classList.add("hidden");
    setUpcomingNote("");
    syncTriggerNowButtons();
    return;
  }
  box.classList.remove("hidden");
  const statusEl = document.getElementById("next-run-status");
  const countEl = document.getElementById("next-run-countdown");
  const whenEl = document.getElementById("next-run-when");
  syncTriggerNowButtons();

  if (running && upcoming) {
    if (kicker) kicker.textContent = "Next run";
    fillUpcomingIdentity(upcoming);
    const runPhase = upcoming.phase || "playing";
    if (runPhase === "channel_busy") {
      statusEl.textContent = "Paused · channel busy";
      statusEl.classList.add("is-deferred");
      statusEl.classList.remove("is-running", "is-clear-hold");
      whenEl.textContent =
        upcoming.clear_seconds > 0
          ? `Then ${Math.ceil(upcoming.clear_seconds)}s clear hold before continuing`
          : "Waiting to continue Newsline";
      countEl.textContent = "Waiting for channel";
      setUpcomingNote(newslineProgressNote(upcoming));
      return;
    }
    if (runPhase === "clear_window") {
      statusEl.textContent = "Paused · clear hold";
      statusEl.classList.add("is-clear-hold");
      statusEl.classList.remove("is-running", "is-deferred");
      whenEl.textContent = "Channel clear — hold timer";
      const remain = interpolatedClearRemaining(upcoming);
      if (remain != null && upcoming.clear_seconds > 0 && remain > 0) {
        countEl.textContent = `${formatCountdown(remain * 1000)} remaining`;
      } else {
        countEl.textContent = "Continuing…";
      }
      setUpcomingNote(newslineProgressNote(upcoming));
      return;
    }
    whenEl.textContent = upcoming.piece_label
      ? segmentDisplayName(upcoming.piece_label)
      : upcoming.at
        ? formatWhen(upcoming.at)
        : "—";
    statusEl.textContent = "Running";
    statusEl.classList.add("is-running");
    statusEl.classList.remove("is-deferred", "is-clear-hold");
    const played = elapsedSecondsSince(upcoming.at);
    countEl.textContent =
      played == null ? "Now" : `${formatSegmentElapsed(played)}`;
    if (upcoming.story_index != null && upcoming.story_total != null) {
      setUpcomingNote(`news item ${upcoming.story_index} of ${upcoming.story_total}`);
    } else {
      setUpcomingNote(upcoming.note || "");
    }
    return;
  }

  if (pending) {
    if (kicker) kicker.textContent = "Trigger now";
    fillUpcomingIdentity(pending);
    statusEl.classList.remove("is-running");
    if (pending.phase === "in_progress") {
      statusEl.textContent = "Trigger in progress";
      statusEl.classList.add("is-clear-hold");
      statusEl.classList.remove("is-deferred");
      whenEl.textContent = "Waiting for clear channel or transmitting";
      const remain = interpolatedClearRemaining(pending);
      countEl.textContent =
        remain != null && pending.clear_seconds > 0 && remain > 0
          ? `${formatCountdown(remain * 1000)} remaining`
          : "Now";
    } else if (pending.phase === "clear_window") {
      statusEl.textContent = "Clear to transmit";
      statusEl.classList.add("is-clear-hold");
      statusEl.classList.remove("is-deferred");
      whenEl.textContent = "Channel clear — hold timer";
      // A queued retry's own run time is irrelevant here: the COS poll runs it as soon
      // as the hold is met (nudge_manual_defer_jobs_if_clear_ready).
      const remain = interpolatedClearRemaining(pending);
      if (remain != null && pending.clear_seconds > 0 && remain > 0) {
        countEl.textContent = `${formatCountdown(remain * 1000)} remaining`;
      } else {
        countEl.textContent = "Starting now…";
      }
    } else if (pending.phase === "channel_busy") {
      statusEl.textContent = "Deferred · channel busy";
      statusEl.classList.add("is-deferred");
      statusEl.classList.remove("is-clear-hold");
      whenEl.textContent =
        pending.clear_seconds > 0
          ? `Then ${Math.ceil(pending.clear_seconds)}s clear hold before transmit`
          : "Will play when the channel is clear";
      const giveUp = interpolatedGiveUpRemaining(pending);
      countEl.textContent =
        giveUp != null ? `Give up in ${formatCountdown(giveUp * 1000)}` : "Waiting for channel";
    } else {
      statusEl.textContent = "Deferred · queued";
      statusEl.classList.add("is-deferred");
      statusEl.classList.remove("is-clear-hold");
      whenEl.textContent = "Waiting to retry";
      const retryMs = (pending.retry_in_seconds ?? 0) * 1000;
      countEl.textContent = retryMs > 0 ? `Retry in ${formatCountdown(retryMs)}` : "Retrying…";
    }
    setUpcomingNote("");
    return;
  }

  if (kicker) kicker.textContent = "Next run";
  fillUpcomingIdentity(upcoming);
  whenEl.textContent = upcoming.at ? formatWhen(upcoming.at) : "—";
  statusEl.textContent = "Waiting";
  statusEl.classList.remove("is-running", "is-deferred", "is-clear-hold");
  const at = upcoming.at ? new Date(upcoming.at).getTime() : NaN;
  if (Number.isNaN(at)) {
    countEl.textContent = "—";
    return;
  }
  countEl.textContent = `in ${formatCountdown(at - (Date.now() + clockSkewMs))}`;
  setUpcomingNote(upcoming.note || "");
}

function announcementIsPlaying() {
  return (
    triggerNowBusy ||
    upcoming?.status === "running" ||
    pendingManualTriggers.length > 0
  );
}

function schedulesUsingAnnouncement(announcementId) {
  return schedules.filter((row) => row.announcement_id === announcementId);
}

function syncTriggerNowButtons() {
  const playing = announcementIsPlaying();
  for (const button of document.querySelectorAll("[data-trigger-now]")) {
    button.disabled = playing;
    const scheduleId = button.dataset.scheduleId;
    if (scheduleId) {
      button.textContent = triggerNowLabelForSchedule(scheduleId);
    }
  }
  syncDeleteButtons();
}

function syncDeleteButtons() {
  const playing = announcementIsPlaying();
  for (const button of document.querySelectorAll("[data-delete-button]")) {
    button.disabled = playing;
  }
}

function tickUpcoming() {
  renderUpcoming();
  markUpcomingClockCell();
  if (homeSseConnected) return;
  // When SSE is down (cache, proxy, reconnect), fall back to fast poll during active radio work.
  if (!upcoming && pendingManualTriggers.length === 0 && !triggerNowBusy) return;
  const soon =
    upcoming?.at &&
    new Date(upcoming.at).getTime() - (Date.now() + clockSkewMs) <= UPCOMING_SOON_MS;
  if (
    triggerNowBusy ||
    upcoming?.status === "running" ||
    pendingManualTriggers.length > 0 ||
    soon
  ) {
    refreshHome({ background: true }).catch(() => {});
  }
}

function shortCalendarLabel(label) {
  // Chip text is the first three letters of the server label (Monday → Mon, January → Jan).
  return String(label).slice(0, 3);
}

function renderPriorityDefaults() {
  const el = document.getElementById("priority-defaults");
  if (!el || !scheduleMeta) return;
  const priorities = scheduleMeta.default_priority;
  const standard = [priorities.weekly, priorities.monthly];
  const standardText = standard.every((value) => value === standard[0])
    ? `weekly/monthly ${standard[0]}`
    : `weekly ${priorities.weekly}, monthly ${priorities.monthly}`;
  const countdownText =
    priorities.range === priorities.monthly_range
      ? String(priorities.range)
      : `${priorities.range} (event countdown), ${priorities.monthly_range} (monthly countdown)`;
  el.textContent = `Defaults: emergency ${priorities.emergency}, ${standardText}, event countdown ${countdownText}.`;
}

async function ensureScheduleMeta() {
  if (scheduleMeta) return scheduleMeta;
  const res = await fetch("/api/meta");
  if (!res.ok) throw new Error("Could not load schedule options");
  scheduleMeta = await res.json();
  renderPriorityDefaults();
  return scheduleMeta;
}

function applyTemplatePriority(template) {
  const priority = scheduleMeta.default_priority[template.kind];
  if (template.kind === "baseline" || priority == null) return { ...template };
  return { ...template, priority };
}

function editorPriority(schedule, kind) {
  if (schedule?.priority) return schedule.priority;
  const priorities = scheduleMeta.default_priority;
  if (isCountdownKind(kind)) return priorities.range;
  return priorities.weekly;
}

function fillDayChips(container, selected, { exclusive = false } = {}) {
  container.replaceChildren();
  for (const day of scheduleMeta.days) {
    const value = day.id;
    const label = shortCalendarLabel(day.label);
    const chip = document.createElement("label");
    chip.className = "chip";
    const input = document.createElement("input");
    input.type = exclusive ? "radio" : "checkbox";
    input.name = exclusive ? container.id : value;
    input.value = value;
    input.checked = exclusive ? selected === value : (selected || []).includes(value);
    chip.append(input, document.createTextNode(label));
    container.append(chip);
  }
}

function fillMonthSkip(container, selected) {
  container.replaceChildren();
  for (const month of scheduleMeta.months) {
    const value = month.id;
    const label = shortCalendarLabel(month.label);
    const chip = document.createElement("label");
    chip.className = "chip";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = String(value);
    input.checked = (selected || []).includes(value);
    chip.append(input, document.createTextNode(label));
    container.append(chip);
  }
}

function fillSlotChips(selected) {
  const container = document.getElementById("slot-chips");
  container.replaceChildren();
  for (const stamp of slotStamps()) {
    const chip = document.createElement("label");
    chip.className = "chip";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = stamp;
    input.checked = (selected || []).includes(stamp);
    chip.append(input, document.createTextNode(stamp));
    container.append(chip);
  }
}

function fillEventSlotSelect(selected) {
  const el = document.getElementById("range-event-slot");
  el.replaceChildren();
  if (!selected) {
    const pick = document.createElement("option");
    pick.value = "";
    pick.textContent = "Choose a slot…";
    el.append(pick);
  }
  for (const stamp of slotStamps()) {
    const option = document.createElement("option");
    option.value = stamp;
    option.textContent = stamp;
    option.selected = stamp === selected;
    el.append(option);
  }
  if (!selected) el.value = "";
}

function fillAnnouncementSelect(selected, { allowSilence = false } = {}) {
  const el = document.getElementById("sched-announcement");
  el.replaceChildren();
  if (selected === undefined) {
    const pick = document.createElement("option");
    pick.value = "__pick__";
    pick.textContent = "Choose an announcement…";
    el.append(pick);
  }
  if (allowSilence) {
    const silence = document.createElement("option");
    silence.value = "";
    silence.textContent = "Silence — no transmission";
    el.append(silence);
  }
  for (const item of announcements) {
    if (item.too_long && item.id !== selected) continue;
    const option = document.createElement("option");
    option.value = item.id;
    const newsline = item.kind === "newsline";
    option.textContent = newsline
      ? `${item.name} (Newsline)`
      : item.too_long
        ? `${item.name} (too long)`
        : item.name;
    el.append(option);
  }
  if (selected === undefined) {
    el.value = "__pick__";
  } else if (selected === null || selected === "") {
    el.value = "";
  } else if (selected) {
    el.value = selected;
  }
}

function isCountdownKind(kind) {
  return kind === "range" || kind === "monthly_range";
}

function isMonthlyKind(kind) {
  return kind === "monthly" || kind === "monthly_range";
}

function currentKind() {
  return document.getElementById("sched-kind").value;
}

function syncKindFields() {
  const kind = currentKind();
  const overlay = kind !== "baseline";
  document.getElementById("fields-overlay").classList.toggle("hidden", !overlay);
  document.getElementById("fields-weekly").classList.toggle("hidden", kind !== "weekly");
  document.getElementById("fields-monthly").classList.toggle("hidden", !isMonthlyKind(kind));
  document.getElementById("fields-range").classList.toggle("hidden", kind !== "range");
  document.getElementById("fields-days-before").classList.toggle("hidden", kind !== "monthly_range");
  document.getElementById("fields-event-end").classList.toggle("hidden", !isCountdownKind(kind));
  const showSlots =
    kind === "weekly" ||
    kind === "monthly" ||
    (isCountdownKind(kind) && !document.getElementById("range-all-slots").checked);
  document.getElementById("fields-slots").classList.toggle("hidden", !showSlots);
  document.getElementById("field-priority").classList.toggle("hidden", kind === "baseline" || kind === "emergency");
  document.getElementById("silence-hint").classList.toggle("hidden", kind === "baseline");
  const currentAnnouncement = document.getElementById("sched-announcement").value;
  fillAnnouncementSelect(
    currentAnnouncement === "__pick__" ? undefined : currentAnnouncement,
    { allowSilence: kind !== "baseline" },
  );
  const windowMin = settings.slot_half_window_minutes ?? DEFAULT_SLOT_HALF_WINDOW_MINUTES;
  const offset = document.getElementById("sched-offset");
  offset.min = String(-windowMin);
  offset.max = String(windowMin);
  applyEditorReadOnly();
}

function readSchedulePayload() {
  const kind = currentKind();
  const announcementValue = document.getElementById("sched-announcement").value;
  const payload = {
    name: document.getElementById("sched-name").value.trim(),
    enabled: document.getElementById("sched-enabled").checked,
    kind,
    announcement_id: announcementValue || null,
    offset_minutes: kind === "baseline" ? 0 : Number(document.getElementById("sched-offset").value || 0),
    priority: Number(document.getElementById("sched-priority").value || 0) || undefined,
  };
  if (kind === "weekly" || isMonthlyKind(kind)) {
    const dayBox = kind === "weekly" ? document.getElementById("weekly-days") : document.getElementById("monthly-weekday");
    payload.days = [...dayBox.querySelectorAll("input:checked")].map((el) => el.value);
  }
  if (isMonthlyKind(kind)) {
    payload.occurrence = Number(document.getElementById("monthly-occurrence").value);
    payload.skip_months = [...document.getElementById("monthly-skip").querySelectorAll("input:checked")].map((el) => Number(el.value));
  }
  if (kind === "range") {
    payload.start_date = document.getElementById("range-start").value || null;
    payload.event_date = document.getElementById("range-event-date").value || null;
    payload.event_slot = document.getElementById("range-event-slot").value || null;
  }
  if (kind === "monthly_range") {
    payload.days_before = Number(document.getElementById("days-before").value);
    payload.event_slot = document.getElementById("range-event-slot").value || null;
  }
  const needSlots =
    kind === "weekly" ||
    kind === "monthly" ||
    (isCountdownKind(kind) && !document.getElementById("range-all-slots").checked);
  payload.slots = needSlots
    ? [...document.getElementById("slot-chips").querySelectorAll("input:checked")].map((el) => el.value)
    : [];
  return payload;
}

function fillScheduleForm(schedule) {
  const kind = schedule?.kind || "baseline";
  document.getElementById("sched-name").value = schedule?.name || "";
  document.getElementById("sched-kind").value = kind;
  document.getElementById("sched-enabled").checked = schedule?.enabled !== false;
  document.getElementById("sched-offset").value = String(schedule?.offset_minutes ?? 0);
  document.getElementById("sched-priority").value = String(editorPriority(schedule, kind));
  fillDayChips(document.getElementById("weekly-days"), schedule?.days || []);
  fillDayChips(document.getElementById("monthly-weekday"), schedule?.days?.[0] || "", { exclusive: true });
  document.getElementById("monthly-occurrence").value = String(schedule?.occurrence || 1);
  fillMonthSkip(document.getElementById("monthly-skip"), schedule?.skip_months || []);
  document.getElementById("range-start").value = schedule?.start_date || "";
  document.getElementById("range-event-date").value = schedule?.event_date || "";
  document.getElementById("days-before").value = String(schedule?.days_before ?? 7);
  fillEventSlotSelect(schedule?.event_slot);
  document.getElementById("range-all-slots").checked = !isCountdownKind(kind) || !(schedule?.slots || []).length;
  fillSlotChips(schedule?.slots || []);
  const hasAnnouncementKey = Boolean(schedule && Object.prototype.hasOwnProperty.call(schedule, "announcement_id"));
  const selectedAnnouncement = hasAnnouncementKey ? schedule.announcement_id : undefined;
  fillAnnouncementSelect(selectedAnnouncement, { allowSilence: kind !== "baseline" });
  const invalid = document.getElementById("schedule-invalid");
  if (invalid) {
    invalid.textContent = schedule?.warning || "";
    invalid.classList.toggle("hidden", !schedule?.warning);
  }
  if (schedule && Object.prototype.hasOwnProperty.call(schedule, "announcement_id") && !schedule.announcement_id) {
    document.getElementById("sched-announcement").value = "";
  }
  syncKindFields();
}

/** @param {Array.<ClockCell>|null|undefined} clock */
function renderClock(clock) {
  const grid = document.getElementById("clock-grid");
  grid.replaceChildren();
  for (const row of clock || []) {
    const cell = document.createElement("div");
    cell.className = `clock-cell source-${row.source || "none"}`;
    const stamp = document.createElement("span");
    stamp.className = "clock-stamp";
    stamp.textContent = row.slot;
    const label = document.createElement("span");
    label.className = "clock-label";
    const fire = row.fire_at && row.fire_at !== row.slot_at ? ` → ${formatWhen(row.fire_at).split(", ")[1] || ""}` : "";
    label.textContent = `${row.label || "—"}${fire}`;
    cell.append(stamp, label);
    if (row.warning) {
      const note = document.createElement("span");
      note.className = "exclusion-line";
      note.textContent = row.warning;
      cell.append(note);
      cell.classList.add("has-warning");
    }
    if (row.slot) cell.dataset.slot = row.slot;
    if (row.slot_at) cell.dataset.slotAt = row.slot_at;
    if (row.schedule_id) {
      cell.addEventListener("click", () => goToHash(`#/schedules/${row.schedule_id}`));
    }
    grid.append(cell);
  }
  markUpcomingClockCell();
}

/** Calendar date of an instant in the station timezone (YYYY-MM-DD). */
function stationDateStamp(instant) {
  const tz = settings.timezone;
  if (!tz || !(instant instanceof Date) || Number.isNaN(instant.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(instant);
  const pick = (type) => parts.find((part) => part.type === type)?.value || "";
  const year = pick("year");
  const month = pick("month");
  const day = pick("day");
  if (!year || !month || !day) return "";
  return `${year}-${month}-${day}`;
}

/** Mark the next transmission's slot when the preview date is today. */
function markUpcomingClockCell() {
  const grid = document.getElementById("clock-grid");
  if (!grid) return;
  for (const cell of grid.querySelectorAll(".clock-upcoming")) {
    cell.classList.remove("clock-upcoming");
    cell.removeAttribute("aria-current");
  }
  const preview = document.getElementById("clock-date")?.value || "";
  const today = stationDateStamp(new Date(Date.now() + clockSkewMs));
  if (!preview || !today || preview !== today) return;
  if (upcoming?.status !== "waiting" || !upcoming.slot || !upcoming.at) return;
  const fireDay = stationDateStamp(new Date(upcoming.at));
  if (fireDay !== preview) return;
  const slot = upcoming.slot;
  const cell = [...grid.querySelectorAll(".clock-cell")].find((item) => item.dataset.slot === slot);
  if (!cell) return;
  cell.classList.add("clock-upcoming");
  cell.setAttribute("aria-current", "true");
}

function nextRunSortKey(value) {
  if (!value) return Number.POSITIVE_INFINITY;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms;
}

/** @param {Array.<ScheduleRow>} rows */
function renderSchedules(rows) {
  const body = document.getElementById("schedule-rows");
  const empty = document.getElementById("schedule-empty");
  body.replaceChildren();
  empty.classList.toggle("hidden", rows.length > 0);
  const sorted = [...rows].sort((a, b) => {
    const diff = nextRunSortKey(a.next_run_at) - nextRunSortKey(b.next_run_at);
    if (diff !== 0) return diff;
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  for (const row of sorted) {
    const tr = document.createElement("tr");
    if (row.warning) tr.classList.add("has-warning");
    if (!row.enabled) tr.classList.add("disabled");
    tr.innerHTML = `
      <td>${escapeHtml(row.name)}</td>
      <td>${escapeHtml(row.summary)}${row.warning ? `<div class="exclusion-line">${escapeHtml(row.warning)}</div>` : ""}</td>
      <td>${escapeHtml(row.announcement_name || (row.summary.includes("silence") ? "Silence" : "—"))}</td>
      <td>${escapeHtml(row.last_run_at ? formatWhen(row.last_run_at) : "Never")}</td>
      <td>${escapeHtml(row.next_run_at ? formatWhen(row.next_run_at) : "—")}</td>
      <td class="row-actions"></td>
    `;
    const actions = tr.querySelector(".row-actions");
    if (!readOnly) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "danger";
      del.textContent = "Delete";
      del.dataset.deleteButton = "1";
      del.disabled = announcementIsPlaying();
      del.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (announcementIsPlaying()) return;
        const label = row.name ? `the schedule "${row.name}"` : "this schedule";
        if (!window.confirm(`Are you sure that you want to delete ${label}?`)) return;
        const res = await fetch(`/api/schedules/${row.schedule_id}`, { method: "DELETE" });
        if (res.ok) refreshHome().catch(() => {});
      });
      actions.append(del);
      if (triggerNowEnabled) {
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "ghost";
        trigger.dataset.triggerNow = "1";
        const rowPending = pendingManualForSchedule(row.schedule_id);
        const rowPlaying =
          upcoming?.status === "running" && upcoming?.schedule_id === row.schedule_id;
        trigger.textContent = triggerNowLabelForSchedule(row.schedule_id);
        trigger.title = rowPlaying
          ? "Announcement is on the air"
          : rowPending
            ? rowPending.phase === "clear_window"
              ? "Waiting for clear-channel hold before transmit"
              : "Deferred — waiting for channel to clear"
            : "";
        trigger.dataset.scheduleId = row.schedule_id;
        trigger.disabled = announcementIsPlaying();
        trigger.addEventListener("click", async (event) => {
          event.stopPropagation();
          if (announcementIsPlaying()) return;
          triggerNowBusy = true;
          syncTriggerNowButtons();
          const controller = new AbortController();
          const timeoutId = setTimeout(
            () => controller.abort(),
            manualTriggerFetchTimeoutMs(),
          );
          try {
            const res = await fetch(`/api/schedules/${row.schedule_id}/trigger`, {
              method: "POST",
              signal: controller.signal,
            });
            let payload = {};
            try {
              payload = await res.json();
            } catch {
              /* ignore */
            }
            if (!res.ok) {
              const detail = payload.detail || res.statusText;
              window.alert(typeof detail === "string" ? detail : "Could not trigger");
              if (res.status === 409) await refreshHome().catch(() => {});
            } else if (payload.result === "deferred") {
              await refreshHome().catch(() => {});
            }
          } catch (err) {
            if (err?.name === "AbortError") {
              window.alert("Trigger timed out. If buttons stay disabled, refresh the page.");
            } else {
              window.alert("Could not trigger");
            }
          } finally {
            clearTimeout(timeoutId);
            triggerNowBusy = false;
            await refreshHome().catch(() => {});
            syncTriggerNowButtons();
          }
        });
        actions.prepend(trigger);
      }
    }
    tr.addEventListener("click", () => goToHash(`#/schedules/${row.schedule_id}`));
    body.append(tr);
  }
}

function newslineCheckActive(newsline) {
  if (!newsline) return false;
  if (newsline.in_progress) return true;
  return newsline.phase === "checking" || newsline.phase === "downloading" || newsline.phase === "splitting";
}

function syncNewslineCheckButton() {
  const button = document.getElementById("newsline-check");
  if (!button) return;
  const checking = newslineCheckActive(lastNewsline);
  const playing = newslinePlaying || Boolean(lastNewsline?.playing);
  button.disabled = readOnly || checking || playing;
  if (readOnly) {
    button.title = "";
  } else if (playing) {
    button.title = "Unavailable while Newsline is on the air";
  } else if (checking) {
    button.title = "A Newsline check is already running";
  } else {
    button.title = "";
  }
}

function syncNewslinePoll(newsline) {
  if (newslineCheckActive(newsline) || newslinePlaying || newsline?.playing) {
    if (!newslinePollTimer) {
      newslinePollTimer = setInterval(
        () => refreshHome({ background: true }).catch(() => {}),
        NEWSLINE_PROGRESS_POLL_MS,
      );
    }
    return;
  }
  if (newslinePollTimer) {
    clearInterval(newslinePollTimer);
    newslinePollTimer = null;
  }
}

function elapsedSecondsSince(iso) {
  const started = Date.parse(iso);
  if (Number.isNaN(started)) return null;
  return Math.max(0, (Date.now() + clockSkewMs - started) / 1000);
}

function newslineElapsedSuffix(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "";
  return ` ${formatSegmentElapsed(seconds)}`;
}

function formatReleaseFriday(isoDate) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(isoDate || ""));
  if (!match) return "";
  const dt = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  if (Number.isNaN(dt.getTime())) return "";
  const month = dt.toLocaleString("en-US", { month: "short" });
  return `${month} ${dt.getDate()}, ${dt.getFullYear()}`;
}

function formatPublished(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(String(iso || ""));
  if (!match) return "";
  const shown = formatReleaseFriday(`${match[1]}-${match[2]}-${match[3]}`);
  if (!shown || !match[4]) return shown;
  return `${shown}, ${match[4]}:${match[5]}`;
}

function newslineFacts(lead, newsline) {
  const lines = Array.isArray(lead) ? lead.filter(Boolean) : [];
  if (newsline) {
    const dates = [];
    const published = formatPublished(newsline.published_on);
    if (published) dates.push(`Published ${published}.`);
    const airDate = formatReleaseFriday(newsline.release_friday);
    if (airDate) dates.push(`Air date ${airDate}.`);
    if (dates.length) {
      const sentence = dates.join(" ");
      if (lines.length) lines[lines.length - 1] = `${lines[lines.length - 1]} ${sentence}`;
      else lines.push(sentence);
    }
    if (newsline.last_check_at) lines.push(`Last checked ${formatWhen(newsline.last_check_at)}.`);
  }
  return lines.join("\n");
}

/** @param {NewslineStatus|null|undefined} newsline */
function renderNewsline(newsline) {
  const status = document.getElementById("newsline-status");
  if (!status) return;
  lastNewsline = newsline;
  if (newsline && typeof newsline.playing === "boolean") {
    newslinePlaying = newsline.playing;
  }
  syncNewslinePoll(newsline);
  syncNewslineCheckButton();
  if (!newsline) {
    status.textContent = "No report downloaded yet.";
    return;
  }
  const working = newsline.working_report;
  const elapsed = newslineElapsedSuffix(newsline.elapsed_seconds);
  if (newsline.phase === "downloading") {
    const head = working
      ? `Downloading Newsline report ${working}…`
      : "Downloading the Newsline report…";
    status.textContent = `${head}${elapsed}`;
    return;
  }
  if (newsline.phase === "splitting") {
    const head = working
      ? `Processing Newsline report ${working}…`
      : "Processing the Newsline report…";
    const step = newsline.step ? ` ${newsline.step}` : "";
    status.textContent = `${head}${step}${elapsed}`;
    return;
  }
  if (newsline.phase === "checking" || (newsline.in_progress && newsline.phase !== "idle")) {
    status.textContent = `Checking for a new Newsline report…${elapsed}`;
    return;
  }
  if (newsline.error) {
    const detail = String(newsline.error).trim();
    const stopped = /[.!?]$/.test(detail) ? detail : `${detail}.`;
    status.textContent = newslineFacts([`Last check failed: ${stopped}`], newsline);
    return;
  }
  const unchanged = newsline.outcome === "current";
  if (newsline.ready && newsline.report_number) {
    const lead = [`Report ${newsline.report_number} ready.`];
    if (unchanged) lead.unshift("No new Newsline report.");
    status.textContent = newslineFacts(lead, newsline);
    return;
  }
  if (newsline.report_number) {
    const lead = [`Report ${newsline.report_number} is downloaded but not split yet.`];
    if (unchanged) lead.unshift("No new Newsline report.");
    status.textContent = newslineFacts(lead, newsline);
    return;
  }
  status.textContent = newslineFacts(["No report downloaded yet."], newsline);
}

function spokenAnnouncements() {
  return announcements.filter((item) => (item.kind || "tts") === "tts" && !item.too_long);
}

function fillSpokenSelect(id, selected, { optional = false } = {}) {
  const el = document.getElementById(id);
  if (!el) return;
  el.replaceChildren();
  if (optional) {
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "None";
    el.append(none);
  }
  for (const item of spokenAnnouncements()) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    el.append(option);
  }
  if (selected) {
    const current = announcements.find((item) => item.id === selected);
    if (current && ![...el.options].some((option) => option.value === selected)) {
      const option = document.createElement("option");
      option.value = current.id;
      option.textContent = current.too_long ? `${current.name} (too long)` : current.name;
      el.append(option);
    }
    el.value = selected;
  }
}

function syncAnnouncementKind() {
  const kind = document.getElementById("announcement-kind").value;
  document.getElementById("newsline-fields").classList.toggle("hidden", kind !== "newsline");
  document.getElementById("spoken-fields").classList.toggle("hidden", kind === "newsline");
}

/** @param {AnnouncementItem|null|undefined} item */
function showLengthWarning(item) {
  const box = document.getElementById("length-warning");
  if (!box) return;
  if (!item || !item.too_long) {
    box.textContent = "";
    box.classList.add("hidden");
    return;
  }
  const limit = Math.max(0, Number(settings.repeater_timeout_seconds ?? 180) - 2);
  box.textContent = `This announcement is ${Number(item.play_seconds).toFixed(1)} seconds, longer than the ${limit} second limit. Shorten it and save again.`;
  box.classList.remove("hidden");
}

/** @param {Array.<AnnouncementItem>} items */
function renderLibrary(items) {
  const body = document.getElementById("library-rows");
  const empty = document.getElementById("library-empty");
  body.replaceChildren();
  empty.classList.toggle("hidden", items.length > 0);
  for (const item of items) {
    const tr = document.createElement("tr");
    const preview = item.kind === "newsline"
      ? "Amateur Radio Newsline"
      : (item.text || "").replace(/\s+/g, " ").slice(0, 90);
    if (item.too_long) tr.classList.add("has-warning");
    const lengthLabel =
      item.kind !== "newsline" && item.play_seconds != null && Number.isFinite(Number(item.play_seconds))
        ? formatSegmentElapsed(item.play_seconds)
        : "—";
    tr.innerHTML = `
      <td>${escapeHtml(item.name)}</td>
      <td>${escapeHtml(preview)}${item.kind !== "newsline" && preview.length === 90 ? "…" : ""}${
        item.too_long ? `<div class="exclusion-line">Too long (${Number(item.play_seconds).toFixed(1)}s). Shorten it and save again.</div>` : ""
      }</td>
      <td>${escapeHtml(lengthLabel)}</td>
      <td class="row-actions"></td>
    `;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "Delete";
    del.dataset.deleteButton = "1";
    del.disabled = announcementIsPlaying();
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (announcementIsPlaying()) return;
      const usedBy = schedulesUsingAnnouncement(item.id);
      if (usedBy.length > 0) {
        const names = usedBy.map((row) => row.name).join(", ");
        window.alert(
          `This announcement cannot be deleted because it is in use.\n\nSchedules: ${names}`,
        );
        return;
      }
      const label = item.name ? `the announcement "${item.name}"` : "this announcement";
      if (!window.confirm(`Are you sure that you want to delete ${label}?`)) return;
      const res = await fetch(`/api/announcements/${item.id}`, { method: "DELETE" });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          detail = (await res.json()).detail || detail;
        } catch {
          /* ignore */
        }
        window.alert(typeof detail === "string" ? detail : "Could not delete");
        return;
      }
      refreshHome().catch(() => {});
    });
    if (!readOnly) tr.querySelector(".row-actions").append(del);
    tr.addEventListener("click", () => goToHash(`#/announcements/${item.id}`));
    body.append(tr);
  }
}

function weekdayFromDateStamp(stamp) {
  const [year, month, day] = String(stamp || "").split("-").map(Number);
  if (!year || !month || !day) return "";
  return new Date(year, month - 1, day).toLocaleDateString("en-US", { weekday: "long" });
}

function updateClockWeekday(clockDate) {
  const el = document.getElementById("clock-weekday");
  if (el) el.textContent = weekdayFromDateStamp(clockDate);
}

function applyHomeLiveStatus(payload) {
  if (payload.now) {
    const serverNow = Date.parse(payload.now);
    if (!Number.isNaN(serverNow)) clockSkewMs = serverNow - Date.now();
  }
  const fetchedAt = payload.now ? Date.parse(payload.now) : Date.now() + clockSkewMs;
  const fetchedAtMs = Number.isNaN(fetchedAt) ? Date.now() + clockSkewMs : fetchedAt;
  upcoming = payload.upcoming
    ? /** @type {HomeUpcoming} */ ({ ...payload.upcoming, fetchedAt: fetchedAtMs })
    : null;
  if (typeof payload.newsline_playing === "boolean") {
    newslinePlaying = payload.newsline_playing;
  }
  pendingManualTriggers = (payload.pending_manual_triggers || []).map((item) => ({
    ...item,
    fetchedAt: fetchedAtMs,
  }));
  renderUpcoming();
  renderManualTriggerDropNotice(payload.manual_trigger_drop_notice || null);
  renderSkipNotice(payload.skip_notice || null);
  syncNewslineCheckButton();
  syncNewslinePoll(lastNewsline);
  syncTriggerNowButtons();
}

async function refreshHome({ background = false } = {}) {
  if (homeBusy) return;
  homeBusy = true;
  try {
    const dateField = document.getElementById("clock-date");
    const dateQuery = dateField.value ? `?clock_date=${dateField.value}` : "";
    const opts = background ? { headers: BACKGROUND_HEADERS } : {};
    const [schedRes, libRes] = await Promise.all([
      fetch(`/api/schedules${dateQuery}`, opts),
      fetch("/api/announcements", opts),
    ]);
    if (!schedRes.ok) throw new Error("Could not load schedules");
    const payload = await schedRes.json();
    const library = libRes.ok ? await libRes.json() : { announcements: [] };
    announcements = library.announcements || [];
    schedules = payload.schedules || [];
    settings = payload.settings || settings;
    triggerNowEnabled = Boolean(payload.trigger_now_enabled);
    renderNewsline(payload.newsline || null);
    applyHomeLiveStatus(payload);
    const warn = document.getElementById("home-warnings");
    const notes = payload.warnings || [];
    warn.classList.toggle("hidden", notes.length === 0);
    warn.textContent = notes.join(" ");
    if (payload.clock_date && !dateField.value) dateField.value = payload.clock_date;
    updateClockWeekday(dateField.value || payload.clock_date);
    renderClock(payload.clock || []);
    renderSchedules(payload.schedules || []);
    renderLibrary(announcements);
    document.getElementById("baseline-randomize").checked = Boolean(settings.baseline_randomize);
    const scheduleEl = document.getElementById("newsline-schedule");
    if (scheduleEl) scheduleEl.textContent = newslineScheduleText(settings);
  } finally {
    homeBusy = false;
  }
}

function parseSseBlock(block) {
  let eventName = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    return { event: eventName, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

function takeSseEvents(buffer) {
  const events = [];
  let splitAt = buffer.indexOf("\n\n");
  while (splitAt >= 0) {
    const block = buffer.slice(0, splitAt);
    buffer = buffer.slice(splitAt + 2);
    splitAt = buffer.indexOf("\n\n");
    const parsed = parseSseBlock(block);
    if (parsed) events.push(parsed);
  }
  return { buffer, events };
}

async function streamHomeLiveEvents(signal) {
  const res = await fetch("/api/events/home", {
    headers: BACKGROUND_HEADERS,
    credentials: "same-origin",
    signal,
  });
  if (!res.ok) throw new Error("Could not open home event stream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  homeSseConnected = true;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const homeEvents = takeSseEvents(buffer);
      buffer = homeEvents.buffer;
      for (const parsed of homeEvents.events) {
        if (parsed.event === "data_changed") {
          refreshHome({ background: true }).catch(() => {});
        } else if (parsed.event === "snapshot" || parsed.event === "status") {
          applyHomeLiveStatus(parsed.data);
        }
      }
    }
  } finally {
    homeSseConnected = false;
    reader.releaseLock();
  }
}

async function runHomeSseLoop() {
  let backoffMs = 1000;
  while (homeSseWanted) {
    const controller = new AbortController();
    homeSseAbort = controller;
    try {
      await streamHomeLiveEvents(controller.signal);
      backoffMs = 1000;
    } catch (err) {
      if (!homeSseWanted || err?.name === "AbortError") break;
      await new Promise((resolve) => setTimeout(resolve, backoffMs));
      backoffMs = Math.min(backoffMs * 2, 30000);
    } finally {
      if (homeSseAbort === controller) homeSseAbort = null;
    }
  }
  homeSseConnected = false;
}

function stopHomeSse() {
  homeSseWanted = false;
  if (homeSseAbort) {
    homeSseAbort.abort();
    homeSseAbort = null;
  }
  homeSseConnected = false;
}

async function refreshSystemStats() {
  if (statsBusy || !currentUser) return;
  statsBusy = true;
  try {
    const res = await fetch("/api/system/stats", { headers: BACKGROUND_HEADERS });
    if (res.ok) {
      renderSystemStats(await res.json());
    } else {
      renderSystemStats({
        celsius: null,
        thermal_state: null,
        cpu_percent: null,
        memory_percent: null,
        memory_used_bytes: null,
        memory_available_bytes: null,
      });
    }
  } finally {
    statsBusy = false;
  }
}

async function streamSystemStatsEvents(signal) {
  const res = await fetch("/api/events/stats", {
    headers: BACKGROUND_HEADERS,
    credentials: "same-origin",
    signal,
  });
  if (!res.ok) throw new Error("Could not open system stats event stream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  statsSseConnected = true;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const statsEvents = takeSseEvents(buffer);
      buffer = statsEvents.buffer;
      for (const parsed of statsEvents.events) {
        if (parsed.event === "snapshot" || parsed.event === "stats") {
          renderSystemStats(parsed.data);
        }
      }
    }
  } finally {
    statsSseConnected = false;
    reader.releaseLock();
  }
}

async function runStatsSseLoop() {
  let backoffMs = 1000;
  while (statsSseWanted) {
    const controller = new AbortController();
    statsSseAbort = controller;
    try {
      await streamSystemStatsEvents(controller.signal);
      backoffMs = 1000;
    } catch (err) {
      if (!statsSseWanted || err?.name === "AbortError") break;
      await new Promise((resolve) => setTimeout(resolve, backoffMs));
      backoffMs = Math.min(backoffMs * 2, 30000);
    } finally {
      if (statsSseAbort === controller) statsSseAbort = null;
    }
  }
  statsSseConnected = false;
}

function stopStatsSse() {
  statsSseWanted = false;
  if (statsSseAbort) {
    statsSseAbort.abort();
    statsSseAbort = null;
  }
  statsSseConnected = false;
}

function celsiusThermalText(celsius) {
  const rounded = Math.round(Number(celsius));
  let label = "Critical";
  let band = "hot";
  if (rounded < 55) {
    label = "Nominal";
    band = "cool";
  } else if (rounded < 70) {
    label = "Fair";
    band = "warm";
  }
  return { text: `${label} (${rounded}°C)`, band };
}

function formatMemoryAmount(bytes) {
  if (bytes == null || bytes === "") return null;
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return null;
  const gb = 1024 * 1024 * 1024;
  if (value >= gb) return `${(Math.round((value / gb) * 10) / 10).toFixed(1)} GB`;
  return `${Math.round(value / (1024 * 1024))} MB`;
}

function formatMemorySlot(percent, usedBytes, availableBytes) {
  const hasPercent = percent != null && !Number.isNaN(Number(percent));
  const parts = [];
  if (hasPercent) parts.push(`${Math.round(Number(percent))}%`);
  const used = formatMemoryAmount(usedBytes);
  const available = formatMemoryAmount(availableBytes);
  if (used) parts.push(`${used} used`);
  if (available) parts.push(`${available} available`);
  if (!hasPercent) parts.unshift("—");
  if (parts.length === 1 && parts[0] === "—") return "—";
  return parts.join(" · ");
}

function renderSystemStats(payload) {
  const chip = document.getElementById("system-stats");
  const tempEl = document.getElementById("system-stats-temp");
  const cpuEl = document.getElementById("system-stats-cpu");
  const memEl = document.getElementById("system-stats-mem");
  if (!chip || !tempEl || !cpuEl || !memEl) return;

  const thermalState =
    payload && typeof payload.thermal_state === "string" ? payload.thermal_state : null;
  const celsius = payload && payload.celsius != null ? Number(payload.celsius) : null;
  const cpuPercent = payload && payload.cpu_percent != null ? Number(payload.cpu_percent) : null;
  const memoryPercent =
    payload && payload.memory_percent != null ? Number(payload.memory_percent) : null;
  const memoryUsed =
    payload && payload.memory_used_bytes != null ? payload.memory_used_bytes : null;
  const memoryAvailable =
    payload && payload.memory_available_bytes != null ? payload.memory_available_bytes : null;

  const thermalLabels = {
    nominal: "Nominal",
    fair: "Fair",
    serious: "Serious",
    critical: "Critical",
  };
  let band = "na";
  let tempText = "—";
  if (thermalState && thermalLabels[thermalState]) {
    tempText = thermalLabels[thermalState];
    if (thermalState === "nominal") band = "cool";
    else if (thermalState === "fair") band = "warm";
    else if (thermalState === "serious") band = "serious";
    else if (thermalState === "critical") band = "hot";
  } else if (celsius != null && !Number.isNaN(celsius)) {
    const thermal = celsiusThermalText(celsius);
    tempText = thermal.text;
    band = thermal.band;
  }

  chip.classList.remove("temp-cool", "temp-warm", "temp-serious", "temp-hot", "temp-na");
  chip.classList.add(`temp-${band}`);
  tempEl.textContent = tempText;
  cpuEl.textContent =
    cpuPercent != null && !Number.isNaN(cpuPercent) ? `${Math.round(cpuPercent)}%` : "—";
  memEl.textContent = formatMemorySlot(memoryPercent, memoryUsed, memoryAvailable);

  const parts = [
    thermalState ? `Thermal ${tempEl.textContent}` : `CPU temp ${tempEl.textContent}`,
    `CPU ${cpuEl.textContent}`,
    `MEM ${memEl.textContent}`,
  ];
  chip.title = parts.join(" · ");
}

function startHomePolling() {
  if (!pollTimer)
    pollTimer = setInterval(
      () => refreshHome({ background: true }).catch(() => {}),
      HOME_REST_POLL_MS,
    );
  if (!countdownTimer) countdownTimer = setInterval(tickUpcoming, COUNTDOWN_TICK_MS);
  if (!homeSseWanted) {
    homeSseWanted = true;
    runHomeSseLoop().catch(() => {});
  }
}

function stopHomePolling() {
  stopHomeSse();
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (newslinePollTimer) {
    clearInterval(newslinePollTimer);
    newslinePollTimer = null;
  }
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function startStatsPolling() {
  if (!statsTimer) {
    statsTimer = setInterval(() => {
      if (!statsSseConnected) refreshSystemStats().catch(() => {});
    }, STATS_REST_POLL_MS);
  }
  if (!statsSseWanted) {
    statsSseWanted = true;
    runStatsSseLoop().catch(() => {});
  }
}

function stopStatsPolling() {
  stopStatsSse();
  if (statsTimer) {
    clearInterval(statsTimer);
    statsTimer = null;
  }
}

function updateCount() {
  const used = textEl.value.length;
  const max = textEl.maxLength;
  document.getElementById("count").textContent = max > 0 ? `${used} / ${max}` : String(used);
}

function updateDeliveryLabels() {
  document.getElementById("speed-value").textContent = `${Number(speedEl.value).toFixed(2)}×`;
  document.getElementById("pause-value").textContent = `${Number(pauseEl.value).toFixed(2)}s`;
}

function setStatus(text, kind = "") {
  for (const id of ["status", "newsline-play-status"]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.textContent = text;
    el.className = kind;
  }
}

function setPlaybackButtons(playing) {
  document.getElementById("speak").disabled = playing;
  document.getElementById("stop").disabled = !playing;
  const play = document.getElementById("newsline-play");
  const stopNews = document.getElementById("newsline-stop");
  if (play) play.disabled = playing;
  if (stopNews) stopNews.disabled = !playing;
}

function setEditorPlaying(playing) {
  editorPlaying = playing;
  applyEditorReadOnly();
}

function showError(id, text) {
  const el = document.getElementById(id);
  el.hidden = !text;
  el.textContent = text || "";
}

function persist() {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      voice: voiceEl.value,
      speed: Number(speedEl.value),
      sentence_pause: Number(pauseEl.value),
    }),
  );
}

function readPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
  } catch {
    return {};
  }
}

function fillVoiceSelect(select, preferred) {
  const selected = preferred || catalog.default_voice;
  select.replaceChildren();
  for (const voice of catalog.voices) {
    const option = document.createElement("option");
    option.value = voice.id;
    const bits = [voice.name, voice.gender, voice.locale, voice.quality].filter(Boolean);
    option.textContent = bits.join(" · ");
    option.selected = voice.id === selected;
    select.append(option);
  }
}

function renderVoices(preferred) {
  fillVoiceSelect(voiceEl, preferred);
  const phonemeVoice = document.getElementById("phoneme-voice");
  if (phonemeVoice) fillVoiceSelect(phonemeVoice, phonemeVoice.value || catalog.default_voice);
}

function applyBrandingFromCatalog() {
  if (!catalog) return;
  const title = catalog.app_title || "Announcements";
  document.title = title;
  const appTitle = document.getElementById("app-title-heading");
  if (appTitle) appTitle.textContent = title;
  const stationHeading = document.getElementById("station-heading");
  if (stationHeading && catalog.station_name) stationHeading.textContent = catalog.station_name;
  const logo = document.getElementById("station-logo");
  if (logo && catalog.station_name) logo.alt = catalog.station_name;
  const maxChars = catalog.max_text_chars;
  if (maxChars != null && textEl) {
    textEl.maxLength = maxChars;
    updateCount();
  }
}

async function loadVoices() {
  const res = await fetch("/api/voices");
  if (!res.ok) throw new Error("Could not load voices");
  catalog = await res.json();
  applyBrandingFromCatalog();
  const saved = readPrefs();
  speedEl.value = String(saved.speed ?? catalog.speed ?? 1);
  pauseEl.value = String(saved.sentence_pause ?? catalog.sentence_pause ?? 0.25);
  updateDeliveryLabels();
  renderVoices(saved.voice);
}

async function ensureVoices() {
  if (catalog) return;
  await loadVoices();
}

async function loadDefaultScript() {
  const res = await fetch("/static/default-announcement.txt");
  if (!res.ok) return;
  defaultScript = (await res.text()).trim();
}

function newAnnouncementDefaults() {
  return {
    voice: catalog?.default_voice ?? null,
    speed: catalog?.speed ?? 1,
    sentence_pause: catalog?.sentence_pause ?? 0.25,
    busy_retry_seconds: catalog?.busy_retry_seconds ?? 5,
    busy_give_up_seconds: catalog?.busy_give_up_seconds ?? 45,
  };
}

function manualTriggerFetchTimeoutMs() {
  const maxSec = catalog?.max_busy_give_up_seconds;
  if (maxSec != null && maxSec > 0) return maxSec * 1000;
  return MANUAL_TRIGGER_FETCH_TIMEOUT_MS;
}

function blankAnnouncement() {
  const defaults = newAnnouncementDefaults();
  editingAnnouncementId = null;
  document.getElementById("announcement-title").textContent = "New announcement";
  document.getElementById("announcement-kind").value = "tts";
  nameEl.value = "";
  textEl.value = defaultScript;
  document.getElementById("use-phoneme-dictionary").checked = true;
  schedulePhonemeMatches();
  fillSpokenSelect("news-opening", "", { optional: true });
  fillSpokenSelect("news-station", spokenAnnouncements()[0]?.id || "");
  fillSpokenSelect("news-closing", "", { optional: true });
  fillSpokenSelect("news-resume", "", { optional: true });
  document.getElementById("news-gap").value = "2";
  showLengthWarning(null);
  syncAnnouncementKind();
  speedEl.value = String(defaults.speed);
  pauseEl.value = String(defaults.sentence_pause);
  document.getElementById("busy-retry").value = String(defaults.busy_retry_seconds);
  document.getElementById("busy-giveup").value = String(defaults.busy_give_up_seconds);
  if (catalog) renderVoices(defaults.voice);
  updateCount();
  updateDeliveryLabels();
  editorPlaySeconds = null;
  showKnownPlayLength();
}

async function loadAnnouncement(id) {
  const res = await fetch(`/api/announcements/${id}`);
  if (!res.ok) throw new Error("Announcement not found");
  const item = await res.json();
  editingAnnouncementId = item.id;
  document.getElementById("announcement-title").textContent = readOnly
    ? "View announcement"
    : "Edit announcement";
  document.getElementById("announcement-kind").value = item.kind || "tts";
  nameEl.value = item.name || "";
  textEl.value = item.text || "";
  document.getElementById("use-phoneme-dictionary").checked = item.use_phoneme_dictionary !== false;
  schedulePhonemeMatches();
  fillSpokenSelect("news-opening", item.opening_announcement_id || "", { optional: true });
  fillSpokenSelect("news-station", item.station_id_announcement_id || "");
  fillSpokenSelect("news-closing", item.closing_announcement_id || "", { optional: true });
  fillSpokenSelect("news-resume", item.resume_announcement_id || "", { optional: true });
  document.getElementById("news-gap").value = String(item.ptt_gap_seconds ?? 2);
  showLengthWarning(item);
  syncAnnouncementKind();
  document.getElementById("busy-retry").value = String(item.busy_retry_seconds ?? 5);
  document.getElementById("busy-giveup").value = String(item.busy_give_up_seconds ?? 45);
  speedEl.value = String(item.speed ?? 1);
  pauseEl.value = String(item.sentence_pause ?? 0.25);
  if (item.voice) renderVoices(item.voice);
  updateCount();
  updateDeliveryLabels();
  const savedSeconds = Number(item.play_seconds);
  editorPlaySeconds =
    item.kind !== "newsline" && Number.isFinite(savedSeconds) && savedSeconds > 0
      ? savedSeconds
      : null;
  showKnownPlayLength();
}

async function saveAnnouncement() {
  showError("announcement-error", "");
  const kind = document.getElementById("announcement-kind").value;
  const body = {
    name: nameEl.value.trim(),
    kind,
    text: kind === "newsline" ? "Newsline" : textEl.value.trim(),
    voice: voiceEl.value,
    speed: Number(speedEl.value),
    sentence_pause: Number(pauseEl.value),
    use_phoneme_dictionary: document.getElementById("use-phoneme-dictionary").checked,
    busy_retry_seconds: Number(document.getElementById("busy-retry").value),
    busy_give_up_seconds: Number(document.getElementById("busy-giveup").value),
    opening_announcement_id: document.getElementById("news-opening").value || null,
    station_id_announcement_id: document.getElementById("news-station").value || null,
    closing_announcement_id: document.getElementById("news-closing").value || null,
    resume_announcement_id: document.getElementById("news-resume").value || null,
    ptt_gap_seconds: Number(document.getElementById("news-gap").value),
  };
  if (!body.name) {
    showError("announcement-error", "Name is required.");
    return;
  }
  if (kind === "tts" && !textEl.value.trim()) {
    showError("announcement-error", "Announcement text is required.");
    return;
  }
  if (kind === "newsline" && !body.station_id_announcement_id) {
    showError("announcement-error", "Choose a station ID announcement.");
    return;
  }
  persist();
  const isNew = !editingAnnouncementId;
  const res = await fetch(isNew ? "/api/announcements" : `/api/announcements/${editingAnnouncementId}`, {
    method: isNew ? "POST" : "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    showError("announcement-error", await errorDetail(res));
    return;
  }
  /** @type {AnnouncementItem} */
  const saved = await res.json();
  if (saved.too_long) {
    showLengthWarning(saved);
    showError(
      "announcement-error",
      `Saved, but it is too long to play (${Number(saved.play_seconds).toFixed(1)}s). Shorten it and save again.`,
    );
    return;
  }
  clearEditorGuard();
  goToHash("#/");
}

async function saveSchedule() {
  showError("schedule-error", "");
  const body = readSchedulePayload();
  if (!body.name) {
    showError("schedule-error", "Name is required.");
    return;
  }
  if (document.getElementById("sched-announcement").value === "__pick__") {
    showError("schedule-error", "Pick an announcement.");
    return;
  }
  if (body.kind === "baseline" && !body.announcement_id) {
    showError("schedule-error", "Pick an announcement for the baseline.");
    return;
  }
  const isNew = !editingScheduleId;
  const res = await fetch(isNew ? "/api/schedules" : `/api/schedules/${editingScheduleId}`, {
    method: isNew ? "POST" : "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    showError("schedule-error", await errorDetail(res));
    return;
  }
  clearEditorGuard();
  goToHash("#/");
}

async function errorDetail(res) {
  try {
    const payload = await res.json();
    const detail = payload.detail;
    if (typeof detail === "string") return detail;
  } catch {
    /* ignore */
  }
  return res.statusText || "Could not save";
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
  setEditorPlaying(true);
  setStatus("Preparing voice…");
  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice: voiceEl.value,
        speed: Number(speedEl.value),
        sentence_pause: Number(pauseEl.value),
        use_phoneme_dictionary: document.getElementById("use-phoneme-dictionary").checked,
      }),
      signal: controller.signal,
    });
    if (mySession !== session) return;
    if (!res.ok) {
      const detail = await errorDetail(res);
      if (mySession !== session) return;
      if (!controller.signal.aborted) {
        setStatus(detail || "Playback failed", "error");
        setEditorPlaying(false);
      }
      return;
    }
    const sampleRate = Number(res.headers.get("X-Sample-Rate") || "22050");
    const totalSeconds = Number(res.headers.get("X-Play-Seconds") || "0");
    const knownTotal = Number.isFinite(totalSeconds) && totalSeconds > 0 ? totalSeconds : 0;
    if (knownTotal > 0) editorPlaySeconds = knownTotal;
    await currentPlayer.play(res.body, sampleRate, controller.signal, {
      totalSeconds: knownTotal,
    });
  } catch (err) {
    if (mySession !== session) return;
    if (err.name !== "AbortError" && !controller.signal.aborted) {
      setStatus(err.message || "Playback failed", "error");
      setEditorPlaying(false);
      return;
    }
  } finally {
    if (mySession === session) setEditorPlaying(false);
  }
  if (mySession !== session) return;
  showKnownPlayLength();
}

function pttLeadSeconds() {
  const raw = Number(catalog?.ptt_lead_seconds);
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  return raw;
}

function newslineGapSeconds() {
  const raw = Number(document.getElementById("news-gap")?.value);
  if (!Number.isFinite(raw)) return 0;
  return Math.min(30, Math.max(0, raw));
}

async function abortableWait(seconds, signal, ctx) {
  if (!(seconds > 0)) return;
  if (signal.aborted) throw new DOMException("Stopped", "AbortError");
  if (ctx && ctx.state === "running") await ctx.suspend();
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, seconds * 1000);
      const onAbort = () => {
        clearTimeout(timer);
        reject(new DOMException("Stopped", "AbortError"));
      };
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    });
  } finally {
    if (ctx && ctx.state === "suspended" && !signal.aborted) await ctx.resume();
  }
}

function uint32be(bytes) {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0);
}

function segmentDisplayName(name) {
  const raw = String(name || "").trim();
  return (raw || "Segment").replace(/\.mp3$/i, "");
}

function formatSegmentElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function idlePlayStatus(seconds) {
  return `Length ${formatSegmentElapsed(seconds)}`;
}

function playingStatus(elapsedSeconds, totalSeconds) {
  return `Playing… ${formatSegmentElapsed(elapsedSeconds)} / ${formatSegmentElapsed(totalSeconds)}`;
}

function showKnownPlayLength() {
  if (editorPlaying) return;
  if (editorPlaySeconds != null && Number.isFinite(editorPlaySeconds) && editorPlaySeconds > 0) {
    setStatus(idlePlayStatus(editorPlaySeconds));
    return;
  }
  setStatus("Ready");
}

function clearKnownPlayLength() {
  editorPlaySeconds = null;
  if (!editorPlaying) setStatus("Ready");
}

function streamingPieceStatus(name, elapsedSeconds) {
  return `Streaming… ${segmentDisplayName(name)} ${formatSegmentElapsed(elapsedSeconds)}`;
}

async function readExact(reader, leftover, size) {
  let buffer = leftover;
  while (buffer.length < size) {
    const { done, value } = await reader.read();
    if (done) {
      if (!buffer.length) return { bytes: null, leftover: buffer };
      throw new Error("Newsline preview ended in the middle of a piece");
    }
    const combined = new Uint8Array(buffer.length + value.length);
    combined.set(buffer);
    combined.set(value, buffer.length);
    buffer = combined;
  }
  return { bytes: buffer.slice(0, size), leftover: buffer.slice(size) };
}

async function playNewsline() {
  showError("announcement-error", "");
  cancelInFlight();
  const mySession = session;
  const controller = new AbortController();
  abort = controller;
  const currentPlayer = new PcmPlayer();
  player = currentPlayer;
  const leadSeconds = pttLeadSeconds();
  const gapSeconds = newslineGapSeconds();
  setEditorPlaying(true);
  setStatus("Preparing Newsline…");
  try {
    const res = await fetch("/api/newsline/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        opening_announcement_id: document.getElementById("news-opening").value || null,
        station_id_announcement_id: document.getElementById("news-station").value || null,
        closing_announcement_id: document.getElementById("news-closing").value || null,
        resume_announcement_id: document.getElementById("news-resume").value || null,
      }),
      signal: controller.signal,
    });
    if (mySession !== session) return;
    if (!res.ok) {
      const detail = await errorDetail(res);
      if (res.status === 400) {
        showError("announcement-error", detail);
        setStatus(detail, "error");
        return;
      }
      if (mySession !== session) return;
      setStatus(detail || "Playback failed", "error");
      return;
    }
    const sampleRate = Number(res.headers.get("X-Sample-Rate") || "22050");
    setStatus("Streaming…", "playing");
    if (res.headers.get("X-Preview-Framing") !== "name32+length32") {
      if (mySession !== session) return;
      setStatus("Restart the app to play Newsline with PTT gaps.", "error");
      return;
    }
    await currentPlayer.playFramed(res.body, sampleRate, controller.signal, {
      leadSeconds,
      gapSeconds,
    });
    if (mySession !== session) return;
    if (!controller.signal.aborted) setStatus("Done");
  } catch (err) {
    if (mySession !== session) return;
    if (err.name === "AbortError" || controller.signal.aborted) setStatus("Stopped");
    else setStatus(err.message || "Playback failed", "error");
  } finally {
    if (mySession === session) setEditorPlaying(false);
  }
}

function stopEditorPlayback() {
  const hadPlayback = Boolean(abort || player || editorPlaying);
  if (!hadPlayback) return false;
  cancelInFlight();
  setEditorPlaying(false);
  showKnownPlayLength();
  return true;
}

function stop() {
  stopEditorPlayback();
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
    this._segmentClock = null;
  }

  async play(stream, sampleRate, signal, { totalSeconds = 0 } = {}) {
    if (!stream) return;
    this.ctx = new AudioContext({ sampleRate });
    const reader = stream.getReader();
    this.reader = reader;
    const onAbort = () => this.stop();
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) {
      onAbort();
      return;
    }
    this._startPlayClock(totalSeconds, signal);
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
      if (this.stopped || signal.aborted || err.name === "AbortError" || err.name === "InvalidStateError") return;
      throw err;
    } finally {
      this._stopSegmentClock();
      signal.removeEventListener("abort", onAbort);
      this._cancelReader();
    }
  }

  async playTone(hz, seconds, signal) {
    if (this.stopped || signal.aborted) return;
    if (!this.ctx) this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") await this.ctx.resume();
    if (this.stopped || signal.aborted) return;
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = hz;
    const now = this.ctx.currentTime;
    const dur = Math.max(0.02, seconds);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.75, now + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + dur);
    osc.connect(gain);
    gain.connect(this.ctx.destination);
    this.sources.push(osc);
    osc.start(now);
    osc.stop(now + dur + 0.01);
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        resolve();
      };
      const onAbort = () => {
        if (settled) return;
        settled = true;
        try {
          osc.stop();
        } catch {
          /* already stopped */
        }
        reject(new DOMException("Stopped", "AbortError"));
      };
      osc.onended = finish;
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  async playBuffer(bytes, sampleRate, signal) {
    if (!bytes || !bytes.byteLength || this.stopped || signal.aborted) return;
    if (!this.ctx) this.ctx = new AudioContext({ sampleRate });
    if (this.ctx.state === "suspended") await this.ctx.resume();
    if (this.stopped || signal.aborted) return;
    const usable = bytes.byteLength - (bytes.byteLength % 2);
    if (usable <= 0) return;
    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, usable / 2);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i += 1) float32[i] = int16[i] / 32768;
    const buffer = this.ctx.createBuffer(1, float32.length, sampleRate);
    buffer.copyToChannel(float32, 0);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);
    this.sources.push(source);
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        resolve();
      };
      const onAbort = () => {
        if (settled) return;
        settled = true;
        try {
          source.stop();
        } catch {
          /* already stopped */
        }
        reject(new DOMException("Stopped", "AbortError"));
      };
      source.onended = finish;
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
      source.start();
    });
  }

  async playFramed(stream, sampleRate, signal, { leadSeconds = 0, gapSeconds = 0 } = {}) {
    if (!stream) return;
    const reader = stream.getReader();
    this.reader = reader;
    const onAbort = () => this.stop();
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) {
      onAbort();
      return;
    }
    try {
      await this._playFramedLoop(reader, sampleRate, signal, leadSeconds, gapSeconds);
    } catch (err) {
      if (this.stopped || signal.aborted || err.name === "AbortError" || err.name === "InvalidStateError") return;
      throw err;
    } finally {
      this._stopSegmentClock();
      signal.removeEventListener("abort", onAbort);
      this._cancelReader();
    }
  }

  async _playFramedLoop(reader, sampleRate, signal, leadSeconds, gapSeconds) {
    if (!this.ctx) this.ctx = new AudioContext({ sampleRate });
    await this.ctx.resume();
    let leftover = new Uint8Array(0);
    const first = await readExact(reader, leftover, 4);
    leftover = first.leftover;
    let nextNameHeader = first.bytes;
    while (nextNameHeader && !this.stopped && !signal.aborted) {
      const nameLen = uint32be(nextNameHeader);
      if (nameLen > 4096) throw new Error("Newsline preview piece name is too long");
      const namePart = await readExact(reader, leftover, nameLen);
      leftover = namePart.leftover;
      if (!namePart.bytes) throw new Error("Newsline preview ended in the middle of a piece");
      const label = new TextDecoder().decode(namePart.bytes);
      const pcmHeader = await readExact(reader, leftover, 4);
      leftover = pcmHeader.leftover;
      if (!pcmHeader.bytes) throw new Error("Newsline preview ended in the middle of a piece");
      const pcmLen = uint32be(pcmHeader.bytes);
      const piece = await readExact(reader, leftover, pcmLen);
      leftover = piece.leftover;
      if (!piece.bytes) throw new Error("Newsline preview ended in the middle of a piece");
      await this.playTone(PTT_ENGAGE_HZ, PTT_TONE_SECONDS, signal);
      if (this.stopped || signal.aborted) return;
      if (leadSeconds > 0) setStatus("PTT lead…", "playing");
      await abortableWait(leadSeconds, signal, this.ctx);
      if (this.stopped || signal.aborted) return;
      this._startSegmentClock(label, signal);
      try {
        await this.playBuffer(piece.bytes, sampleRate, signal);
      } finally {
        this._stopSegmentClock();
      }
      if (this.stopped || signal.aborted) return;
      await this.playTone(PTT_RELEASE_HZ, PTT_TONE_SECONDS, signal);
      if (this.stopped || signal.aborted) return;
      const following = await readExact(reader, leftover, 4);
      leftover = following.leftover;
      nextNameHeader = following.bytes;
      if (nextNameHeader) {
        if (gapSeconds > 0) setStatus("Unkey gap…", "playing");
        await abortableWait(gapSeconds, signal, this.ctx);
      }
    }
  }

  _startPlayClock(totalSeconds, signal) {
    this._stopSegmentClock();
    const started = Date.now();
    const tick = () => {
      if (this.stopped || signal.aborted) {
        this._stopSegmentClock();
        return;
      }
      setStatus(playingStatus((Date.now() - started) / 1000, totalSeconds), "playing");
    };
    tick();
    this._segmentClock = setInterval(tick, 1000);
  }

  _startSegmentClock(name, signal) {
    this._stopSegmentClock();
    const started = Date.now();
    const tick = () => {
      if (this.stopped || signal.aborted) {
        this._stopSegmentClock();
        return;
      }
      setStatus(streamingPieceStatus(name, (Date.now() - started) / 1000), "playing");
    };
    tick();
    this._segmentClock = setInterval(tick, 1000);
  }

  _stopSegmentClock() {
    if (this._segmentClock == null) return;
    clearInterval(this._segmentClock);
    this._segmentClock = null;
  }

  stop() {
    this.stopped = true;
    this._stopSegmentClock();
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

function noteConfigGeneration(res) {
  const raw = res.headers.get("X-Config-Generation");
  if (raw == null || raw === "") return;
  const generation = Number(raw);
  if (!Number.isFinite(generation)) return;
  if (seenConfigGeneration == null) {
    seenConfigGeneration = generation;
    return;
  }
  if (generation > seenConfigGeneration) {
    seenConfigGeneration = generation;
    location.reload();
  }
}

function apiErrorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    const message = detail[0]?.msg;
    if (typeof message === "string" && message) {
      return message.replace(/^Value error,\s*/, "");
    }
  }
  return fallback;
}

function fillLogoDisplayForm() {
  const widthInput = document.getElementById("logo-display-width");
  const heightInput = document.getElementById("logo-display-height");
  const square = document.getElementById("logo-display-square");
  if (!widthInput || !heightInput || !square) return;
  widthInput.value = widthInput.getAttribute("value") || "";
  heightInput.value = heightInput.getAttribute("value") || "";
  square.checked = widthInput.value === heightInput.value;
  applyLogoSquareMode();
}

function showAdminSection(section) {
  for (const name of ["users", "environment", "branding"]) {
    document.getElementById(`admin-${name}`).classList.toggle("hidden", name !== section);
    document.getElementById(`admin-tab-${name}`).classList.toggle("is-active", name === section);
  }
  if (section === "branding") fillLogoDisplayForm();
}

function adminSectionFromKey(key) {
  const section = String(key || "").split("/")[1];
  if (section === "environment" || section === "branding") return section;
  return "users";
}

function formatSavedAt(iso, timeZone) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timeZone || undefined,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  } catch {
    return date.toLocaleString();
  }
}

function hhmmField(id) {
  const raw = document.getElementById(id).value.trim();
  const match = /^(\d{2}):(\d{2})/.exec(raw);
  return match ? `${match[1]}:${match[2]}` : raw;
}

function newslineScheduleText(settings) {
  const start = (settings && settings.news_check_start) || "12:00";
  const stop = (settings && settings.news_check_stop) || "18:00";
  const hours = Number(settings && settings.news_check_interval_hours);
  const shown = Number.isFinite(hours) ? String(hours) : "1";
  const unit = hours === 1 ? "hour" : "hours";
  const timeout = Number(settings && settings.repeater_timeout_seconds);
  const seconds = Number.isFinite(timeout) ? String(timeout) : "180";
  return `Checks for new release Thursday ${start} through Friday ${stop}, every ${shown} ${unit}.\nRepeater timeout ${seconds} seconds.`;
}

function readConfigForm() {
  return {
    station_call: document.getElementById("cfg-station-call").value.trim(),
    station_name: document.getElementById("cfg-station-name").value.trim(),
    app_title: document.getElementById("cfg-app-title").value.trim(),
    default_voice: document.getElementById("cfg-default-voice").value,
    speed: Number(document.getElementById("cfg-speed").value),
    sentence_pause: Number(document.getElementById("cfg-pause").value),
    ptt_lead_seconds: Number(document.getElementById("cfg-ptt-lead").value),
    busy_retry_seconds: Number(document.getElementById("cfg-busy-retry").value),
    busy_give_up_seconds: Number(document.getElementById("cfg-busy-give-up").value),
    max_text_chars: Number(document.getElementById("cfg-max-chars").value),
    piper_qualities: document.getElementById("cfg-qualities").value,
    timezone: document.getElementById("cfg-timezone").value.trim(),
    slot_lookahead_hours: Number(document.getElementById("cfg-lookahead").value),
    scheduler_max_wait_seconds: Number(document.getElementById("cfg-max-wait").value),
    session_idle_seconds: Number(document.getElementById("cfg-idle").value),
    login_max_failures: Number(document.getElementById("cfg-login-failures").value),
    login_window_seconds: Number(document.getElementById("cfg-login-window").value),
    trigger_now: document.getElementById("cfg-trigger-now").checked,
    fire_verbose_log: document.getElementById("cfg-fire-verbose").checked,
    news_check_start: hhmmField("cfg-news-start"),
    news_check_stop: hhmmField("cfg-news-stop"),
    news_check_interval_hours: Number(document.getElementById("cfg-news-interval").value),
    repeater_timeout_seconds: Number(document.getElementById("cfg-repeater-timeout").value),
  };
}

function fillConfigForm(values) {
  document.getElementById("cfg-station-call").value = values.station_call || "";
  document.getElementById("cfg-station-name").value = values.station_name || "";
  document.getElementById("cfg-app-title").value = values.app_title || "";
  document.getElementById("cfg-qualities").value = values.piper_qualities || "all";
  document.getElementById("cfg-speed").value = String(values.speed ?? "");
  document.getElementById("cfg-pause").value = String(values.sentence_pause ?? "");
  document.getElementById("cfg-ptt-lead").value = String(values.ptt_lead_seconds ?? "");
  document.getElementById("cfg-busy-retry").value = String(values.busy_retry_seconds ?? "");
  document.getElementById("cfg-busy-give-up").value = String(values.busy_give_up_seconds ?? "");
  document.getElementById("cfg-max-chars").value = String(values.max_text_chars ?? "");
  document.getElementById("cfg-timezone").value = values.timezone || "";
  document.getElementById("cfg-lookahead").value = String(values.slot_lookahead_hours ?? "");
  document.getElementById("cfg-max-wait").value = String(values.scheduler_max_wait_seconds ?? "");
  document.getElementById("cfg-idle").value = String(values.session_idle_seconds ?? "");
  document.getElementById("cfg-login-failures").value = String(values.login_max_failures ?? "");
  document.getElementById("cfg-login-window").value = String(values.login_window_seconds ?? "");
  document.getElementById("cfg-trigger-now").checked = Boolean(values.trigger_now);
  document.getElementById("cfg-fire-verbose").checked = Boolean(values.fire_verbose_log);
  document.getElementById("cfg-news-start").value = values.news_check_start || "12:00";
  document.getElementById("cfg-news-stop").value = values.news_check_stop || "18:00";
  document.getElementById("cfg-news-interval").value = String(values.news_check_interval_hours ?? 1);
  document.getElementById("cfg-repeater-timeout").value = String(values.repeater_timeout_seconds ?? 180);
}

async function fillVoiceOptions(selected) {
  const res = await fetch("/api/voices");
  const catalogPayload = res.ok ? await res.json() : { voices: [] };
  const select = document.getElementById("cfg-default-voice");
  select.replaceChildren();
  let found = false;
  for (const voice of catalogPayload.voices || []) {
    const option = document.createElement("option");
    option.value = voice.id;
    const bits = [voice.name, voice.quality].filter(Boolean);
    option.textContent = bits.length ? `${bits.join(" · ")} (${voice.id})` : voice.id;
    if (voice.id === selected) found = true;
    select.append(option);
  }
  if (selected && !found) {
    const option = document.createElement("option");
    option.value = selected;
    option.textContent = selected;
    select.append(option);
  }
  if (selected) select.value = selected;
}

async function refreshAdminConfig() {
  setError("config-error", "");
  const res = await fetch("/api/admin/config");
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    setError("config-error", apiErrorMessage(payload, "Could not load configuration"));
    return;
  }
  const current = payload.current || {};
  const values = current.values || {};
  fillConfigForm(values);
  await fillVoiceOptions(values.default_voice || "");
  document.getElementById("cfg-short").value = "";
  document.getElementById("cfg-long").value = "";
  const when = formatSavedAt(current.saved_at, values.timezone);
  document.getElementById("config-current-when").textContent = when ? `Current configuration, saved ${when}` : "";
  document.getElementById("config-current-short").textContent = current.short_description || "";
  const longEl = document.getElementById("config-current-long");
  longEl.textContent = current.long_description || "";
  longEl.classList.toggle("hidden", !current.long_description);
  renderConfigHistory(payload);
}

function renderConfigHistory(payload) {
  const body = document.getElementById("config-history-rows");
  body.replaceChildren();
  const current = payload.current || {};
  const zone = current.values?.timezone;
  const previous = (payload.history || []).filter((entry) => entry.id !== current.id);
  if (!previous.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.textContent = "No earlier configurations yet.";
    tr.append(td);
    body.append(tr);
    return;
  }
  for (const entry of previous) {
    const tr = document.createElement("tr");
    const when = document.createElement("td");
    when.textContent = formatSavedAt(entry.saved_at, zone);
    const desc = document.createElement("td");
    desc.textContent = entry.short_description || "";
    const actions = document.createElement("td");
    actions.className = "row-actions";
    if (entry.long_description) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "ghost";
      more.textContent = "Description";
      const detail = document.createElement("p");
      detail.className = "lede tight hidden";
      detail.textContent = entry.long_description;
      more.addEventListener("click", () => detail.classList.toggle("hidden"));
      desc.append(detail);
      actions.append(more);
    }
    const revert = document.createElement("button");
    revert.type = "button";
    revert.className = "ghost";
    revert.textContent = "Revert";
    revert.addEventListener("click", () => {
      const label = `${formatSavedAt(entry.saved_at, zone)} (${entry.short_description})`;
      if (!window.confirm(`Revert to the configuration saved ${label}?`)) return;
      revertConfig(entry.id).catch((err) => window.alert(err.message));
    });
    actions.append(revert);
    tr.append(when, desc, actions);
    body.append(tr);
  }
}

async function saveAdminConfig() {
  setError("config-error", "");
  const res = await fetch("/api/admin/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      values: readConfigForm(),
      short_description: document.getElementById("cfg-short").value,
      long_description: document.getElementById("cfg-long").value,
    }),
  });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    setError("config-error", apiErrorMessage(payload, "Could not save configuration"));
    return;
  }
  location.reload();
}

async function revertConfig(id) {
  const res = await fetch("/api/admin/config/revert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    window.alert(apiErrorMessage(payload, "Could not revert"));
    return;
  }
  if (payload.changed === false) return;
  location.reload();
}

function refreshBrandingPreview() {
  const generation = seenConfigGeneration ?? Date.now();
  const logo = document.getElementById("branding-logo-preview");
  const icon = document.getElementById("branding-favicon-preview");
  if (logo) logo.src = `/branding/logo?v=${generation}`;
  if (icon) icon.src = `/branding/favicon?v=${generation}`;
}

function logoSquareConstrained() {
  const square = document.getElementById("logo-display-square");
  return Boolean(square && square.checked);
}

function applyLogoSquareMode() {
  const square = logoSquareConstrained();
  const widthInput = document.getElementById("logo-display-width");
  const heightInput = document.getElementById("logo-display-height");
  const heightRow = document.getElementById("logo-display-height-row");
  const widthLabel = document.getElementById("logo-display-width-label");
  if (square && widthInput && heightInput) heightInput.value = widthInput.value;
  if (heightRow) heightRow.classList.toggle("hidden", square);
  if (widthLabel) widthLabel.textContent = square ? "Header size" : "Header width";
  previewLogoDisplay();
}

function readLogoDisplay() {
  const width = Number(document.getElementById("logo-display-width").value);
  const height = logoSquareConstrained()
    ? width
    : Number(document.getElementById("logo-display-height").value);
  if (!Number.isInteger(width) || !Number.isInteger(height)) return null;
  return { width, height };
}

function previewLogoDisplay() {
  const size = readLogoDisplay();
  const preview = document.getElementById("branding-logo-preview");
  if (!size || !preview) return;
  preview.style.setProperty("--logo-width", `${size.width}px`);
  preview.style.setProperty("--logo-height", `${size.height}px`);
  preview.width = size.width;
  preview.height = size.height;
}

async function saveLogoDisplay() {
  setError("logo-display-error", "");
  const size = readLogoDisplay();
  if (!size) {
    setError("logo-display-error", "Enter a whole number of pixels for width and height");
    return;
  }
  const res = await fetch("/api/admin/branding/display", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(size),
  });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    setError("logo-display-error", apiErrorMessage(payload, "Could not save the display size"));
    return;
  }
  location.reload();
}

async function uploadBranding(url, inputId, errorId, extra) {
  setError(errorId, "");
  const input = document.getElementById(inputId);
  const file = input.files && input.files[0];
  if (!file) {
    setError(errorId, "Choose a file first");
    return;
  }
  const body = new FormData();
  body.append("file", file);
  if (extra) {
    for (const [key, value] of Object.entries(extra)) body.append(key, value);
  }
  const res = await fetch(url, { method: "POST", body });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    setError(errorId, apiErrorMessage(payload, "Could not upload"));
    return;
  }
  location.reload();
}

async function resetBranding(url, errorId) {
  setError(errorId, "");
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) {
    let payload = {};
    try {
      payload = await res.json();
    } catch {
      /* ignore */
    }
    setError(errorId, apiErrorMessage(payload, "Could not restore the built-in image"));
    return;
  }
  location.reload();
}

function setPhonemeStatus(text, kind = "") {
  const el = document.getElementById("phoneme-status");
  if (!el) return;
  el.textContent = text;
  el.className = kind;
}

function stopPhonemePreview() {
  cancelInFlight();
  setPhonemeStatus("Ready");
}

function schedulePhonemeMatches() {
  window.clearTimeout(phonemeMatchTimer);
  phonemeMatchTimer = window.setTimeout(() => {
    refreshPhonemeMatches().catch(() => {});
  }, 250);
}

async function refreshPhonemeMatches() {
  const list = document.getElementById("phoneme-matches");
  if (!list) return;
  const enabled = document.getElementById("use-phoneme-dictionary").checked;
  const text = textEl.value.trim();
  if (!enabled || !text) {
    list.replaceChildren();
    return;
  }
  const res = await fetch("/api/phonemes/matches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: textEl.value }),
  });
  if (!res.ok) {
    list.replaceChildren();
    return;
  }
  const payload = await res.json();
  list.replaceChildren();
  for (const entry of payload.entries || []) {
    const li = document.createElement("li");
    li.textContent = `${entry.spelling} → ${entry.ipa || entry.alternate}`;
    list.append(li);
  }
}

async function loadPhonemeDictionary() {
  setError("phoneme-error", "");
  const res = await fetch("/api/phonemes");
  if (!res.ok) {
    setError("phoneme-error", await errorDetail(res));
    return;
  }
  const payload = await res.json();
  phonemeEntries = Array.isArray(payload.entries) ? payload.entries : [];
  renderPhonemeRows();
  applyPhonemeReadOnly();
}

function renderPhonemeRows() {
  const body = document.getElementById("phoneme-rows");
  body.replaceChildren();
  if (!phonemeEntries.length) {
    const tr = document.createElement("tr");
    tr.className = "phoneme-empty";
    const td = document.createElement("td");
    td.colSpan = 4;
    td.textContent = "No dictionary entries yet.";
    tr.append(td);
    body.append(tr);
    return;
  }
  phonemeEntries.forEach((entry, index) => {
    const tr = document.createElement("tr");
    const spelling = document.createElement("td");
    spelling.textContent = entry.spelling;
    const ipa = document.createElement("td");
    ipa.className = "phoneme-ipa";
    ipa.textContent = entry.ipa;
    const alternate = document.createElement("td");
    alternate.textContent = entry.alternate || "";
    const actions = document.createElement("td");
    const speakWord = document.createElement("button");
    speakWord.type = "button";
    speakWord.className = "ghost small";
    speakWord.textContent = "Speak word";
    speakWord.addEventListener("click", () => {
      playPhonemePreview(entry.spelling, "Speaking word…").catch((err) =>
        setError("phoneme-error", err.message),
      );
    });
    actions.append(speakWord);
    if (entry.ipa) {
      const speakIpa = document.createElement("button");
      speakIpa.type = "button";
      speakIpa.className = "ghost small";
      speakIpa.textContent = "Speak IPA";
      speakIpa.addEventListener("click", () => {
        playPhonemePreview(`[[${entry.ipa}]]`, "Speaking IPA…").catch((err) =>
          setError("phoneme-error", err.message),
        );
      });
      actions.append(speakIpa);
    }
    if (entry.alternate) {
      const speakAlternate = document.createElement("button");
      speakAlternate.type = "button";
      speakAlternate.className = "ghost small";
      speakAlternate.textContent = "Speak alternate";
      speakAlternate.addEventListener("click", () => {
        playPhonemePreview(entry.alternate, "Speaking alternate…").catch((err) =>
          setError("phoneme-error", err.message),
        );
      });
      actions.append(speakAlternate);
    }
    if (!readOnly) {
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "ghost small";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => beginPhonemeEdit(index));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost small";
      remove.textContent = "Delete";
      remove.addEventListener("click", () => {
        deletePhonemeEntry(index).catch((err) => setError("phoneme-error", err.message));
      });
      actions.append(edit, remove);
    }
    tr.append(spelling, ipa, alternate, actions);
    body.append(tr);
  });
}

function applyPhonemeReadOnly() {
  for (const id of ["phoneme-spelling", "phoneme-ipa", "phoneme-alternate", "phoneme-save", "phoneme-fill-ipa"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = readOnly;
  }
  const save = document.getElementById("phoneme-save");
  if (save) save.classList.toggle("hidden", readOnly);
}

function beginPhonemeEdit(index) {
  const entry = phonemeEntries[index];
  if (!entry) return;
  phonemeEditIndex = index;
  document.getElementById("phoneme-spelling").value = entry.spelling;
  document.getElementById("phoneme-ipa").value = entry.ipa || "";
  document.getElementById("phoneme-alternate").value = entry.alternate || "";
  document.getElementById("phoneme-cancel").classList.remove("hidden");
}

function clearPhonemeForm() {
  phonemeEditIndex = -1;
  document.getElementById("phoneme-spelling").value = "";
  document.getElementById("phoneme-ipa").value = "";
  document.getElementById("phoneme-alternate").value = "";
  document.getElementById("phoneme-cancel").classList.add("hidden");
}

async function putPhonemeEntries(entries) {
  const res = await fetch("/api/phonemes", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries }),
  });
  if (!res.ok) {
    setError("phoneme-error", await errorDetail(res));
    return false;
  }
  const payload = await res.json();
  phonemeEntries = payload.entries || [];
  renderPhonemeRows();
  schedulePhonemeMatches();
  return true;
}

async function savePhonemeForm() {
  setError("phoneme-error", "");
  const spelling = document.getElementById("phoneme-spelling").value.trim();
  const ipa = document.getElementById("phoneme-ipa").value.trim();
  const alternate = document.getElementById("phoneme-alternate").value.trim();
  if (!spelling) {
    setError("phoneme-error", "Spelling is required.");
    return;
  }
  if (ipa && alternate) {
    setError("phoneme-error", "Enter IPA or alternate text, not both.");
    return;
  }
  if (!ipa && !alternate) {
    setError("phoneme-error", "Enter IPA or alternate text.");
    return;
  }
  const next = phonemeEntries.map((entry) => ({
    spelling: entry.spelling,
    ipa: entry.ipa || "",
    alternate: entry.alternate || "",
  }));
  const row = { spelling, ipa, alternate };
  if (phonemeEditIndex >= 0) next[phonemeEditIndex] = row;
  else next.push(row);
  if (await putPhonemeEntries(next)) clearPhonemeForm();
}

async function deletePhonemeEntry(index) {
  const next = phonemeEntries.filter((_, itemIndex) => itemIndex !== index);
  if (phonemeEditIndex === index) clearPhonemeForm();
  await putPhonemeEntries(next);
}

async function fillIpaField(sourceId, targetId) {
  setError("phoneme-error", "");
  const text = document.getElementById(sourceId).value.trim();
  if (!text) {
    setError("phoneme-error", "Type a word or phrase first.");
    return;
  }
  const res = await fetch("/api/phonemes/ipa", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    setError("phoneme-error", await errorDetail(res));
    return;
  }
  const payload = await res.json();
  document.getElementById(targetId).value = payload.ipa || "";
}

function speakPhonemeField(sourceId, { phonemes, emptyMessage, status }) {
  setError("phoneme-error", "");
  const value = document.getElementById(sourceId).value.trim();
  if (!value) {
    setError("phoneme-error", emptyMessage);
    return;
  }
  const text = phonemes ? `[[${value}]]` : value;
  playPhonemePreview(text, status).catch((err) => setError("phoneme-error", err.message));
}

async function playPhonemePreview(text, status) {
  await ensureVoices();
  cancelInFlight();
  const mySession = session;
  const controller = new AbortController();
  abort = controller;
  const currentPlayer = new PcmPlayer();
  player = currentPlayer;
  setPhonemeStatus(status);
  try {
    const res = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice: document.getElementById("phoneme-voice")?.value || catalog?.default_voice || null,
        speed: catalog?.speed ?? 1,
        sentence_pause: catalog?.sentence_pause ?? 0.25,
        use_phoneme_dictionary: false,
      }),
      signal: controller.signal,
    });
    if (mySession !== session) return;
    if (!res.ok) {
      setPhonemeStatus((await errorDetail(res)) || "Playback failed", "error");
      return;
    }
    const sampleRate = Number(res.headers.get("X-Sample-Rate") || "22050");
    await currentPlayer.play(res.body, sampleRate, controller.signal, { totalSeconds: 0 });
    if (mySession === session) setPhonemeStatus("Ready");
  } catch (err) {
    if (mySession !== session) return;
    if (err.name !== "AbortError" && !controller.signal.aborted) {
      setPhonemeStatus(err.message || "Playback failed", "error");
    }
  }
}

function routeKey(hash = location.hash) {
  const raw = String(hash || "").replace(/^#/, "") || "/";
  const [path] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  if (!parts.length) return "home";
  if (parts[0] === "login") return "login";
  if (parts[0] === "setup") return "setup";
  if (parts[0] === "users" || parts[0] === "admin") return "admin";
  if (parts[0] === "help") return "help";
  if (parts[0] === "phonemes") return "phonemes";
  if (parts[0] === "announcements") return parts[1] === "new" ? "ann-new" : `ann/${parts[1]}`;
  if (parts[0] === "schedules") {
    if (parts[1] === "new") return `sched-new${location.hash.includes("?") ? location.hash.slice(location.hash.indexOf("?")) : ""}`;
    return `sched/${parts[1]}`;
  }
  return "home";
}

async function route() {
  await boot;
  const serverStarting = !(await fetchServerReady());
  if (serverStarting) {
    showBoot();
    await yieldToPaint();
    await waitForServerReady();
  }
  try {
    const auth = await refreshAuth();
    authReady = true;
    if (auth.needsSetup) {
      stopEditorPlayback();
      stopHomePolling();
      stopStatsPolling();
      showScreen("view-setup");
      editorKey = "setup";
      clearEditorGuard();
      if (location.hash !== "#/setup") {
        ignoreHashChange = true;
        history.replaceState(null, "", "#/setup");
        ignoreHashChange = false;
      }
      return;
    }
    if (!auth.authenticated) {
      stopEditorPlayback();
      stopHomePolling();
      stopStatsPolling();
      showScreen("view-login");
      editorKey = "login";
      clearEditorGuard();
      if (location.hash !== "#/login") {
        ignoreHashChange = true;
        history.replaceState(null, "", "#/login");
        ignoreHashChange = false;
      }
      return;
    }
    startStatsPolling();
    const nextKey = routeKey();
    if (nextKey !== editorKey && isEditorDirty() && !confirmDiscardEdits()) {
      ignoreHashChange = true;
      history.replaceState(null, "", editorKey === "home" ? "#/" : location.hash);
      ignoreHashChange = false;
      if (editorKey === "home") showScreen("view-home");
      else if (editorKey === "help") showScreen("view-help");
      else if (editorKey === "phonemes") showScreen("view-phonemes");
      else if (String(editorKey).startsWith("admin")) {
        showScreen("view-admin");
        showAdminSection(adminSectionFromKey(editorKey));
      }
      else if (editorKey.startsWith("ann")) showScreen("view-announcement");
      else if (editorKey.startsWith("sched")) showScreen("view-schedule");
      return;
    }
    if (String(editorKey || "").startsWith("ann") && nextKey !== editorKey) {
      stopEditorPlayback();
    }
    if (editorKey === "phonemes" && nextKey !== "phonemes") {
      stopPhonemePreview();
    }
    const raw = (location.hash.replace(/^#/, "") || "/").split("?")[0];
    const parts = raw.split("/").filter(Boolean);
    const params = new URLSearchParams((location.hash.split("?")[1] || "").replace(/#/g, ""));
    showError("announcement-error", "");
    showError("schedule-error", "");
    if (parts[0] === "login" || parts[0] === "setup") {
      goToHash("#/");
      return;
    }
    if (parts[0] === "users") {
      goToHash("#/admin/users");
      return;
    }
    if (parts[0] === "admin") {
      if (!isAdmin()) {
        goToHash("#/");
        return;
      }
      const section = parts[1] || "users";
      if (section !== "users" && section !== "environment" && section !== "branding") {
        goToHash("#/admin/users");
        return;
      }
      if (!parts[1]) {
        goToHash("#/admin/users");
        return;
      }
      stopHomePolling();
      editorKey = `admin/${section}`;
      clearEditorGuard();
      showScreen("view-admin");
      showAdminSection(section);
      if (section === "users") {
        resetUserForm();
        await refreshUsers();
      } else if (section === "environment") {
        await refreshAdminConfig();
      } else {
        refreshBrandingPreview();
      }
      return;
    }
    if (parts[0] === "help") {
      stopHomePolling();
      await ensureScheduleMeta();
      showScreen("view-help");
      editorKey = "help";
      clearEditorGuard();
      return;
    }
    if (parts[0] === "phonemes") {
      stopHomePolling();
      showScreen("view-phonemes");
      editorKey = "phonemes";
      clearEditorGuard();
      await ensureVoices();
      await loadPhonemeDictionary();
      return;
    }
    if (parts[0] === "announcements") {
      if (parts[1] === "new" && readOnly) {
        goToHash("#/");
        return;
      }
      stopHomePolling();
      showScreen("view-announcement");
      clearEditorGuard();
      await Promise.all([ensureVoices(), boot]);
      if (parts[1] === "new") blankAnnouncement();
      else await loadAnnouncement(parts[1]);
      applyEditorReadOnly();
      editorKey = nextKey;
      markEditorClean();
      return;
    }
    if (parts[0] === "schedules") {
      if (parts[1] === "new" && readOnly) {
        goToHash("#/");
        return;
      }
      stopHomePolling();
      showScreen("view-schedule");
      clearEditorGuard();
      const metaReady = ensureScheduleMeta();
      if (!announcements.length) {
        const lib = await fetch("/api/announcements");
        announcements = lib.ok ? (await lib.json()).announcements || [] : [];
      }
      await metaReady;
      if (parts[1] === "new") {
        editingScheduleId = null;
        document.getElementById("schedule-title").textContent = "New schedule";
        const template = TEMPLATES[params.get("template") || "baseline"] || TEMPLATES.baseline;
        fillScheduleForm(applyTemplatePriority(template));
      } else {
        editingScheduleId = parts[1];
        document.getElementById("schedule-title").textContent = readOnly ? "View schedule" : "Edit schedule";
        const res = await fetch(`/api/schedules/${parts[1]}`);
        if (!res.ok) {
          const message = "Schedule not found";
          if (isBootScreenActive() || serverStarting) showBootError(message);
          else window.alert(message);
          return;
        }
        fillScheduleForm(await res.json());
      }
      applyEditorReadOnly();
      editorKey = nextKey;
      markEditorClean();
      return;
    }
    editorKey = "home";
    clearEditorGuard();
    showScreen("view-home");
    startHomePolling();
    await Promise.all([refreshHome(), ensureVoices()]);
  } catch (err) {
    const message = err?.message || "Something went wrong";
    if (isBootScreenActive() || serverStarting) showBootError(message);
    else window.alert(message);
  }
}

document.getElementById("new-announcement").addEventListener("click", () => {
  if (readOnly) return;
  goToHash("#/announcements/new");
});
document.getElementById("nav-home").addEventListener("click", () => goToHash("#/"));
document.getElementById("nav-help").addEventListener("click", () => goToHash("#/help"));
document.getElementById("nav-phonemes").addEventListener("click", () => goToHash("#/phonemes"));
document.getElementById("back-phonemes").addEventListener("click", () => goToHash("#/"));
document.getElementById("use-phoneme-dictionary").addEventListener("change", () => {
  schedulePhonemeMatches();
  syncEditorSave();
});
document.getElementById("phoneme-speak-spelling").addEventListener("click", () => {
  speakPhonemeField("phoneme-spelling", {
    phonemes: false,
    emptyMessage: "Type a spelling first.",
    status: "Speaking spelling…",
  });
});
document.getElementById("phoneme-speak-ipa").addEventListener("click", () => {
  speakPhonemeField("phoneme-ipa", {
    phonemes: true,
    emptyMessage: "Type or generate IPA first.",
    status: "Speaking IPA…",
  });
});
document.getElementById("phoneme-speak-alternate").addEventListener("click", () => {
  speakPhonemeField("phoneme-alternate", {
    phonemes: false,
    emptyMessage: "Type alternate text first.",
    status: "Speaking alternate…",
  });
});
document.getElementById("phoneme-fill-ipa").addEventListener("click", () => {
  fillIpaField("phoneme-spelling", "phoneme-ipa").catch((err) => setError("phoneme-error", err.message));
});
document.getElementById("phoneme-save").addEventListener("click", () => {
  savePhonemeForm().catch((err) => setError("phoneme-error", err.message));
});
document.getElementById("phoneme-cancel").addEventListener("click", () => {
  setError("phoneme-error", "");
  clearPhonemeForm();
});
document.getElementById("nav-admin").addEventListener("click", () => goToHash("#/admin/users"));
document.getElementById("admin-tab-users").addEventListener("click", () => goToHash("#/admin/users"));
document.getElementById("admin-tab-environment").addEventListener("click", () => goToHash("#/admin/environment"));
document.getElementById("admin-tab-branding").addEventListener("click", () => goToHash("#/admin/branding"));
document.getElementById("config-save").addEventListener("click", () => {
  saveAdminConfig().catch((err) => window.alert(err.message));
});
document.getElementById("logo-display-square").addEventListener("change", applyLogoSquareMode);
document.getElementById("logo-display-width").addEventListener("input", () => {
  if (logoSquareConstrained()) {
    document.getElementById("logo-display-height").value = document.getElementById("logo-display-width").value;
  }
  previewLogoDisplay();
});
document.getElementById("logo-display-height").addEventListener("input", previewLogoDisplay);
document.getElementById("logo-display-save").addEventListener("click", () => {
  saveLogoDisplay().catch((err) => window.alert(err.message));
});
document.getElementById("logo-upload").addEventListener("click", () => {
  const also = document.getElementById("logo-also-favicon").checked ? "true" : "false";
  uploadBranding("/api/admin/branding/logo", "logo-file", "logo-error", { also_favicon: also }).catch((err) =>
    window.alert(err.message)
  );
});
document.getElementById("favicon-upload").addEventListener("click", () => {
  uploadBranding("/api/admin/branding/favicon", "favicon-file", "favicon-error").catch((err) =>
    window.alert(err.message)
  );
});
document.getElementById("logo-reset").addEventListener("click", () => {
  resetBranding("/api/admin/branding/logo", "logo-error").catch((err) => window.alert(err.message));
});
document.getElementById("favicon-reset").addEventListener("click", () => {
  resetBranding("/api/admin/branding/favicon", "favicon-error").catch((err) => window.alert(err.message));
});
document.getElementById("nav-logout").addEventListener("click", () => {
  logout().catch((err) => window.alert(err.message));
});
document.getElementById("setup-submit").addEventListener("click", () => {
  submitSetup().catch((err) => window.alert(err.message));
});
document.getElementById("login-submit").addEventListener("click", () => {
  submitLogin().catch((err) => window.alert(err.message));
});
document.getElementById("login-password-toggle").addEventListener("click", () => {
  const input = document.getElementById("login-password");
  const toggle = document.getElementById("login-password-toggle");
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  toggle.setAttribute("aria-pressed", showing ? "false" : "true");
  toggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
});
document.getElementById("login-password").addEventListener("keydown", (event) => {
  if (event.key === "Enter") submitLogin().catch((err) => window.alert(err.message));
});
document.getElementById("setup-password2").addEventListener("keydown", (event) => {
  if (event.key === "Enter") submitSetup().catch((err) => window.alert(err.message));
});
document.getElementById("user-save").addEventListener("click", () => {
  saveUser().catch((err) => window.alert(err.message));
});
document.getElementById("user-cancel").addEventListener("click", resetUserForm);
document.getElementById("clock-date").addEventListener("change", () => {
  refreshHome().catch(() => {});
});
document.getElementById("clock-today").addEventListener("click", () => {
  document.getElementById("clock-date").value = "";
  refreshHome().catch(() => {});
});
document.getElementById("back-announcement").addEventListener("click", () => goToHash("#/"));
document.getElementById("back-schedule").addEventListener("click", () => goToHash("#/"));
document.getElementById("save-announcement").addEventListener("click", () => {
  if (readOnly) return;
  saveAnnouncement().catch((err) => window.alert(err.message));
});
document.getElementById("save-schedule").addEventListener("click", () => {
  if (readOnly) return;
  saveSchedule().catch((err) => window.alert(err.message));
});
document.getElementById("speak").addEventListener("click", speak);
document.getElementById("stop").addEventListener("click", stop);
document.getElementById("newsline-play").addEventListener("click", () => {
  playNewsline().catch(() => {});
});
document.getElementById("newsline-stop").addEventListener("click", stop);
document.getElementById("reset").addEventListener("click", resetAnnouncementEdits);
document.getElementById("newsline-reset").addEventListener("click", resetAnnouncementEdits);
textEl.addEventListener("input", () => {
  updateCount();
  clearKnownPlayLength();
  schedulePhonemeMatches();
});
speedEl.addEventListener("input", () => {
  updateDeliveryLabels();
  persist();
  clearKnownPlayLength();
});
pauseEl.addEventListener("input", () => {
  updateDeliveryLabels();
  persist();
  clearKnownPlayLength();
});
voiceEl.addEventListener("change", () => {
  persist();
  clearKnownPlayLength();
});
document.getElementById("sched-kind").addEventListener("change", syncKindFields);
document.getElementById("range-all-slots").addEventListener("change", syncKindFields);

const menu = document.getElementById("template-menu");
document.getElementById("new-schedule").addEventListener("click", (event) => {
  if (readOnly) return;
  event.stopPropagation();
  menu.classList.toggle("hidden");
});
menu.addEventListener("click", (event) => {
  const button = event.target.closest("[data-template]");
  if (!button) return;
  menu.classList.add("hidden");
  goToHash(`#/schedules/new?template=${button.dataset.template}`);
});
document.addEventListener("click", () => menu.classList.add("hidden"));

const manualDropDismiss = document.getElementById("manual-trigger-drop-dismiss");
if (manualDropDismiss) {
  manualDropDismiss.addEventListener("click", () => {
    dismissManualTriggerDrop().catch(() => {});
  });
}

document.getElementById("announcement-kind").addEventListener("change", syncAnnouncementKind);

for (const id of ["view-announcement", "view-schedule"]) {
  const view = document.getElementById(id);
  view.addEventListener("input", syncEditorSave);
  view.addEventListener("change", syncEditorSave);
}

document.getElementById("newsline-check").addEventListener("click", async () => {
  const button = document.getElementById("newsline-check");
  if (button?.disabled) return;
  const status = document.getElementById("newsline-status");
  if (status) status.textContent = "Checking for a new Newsline report…";
  const res = await fetch("/api/newsline/check", { method: "POST" });
  if (res.status === 409) {
    let detail = "A Newsline check is already running.";
    try {
      const body = await res.json();
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    if (status) status.textContent = detail;
    refreshHome({ background: true }).catch(() => {});
    return;
  }
  if (!res.ok) {
    if (status) status.textContent = "Could not start the Newsline check.";
    return;
  }
  await refreshHome();
});

document.getElementById("skip-notice-dismiss").addEventListener("click", async () => {
  await fetch("/api/skip-notice/dismiss", { method: "POST" });
  renderSkipNotice(null);
});

document.getElementById("baseline-randomize").addEventListener("change", async (event) => {
  if (readOnly) return;
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ baseline_randomize: event.target.checked }),
  });
  refreshHome().catch(() => {});
});

document.getElementById("boot-retry").addEventListener("click", () => {
  route().catch(() => {});
});
window.addEventListener("hashchange", () => {
  if (ignoreHashChange) return;
  route().catch((err) => {
    if (!isBootScreenActive()) window.alert(err.message);
  });
});
window.addEventListener("pagehide", () => {
  stopEditorPlayback();
});
window.addEventListener("beforeunload", (event) => {
  stopEditorPlayback();
  if (!isEditorDirty()) return;
  event.preventDefault();
  // Chrome still requires returnValue for the leave prompt.
  // noinspection JSDeprecatedSymbols
  event.returnValue = "";
});

boot = loadDefaultScript();
updateCount();
updateDeliveryLabels();
updateAuthChrome();
route().catch(() => {});
