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

// Day/month labels and template priorities come from GET /api/meta (app/models.py).
let scheduleMeta = null;

const TEMPLATES = {
  baseline: { name: "Baseline", kind: "baseline", offset_minutes: 0 },
  weekly: {
    name: "Weekly",
    kind: "weekly",
    days: ["sun"],
    slots: ["21:00"],
    offset_minutes: -5,
  },
  monthly: {
    name: "Monthly",
    kind: "monthly",
    days: ["wed"],
    occurrence: 1,
    slots: ["19:00"],
    offset_minutes: -5,
    skip_months: [],
  },
  range: {
    name: "Event countdown",
    kind: "range",
    offset_minutes: 0,
  },
  monthly_range: {
    name: "Monthly countdown",
    kind: "monthly_range",
    days: ["wed"],
    occurrence: 1,
    event_slot: "19:00",
    days_before: 7,
    offset_minutes: 0,
  },
  silence: {
    name: "Silence",
    kind: "weekly",
    days: ["sun"],
    slots: ["21:30"],
    offset_minutes: 0,
    announcement_id: null,
  },
  emergency: {
    name: "Emergency",
    kind: "emergency",
    offset_minutes: 0,
  },
};

const voiceEl = document.getElementById("voice");
const speedEl = document.getElementById("speed");
const pauseEl = document.getElementById("pause");
const textEl = document.getElementById("text");
const nameEl = document.getElementById("name");

let catalog = null;
let player = null;
let abort = null;
let editorPlaying = false;
let session = 0;
let pollTimer = null;
let newslinePollTimer = null;
let statsTimer = null;
let countdownTimer = null;
let homeSseWanted = false;
let homeSseAbort = null;
let homeSseConnected = false;
let statsSseWanted = false;
let statsSseAbort = null;
let statsSseConnected = false;
let upcoming = null;
let clockSkewMs = 0;
let homeBusy = false;
let statsBusy = false;
let ledFailed = false;
let ledBlinkTimer = null;
let ledBlinkUntil = 0;
let defaultScript = "";
let announcements = [];
let schedules = [];
let settings = {
  slot_half_window_minutes: DEFAULT_SLOT_HALF_WINDOW_MINUTES,
  baseline_randomize: false,
};
let triggerNowEnabled = false;
let triggerNowBusy = false;
let pendingManualTriggers = [];
let editingAnnouncementId = null;
let editingScheduleId = null;
let currentUser = null;
let authReady = false;
let readOnly = false;
let editorBaseline = null;
let editorKey = "";
let ignoreHashChange = false;
let boot = Promise.resolve();

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
  document.getElementById("nav-users").classList.toggle("hidden", !isAdmin());
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
  for (const id of ["newsline-check", "news-check-start", "news-check-stop", "repeater-timeout"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = readOnly;
  }
  document.getElementById("newsline-check")?.classList.toggle("hidden", readOnly);
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
    document.getElementById("news-gap"),
    document.getElementById("newsline-reset"),
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
  const save = document.getElementById("save-announcement");
  if (save) save.disabled = lockAnnouncement;
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
  return document.getElementById("view-boot")?.classList.contains("active");
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
  panel?.classList.remove("has-error");
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
  panel?.classList.add("has-error");
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
    route();
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
      name: nameEl.value,
      text: textEl.value,
      voice: voiceEl.value,
      speed: speedEl.value,
      pause: pauseEl.value,
      busyRetry: document.getElementById("busy-retry").value,
      busyGiveup: document.getElementById("busy-giveup").value,
    });
  }
  if (document.getElementById("view-schedule").classList.contains("active")) {
    return JSON.stringify(readSchedulePayload());
  }
  return "";
}

function isEditorDirty() {
  return editorBaseline !== null && formSnapshot() !== editorBaseline;
}

function markEditorClean() {
  editorBaseline = formSnapshot();
}

