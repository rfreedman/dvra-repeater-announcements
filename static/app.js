const voiceEl = document.getElementById("voice");
const speedEl = document.getElementById("speed");
const pauseEl = document.getElementById("pause");
const speedValueEl = document.getElementById("speed-value");
const pauseValueEl = document.getElementById("pause-value");
const textEl = document.getElementById("text");
const countEl = document.getElementById("count");
const statusEl = document.getElementById("status");
const speakBtn = document.getElementById("speak");
const stopBtn = document.getElementById("stop");
const resetBtn = document.getElementById("reset");
const nameEl = document.getElementById("name");
const busyRetryEl = document.getElementById("busy-retry");
const busyGiveupEl = document.getElementById("busy-giveup");
const saveBtn = document.getElementById("save-schedule");
const backBtn = document.getElementById("back-grid");
const newBtn = document.getElementById("new-schedule");
const schedBody = document.getElementById("schedule-rows");
const schedEmpty = document.getElementById("schedule-empty");
const viewGrid = document.getElementById("view-grid");
const viewEdit = document.getElementById("view-editor");
const editTitle = document.getElementById("editor-title");
const formError = document.getElementById("editor-error");
const dailyTimesEl = document.getElementById("daily-times");
const weeklyTimesEl = document.getElementById("weekly-times");
const weeklyDaysEl = document.getElementById("weekly-days");
const hourlyMinutesEl = document.getElementById("hourly-minutes");
const exclusionListEl = document.getElementById("exclusion-rows");
const kindEl = document.getElementById("kind");
const PREFS_KEY = "booth-prefs";
const DAYS = [
  ["mon", "Mon"],
  ["tue", "Tue"],
  ["wed", "Wed"],
  ["thu", "Thu"],
  ["fri", "Fri"],
  ["sat", "Sat"],
  ["sun", "Sun"],
];

let catalog = null;
let player = null;
let abort = null;
let session = 0;
let editing = { announcementId: null, scheduleId: null };
let pollTimer = null;
let countdownTimer = null;
let upcoming = null;
let clockSkewMs = 0;
let gridBusy = false;
let defaultScript = "";
let editorBaseline = null;
let editorKey = "";
let ignoreHashChange = false;
let boot = Promise.resolve();

textEl.addEventListener("input", updateCount);
speakBtn.addEventListener("click", speak);
stopBtn.addEventListener("click", stop);
resetBtn.addEventListener("click", resetPrefs);
saveBtn.addEventListener("click", saveCurrent);
backBtn.addEventListener("click", () => {
  goToHash("#/");
});
newBtn.addEventListener("click", () => {
  goToHash("#/new");
});
voiceEl.addEventListener("change", persist);
speedEl.addEventListener("input", () => {
  updateDeliveryLabels();
  persist();
});
pauseEl.addEventListener("input", () => {
  updateDeliveryLabels();
  persist();
});
kindEl.addEventListener("change", syncKindFields);
document.getElementById("once-time").addEventListener("blur", () => {
  const el = document.getElementById("once-time");
  const next = normalizeHhMm(el.value);
  if (next) el.value = next;
});
document.getElementById("add-time").addEventListener("click", () => addListedTime(dailyTimesEl, "12:00"));
document.getElementById("add-weekly-time").addEventListener("click", () => addListedTime(weeklyTimesEl, "12:00"));
document.getElementById("add-hourly-minute").addEventListener("click", () => addHourlyMinute(0));
document.getElementById("add-exclusion").addEventListener("click", () => addExclusionRow());

function updateCount() {
  countEl.textContent = `${textEl.value.length} / ${textEl.maxLength}`;
}

function updateDeliveryLabels() {
  speedValueEl.textContent = `${Number(speedEl.value).toFixed(2)}×`;
  pauseValueEl.textContent = `${Number(pauseEl.value).toFixed(2)}s`;
}

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = kind;
}

function showFormError(text, kind = "error") {
  formError.hidden = !text;
  formError.textContent = text || "";
  formError.className = kind === "warning" ? "status-warning" : "status-error";
}

function currentKind() {
  return kindEl.value || "hourly";
}

function syncKindFields() {
  const kind = currentKind();
  document.getElementById("fields-hourly").classList.toggle("hidden", kind !== "hourly");
  document.getElementById("fields-daily").classList.toggle("hidden", kind !== "daily");
  document.getElementById("fields-weekly").classList.toggle("hidden", kind !== "weekly");
  document.getElementById("fields-once").classList.toggle("hidden", kind !== "once");
  document.getElementById("fields-exclusions").classList.toggle("hidden", kind === "once");
}

