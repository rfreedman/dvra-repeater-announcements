# Code review findings and fixes

Review of the whole project (mostly written by Cursor Composer), October 11, 2026.
Each entry lists the symptom, the cause, what changed, and the test that now guards it.
Short "don't repeat this" lessons for agents live in
[`.cursor/rules/code-review-lessons.mdc`](../.cursor/rules/code-review-lessons.mdc).

How the bugs were confirmed: the scheduler bugs were reproduced against the real
APScheduler with a shifted/frozen clock before fixing; the new tests in
`tests/test_sync_jobs.py` and `tests/test_defer_retry_delay.py` fail on the old code.

---

## Errors (behavior bugs)

### 1. A slot could transmit twice
- **Symptom:** a UI save, a "Trigger now", or a restart within 30 s after a slot fired made the same slot play again (for baselines, the *next* baseline in the rotation). Reproduced: 15:00 played "Alpha", an edit at 15:00:03 made it play "Bravo".
- **Cause:** `sync_jobs()` re-added every slot whose fire time was within the last 30 s. APScheduler had already run and removed that job; the re-added job had a past `DateTrigger` inside the 30 s misfire grace, so it ran again.
- **Fix:** `app/scheduler.py` `sync_jobs()` only *adds* jobs for future fire times. A past-due job is kept only if it still exists (scheduler woke late). A restart after a fire time skips that slot instead of playing it late (documented in README).
- **Tests:** `test_fired_slot_is_not_rearmed_by_a_later_sync`, `test_pending_past_slot_job_is_kept`, `test_slot_jobs_are_not_added_in_the_past`.

### 2. Overlays with a positive (late) offset were silently lost
- **Symptom:** an overlay on the 15:30 slot at +5 min (fires 15:35) was deleted by any `sync_jobs()` between 15:30:30 and 15:35 (UI edit, restart). If the channel was busy at 15:35, the retry was deleted immediately (the fire itself calls `sync_jobs()`), with no log.
- **Cause:** `sync_jobs()` built its "wanted" set from slots whose *center* (:00/:30) was after `now - 30 s`, not from the fire time.
- **Fix:** the window now looks back `slot_half_window_minutes + 30 s` and decides by `fire_at`.
- **Tests:** `test_late_offset_overlay_survives_sync_after_slot_center`, `test_late_offset_overlay_is_scheduled_by_sync_after_slot_center`.

### 3. Busy retries stopped about 30 s after the slot
- **Symptom:** with the UI's old defaults (retry 30 s, give-up 300 s) you got about one retry, then a silent drop with no "dropped" log line.
- **Cause:** same pruning — `defer:` jobs were removed when their slot was no longer "wanted".
- **Fix:** busy-retry jobs now live until their own give-up deadline; `sync_jobs()` removes one only if the slot no longer has anything to transmit (`none`/`silence`).
- **Tests:** `test_busy_retry_survives_until_its_own_deadline`, `test_busy_retry_removed_when_slot_has_nothing_to_play`.

### 4. Two DRAs played one after the other, not together
- **Symptom:** (production = one NUC, two DRAs) radio 1 played the whole announcement while radio 2 was keyed on silence; then radio 2 played it.
- **Cause:** `sounddevice` `stream.write()` blocks until the data is buffered. A cached announcement is a single chunk, so the first stream got all of it before the second got anything.
- **Fix:** `app/dra_playback.py` builds one float32 program per device (lead silence + resampled audio) and `write_programs_interleaved()` feeds the streams in alternating 50 ms blocks.
- **Test:** `test_two_radios_are_fed_in_alternating_short_blocks` (`tests/test_dra_hardware_fixes.py`). Not yet verified on two physical DRAs.

### 5. Possible stuck PTT when the second radio fails to key
- **Symptom:** if radio 1 keyed and radio 2's HID write failed, radio 1 stayed keyed.
- **Cause:** `DraRadio.set_ptt(True)` collected keyed devices in a local list; the error handler unkeyed `self._ptt_devices` (still empty). `transmit()`'s `finally` never ran because `set_ptt(True)` raised before its `try`.
- **Fix:** `app/dra_radio.py` tracks each device as it keys and unkeys exactly those on failure.
- **Test:** `test_partial_ptt_failure_unkeys_radios_already_keyed`.

