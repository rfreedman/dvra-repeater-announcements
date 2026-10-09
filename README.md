# Announcements

Local Piper text-to-speech with a FastAPI UI and a CLI. Audio is generated and played **entirely in memory** — nothing is written to disk as a WAV/MP3.

Piper is fast enough for Raspberry Pi, streams one sentence at a time, and has many English voices (plus any [Piper voice](https://rhasspy.github.io/piper-samples/)).

## Requirements

- macOS or Linux (64-bit). Raspberry Pi OS 64-bit works; 32-bit does not.
- Python 3.10+
- For CLI playback: PortAudio (`brew install portaudio` on macOS, `sudo apt install libportaudio2` on Debian/Raspberry Pi)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The first run downloads the default voice into `voices/` (gitignored).

```bash
python -m app download
python -m app download amy ryan
```

## Web UI

```bash
python -m app serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On first visit, create the initial **Admin** account. After that, sign in with:

- **Admin** — full access, including user management
- **Read-Only** — view schedules and announcements, Speak preview only (no edits, no Users page)

The home screen shows the next run, today’s half-hour slots, schedules, and the announcement library. **How to schedule** (in the header, and in [docs/how-to-schedule.md](docs/how-to-schedule.md)) is the operator guide.

Write the spoken text under **New announcement**. Timing lives on **schedules**, which point at that text:

- A **baseline** fills every hour and half-hour unless something else claims the slot. Several baselines take turns.
- An **overlay** (weekly net, monthly net, event countdown, once, silence, or emergency) replaces the baseline for the slots it matches. You can slide an overlay a few minutes early or late inside the slot window; it still occupies that slot. Example: the 15:30 slot at −5 minutes fires at 15:25, and 15:30 stays silent.

Scheduled playback happens on the **server's audio device** (with stub PTT key-up and a lead-in delay), even if the browser is closed. The Speak button is preview only and does not key the radio.

Insert a timed silence in the script with `[pause:SECONDS]` (optional trailing `s`). The tag is not spoken. Duration is capped at 10 seconds; invalid tags such as `[pause]` are left as ordinary text.

```text
This is w2-zee-q. [pause:2] The Delaware Valley Radio Association...
```

`[pause:1.5]` and `[pause:1.5s]` both pause 1.5 seconds. Tagged pauses are extra silence at that location; the **Sentence pause** slider still applies between Piper’s sentence chunks.

## CLI

Run commands from the project root with the virtualenv active:

```bash
python -m app <command> ...
```

If the first argument is not a known command (`speak`, `serve`, `voices`, `download`, `users`), it is treated as **speak** text. These are equivalent:

```bash
python -m app speak "Hello"
python -m app "Hello"
```

CLI behavior respects the same [configuration](#configuration) and optional `.env` as the web app (voices directory, default voice, speed, host/port, `TTS_USERS_PATH`, and so on). User accounts are stored in `data/users.json` by default.

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Usage error or unexpected failure |
| `2` | User input error (missing text, unknown user, validation error, voice error) |

### `speak`

Synthesize text with Piper and play it on the local audio device (in memory; no WAV file).

```bash
python -m app speak [options] [text ...]
```

| Option | Default | Description |
| --- | --- | --- |
| `-v`, `--voice` | default voice | Piper voice id or alias from `config/piper-voices.json` |
| `--speed` | `TTS_SPEED` (`1.0`) | Speaking rate; lower is slower |
| `--pause`, `--sentence-pause` | `TTS_SENTENCE_PAUSE` (`0.25`) | Silence between sentences (seconds) |
| `--stdin` | off | Read all text from standard input instead of arguments |

The resolved voice id is printed on stderr. Script tags such as `[pause:2]` in the text are honored the same way as in the web UI.

Examples:

```bash
python -m app speak "Hello from the announcements."
python -m app speak --voice amy "Piper voice aliases work too."
python -m app speak --speed 0.75 --pause 0.6 "Hello. Take your time with this."
echo "From a pipe" | python -m app speak --stdin
```

### `serve`

Start the FastAPI web UI and HTTP API (announcements, schedules, auth, scheduler).

```bash
python -m app serve [--host HOST] [--port PORT] [--preload]
```

| Option | Default | Description |
| --- | --- | --- |
| `--host` | `TTS_HOST` (`0.0.0.0`) | Bind address |
| `--port` | `TTS_PORT` (`8000`) | Listen port |
| `--preload` | — | Deprecated; voices are always preloaded at startup |

```bash
python -m app serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) (or your host/port). Scheduled announcements play on the **server’s** audio output, not the browser.

### `voices`

List Piper voices known to the app (featured catalog plus models on disk), with id, alias, name, locale, quality, and whether the model is already downloaded.

```bash
python -m app voices
```

### `download`

Download (if needed) and load voice models into `voices/piper/` without speaking. Useful before going on-air or on a slow link.

```bash
python -m app download [voice ...]
```

Each argument is a voice id or alias. With no arguments, prepares the **default** voice (`TTS_DEFAULT_VOICE`).

```bash
python -m app download
python -m app download amy ryan
```

### `users`

Manage web UI logins stored in `TTS_USERS_PATH` (default `data/users.json`). These commands work even when you cannot sign in to the UI—use them for recovery on the server console.

```bash
python -m app users list
python -m app users create USERNAME [options]
python -m app users passwd USERNAME [--password PASSWORD]
python -m app users delete USERNAME
```

**`users list`** — Print `username`, `role`, and internal `id` (tab-separated). If no users exist, the CLI suggests creating the first admin or using the web setup screen.

**`users create`** — Add a user.

| Option | Description |
| --- | --- |
| `--password` | Password on the command line (avoid on shared hosts); otherwise prompted twice |
| `--role` | `admin` or `readonly` (default: `readonly`) |
| `--admin` | Same as `--role admin` |

Passwords must be at least **8 characters**. Roles match the web UI: **admin** (full access, user management) and **readonly** (view and Speak preview only).

```bash
python -m app users create --admin alice
python -m app users create bob --role readonly
```

**`users passwd`** — Set a new password for an existing user (forgotten password / lockout recovery). Prompts twice unless `--password` is given.

```bash
python -m app users passwd alice
```

**`users delete`** — Remove a user. The **last admin** cannot be deleted or demoted via the API; the CLI enforces the same rule when deleting would leave no admin.

```bash
python -m app users delete bob
```

## Configuration

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
| `TTS_SESSION_IDLE_SECONDS` | `1800` (30 min) | Log out after this much idle time |
| `TTS_PCM_CACHE_DIR` | `./data/pcm` | Pre-rendered announcement PCM (written on save and at startup; scheduled fires play this) |
| `TTS_LOG_DIR` | `./data/logs` | Daily `announcements.log` files (30 days; uvicorn access lines are not written here) |
| `TTS_PTT_LEAD_SECONDS` | `0.4` | Delay after PTT before audio |
| `TTS_BUSY_RETRY_SECONDS` | `5` | Default retry while the channel is busy |
| `TTS_BUSY_GIVE_UP_SECONDS` | `45` | Default drop this fire if still busy |
| `TTS_SCHEDULER_MAX_WAIT_SECONDS` | `5` | How long the scheduler may sleep before checking the clock again |
| `TTS_SLOT_HALF_WINDOW_MINUTES` | `10` | How far an overlay may shift from its slot center |
| `TTS_TRIGGER_NOW` | `false` | Dev only: show **Trigger now** on schedule rows (does not change Last or the next slot) |

On a Raspberry Pi or other low-RAM host, set `TTS_PIPER_QUALITIES=medium` so only medium models are listed and preloaded. High-quality voices use more CPU and RAM.

### Piper voices

Browse samples at [piper-samples](https://rhasspy.github.io/piper-samples/) and downloadable models on Hugging Face [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices).

**Featured voices** live in [`config/piper-voices.json`](config/piper-voices.json) (override with `TTS_PIPER_VOICES_FILE`). They appear in the UI with friendly names/aliases, are **preloaded at startup**, and are **re-downloaded automatically** if the matching `.onnx` under `voices/piper/` is missing. Deleting a featured model file without removing it from the JSON means it comes back on the next start (Hugging Face or custom URLs).

**Download source:** omit `onnx_url` / `config_url` to use Piper’s Hugging Face download for standard voice ids (`en_US-…-medium`, etc.). Set optional `onnx_url` (and usually `config_url`) to HTTP(S) URLs for custom or mirrored models; files are still stored as `{id}.onnx` and `{id}.onnx.json` under `voices/piper/`.

**Extra voices on disk:** copy `{id}.onnx` and `{id}.onnx.json` into `voices/piper/`. They show up in the UI (subject to `TTS_PIPER_QUALITIES`) and are loaded from disk when present. They are **not** re-downloaded if you delete them, unless something later asks for that exact voice id (for example Speak / prepare for an announcement that still references it).

**Add a featured voice**

1. Edit `config/piper-voices.json` and copy an existing object in the `voices` array.
2. Set `id` to the Piper voice id (for example `en_US-libritts_r-medium`), or any filename stem you want on disk for a custom download.
3. Choose a unique `alias` (used by the CLI, e.g. `--voice libritts`).
4. Fill `name`, `gender`, `locale`, `quality`, and `description`.
5. Optionally set `onnx_url` and `config_url` to HTTPS (or HTTP) download locations; leave them out to use Hugging Face for standard Piper ids.
6. Restart the app. The first startup downloads the model into `voices/piper/` if it is not already there.

Example with custom URLs (commented — do not paste live secrets into the default JSON):

```json
{
  "id": "norman",
  "alias": "norman",
  "name": "Norman",
  "gender": "male",
  "locale": "en_US",
  "quality": "medium",
  "description": "Custom voice.",
  "onnx_url": "https://example.com/voices/norman.onnx",
  "config_url": "https://example.com/voices/norman.onnx.json"
}
```

Optional `.env` in the project root (see [`.env.example`](.env.example)). Variables already set in the shell override that file.

## API

`POST /api/speak`

```json
{ "text": "Hello. Pause after this.", "voice": "en_US-lessac-medium", "speed": 0.75, "sentence_pause": 0.6 }
```

Streams 16-bit little-endian mono PCM. Format headers:

- `X-Voice`
- `X-Sample-Rate` (usually 22050)
- `X-Sample-Width` (`2`), `X-Channels` (`1`)

`GET /api/voices` lists voices. `POST /api/prepare` downloads/loads a model without speaking. Most `/api/*` routes require a login session cookie. User management is under `/api/users` (Admin only). Auth helpers: `/api/auth/status`, `/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`.

`GET /api/system/stats` returns `{"celsius", "thermal_state", "cpu_percent", "memory_percent"}` (authenticated). CPU% uses `psutil.cpu_percent(interval=None)` (average since the previous poll, ~15s with the UI poller); memory uses psutil. Temperature is °C from Linux thermal sysfs, or macOS `ProcessInfo.thermalState` (`nominal` / `fair` / `serious` / `critical`) when Celsius is unavailable.

`GET /api/schedules` returns upcoming fire, today’s slot clock, warnings, settings, and schedule rows. Announcements are `GET/POST /api/announcements` and `PUT/DELETE /api/announcements/{id}`. Schedules are `GET/POST /api/schedules` and `PUT/PATCH/DELETE /api/schedules/{id}`. `PUT /api/settings` updates baseline shuffle and the slot window.

```bash
pytest
```