function normalizeHhMm(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  const match = text.match(/^(\d{1,2})(?::(\d{1,2}))?$/);
  if (!match) return text;
  const hour = Number(match[1]);
  const minute = Number(match[2] || "0");
  if (hour > 23 || minute > 59) return text;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function createTimeInput(value, className) {
  const input = document.createElement("input");
  input.type = "text";
  input.inputMode = "numeric";
  input.placeholder = "HH:MM";
  input.maxLength = 5;
  input.lang = "en-GB";
  input.className = ["time-24", className].filter(Boolean).join(" ");
  input.setAttribute("aria-label", "Time, 24-hour HH:MM");
  input.value = value || "";
  input.addEventListener("blur", () => {
    const next = normalizeHhMm(input.value);
    if (next) input.value = next;
  });
  return input;
}

function addHourlyMinute(value) {
  const row = document.createElement("div");
  row.className = "time-row";
  const input = document.createElement("input");
  input.type = "number";
  input.min = "0";
  input.max = "59";
  input.step = "1";
  input.value = String(value ?? 0);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => {
    if (hourlyMinutesEl.children.length > 1) row.remove();
  });
  row.append(input, remove);
  hourlyMinutesEl.append(row);
}

function addListedTime(container, value) {
  const row = document.createElement("div");
  row.className = "time-row";
  const input = createTimeInput(value || "12:00");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
  });
  row.append(input, remove);
  container.append(row);
}

function fillDayChips(container, selected) {
  container.replaceChildren();
  const chosen = selected || [];
  for (const [id, label] of DAYS) {
    const chip = document.createElement("label");
    chip.className = "day-chip";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = id;
    box.checked = chosen.includes(id);
    chip.append(box, document.createTextNode(label));
    container.append(chip);
  }
}

function readTimes(container) {
  return [...container.querySelectorAll(".time-24")].map((el) => normalizeHhMm(el.value)).filter(Boolean);
}

function readWeeklyDays() {
  return [...weeklyDaysEl.querySelectorAll('input[type="checkbox"]:checked')].map((el) => el.value);
}

function addExclusionRow(init = {}) {
  const row = document.createElement("div");
  row.className = "exclusion-row";
  const main = document.createElement("div");
  main.className = "exclusion-main";
  for (const [id, label] of DAYS) {
    const chip = document.createElement("label");
    chip.className = "day-chip";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = id;
    box.checked = (init.days || ["sun"]).includes(id);
    chip.append(box, document.createTextNode(label));
    main.append(chip);
  }
  const start = createTimeInput(
    init.start || (init.time && !init.end ? init.time : ""),
    "exclusion-start",
  );
  start.title = "Range start; leave blank with end blank to skip the whole day";
  const end = createTimeInput(
    init.end || (init.time && !init.start ? init.time : ""),
    "exclusion-end",
  );
  end.title = "Range end";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => row.remove());
  main.append(start, end, remove);
  const note = document.createElement("input");
  note.type = "text";
  note.className = "exclusion-note";
  note.placeholder = "Note (optional)";
  note.maxLength = 120;
  note.value = init.note || "";
  note.setAttribute("aria-label", "Exclusion note");
  row.append(main, note);
  exclusionListEl.append(row);
}

function readExclusions() {
  return [...exclusionListEl.querySelectorAll(".exclusion-row")]
    .map((row) => {
      const days = [...row.querySelectorAll('input[type="checkbox"]:checked')].map((el) => el.value);
      const start = normalizeHhMm(row.querySelector(".exclusion-start")?.value);
      const end = normalizeHhMm(row.querySelector(".exclusion-end")?.value);
      const note = row.querySelector(".exclusion-note")?.value.trim();
      if (!days.length) return null;
      const item = note ? { note } : {};
      if (start && end) return { kind: "day_time", days, start, end, ...item };
      if (!start && !end) return { kind: "day", days, ...item };
      return { kind: "day_time", days, start: start || end, end: end || start, ...item };
    })
    .filter(Boolean);
}

function readSchedulePayload() {
  const kind = currentKind();
  const payload = {
    id: editing.scheduleId || undefined,
    enabled: true,
    kind,
    exclusions: kind === "once" ? [] : readExclusions(),
  };
  if (kind === "hourly") {
    payload.minutes = [...hourlyMinutesEl.querySelectorAll('input[type="number"]')]
      .map((el) => Number(el.value))
      .filter((value) => Number.isFinite(value));
  } else if (kind === "daily") {
    payload.times = readTimes(dailyTimesEl);
  } else if (kind === "weekly") {
    payload.days = readWeeklyDays();
    payload.times = readTimes(weeklyTimesEl);
  } else {
    const date = document.getElementById("once-date").value;
    const time = normalizeHhMm(document.getElementById("once-time").value);
    if (date && time) payload.at = `${date}T${time}:00`;
  }
  return payload;
}

