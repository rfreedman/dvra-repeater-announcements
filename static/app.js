const PREFS_KEY = "announcements-prefs";
const NEW_ANNOUNCEMENT = {
  voice: "en_US-ryan-medium",
  speed: 0.8,
  sentence_pause: 0.25,
  busy_retry_seconds: 30,
  busy_give_up_seconds: 300,
};
const DAYS = [
  ["mon", "Mon"],
  ["tue", "Tue"],
  ["wed", "Wed"],
  ["thu", "Thu"],
  ["fri", "Fri"],
  ["sat", "Sat"],
  ["sun", "Sun"],
];
const MONTHS = [
  [1, "Jan"],
  [2, "Feb"],
  [3, "Mar"],
  [4, "Apr"],
  [5, "May"],
  [6, "Jun"],
  [7, "Jul"],
  [8, "Aug"],
  [9, "Sep"],
  [10, "Oct"],
  [11, "Nov"],
  [12, "Dec"],
];
const TEMPLATES = {
  baseline: { name: "Baseline", kind: "baseline", offset_minutes: 0 },
  weekly: {
    name: "Weekly",
    kind: "weekly",
    days: ["sun"],
    slots: ["21:00"],
    offset_minutes: -5,
    priority: 50,
  },
  monthly: {
    name: "Monthly",
    kind: "monthly",
    days: ["wed"],
    occurrence: 1,
    slots: ["19:00"],
    offset_minutes: -5,
    skip_months: [],
    priority: 50,
  },
  range: { name: "Event countdown", kind: "range", offset_minutes: 0, priority: 20 },
  monthly_range: {
    name: "Monthly countdown",
    kind: "monthly_range",
    days: ["wed"],
    occurrence: 1,
    event_slot: "19:00",
    days_before: 7,
    offset_minutes: 0,
    priority: 20,
  },
  once: { name: "Once", kind: "once", slots: ["12:00"], offset_minutes: 0, priority: 50 },
  silence: {
    name: "Silence",
    kind: "weekly",
    days: ["sun"],
    slots: ["21:30"],
    offset_minutes: 0,
    announcement_id: null,
    priority: 50,
  },
  emergency: { name: "Emergency", kind: "emergency", offset_minutes: 0, priority: 100 },
};

const voiceEl = document.getElementById("voice");
const speedEl = document.getElementById("speed");
const pauseEl = document.getElementById("pause");
const textEl = document.getElementById("text");
const nameEl = document.getElementById("name");

let catalog = null;
let player = null;
let abort = null;
let session = 0;
let pollTimer = null;
let countdownTimer = null;
let upcoming = null;
let clockSkewMs = 0;
let homeBusy = false;
let ledFailed = false;
let ledBlinkTimer = null;
let ledBlinkUntil = 0;
let defaultScript = "";
let announcements = [];
let settings = { slot_half_window_minutes: 10, baseline_randomize: false };
let triggerNowEnabled = false;
let triggerNowBusy = false;
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
  ];
}

