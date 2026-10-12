#!/usr/bin/env bash
# Create GitHub issues from the Oct 2026 code-review backlog. Idempotent-ish: skips titles that already exist.
# Requires: gh auth login
set -euo pipefail

cd "$(dirname "$0")/.."
GH="${GH:-gh}"
if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
  REPO="$GITHUB_REPOSITORY"
else
  REPO="$("$GH" repo view --json nameWithOwner -q .nameWithOwner)"
fi

if ! "$GH" auth status >/dev/null 2>&1; then
  echo "Run: gh auth login" >&2
  exit 1
fi

label_ensure() {
  local name="$1" color="$2" desc="$3"
  if ! "$GH" label list --repo "$REPO" --json name --jq ".[] | select(.name==\"$name\") | .name" | grep -qx "$name"; then
    "$GH" label create "$name" --repo "$REPO" --color "$color" --description "$desc" || true
  fi
}

label_ensure tech-debt b60205 "Refactor or consistency; no urgent bug"
label_ensure enhancement 0075ca "New behavior or product improvement"
label_ensure documentation 0e8a16 "Docs or operator-facing notes"
label_ensure frontend 5319e7 "static/app.js or UI"
label_ensure testing fbca04 "Test coverage or bench verification"
label_ensure security d73a4a "Auth, sessions, or abuse limits"

issue_exists() {
  local title="$1"
  "$GH" issue list --repo "$REPO" --state all --search "in:title $(printf %q "$title")" --json title --jq '.[].title' 2>/dev/null | grep -Fxq "$title"
}