function fillScheduleForm(schedule) {
  dailyTimesEl.replaceChildren();
  weeklyTimesEl.replaceChildren();
  hourlyMinutesEl.replaceChildren();
  exclusionListEl.replaceChildren();
  const kind = schedule?.kind === "interval" ? "hourly" : schedule?.kind || "hourly";
  kindEl.value = ["hourly", "daily", "weekly", "once"].includes(kind) ? kind : "hourly";
  const minutes = schedule?.minutes?.length
    ? schedule.minutes
    : [schedule?.minute ?? 0];
  minutes.forEach((minute) => addHourlyMinute(minute));
  if (!hourlyMinutesEl.children.length) addHourlyMinute(0);
  if (kind === "daily" && schedule?.times?.length) {
    schedule.times.forEach((time) => addListedTime(dailyTimesEl, time));
  } else {
    addListedTime(dailyTimesEl, "09:00");
  }
  fillDayChips(weeklyDaysEl, kind === "weekly" ? schedule?.days : []);
  if (kind === "weekly" && schedule?.times?.length) {
    schedule.times.forEach((time) => addListedTime(weeklyTimesEl, time));
  } else {
    addListedTime(weeklyTimesEl, "19:00");
  }
  if (kind === "once" && schedule?.at) {
    const dt = new Date(schedule.at);
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, "0");
    const d = String(dt.getDate()).padStart(2, "0");
    const hh = String(dt.getHours()).padStart(2, "0");
    const mm = String(dt.getMinutes()).padStart(2, "0");
    document.getElementById("once-date").value = `${y}-${m}-${d}`;
    document.getElementById("once-time").value = `${hh}:${mm}`;
  }
  (schedule?.exclusions || []).forEach((item) => addExclusionRow(item));
  syncKindFields();
}

