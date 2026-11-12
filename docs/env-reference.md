# Environment reference

**Piper** is a text-to-speech (TTS) library. It turns announcement text into the speech the radio transmits.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TTS_DEFAULT_VOICE` | `en_US-ryan-medium` | Default Piper voice |
| `TTS_VOICES_DIR` | `./voices` | Model cache (`voices/piper/` holds `.onnx` files) |
| `TTS_PIPER_VOICES_FILE` | `config/piper-voices.json` | Featured voice catalog (UI list, aliases, startup preload) |
| `TTS_PIPER_QUALITIES` | `all` | `all` = list/preload every `voices/piper/*.onnx`; `medium` = only `*-medium` |
| `TTS_SPEED` | `1.0` | Speaking rate (`< 1` is slower) |
| `TTS_SENTENCE_PAUSE` | `0.25` | Silence between sentences, in seconds |
| `TTS_HOST` / `TTS_PORT` | `0.0.0.0` / `8000` | Bind address |
| `TTS_TIMEZONE` | `America/New_York` | Clock for schedules |
| `TTS_DATA_DIR` | `./data` | Saved announcements JSON |
| `TTS_USERS_PATH` | `$TTS_DATA_DIR/users.json` | Web UI users |
| `TTS_SESSION_SECRET` | auto (`data/.session_secret`) | Signs login session cookies |
| `TTS_SESSION_IDLE_SECONDS` | `1800` (30 min) | Log out after this much idle time (the UI's automatic 15 s refreshes do not count as activity) |
| `TTS_LOGIN_MAX_FAILURES` | `5` | Failed sign-ins for the same username from the same client before `429` |
| `TTS_LOGIN_WINDOW_SECONDS` | `300` | How long those failures stay counted, in seconds |
| `TTS_PCM_CACHE_DIR` | `./data/pcm` | Pre-rendered announcement PCM (written on save and at startup; scheduled fires play this) |
| `TTS_LOG_DIR` | `./data/logs` | Daily rotating `announcements.log` (app + uvicorn server) and filtered `access.log` (page/asset/major API hits; home polls/health/SSE omitted). Access is file-only (not stdout/stderr). Kept 30 days (`TTS_LOG_KEEP_DAYS`). |
| `TTS_PTT_LEAD_SECONDS` | `0.4` | Delay after PTT before audio (on **dra**, silence is streamed on USB; on **stub**, host sleep) |
| `TTS_RADIO_BACKEND` | `stub` | `stub` = in-memory PTT/COS. A **DRA** (Digital Radio Adapter) is the USB box between the computer and one radio. This project uses the [Masters Communications **DRA-36M**](https://www.masterscommunications.com/products/radio-adapter/dra/dra36m.html). It plays the announcement into the radio and keys the transmitter. `dra` means the app is using those adapters (requires `hidapi`). Confirm the active mode in the startup log: `(radio mode: stub)` or `(radio mode: dra)` |
| `TTS_DRA_USB_CHANNEL` | `left` | USB channel for DATA IN (**JU3=L** → `left`) |
| `TTS_DRA_COS_LINE` | `vol_dn` | HID COS decode (`vol_dn` or `allstar3`) |
| `TTS_DRA_COS_ACTIVE_HIGH` | `false` | Set `true` if idle/busy labels swap on `monitor` |
| `TTS_DRA_AUDIO_DEVICES` | *(auto)* | Optional comma list of PortAudio substrings or indices (**one per DRA**). On the Linux host with **two radios**, use fixed USB ports; set this only if auto-detection order is wrong |
| `TTS_DRA_CLEAR_SECONDS` | `0` | Continuous COS idle required after synthesize, before PTT (`10` matches bench voice-gate) |
| `TTS_DRA_CLEAR_POLL_SECONDS` | `0.25` | COS poll interval during clear wait and background idle tracking |
| `TTS_DRA_FINAL_CHECK_SECONDS` | `1.0` | After the clear hold, keep reading COS this long before PTT; any busy read cancels (catches a key-up as the UI countdown ends). Only applies when `TTS_DRA_CLEAR_SECONDS` > 0 |
| `TTS_DRA_COS_TRACK` | `true` | Background COS polls while the scheduler runs (idle before Trigger now counts toward clear) |
| `TTS_DRA_HID_PATH` | *(auto)* | Hex HID path to pin **one** CM119 (a **single** DRA on the Linux host). **Omit when two DRAs are connected** — each radio needs its own CM119 HID |
| `TTS_DRA_ALSA_UNMUTE` | `true` | Linux **dra** only: run `amixer` to unmute CM119 **Speaker** at startup (see [Linux host notes](linux-host.md)). Set `false` to skip |
| `TTS_DRA_ALSA_SPEAKER_LEVEL` | `100%` | ALSA **Speaker** level passed to `amixer sset` when unmuting |
| `TTS_BUSY_RETRY_SECONDS` | `5` | Retry interval while the channel is busy; also the value pre-filled for a **new announcement** in the UI |
| `TTS_BUSY_GIVE_UP_SECONDS` | `45` | Drop this fire if still busy after this long; also pre-filled for a new announcement |
| `TTS_SCHEDULER_MAX_WAIT_SECONDS` | `5` | How long the scheduler may sleep before checking the clock again |
| `TTS_NEWS_CHECK_START` | `12:00` | Thursday start of the Amateur Radio Newsline check window (`HH:MM`, station timezone) |
| `TTS_NEWS_CHECK_STOP` | `18:00` | Friday end of that window (`HH:MM`) |
| `TTS_NEWS_CHECK_INTERVAL_HOURS` | `1` | Hours between checks inside the window (`0.5` is every 30 minutes; `0.5`–`24`) |
| `TTS_REPEATER_TIMEOUT_SECONDS` | `180` | Repeater time limit for one transmission (`30`–`600`). Spoken audio and Newsline story files must finish 2 seconds sooner |
| `TTS_SLOT_HALF_WINDOW_MINUTES` | `10` | How far an overlay may shift from its slot center |
| `TTS_TRIGGER_NOW` | `false` | Dev only: show **Trigger now** on schedule rows (does not change Last or the next slot) |

### Station identity

| Variable | Default | Meaning |
| --- | --- | --- |
| `STATION_CALL` | *(required)* | Call sign shown in the UI / idle assertion name |
| `STATION_NAME` | *(required)* | Club or station name (page heading and logo alt text) |
| `APP_TITLE` | `{STATION_CALL} Announcements` | FastAPI / browser title; omit to use the derived default |

Startup fails if `STATION_CALL` or `STATION_NAME` is missing or blank. Set them in `.env` (see [`.env.example`](../.env.example)).

### Defer timing (fixed in code, not env vars)

These complement `TTS_BUSY_RETRY_SECONDS` on each announcement and `TTS_DRA_CLEAR_SECONDS` / `TTS_DRA_CLEAR_POLL_SECONDS` for COS. They are **not** configurable today; names live in `app/fire.py` and `app/scheduler.py` if you need to tune behavior in a fork.

| Constant | Default | Role |
| --- | --- | --- |
| `DEFER_CLEAR_NONBLOCKING_MAX_SECONDS` | `0.5` | After **Trigger now**, first clear-hold retry is scheduled within this cap so the HTTP request returns while the COS poll thread keeps waiting. |
| `DEFER_RETRY_WHEN_CLEAR_MET_CAP_SECONDS` | `1.0` | When the clear hold is already satisfied, defer retries run at most this often instead of the full per-announcement busy retry interval. |
| `SCHEDULER_MIN_WAKE_SECONDS` | `0.5` | Shortest sleep between APScheduler wakeups (avoids missing fires under macOS App Nap). |
| `MANUAL_NUDGE_SKIP_IF_RUN_WITHIN_SECONDS` | `0.2` | Background COS nudge does not reschedule a manual defer that is already about to run. |
| `MANUAL_NUDGE_PTT_TIMER_PROTECT_SECONDS` | `2.0` | Short PTT defer retries keep their scheduled time instead of being pulled forward when the channel clears. |
| `MANUAL_NUDGE_MIN_WAIT_FOR_UNKNOWN_KIND_SECONDS` | `2.0` | Only `busy` / `clear` / `ptt` defers with a longer wait are nudged when idle credit meets the clear hold. |
| `MANUAL_NUDGE_RUN_DELAY_MS` | `50` | How soon to run a nudged manual defer after the clear hold is met. |

On a low-RAM host, set `TTS_PIPER_QUALITIES=medium` so only medium models are listed and preloaded. High-quality voices use more CPU and RAM.

### Piper voices

Browse samples at [piper-samples](https://rhasspy.github.io/piper-samples/) and downloadable models on Hugging Face [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices).

**Featured voices** live in [`config/piper-voices.json`](../config/piper-voices.json) (override with `TTS_PIPER_VOICES_FILE`). They appear in the UI with friendly names/aliases, are **preloaded at startup**, and are **re-downloaded automatically** if the matching `.onnx` under `voices/piper/` is missing. Deleting a featured model file without removing it from the JSON means it comes back on the next start (Hugging Face or custom URLs).

**Download source:** omit `onnx_url` / `config_url` to use Piper’s Hugging Face download for standard voice ids (`en_US-…-medium`, etc.). Set optional `onnx_url` (and usually `config_url`) to HTTP(S) URLs for custom or mirrored models; files are still stored as `{id}.onnx` and `{id}.onnx.json` under `voices/piper/`.

**Extra voices on disk:** copy `{id}.onnx` and `{id}.onnx.json` into `voices/piper/`. They show up in the UI (subject to `TTS_PIPER_QUALITIES`) and are loaded from disk when present. They are **not** re-downloaded if you delete them, unless something later asks for that exact voice id (for example Speak / prepare for an announcement that still references it).

**Add a featured voice**

1. Edit [`config/piper-voices.json`](../config/piper-voices.json) and copy an existing object in the `voices` array.
2. Set `id` to the Piper voice id (for example `en_US-libritts_r-medium`), or any filename stem you want on disk for a custom download.
3. Choose a unique `alias` (used by the CLI, e.g. `--voice libritts`).
4. Fill `name`, `gender`, `locale`, `quality`, and `description`.
5. Optionally set `onnx_url` and `config_url` to HTTPS (or HTTP) download locations; leave them out to use Hugging Face for standard Piper ids.
6. Restart the app. The first startup downloads the model into `voices/piper/` if it is not already there.

Example with custom URLs (commented — do not paste live secrets into the default JSON):

```json
{
  "id": "custom",
  "alias": "custom",
  "name": "Custom",
  "gender": "male",
  "locale": "en_US",
  "quality": "medium",
  "description": "Custom voice.",
  "onnx_url": "https://example.com/voices/custom.onnx",
  "config_url": "https://example.com/voices/custom.onnx.json"
}
```

Optional `.env` in the project root (see [`.env.example`](../.env.example)). Variables already set in the shell override that file.