function scheduleEditorControls() {
  return [
    document.getElementById("sched-name"),
    document.getElementById("sched-kind"),
    document.getElementById("sched-announcement"),
    document.getElementById("sched-offset"),
    document.getElementById("monthly-occurrence"),
    document.getElementById("once-date"),
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
  const lockAnnouncement = readOnly;
  for (const el of announcementEditorControls()) {
    if (!el) continue;
    el.disabled = lockAnnouncement;
  }
  document.getElementById("speak").disabled = false;
  document.getElementById("stop").disabled = !abort;
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

function renderUpcoming() {
  const box = document.getElementById("next-run");
  if (!box) return;
  if (!upcoming) {
    box.classList.add("hidden");
    syncTriggerNowButtons();
    return;
  }
  box.classList.remove("hidden");
  document.getElementById("next-run-name").textContent = upcoming.announcement_name || "Untitled";
  document.getElementById("next-run-when").textContent = upcoming.at ? formatWhen(upcoming.at) : "—";
  const statusEl = document.getElementById("next-run-status");
  const running = upcoming.status === "running";
  statusEl.textContent = running ? "Running" : "Waiting";
  statusEl.classList.toggle("is-running", running);
  syncTriggerNowButtons();
  const countEl = document.getElementById("next-run-countdown");
  if (running) {
    countEl.textContent = "Now";
    return;
  }
  const at = upcoming.at ? new Date(upcoming.at).getTime() : NaN;
  if (Number.isNaN(at)) {
    countEl.textContent = "—";
    return;
  }
  countEl.textContent = `in ${formatCountdown(at - (Date.now() + clockSkewMs))}`;
}

function announcementIsPlaying() {
  return triggerNowBusy || upcoming?.status === "running";
}

function syncTriggerNowButtons() {
  const playing = announcementIsPlaying();
  for (const button of document.querySelectorAll("[data-trigger-now]")) {
    button.disabled = playing;
  }
}

function tickUpcoming() {
  renderUpcoming();
  if (!upcoming) return;
  if (upcoming.status === "running" || (upcoming.at && new Date(upcoming.at).getTime() - (Date.now() + clockSkewMs) <= 15000)) {
    refreshHome().catch(() => {});
  }
}

function fillDayChips(container, selected, { exclusive = false } = {}) {
  container.replaceChildren();
  for (const [value, label] of DAYS) {
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
  for (const [value, label] of MONTHS) {
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
  if (allowSilence) {
    const silence = document.createElement("option");
    silence.value = "";
    silence.textContent = "Silence — no transmission";
    el.append(silence);
  }
  for (const item of announcements) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    el.append(option);
  }
  if (selected === null || selected === "") {
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
  document.getElementById("fields-once").classList.toggle("hidden", kind !== "once");
  document.getElementById("fields-range").classList.toggle("hidden", kind !== "range");
  document.getElementById("fields-days-before").classList.toggle("hidden", kind !== "monthly_range");
  document.getElementById("fields-event-end").classList.toggle("hidden", !isCountdownKind(kind));
  const showSlots =
    kind === "weekly" ||
    kind === "monthly" ||
    kind === "once" ||
    (isCountdownKind(kind) && !document.getElementById("range-all-slots").checked);
  document.getElementById("fields-slots").classList.toggle("hidden", !showSlots);
  document.getElementById("field-priority").classList.toggle("hidden", kind === "baseline" || kind === "emergency");
  document.getElementById("silence-hint").classList.toggle("hidden", kind === "baseline");
  fillAnnouncementSelect(document.getElementById("sched-announcement").value, { allowSilence: kind !== "baseline" });
  const windowMin = settings.slot_half_window_minutes ?? 10;
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
  if (kind === "once") payload.on_date = document.getElementById("once-date").value || null;
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
    kind === "once" ||
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
  document.getElementById("sched-priority").value = String(
    schedule?.priority || (isCountdownKind(kind) ? 20 : 50),
  );
  fillDayChips(document.getElementById("weekly-days"), schedule?.days || ["sun"]);
  fillDayChips(document.getElementById("monthly-weekday"), schedule?.days?.[0] || "wed", { exclusive: true });
  document.getElementById("monthly-occurrence").value = String(schedule?.occurrence || 1);
  fillMonthSkip(document.getElementById("monthly-skip"), schedule?.skip_months || []);
  document.getElementById("once-date").value = schedule?.on_date || "";
  document.getElementById("range-start").value = schedule?.start_date || "";
  document.getElementById("range-event-date").value = schedule?.event_date || "";
  document.getElementById("days-before").value = String(schedule?.days_before ?? 7);
  fillEventSlotSelect(schedule?.event_slot || "19:00");
  document.getElementById("range-all-slots").checked = !isCountdownKind(kind) || !(schedule?.slots || []).length;
  fillSlotChips(schedule?.slots || (kind === "weekly" ? ["21:00"] : kind === "monthly" ? ["19:00"] : ["12:00"]));
  fillAnnouncementSelect(schedule?.announcement_id ?? announcements[0]?.id, { allowSilence: kind !== "baseline" });
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
      del.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (!window.confirm(`Delete schedule “${row.name}”?`)) return;
        const res = await fetch(`/api/schedules/${row.schedule_id}`, { method: "DELETE" });
        if (res.ok) refreshHome();
      });
      actions.append(del);
      if (triggerNowEnabled) {
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "ghost";
        trigger.dataset.triggerNow = "1";
        trigger.textContent = "Trigger now";
        trigger.disabled = announcementIsPlaying();
        trigger.addEventListener("click", async (event) => {
          event.stopPropagation();
          if (announcementIsPlaying()) return;
          triggerNowBusy = true;
          syncTriggerNowButtons();
          try {
            const res = await fetch(`/api/schedules/${row.schedule_id}/trigger`, { method: "POST" });
            let payload = {};
            try {
              payload = await res.json();
            } catch {
              /* ignore */
            }
            if (!res.ok) {
              const detail = payload.detail || res.statusText;
              window.alert(typeof detail === "string" ? detail : "Could not trigger");
            }
          } finally {
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

function renderLibrary(items) {
  const body = document.getElementById("library-rows");
  const empty = document.getElementById("library-empty");
  body.replaceChildren();
  empty.classList.toggle("hidden", items.length > 0);
  for (const item of items) {
    const tr = document.createElement("tr");
    const preview = (item.text || "").replace(/\s+/g, " ").slice(0, 90);
    tr.innerHTML = `
      <td>${escapeHtml(item.name)}</td>
      <td>${escapeHtml(preview)}${preview.length === 90 ? "…" : ""}</td>
      <td class="row-actions"></td>
    `;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "Delete";
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!window.confirm(`Delete announcement “${item.name}”?`)) return;
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

async function refreshHome() {
  if (homeBusy) return;
  homeBusy = true;
  try {
    const dateField = document.getElementById("clock-date");
    const dateQuery = dateField.value ? `?clock_date=${dateField.value}` : "";
    const [schedRes, libRes, statsRes] = await Promise.all([
      fetch(`/api/schedules${dateQuery}`),
      fetch("/api/announcements"),
      fetch("/api/system/stats"),
    ]);
    if (!schedRes.ok) throw new Error("Could not load schedules");
    const payload = await schedRes.json();
    const library = libRes.ok ? await libRes.json() : { announcements: [] };
    announcements = library.announcements || [];
    settings = payload.settings || settings;
    triggerNowEnabled = Boolean(payload.trigger_now_enabled);
    if (payload.now) {
      const serverNow = Date.parse(payload.now);
      if (!Number.isNaN(serverNow)) clockSkewMs = serverNow - Date.now();
    }
    upcoming = payload.upcoming || null;
    renderUpcoming();
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
    syncTriggerNowButtons();
    if (statsRes.ok) {
      renderSystemStats(await statsRes.json());
    } else {
      renderSystemStats({ celsius: null, cpu_percent: null, memory_percent: null });
    }
  } finally {
    homeBusy = false;
  }
}

function renderSystemStats(payload) {
  const chip = document.getElementById("system-stats");
  const tempEl = document.getElementById("system-stats-temp");
  const cpuEl = document.getElementById("system-stats-cpu");
  const memEl = document.getElementById("system-stats-mem");
  if (!chip || !tempEl || !cpuEl || !memEl) return;

  const celsius = payload && payload.celsius != null ? Number(payload.celsius) : null;
  const cpuPercent = payload && payload.cpu_percent != null ? Number(payload.cpu_percent) : null;
  const memoryPercent =
    payload && payload.memory_percent != null ? Number(payload.memory_percent) : null;

  let band = "na";
  if (celsius != null && !Number.isNaN(celsius)) {
    if (celsius < 55) band = "cool";
    else if (celsius < 70) band = "warm";
    else band = "hot";
  }
  chip.classList.remove("temp-cool", "temp-warm", "temp-hot", "temp-na");
  chip.classList.add(`temp-${band}`);

  tempEl.textContent = band === "na" ? "—" : `${Math.round(celsius)}°C`;
  cpuEl.textContent =
    cpuPercent != null && !Number.isNaN(cpuPercent) ? `${Math.round(cpuPercent)}%` : "—";
  memEl.textContent =
    memoryPercent != null && !Number.isNaN(memoryPercent) ? `${Math.round(memoryPercent)}%` : "—";

  const parts = [
    `CPU temp ${tempEl.textContent}`,
    `CPU ${cpuEl.textContent}`,
    `MEM ${memEl.textContent}`,
  ];
  chip.title = parts.join(" · ");
}

function startHomePolling() {
  if (!pollTimer) pollTimer = setInterval(() => refreshHome().catch(() => {}), 15000);
  if (!countdownTimer) countdownTimer = setInterval(tickUpcoming, 1000);
}

function stopHomePolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
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
  const el = document.getElementById("status");
  el.textContent = text;
  el.className = kind;
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

function blankAnnouncement() {
  editingAnnouncementId = null;
  document.getElementById("announcement-title").textContent = "New announcement";
  nameEl.value = "";
  textEl.value = defaultScript;
  speedEl.value = String(NEW_ANNOUNCEMENT.speed);
  pauseEl.value = String(NEW_ANNOUNCEMENT.sentence_pause);
  document.getElementById("busy-retry").value = String(NEW_ANNOUNCEMENT.busy_retry_seconds);
  document.getElementById("busy-giveup").value = String(NEW_ANNOUNCEMENT.busy_give_up_seconds);
  if (catalog) renderVoices(NEW_ANNOUNCEMENT.voice);
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
  nameEl.value = item.name || "";
  textEl.value = item.text || "";
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
  const body = {
    name: nameEl.value.trim(),
    text: textEl.value.trim(),
    voice: voiceEl.value,
    speed: Number(speedEl.value),
    sentence_pause: Number(pauseEl.value),
    busy_retry_seconds: Number(document.getElementById("busy-retry").value),
    busy_give_up_seconds: Number(document.getElementById("busy-giveup").value),
  };
  if (!body.name) {
    showError("announcement-error", "Name is required.");
    return;
  }
  if (!body.text) {
    showError("announcement-error", "Announcement text is required.");
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
  document.getElementById("speak").disabled = true;
  document.getElementById("stop").disabled = false;
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
    document.getElementById("speak").disabled = false;
    document.getElementById("stop").disabled = true;
  }
}

function stop() {
  const hadPlayback = Boolean(abort || player);
  cancelInFlight();
  document.getElementById("speak").disabled = false;
  document.getElementById("stop").disabled = true;
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
  const auth = await refreshAuth();
  authReady = true;
  if (auth.needsSetup) {
    stopHomePolling();
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
    stopHomePolling();
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
  const nextKey = routeKey();
  if (nextKey !== editorKey && isEditorDirty() && !confirmDiscardEdits()) {
    ignoreHashChange = true;
    history.replaceState(null, "", editorKey === "home" ? "#/" : location.hash);
    ignoreHashChange = false;
    return;
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
    showScreen("view-users");
    editorKey = "users";
    clearEditorGuard();
    resetUserForm();
    await refreshUsers();
    return;
  }
  if (parts[0] === "help") {
    stopHomePolling();
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
    await ensureVoices().catch(() => {});
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
    if (!announcements.length) {
      const lib = await fetch("/api/announcements");
      announcements = lib.ok ? (await lib.json()).announcements || [] : [];
    }
    if (parts[1] === "new") {
      editingScheduleId = null;
      document.getElementById("schedule-title").textContent = "New schedule";
      const template = TEMPLATES[params.get("template") || "baseline"] || TEMPLATES.baseline;
      fillScheduleForm(template);
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
  await refreshHome();
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

document.getElementById("baseline-randomize").addEventListener("change", async (event) => {
  if (readOnly) return;
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ baseline_randomize: event.target.checked }),
  });
  refreshHome();
});

window.addEventListener("hashchange", () => {
  if (ignoreHashChange) return;
  route().catch((err) => window.alert(err.message));
});
window.addEventListener("beforeunload", (event) => {
  if (!isEditorDirty()) return;
  event.preventDefault();
  event.returnValue = "";
});

boot = loadDefaultScript();
updateCount();
updateDeliveryLabels();
updateAuthChrome();
route().catch((err) => window.alert(err.message));