async function loadDefaultScript() {
  const res = await fetch("/static/default-announcement.txt");
  if (!res.ok) throw new Error("Could not load default announcement");
  defaultScript = (await res.text()).trim();
  if (!editing.announcementId && !textEl.value) {
    textEl.value = defaultScript;
    updateCount();
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

function renderVoices(preferred) {
  const selected = preferred || catalog.default_voice;
  voiceEl.replaceChildren();
  for (const voice of catalog.voices) {
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

function resetPrefs() {
  localStorage.removeItem(PREFS_KEY);
  if (catalog) {
    speedEl.value = String(catalog.speed ?? 1);
    pauseEl.value = String(catalog.sentence_pause ?? 0.25);
    renderVoices(catalog.default_voice);
    updateDeliveryLabels();
  }
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

function renderUpcoming() {
  const box = document.getElementById("next-run");
  if (!box) return;
  if (!upcoming) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  document.getElementById("next-run-name").textContent = upcoming.announcement_name || "Untitled";
  document.getElementById("next-run-when").textContent = upcoming.at ? formatWhen(upcoming.at) : "—";
  const statusEl = document.getElementById("next-run-status");
  const running = upcoming.status === "running";
  statusEl.textContent = running ? "Running" : "Waiting";
  statusEl.classList.toggle("is-running", running);
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

async function fetchSchedulePayload() {
  const res = await fetch("/api/schedules");
  if (!res.ok) throw new Error("Could not load schedules");
  const payload = await res.json();
  if (payload.now) {
    const serverNow = Date.parse(payload.now);
    if (!Number.isNaN(serverNow)) clockSkewMs = serverNow - Date.now();
  }
  upcoming = payload.upcoming || null;
  renderUpcoming();
  return payload;
}

function tickUpcoming() {
  renderUpcoming();
  if (!upcoming) return;
  if (upcoming.status === "running") {
    fetchSchedulePayload().catch(() => {});
    return;
  }
  if (!upcoming.at) return;
  const left = new Date(upcoming.at).getTime() - (Date.now() + clockSkewMs);
  if (left <= 15000) fetchSchedulePayload().catch(() => {});
}

async function refreshGrid() {
  if (gridBusy) return;
  gridBusy = true;
  try {
    const payload = await fetchSchedulePayload();
    const rows = payload.schedules || [];
    schedBody.replaceChildren();
    schedEmpty.classList.toggle("hidden", rows.length > 0);
    for (const row of rows) {
      const tr = document.createElement("tr");
      if (row.warning) tr.classList.add("has-warning");
      const warning = row.warning
        ? `<div class="sched-warning">${escapeHtml(row.warning)}</div>`
        : "";
      tr.innerHTML = `
      <td>${escapeHtml(row.announcement_name)}</td>
      <td>${summaryHtml(row.summary)}${warning}</td>
      <td>${escapeHtml(row.last_run_at ? formatWhen(row.last_run_at) : "Never")}</td>
      <td>${escapeHtml(row.next_run_at ? formatWhen(row.next_run_at) : "—")}</td>
      <td class="row-actions"></td>
    `;
      const dup = document.createElement("button");
      dup.type = "button";
      dup.className = "ghost";
      dup.textContent = "Duplicate";
      dup.addEventListener("click", (event) => {
        event.stopPropagation();
        goToHash(rowHash("copy", row));
      });
      const del = document.createElement("button");
      del.type = "button";
      del.className = "danger";
      del.textContent = "Delete";
      del.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSchedule(row);
      });
      tr.querySelector(".row-actions").append(dup, del);
      tr.addEventListener("click", () => {
        goToHash(rowHash("edit", row));
      });
      schedBody.append(tr);
    }
  } finally {
    gridBusy = false;
  }
}

function rowHash(kind, row) {
  return row.schedule_id
    ? `#/${kind}/${row.announcement_id}/${row.schedule_id}`
    : `#/${kind}/${row.announcement_id}`;
}

function goToHash(hash) {
  const current = location.hash || "#/";
  if (current === hash || (hash === "#/" && (current === "" || current === "#"))) {
    route();
    return;
  }
  location.hash = hash;
}

function summaryHtml(summary) {
  const lines = String(summary || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return "";
  const [base, ...rest] = lines;
  if (!rest.length) return escapeHtml(base);
  const extras = rest
    .map((line) => `<div class="exclusion-line">${escapeHtml(line)}</div>`)
    .join("");
  return `${escapeHtml(base)}${extras}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function deleteSchedule(row) {
  const ok = window.confirm(
    row.schedule_id
      ? `Delete schedule “${String(row.summary || "").split("\n")[0]}” on ${row.announcement_name}?`
      : `Delete announcement “${row.announcement_name}”?`,
  );
  if (!ok) return;
  const url = row.schedule_id
    ? `/api/announcements/${row.announcement_id}/schedules/${row.schedule_id}`
    : `/api/announcements/${row.announcement_id}`;
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) {
    setStatus("Could not delete", "error");
    return;
  }
  await refreshGrid();
}

function showGrid() {
  viewGrid.classList.add("active");
  viewEdit.classList.remove("active");
  if (!pollTimer) pollTimer = setInterval(() => refreshGrid().catch(() => {}), 15000);
  if (!countdownTimer) countdownTimer = setInterval(tickUpcoming, 1000);
  refreshGrid().catch((err) => setStatus(err.message, "error"));
}

function showEdit() {
  viewGrid.classList.remove("active");
  viewEdit.classList.add("active");
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function formSnapshot() {
  return JSON.stringify({
    name: nameEl.value,
    text: textEl.value,
    voice: voiceEl.value,
    speed: speedEl.value,
    pause: pauseEl.value,
    busyRetry: busyRetryEl.value,
    busyGiveup: busyGiveupEl.value,
    schedule: readSchedulePayload(),
  });
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

function routeKey(hash = location.hash) {
  const raw = String(hash || "").replace(/^#/, "") || "/";
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "new") return "new";
  if ((parts[0] === "copy" || parts[0] === "edit") && parts[1]) {
    return `${parts[0]}/${parts[1]}/${parts[2] || ""}`;
  }
  return "grid";
}

function hashForKey(key) {
  if (!key || key === "grid") return "#/";
  if (key === "new") return "#/new";
  return `#/${key}`;
}

function confirmDiscardEdits() {
  return window.confirm("You have unsaved changes. If you leave, those changes will be lost.");
}

function restoreEditorHash() {
  ignoreHashChange = true;
  history.replaceState(null, "", hashForKey(editorKey));
  ignoreHashChange = false;
}

async function route() {
  await boot;
  const parts = (location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  const nextKey = routeKey();
  if (nextKey !== editorKey && isEditorDirty()) {
    if (!confirmDiscardEdits()) {
      restoreEditorHash();
      return;
    }
    clearEditorGuard();
  }
  if (nextKey === editorKey && nextKey !== "grid") {
    return;
  }
  showFormError("");
  if (parts[0] === "new") {
    editing = { announcementId: null, scheduleId: null };
    editTitle.textContent = "New schedule";
    nameEl.value = "";
    busyRetryEl.value = "5";
    busyGiveupEl.value = "45";
    textEl.value = defaultScript;
    updateCount();
    fillScheduleForm(null);
    showEdit();
    editorKey = nextKey;
    markEditorClean();
    return;
  }
  if (parts[0] === "copy" && parts[1]) {
    editing = { announcementId: null, scheduleId: null };
    editTitle.textContent = "New schedule";
    showEdit();
    try {
      await loadAnnouncement(parts[1], parts[2] || null, { asCopy: true });
      nameEl.focus();
    } catch (err) {
      showFormError(err.message);
    }
    editorKey = nextKey;
    markEditorClean();
    return;
  }
  if (parts[0] === "edit" && parts[1]) {
    editing = { announcementId: parts[1], scheduleId: parts[2] || null };
    editTitle.textContent = "Edit schedule";
    showEdit();
    try {
      await loadAnnouncement(parts[1], parts[2] || null);
    } catch (err) {
      showFormError(err.message);
    }
    editorKey = nextKey;
    markEditorClean();
    return;
  }
  editing = { announcementId: null, scheduleId: null };
  editorKey = "grid";
  clearEditorGuard();
  showGrid();
}

async function loadAnnouncement(announcementId, scheduleId, { asCopy = false } = {}) {
  const res = await fetch(`/api/announcements/${announcementId}`);
  if (!res.ok) throw new Error("Announcement not found");
  const item = await res.json();
  nameEl.value = asCopy ? "" : item.name || "";
  textEl.value = item.text || "";
  busyRetryEl.value = String(item.busy_retry_seconds ?? 5);
  busyGiveupEl.value = String(item.busy_give_up_seconds ?? 45);
  speedEl.value = String(item.speed ?? 1);
  pauseEl.value = String(item.sentence_pause ?? 0.25);
  updateDeliveryLabels();
  updateCount();
  if (item.voice) renderVoices(item.voice);
  const schedule = scheduleId
    ? (item.schedules || []).find((row) => row.id === scheduleId)
    : null;
  if (scheduleId && !schedule) throw new Error("Schedule not found");
  fillScheduleForm(schedule || null);
  if (asCopy) return;
  const warnings = [];
  if (!(item.text || "").trim()) warnings.push("announcement text is empty");
  if (!schedule) warnings.push("no schedule is set");
  if (warnings.length) {
    showFormError("Won't play until you fix this: " + warnings.join(" and ") + ".", "warning");
  }
}

async function saveCurrent() {
  showFormError("");
  const body = {
    name: nameEl.value.trim(),
    text: textEl.value.trim(),
    voice: voiceEl.value,
    speed: Number(speedEl.value),
    sentence_pause: Number(pauseEl.value),
    busy_retry_seconds: Number(busyRetryEl.value),
    busy_give_up_seconds: Number(busyGiveupEl.value),
    schedule: readSchedulePayload(),
  };
  if (!body.name) {
    showFormError("Name is required.");
    return;
  }
  if (!body.text) {
    showFormError("Announcement text is required.");
    return;
  }
  if (body.schedule?.kind === "weekly" && !body.schedule.days?.length) {
    showFormError("Select at least one day.");
    return;
  }
  if (
    (body.schedule?.kind === "daily" || body.schedule?.kind === "weekly") &&
    !body.schedule.times?.length
  ) {
    showFormError("Add at least one time.");
    return;
  }
  persist();
  const isNew = !editing.announcementId;
  const url = isNew ? "/api/announcements" : `/api/announcements/${editing.announcementId}`;
  const res = await fetch(url, {
    method: isNew ? "POST" : "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const payload = await res.json();
      detail = payload.detail || detail;
    } catch {
      /* ignore */
    }
    showFormError(typeof detail === "string" ? detail : "Could not save");
    return;
  }
  clearEditorGuard();
  location.hash = "#/";
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
        voice: voiceEl.value,
        speed: Number(speedEl.value),
        sentence_pause: Number(pauseEl.value),
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

boot = Promise.all([
  loadVoices().catch((err) => setStatus(err.message, "error")),
  loadDefaultScript().catch((err) => setStatus(err.message, "error")),
]);
window.addEventListener("hashchange", () => {
  if (ignoreHashChange) return;
  route();
});
window.addEventListener("beforeunload", (event) => {
  if (!isEditorDirty()) return;
  event.preventDefault();
  event.returnValue = "";
});
updateCount();
updateDeliveryLabels();
route();