function clearEditorGuard() {
  editorBaseline = null;
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
    whenEl.textContent = upcoming.at ? formatWhen(upcoming.at) : "—";
    statusEl.textContent = "Running";
    statusEl.classList.add("is-running");
    statusEl.classList.remove("is-deferred", "is-clear-hold");
    countEl.textContent = "Now";
    setUpcomingNote("");
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
      const hold =
        pending.clear_seconds > 0
          ? `Then ${Math.ceil(pending.clear_seconds)}s clear hold before transmit`
          : "Will play when the channel is clear";
      whenEl.textContent = hold;
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
  for (const stamp of slotStamps()) {
    const option = document.createElement("option");
    option.value = stamp;
    option.textContent = stamp;
    option.selected = stamp === (selected || "19:00");
    el.append(option);
  }
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
  } else if (announcements[0]) {
    el.value = announcements[0].id;
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
  fillDayChips(document.getElementById("weekly-days"), schedule?.days || ["sun"]);
  fillDayChips(document.getElementById("monthly-weekday"), schedule?.days?.[0] || "wed", { exclusive: true });
  document.getElementById("monthly-occurrence").value = String(schedule?.occurrence || 1);
  fillMonthSkip(document.getElementById("monthly-skip"), schedule?.skip_months || []);
  document.getElementById("range-start").value = schedule?.start_date || "";
  document.getElementById("range-event-date").value = schedule?.event_date || "";
  document.getElementById("days-before").value = String(schedule?.days_before ?? 7);
  fillEventSlotSelect(schedule?.event_slot || "19:00");
  document.getElementById("range-all-slots").checked = !isCountdownKind(kind) || !(schedule?.slots || []).length;
  fillSlotChips(schedule?.slots || (kind === "weekly" ? ["21:00"] : kind === "monthly" ? ["19:00"] : ["12:00"]));
  const hasAnnouncementKey = Boolean(schedule && Object.prototype.hasOwnProperty.call(schedule, "announcement_id"));
  const selectedAnnouncement = hasAnnouncementKey
    ? schedule.announcement_id
    : kind === "baseline"
      ? announcements[0]?.id
      : undefined;
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
    if (row.schedule_id) {
      cell.addEventListener("click", () => goToHash(`#/schedules/${row.schedule_id}`));
    }
    grid.append(cell);
  }
}

function nextRunSortKey(value) {
  if (!value) return Number.POSITIVE_INFINITY;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms;
}

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
        if (res.ok) refreshHome();
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