### 6. Busy-retry interval ignored when the clear hold is off
- **Symptom:** with `TTS_DRA_CLEAR_SECONDS=0` (the default) retries happened every ≤1 s regardless of the announcement's `busy_retry_seconds`.
- **Cause:** `defer_retry_delay()` treated a disabled clear hold as "already met" and shortened the delay.
- **Fix:** `app/fire.py` only shortens the delay when a clear hold is enabled *and* met.
- **Test:** `test_full_retry_when_clear_hold_disabled`; the existing `test_busy_then_clear_defers_without_writing_last_run` (which had been failing without a `.env`) passes again.

### 7. Test suite failed on a clean checkout
- **Symptom:** 3 of 139 tests failed without a `.env`, 2 with the developer's `.env` — results depended on the machine.
- **Causes and fixes:**
  - Commit `a5f6438` made server startup call `init_radio_at_startup()`, replacing the test's `StubRadio`. `tests/conftest.py` now patches it out (autouse `keep_test_radio`).
  - `app/config.py` loaded the developer's `.env` during tests. It now skips `.env` when `TTS_SKIP_DOTENV=1`, which `conftest.py` sets before importing the app.
  - `GET /api/schedules` sampled the radio (`channel_busy()`) on every poll. `pending_manual_triggers()` now touches the radio only when a manual trigger is pending or in progress.
- **Test:** `test_schedules_poll_does_not_sample_radio_when_nothing_pending`.

### 8. Idle logout never happened while a tab was open
- **Symptom:** the documented 30-minute idle logout never fired; the UI polls every 15 s and every authenticated request reset the timer.
- **Fix:** timer-driven requests in `static/app.js` send an `X-Background-Poll` header; `app/auth.py` authenticates them without refreshing `last_active`.
- **Follow-up:** the server first required the header value to be exactly `"1"`, a magic value typed separately in Python and JS that could silently drift. It now checks only that the header is present, so just the header name (`BACKGROUND_POLL_HEADER`) has to match.
- **Tests:** `test_background_poll_does_not_extend_idle_session` (runs with several values, all ignored) and `test_request_without_background_header_extends_idle_session`.

### 9. "Trigger now" button showed "Playing" late or not at all (found on the NUC after deploy)
- **Symptom:** after a manual trigger the button changed to "Playing" late, sometimes never. Scheduled fires were fine.
- **Cause:** when the clear hold is already met, the trigger transmits *inside* the POST request. The UI's fast (1 s) refresh only ran when something was already queued/running or a scheduled run was within 15 s, so during that POST it waited for the 15 s timer — longer than a short announcement. Scheduled fires hit the "within 15 s" case, which is why they worked. Not seen in Mac stub mode because stub transmits finish almost instantly. Likely predates this review.
- **Fix:** `static/app.js` `tickUpcoming()` also polls while `triggerNowBusy` (a trigger request is in flight).
- **Test:** `tests/test_manual_trigger_status.py` checks the server reports `upcoming.status == "running"` while a manual transmit is in progress. The frontend polling itself has no automated test.

