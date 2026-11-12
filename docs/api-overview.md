# API overview

`POST /api/speak`

```json
{ "text": "Hello. Pause after this.", "voice": "en_US-lessac-medium", "speed": 0.75, "sentence_pause": 0.6 }
```

Streams 16-bit little-endian mono PCM. Format headers:

- `X-Voice`
- `X-Sample-Rate` (usually 22050)
- `X-Sample-Width` (`2`), `X-Channels` (`1`)

**Piper** is a text-to-speech (TTS) library. It turns announcement text into speech. Audio is produced one sentence at a time and sent as it is ready.

`POST /api/phonemes/ipa` with `{ "text": "Delaware" }` returns `{ "ipa" }` from the espeak-ng library bundled with Piper, using the same mode as `espeak-ng -v en-us --ipa=3`. `GET /api/phonemes` and `PUT /api/phonemes` read and replace the pronunciation dictionary (`{ "entries": [{ "spelling", "ipa", "alternate" }] }`). Each entry has exactly one of `ipa` or `alternate`; the unused field is an empty string. PUT is admin-only. `POST /api/phonemes/matches` with `{ "text" }` lists entries that would replace whole words, phrases, or `*shortcut*` spellings in that text. A shortcut spelling includes its asterisks and may contain punctuation. `POST /api/speak` accepts `use_phoneme_dictionary` (default true). When true, dictionary IPA is wrapped in `[[ ]]` before synthesis, and alternate text is inserted as plain words. `{{phrase}}` is always unwrapped and skips the dictionary.

`GET /api/voices` lists voices and the server defaults for new announcements (speed, sentence pause, busy retry / give-up). `POST /api/prepare` downloads/loads a model without speaking. Most `/api/*` routes require a login session cookie. Failed sign-ins for the same username from the same address return `429` after `TTS_LOGIN_MAX_FAILURES` attempts (default 5) within `TTS_LOGIN_WINDOW_SECONDS` (default 300) until the window passes; that limit is **in-memory** and clears on process restart (see [Process restart and in-memory state](#process-restart-and-in-memory-state)). Requests carrying an `X-Background-Poll` header (any value; only its presence is checked) are authenticated but do not reset the idle timer. User management is under `/api/users` (Admin only). Auth helpers: `/api/auth/status`, `/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`.

`GET /api/system/stats` returns `{"celsius", "thermal_state", "cpu_percent", "memory_percent", "memory_used_bytes", "memory_available_bytes"}` (authenticated). The UI uses `GET /api/events/stats` (SSE): an initial `snapshot` event, then `stats` events about every 15s from a single server broadcast (one sample shared across tabs). If the stream drops, the client falls back to REST polling every 15s. CPU% uses `psutil.cpu_percent(interval=None)` (average since the previous sample); memory uses one psutil sample for the percent plus `memory_used_bytes` and `memory_available_bytes`. Temperature is °C from Linux thermal sysfs, or macOS `ProcessInfo.thermalState` (`nominal` / `fair` / `serious` / `critical`) when Celsius is unavailable.

`GET /api/schedules` returns upcoming fire, today’s slot clock, warnings, settings, Newsline status, and schedule rows. **Earlier today** on that clock is simulated from the current baseline rotation (not a play log); see [Today’s schedule grid vs what played](docs/how-to-schedule.md#todays-schedule-grid-vs-what-played). Announcements are `GET/POST /api/announcements` and `PUT/DELETE /api/announcements/{id}`. Schedules are `GET/POST /api/schedules` and `PUT/PATCH/DELETE /api/schedules/{id}`. `PUT /api/settings` updates baseline shuffle and the slot window. The Thursday–Friday Newsline check times, check interval, and repeater timeout are environment settings (`TTS_NEWS_CHECK_START`, `TTS_NEWS_CHECK_STOP`, `TTS_NEWS_CHECK_INTERVAL_HOURS`, `TTS_REPEATER_TIMEOUT_SECONDS`). `POST /api/newsline/check` starts one Newsline download (admin). `POST /api/newsline/preview` streams the latest split report for the editor Play button.

```bash
uv run pytest -q
uv run basedpyright   # static types for app/ and tests/
```

Pushes and pull requests to `main` run the same default suite plus basedpyright in GitHub Actions (see the **Test** workflow; you can also re-run it manually from the Actions tab). basedpyright covers `app/` and `tests/` with no baseline, so any new diagnostic fails the check. Tests set `TTS_SKIP_DOTENV=1` (in `tests/conftest.py`) so your local `.env` cannot change their results. Browser tests for the manual-trigger countdown are opt-in locally: `uv run playwright install chromium` once, then `uv run pytest -m e2e`. On GitHub, run the **E2E** workflow manually from the Actions tab.

To catch Linux-only test paths before push (macOS dev machines skip some ALSA/PortAudio code), run `./scripts/ci-local.sh` (Docker + Ubuntu 24.04). It uses a temporary venv inside the container and does not change your Mac `.venv`.