function syncNewslinePoll(newsline) {
  if (newslineCheckActive(newsline)) {
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

function renderNewsline(newsline) {
  const status = document.getElementById("newsline-status");
  if (!status) return;
  syncNewslinePoll(newsline);
  if (!newsline) {
    status.textContent = "No report downloaded yet.";
    return;
  }
  const working = newsline.working_report;
  if (newsline.phase === "downloading") {
    status.textContent = working
      ? `Downloading Newsline report ${working}…`
      : "Downloading the Newsline report…";
    return;
  }
  if (newsline.phase === "splitting") {
    status.textContent = working
      ? `Processing Newsline report ${working}…`
      : "Processing the Newsline report…";
    return;
  }
  if (newsline.phase === "checking" || (newsline.in_progress && newsline.phase !== "idle")) {
    status.textContent = "Checking for a new Newsline report…";
    return;
  }
  if (newsline.error) {
    status.textContent = `Last check failed: ${newsline.error}`;
    return;
  }
  const unchanged = newsline.outcome === "current";
  if (newsline.ready && newsline.report_number) {
    status.textContent = unchanged
      ? `No new Newsline report. Report ${newsline.report_number} is ready to play.`
      : `Report ${newsline.report_number} is ready to play.`;
    return;
  }
  if (newsline.report_number) {
    status.textContent = unchanged
      ? `No new Newsline report. Report ${newsline.report_number} is downloaded but not split yet.`
      : `Report ${newsline.report_number} is downloaded but not split yet.`;
    return;
  }
  status.textContent = "No report downloaded yet.";
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
    tr.innerHTML = `
      <td>${escapeHtml(item.name)}</td>
      <td>${escapeHtml(preview)}${item.kind !== "newsline" && preview.length === 90 ? "…" : ""}${
        item.too_long ? `<div class="exclusion-line">Too long (${Number(item.play_seconds).toFixed(1)}s). Shorten it and save again.</div>` : ""
      }</td>
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
      refreshHome();
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
  upcoming = payload.upcoming || null;
  const fetchedAt = payload.now ? Date.parse(payload.now) : Date.now() + clockSkewMs;
  pendingManualTriggers = (payload.pending_manual_triggers || []).map((item) => ({
    ...item,
    fetchedAt: Number.isNaN(fetchedAt) ? Date.now() + clockSkewMs : fetchedAt,
  }));
  renderUpcoming();
  renderManualTriggerDropNotice(payload.manual_trigger_drop_notice || null);
  renderSkipNotice(payload.skip_notice || null);
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
    const startEl = document.getElementById("news-check-start");
    const stopEl = document.getElementById("news-check-stop");
    const timeoutEl = document.getElementById("repeater-timeout");
    if (startEl) startEl.value = settings.news_check_start || "12:00";
    if (stopEl) stopEl.value = settings.news_check_stop || "18:00";
    if (timeoutEl) timeoutEl.value = String(settings.repeater_timeout_seconds ?? 180);
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
      renderSystemStats({ celsius: null, thermal_state: null, cpu_percent: null, memory_percent: null });
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
    tempText = `${Math.round(celsius)}°C`;
    if (celsius < 55) band = "cool";
    else if (celsius < 70) band = "warm";
    else band = "hot";
  }

  chip.classList.remove("temp-cool", "temp-warm", "temp-serious", "temp-hot", "temp-na");
  chip.classList.add(`temp-${band}`);
  tempEl.textContent = tempText;
  cpuEl.textContent =
    cpuPercent != null && !Number.isNaN(cpuPercent) ? `${Math.round(cpuPercent)}%` : "—";
  memEl.textContent =
    memoryPercent != null && !Number.isNaN(memoryPercent) ? `${Math.round(memoryPercent)}%` : "—";

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
  document.getElementById("count").textContent = `${textEl.value.length} / ${textEl.maxLength}`;
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

function renderVoices(preferred) {
  const selected = preferred || catalog.default_voice;
  voiceEl.replaceChildren();
  for (const voice of catalog.voices) {
    const option = document.createElement("option");
    option.value = voice.id;
    const bits = [voice.name, voice.gender, voice.locale, voice.quality].filter(Boolean);
    option.textContent = bits.join(" · ");
    option.selected = voice.id === selected;
    voiceEl.append(option);
  }
}

async function loadVoices() {
  const res = await fetch("/api/voices");
  if (!res.ok) throw new Error("Could not load voices");
  catalog = await res.json();
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
  fillSpokenSelect("news-opening", "", { optional: true });
  fillSpokenSelect("news-station", spokenAnnouncements()[0]?.id || "");
  fillSpokenSelect("news-closing", "", { optional: true });
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
  fillSpokenSelect("news-opening", item.opening_announcement_id || "", { optional: true });
  fillSpokenSelect("news-station", item.station_id_announcement_id || "");
  fillSpokenSelect("news-closing", item.closing_announcement_id || "", { optional: true });
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
    busy_retry_seconds: Number(document.getElementById("busy-retry").value),
    busy_give_up_seconds: Number(document.getElementById("busy-giveup").value),
    opening_announcement_id: document.getElementById("news-opening").value || null,
    station_id_announcement_id: document.getElementById("news-station").value || null,
    closing_announcement_id: document.getElementById("news-closing").value || null,
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
      }),
      signal: controller.signal,
    });
    if (mySession !== session) return;
    if (!res.ok) throw new Error(await errorDetail(res));
    const sampleRate = Number(res.headers.get("X-Sample-Rate") || "22050");
    setStatus("Streaming…", "playing");
    await currentPlayer.play(res.body, sampleRate, controller.signal);
    if (mySession !== session) return;
    if (!controller.signal.aborted) setStatus("Done");
  } catch (err) {
    if (mySession !== session) return;
    if (err.name === "AbortError" || controller.signal.aborted) setStatus("Stopped");
    else setStatus(err.message || "Playback failed", "error");
  } finally {
    if (mySession !== session) return;
    setEditorPlaying(false);
  }
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
      throw new Error(detail);
    }
    const sampleRate = Number(res.headers.get("X-Sample-Rate") || "22050");
    setStatus("Streaming…", "playing");
    if (res.headers.get("X-Preview-Framing") !== "name32+length32") {
      throw new Error("Restart the app to play Newsline with PTT gaps.");
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
    if (mySession !== session) return;
    setEditorPlaying(false);
  }
}

function stopEditorPlayback() {
  const hadPlayback = Boolean(abort || player || editorPlaying);
  if (!hadPlayback) return false;
  cancelInFlight();
  setEditorPlaying(false);
  setStatus("Stopped");
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

  async play(stream, sampleRate, signal) {
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
    } catch (err) {
      if (this.stopped || signal.aborted || err.name === "AbortError" || err.name === "InvalidStateError") return;
      throw err;
    } finally {
      this._stopSegmentClock();
      signal.removeEventListener("abort", onAbort);
      this._cancelReader();
    }
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

function routeKey(hash = location.hash) {
  const raw = String(hash || "").replace(/^#/, "") || "/";
  const [path] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  if (!parts.length) return "home";
  if (parts[0] === "login") return "login";
  if (parts[0] === "setup") return "setup";
  if (parts[0] === "users") return "users";
  if (parts[0] === "help") return "help";
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
      else if (editorKey === "users") showScreen("view-users");
      else if (editorKey.startsWith("ann")) showScreen("view-announcement");
      else if (editorKey.startsWith("sched")) showScreen("view-schedule");
      return;
    }
    if (String(editorKey || "").startsWith("ann") && nextKey !== editorKey) {
      stopEditorPlayback();
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
      if (!isAdmin()) {
        goToHash("#/");
        return;
      }
      stopHomePolling();
      editorKey = "users";
      clearEditorGuard();
      resetUserForm();
      showScreen("view-users");
      await refreshUsers();
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
    if (parts[0] === "announcements") {
      if (parts[1] === "new" && readOnly) {
        goToHash("#/");
        return;
      }
      stopHomePolling();
      showScreen("view-announcement");
      await ensureVoices();
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
        if (!res.ok) throw new Error("Schedule not found");
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
document.getElementById("nav-users").addEventListener("click", () => goToHash("#/users"));
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
  saveAnnouncement();
});
document.getElementById("save-schedule").addEventListener("click", () => {
  if (readOnly) return;
  saveSchedule();
});
document.getElementById("speak").addEventListener("click", speak);
document.getElementById("stop").addEventListener("click", stop);
document.getElementById("newsline-play").addEventListener("click", () => {
  playNewsline().catch(() => {});
});
document.getElementById("newsline-stop").addEventListener("click", stop);
document.getElementById("reset").addEventListener("click", () => {
  if (readOnly) return;
  localStorage.removeItem(PREFS_KEY);
  if (catalog) {
    speedEl.value = String(catalog.speed ?? 1);
    pauseEl.value = String(catalog.sentence_pause ?? 0.25);
    renderVoices(catalog.default_voice);
    updateDeliveryLabels();
  }
});
document.getElementById("newsline-reset").addEventListener("click", () => {
  if (readOnly) return;
  fillSpokenSelect("news-opening", "", { optional: true });
  fillSpokenSelect("news-station", spokenAnnouncements()[0]?.id || "");
  fillSpokenSelect("news-closing", "", { optional: true });
  document.getElementById("news-gap").value = "2";
  setStatus("Ready");
});
textEl.addEventListener("input", updateCount);
speedEl.addEventListener("input", () => {
  updateDeliveryLabels();
  persist();
});
pauseEl.addEventListener("input", () => {
  updateDeliveryLabels();
  persist();
});
voiceEl.addEventListener("change", persist);
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

async function saveNewslineSettings() {
  if (readOnly) return;
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      news_check_start: document.getElementById("news-check-start").value,
      news_check_stop: document.getElementById("news-check-stop").value,
      repeater_timeout_seconds: Number(document.getElementById("repeater-timeout").value),
    }),
  });
  refreshHome();
}

for (const id of ["news-check-start", "news-check-stop", "repeater-timeout"]) {
  document.getElementById(id).addEventListener("change", () => {
    saveNewslineSettings().catch(() => {});
  });
}

document.getElementById("newsline-check").addEventListener("click", async () => {
  const status = document.getElementById("newsline-status");
  if (status) status.textContent = "Checking for a new Newsline report…";
  const res = await fetch("/api/newsline/check", { method: "POST" });
  if (res.status === 409) {
    status.textContent = "A Newsline check is already running.";
    return;
  }
  if (!res.ok) {
    document.getElementById("newsline-status").textContent = "Could not start the Newsline check.";
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
  refreshHome();
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
  event.returnValue = "";
});

boot = loadDefaultScript();
updateCount();
updateDeliveryLabels();
updateAuthChrome();
route().catch(() => {});