### 10. "Trigger in progress" instead of the clear-hold countdown (found on the NUC)
- **Symptom:** pressing Trigger now right after the channel went idle (but before the 10 s clear hold) showed "Trigger in progress / Waiting for clear channel or transmitting" with no countdown. On-air behavior was correct. Pressing it while the channel was busy showed the countdown as expected.
- **Cause:** the HTTP trigger defers the clear wait to a scheduler retry about 0.5 s later. That retry is "in flight" while it blocks for the rest of the hold. `_pending_row_for_manual` checked in-flight *before* busy and clear hold, so the whole wait was labeled `in_progress`. (The UI's `in_progress` countdown could never appear: `interpolatedClearRemaining` only works for `clear_window`.) In the busy case the retry usually runs only once the hold is already met, so the bug didn't show.
- **Fix:** `app/scheduler.py`: radio state (busy, then clear hold) now takes priority over in-flight; `in_progress` is used only when no clear hold is configured. In-flight runs also record their give-up deadline (`_set_manual_inflight_deadline`), so "Give up in" stays correct after the retry job has been consumed.
- **Test:** `tests/test_manual_trigger_status.py` (phase ordering and in-flight give-up time).

### 11. Short kerchunks did not reset the clear hold (found on the NUC)
- **Symptom:** during a manual trigger's clear-hold countdown, a quick kerchunk did not reset the countdown; it took about a second of transmission. This affected the real on-air hold, not just the display.
- **Cause:** `DraRadio._apply_cos_busy` reset idle credit only after **two consecutive** busy samples. At the 0.25 s poll, a kerchunk shorter than about 0.5 s was ignored. The NUC verbose log confirmed it: one `decoded_busy=True` sample while idle credit kept climbing. The debounce dated from the Mac integration and wasn't documented. Missed or garbled HID reads already reach the code as "no reading" (keeping idle credit), not as busy, so the debounce wasn't protecting against bad reads.
- **Fix:** one decoded busy sample resets idle credit. Removed the now-dead "busy sample ignored" bypass in `wait_for_transmit_clear`.
- **Test:** `tests/test_dra_cos_idle.py::test_single_busy_sample_resets_idle_credit` (previously asserted the opposite).
- **Remaining limit:** a kerchunk that starts and ends between two polls (under about 0.25 s) can still be missed, because only the latest HID report is decoded. Catching it would mean decoding every queued HID interrupt report (Linux only); not done, pending hardware testing.

### 12. Key-up in the last second of the clear hold did not stop the transmit (found on the NUC)
- **Symptom:** with the UI showing 0:01 remaining, keying up (even for a long transmission) did not reset the hold; the announcement played.
- **Causes:**
  - The "before PTT" check used `channel_blocks_transmit`, which **skips reading COS** once idle credit has reached the clear hold, so there was no real final check. A test (`test_fire_transmits_when_clear_hold_met_despite_busy_sample`) required this, based on the same "busy samples are flaky" assumption as the debounce in error 11.
  - The UI countdown rounds up and refreshes once a second, so "0:01" can mean the server is about to key, or already has. Not confirmed with logs; the user chose to fix it on that assumption.
- **Fix:** `app/fire.py` `_confirm_clear_before_ptt` reads COS directly, never trusting idle credit. With a clear hold it keeps reading for `TTS_DRA_FINAL_CHECK_SECONDS` (default 1.0 s, one read per poll interval) before PTT. Any busy read defers or drops the fire as a busy defer. Effect: the real hold is the clear hold plus up to 1 s; the UI shows "Starting…" during that last part.
- **Tests:** `tests/test_clear_hold_transmit.py` (fresh busy read defers despite idle credit; key-up mid-window defers; the window reads for its full length).

### 13. "Starting in mm:ss" flashed when a busy-deferred trigger reached the end of its hold
- **Symptom:** a trigger that had waited out a busy channel briefly showed "Starting in xx:xx" at the end of the countdown; an immediate trigger showed "Starting now…".
- **Cause:** the UI showed the queued retry job's own run time (up to `busy_retry_seconds` away). That time never applies once the hold is met, because the COS poll thread pulls the job forward right away (`nudge_manual_defer_jobs_if_clear_ready`). Display only; the trigger always ran right away.
- **Fix:** `static/app.js`: in the clear-hold phase, a finished countdown always shows "Starting now…" / "Starting…", whatever the retry time says.

---

## Tech debt addressed

| Item | Change |
| --- | --- |
| Stray committed file `:memory:.ses` | Deleted; `:memory:*` added to `.gitignore`. |
| Unknown data-file version silently treated as empty (next save erased data; an emptied `users.json` would reopen first-admin setup to anyone) | `app/store.py` `read_versioned_json()` raises `DataFileError`; used by both stores. Startup reads both files so the app fails fast with a clear message. Empty file or `{}` = fresh install. Tests: `test_unversioned_json_is_refused_not_overwritten`, `test_unknown_users_file_is_refused_not_overwritten`. |
| `/api/speak` claimed to stream but generated everything first | New `EngineRegistry.stream_synthesize()`; `/api/speak` uses it. Piper's voice lock is now held per sentence, never across a `yield`, so a slow client cannot block scheduled synthesis (`test_piper_voice_lock_is_not_held_while_consumer_waits`). |
| Saving an announcement rendered PCM inside the request, under one global lock shared with fires | `warm_announcement_in_background()` renders on a daemon thread; `app/pcm_cache.py` uses one lock per announcement (`test_saving_announcement_does_not_wait_for_pcm_render`). |
| `GET /api/schedules` rescheduled APScheduler jobs | The manual-defer "nudge" now runs only from the background COS poll thread (`app/cos_track.py`), not from HTTP handlers. |
| Frontend new-announcement defaults (retry 30 s / give-up 300 s / speed 0.8 / hard-coded voice) disagreed with backend and README (5 / 45 / 1.0) | `/api/voices` returns the server defaults; `static/app.js` `newAnnouncementDefaults()` uses them. **Behavior change:** new announcements now pre-fill 5 s / 45 s / speed 1.0 unless `TTS_BUSY_RETRY_SECONDS`, `TTS_BUSY_GIVE_UP_SECONDS`, `TTS_SPEED` are set. Existing announcements are unchanged. |
| Hard-coded `45` and grace literals in the scheduler | `DEFAULT_BUSY_GIVE_UP_SECONDS`, `SLOT_MISFIRE_GRACE_SECONDS`, `DEFER_MISFIRE_GRACE_SECONDS`. |
| Scheduled-fire and manual-trigger setup duplicated; `pending_manual_triggers()` patched rows in place | Shared `_fire_window()`, `_add_defer_job()`, `_parse_local()`, `_manual_schedule_id()`; `pending_manual_triggers()` rewritten as one loop. Redundant `sync_jobs()` calls in the manual path removed (manual triggers never change slot jobs). |
| Three time-zone sources used inconsistently | Slot math uses the document's `Settings.timezone` only (`_countdown_occupies` uses the slot's own zone; `schedule_warning` uses the document zone; scheduler timestamps use the store zone). `Schedule.timezone` is kept for stored-data compatibility and commented as unused. |
| DST "duplicate slot keys" (raised in the review) | **Correction: not a bug.** Verified: keys never collide. The repeated 01:00–01:59 hour gets slots only on its first pass; spring-forward skips 02:00/02:30. Locked in by `test_dst_transitions_never_produce_duplicate_slot_keys`. |
| `scripts/dra36m_check.py` duplicated 7 helpers from `app/cm119.py` / `app/dra_playback.py` | The script imports them (`sys.path` set once at the top); only its legacy `dra_mac` COS mode stays local. `cmd_ptt` no longer prints the HID byte count (app helper returns `None`). |
| `TTS_DRA_COS_LINE` not validated | `app/config.py` warns and falls back to `vol_dn` for anything other than `vol_dn`, `vol_up`, `allstar3`. |
| `logging.basicConfig` in the lifespan (no-op under uvicorn) | Removed. |
| FastAPI title "TTS Stream" | Renamed "DVRA Repeater Announcements". |
| No login rate limiting | `LoginRateLimiter` in `app/auth.py`: 5 failures per client+username in 5 min → `429` with `Retry-After` (`test_repeated_failed_logins_are_rate_limited`). In memory; resets on restart. |
| Small items | Unused `Engine.blurb` removed; `RunningOnKeyRadio` moved out of `handle_fire` to module level; redundant `locked()` check before releasing the playback lock removed; unused imports cleaned in app, scripts, and tests. |
| `Announcement` model lacked API validation bounds (GitHub #1) | Shared limits in `app/config.py` (`MIN_SPEECH_SPEED`, `MAX_*`); `Announcement` uses matching `Field` constraints and `normalize_announcement_text()`; `SpeakRequest` / `AnnouncementIn` use the same constants. Invalid rows fail on load via `StoreDocument`. Tests: `tests/test_announcement_validation.py`. |

## Known limitations left as-is

- Slot jobs are re-resolved at fire time, but the UI clock for *earlier today* is simulated from the current rotation index, so past baseline rows can show a different name than what actually played.
- If an overlay's offset is edited so its fire time moves into the past, an already-scheduled job still fires at the old time.
- Busy retries and the login limiter are in memory; a restart clears them.