create_issue() {
  local title="$1"
  shift
  local labels=()
  while [[ $# -gt 1 ]]; do
    labels+=("$1")
    shift
  done
  local body="$1"
  if issue_exists "$title"; then
    echo "SKIP (exists): $title"
    return 0
  fi
  local args=(issue create --repo "$REPO" --title "$title" --body "$body")
  for L in "${labels[@]}"; do
    args+=(--label "$L")
  done
  "$GH" "${args[@]}"
  echo "CREATED: $title"
}

# --- High priority (consistency / small diffs) ---

create_issue "Align Announcement model with API validation bounds" tech-debt \
"## Context
Code review (Oct 2026). \`AnnouncementIn\` / \`SpeakRequest\` in \`app/server.py\` enforce text length, speed (\`gt=0.25, le=2.0\`), sentence pause, and busy retry caps. \`Announcement\` in \`app/models.py\` does not, so non-API paths or future code could persist invalid data.

## Task
- Add matching validators/Field constraints on \`Announcement\`, or document a single validation entry point and enforce it everywhere saves occur.
- Regression test: invalid announcement rejected on PUT/POST and via \`Schedule.model_validate\` paths if applicable.

## References
- \`app/server.py\` (\`SpeakRequest\`, \`AnnouncementIn\`)
- \`app/models.py\` (\`Announcement\`)"

create_issue "Use SLOT_MINUTES in is_slot_stamp" tech-debt \
"## Context
Slot times are defined as \`SLOT_MINUTES = (0, 30)\` in \`app/slots.py\`, but \`is_slot_stamp()\` in \`app/models.py\` hard-codes \`minute in (0, 30)\`.

## Task
Import or share one constant; avoid drift if slot rules ever change.

## Files
- \`app/models.py\` \`is_slot_stamp\`
- \`app/slots.py\` \`SLOT_MINUTES\`"

create_issue "Name scheduler defer and manual-nudge timing constants" tech-debt \
"## Context
Behavior-critical literals are scattered in \`app/fire.py\` and \`app/scheduler.py\` (e.g. \`0.5\`, \`1.0\`, \`2.0\`, \`0.2\`, \`50\` ms nudge). Grace periods already use named constants (\`SLOT_MISFIRE_GRACE_SECONDS\`, etc.).

## Task
- Promote remaining magic numbers to module-level named constants with brief comments tying them to COS poll / retry behavior.
- Optional: reference in README operator tuning section if any become env-configurable later.

## Files
- \`app/fire.py\` (\`defer_retry_delay\`)
- \`app/scheduler.py\` (\`nudge_manual_defer_jobs_if_clear_ready\`)"

# --- Medium priority ---

create_issue "Extract atomic JSON write helper for data stores" tech-debt \
"## Context
\`AnnouncementStore._write\` and \`UserStore._write\` duplicate tmp-file + \`replace\` atomic write logic.

## Task
- Shared helper (e.g. \`write_json_atomic(path, dict)\`) in \`app/store.py\` or small \`app/persist.py\`.
- Keep behavior identical; add test if not covered."

create_issue "Add named constants for frontend poll intervals and template priorities" frontend tech-debt \
"## Context
\`static/app.js\` uses magic numbers: \`15000\` ms polls, \`1000\` ms countdown, \`15000\` upcoming window, \`600_000\` trigger timeout, default \`slot_half_window_minutes: 10\`, and \`TEMPLATES\` priorities \`50\` / \`20\` / \`100\` mirroring \`DEFAULT_PRIORITY\` in Python.

## Task
- Top-of-file named constants (and comments linking to server defaults / \`app/auth.py\` \`BACKGROUND_POLL_HEADER\` pattern).
- Avoid hard-coding new announcement defaults in JS (already partially fixed via \`/api/voices\`; extend if needed).

## File
- \`static/app.js\`"

create_issue "Type manual-trigger pending rows instead of dict[str, object]" tech-debt \
"## Context
\`app/scheduler.py\` uses loose typing (\`_running\`, \`_manual_inflight\`, \`pending_manual_triggers()\` return type) which obscures fields the UI depends on (\`phase\`, \`clear_remaining_seconds\`, etc.).

## Task
- \`TypedDict\` or small dataclass for pending manual trigger rows and running state.
- No behavior change; improves maintainability and mypy potential."

create_issue "Shared Pydantic model for TTS speed and sentence pause fields" tech-debt \
"## Context
\`SpeakRequest\` and \`AnnouncementIn\` duplicate identical \`speed\` and \`sentence_pause\` Field definitions in \`app/server.py\`.

## Task
- Compose a shared \`TtsSpeechParams\` (or similar) model into both request types."

create_issue "Deduplicate name_present validators on Announcement and Schedule" tech-debt \
"## Context
Identical \`name_present\` \`@field_validator\` on \`Announcement\` and \`Schedule\` in \`app/models.py\`.

## Task
- Shared validator function or mixin; same behavior."

create_issue "Consolidate emergency schedule priority constants" tech-debt \
"## Context
\`DEFAULT_PRIORITY[\"emergency\"]\` and \`EMERGENCY_PRIORITY = 100\` in \`app/models.py\`; \`kind_fields\` sets priority in multiple branches.

## Task
- Single source for emergency priority; simplify validator logic."

create_issue "Split app/server.py into routes and schemas modules" tech-debt enhancement \
"## Context
\`app/server.py\` is ~700+ lines: lifespan, many Pydantic DTOs, CRUD, streaming speak.

## Task
- Move DTOs to \`app/schemas.py\` (or similar).
- Optional: split routers (\`announcements\`, \`schedules\`, \`auth\`, \`speak\`) included from a thin \`server.py\`.
- No API behavior change."

create_issue "Extract manual-trigger state and macOS idle assertion from scheduler" tech-debt \
"## Context
\`app/scheduler.py\` is ~800 lines mixing APScheduler sync, manual trigger UX state, playback lock, and macOS IOPM ctypes.

## Task
- Extract \`manual_trigger_state.py\` (pending rows, inflight, nudge).
- Extract \`mac_idle.py\` or platform helper for \`_inhibit_idle_sleep\`.
- Keep tests green."

create_issue "Constant for CM119 HID read timeout" tech-debt \
"## Context
\`read_hid_sample(..., timeout_ms: int = 300)\` in \`app/cm119.py\` uses a magic default shared with bench script.

## Task
- Module-level \`HID_READ_TIMEOUT_MS\` (optional env for bench only)."

create_issue "Name DRA PTT key retry delay tuple" tech-debt documentation \
"## Context
\`app/dra_radio.py\` uses \`(0.0, 0.03, 0.08, 0.15, 0.3)\` for PTT retries without a named constant or hardware doc link.

## Task
- Named tuple/constant + one-line comment referencing bench tuning."

# --- Low / optional ---

create_issue "Make login rate limiter limits configurable via env" enhancement security \
"## Context
\`LoginRateLimiter\` in \`app/auth.py\` hard-codes 5 failures / 300 s window.

## Task
- Optional \`TTS_LOGIN_MAX_FAILURES\`, \`TTS_LOGIN_WINDOW_SECONDS\` with current defaults.
- Document in README and \`.env.example\`."

create_issue "Remove deprecated serve --preload CLI flag" tech-debt \
"## Context
\`python -m app serve --preload\` is deprecated (always preloads). Still in \`app/cli.py\` and README.

## Task
- Remove flag after a version bump; update README CLI section."

create_issue "Add /api/meta for calendar labels and overlay priority defaults" enhancement frontend \
"## Context
\`static/app.js\` duplicates \`DAY_NAMES\` / month labels and template priorities from Python \`app/models.py\`.

## Task
- Lightweight \`/api/meta\` (or extend existing payload) returning day/month labels and default priorities for schedule templates.
- UI consumes API; reduce drift."

create_issue "deploy.sh: configurable stop and startup wait timeouts" enhancement \
"## Context
\`scripts/deploy.sh\` uses fixed \`stop_timeout=30\` and \`start_timeout=180\` on the NUC.

## Task
- \`DEPLOY_STOP_TIMEOUT\`, \`DEPLOY_START_TIMEOUT\` env vars with same defaults.
- Document in README Deploying section."

create_issue "Narrow broad except Exception on HTTP handlers where practical" tech-debt \
"## Context
\`app/server.py\` speak/prepare and lifespan use broad \`except Exception\`. Some background paths intentionally swallow errors.

## Task
- Map expected engine/IO failures to 4xx/502; log unexpected.
- Do not change cos_track/pcm_cache thread behavior without tests."

create_issue "Document informal Radio protocol extensions" documentation \
"## Context
Stub vs DRA behavior uses \`getattr(radio, \"continuous_idle_seconds\", ...)\`, \`play_transmit_audio\`, \`cos_defer_snapshot\`, etc.

## Task
- Extend \`app/radio.py\` module docstring or a short \`docs/radio-backend.md\` listing optional methods and semantics for implementers."

create_issue "Style: blank line after radio_backend in config.py" tech-debt \
"## Context
\`app/config.py\` \`radio_backend()\` is immediately followed by module-level \`DRA_USB_CHANNEL\` assignment without a blank line.

## Task
- PEP 8 spacing only."

# --- Known limitations (from code-review-findings.md) ---

create_issue "UI clock for earlier today may not match what actually aired" enhancement documentation \
"## Context
Documented limitation: slot jobs re-resolve at fire time, but the UI simulates today's earlier baseline rows from current rotation index.

## Task
- Product decision: accept, or persist \"played at fire time\" snapshot for display.
- Document in README/UI if unchanged.

## Reference
- \`docs/code-review-findings.md\` Known limitations"

create_issue "Editing overlay offset into the past leaves stale scheduled job time" enhancement \
"## Context
If an overlay offset is edited so fire time moves into the past, an existing APScheduler job may still fire at the old time until sync/restart.

## Task
- On schedule save, reconcile or replace jobs whose fire_at no longer matches resolved slot.
- Regression test in \`tests/test_sync_jobs.py\` or schedule API tests."

create_issue "Document in-memory busy retries and login limiter reset on restart" documentation \
"## Context
Busy defer jobs and login rate limits are in-process; restart clears them (known limitation).

## Task
- Operator note in README Deploying / Security section.
- No code change required if documentation suffices."

create_issue "COS: decode queued HID reports to catch sub-poll kerchunks (Linux)" enhancement \
"## Context
Kerchunk shorter than poll interval (~0.25 s) can be missed because only the latest HID report is decoded. Documented in findings error #11 remaining limit.

## Task
- Hardware-tested: drain interrupt queue on Linux in \`app/dra_radio.py\` / \`app/cm119.py\`.
- Regression tests with injected sample sequences.

## Reference
- \`docs/code-review-findings.md\` error 11"

# --- Testing / verification ---

create_issue "Browser or E2E tests for manual trigger polling and countdown UI" testing frontend \
"## Context
Server-side \`tests/test_manual_trigger_status.py\` exists; \`static/app.js\` fast polling during \`triggerNowBusy\` has no automated test (noted in code-review-findings).

## Task
- Playwright or similar: trigger in flight shows running/clear-hold copy; background poll header present.
- Or document manual NUC checklist only if E2E rejected."

create_issue "Bench-verify interleaved dual-DRA playback on two physical radios" testing \
"## Context
\`test_two_radios_are_fed_in_alternating_short_blocks\` guards interleaved writes; findings note not verified on two physical DRAs.

## Task
- Run shack checklist; record in \`docs/final-hardware/bench-record.md\`.
- File follow-up bugs if hardware differs from unit test assumptions."

echo "Done."
