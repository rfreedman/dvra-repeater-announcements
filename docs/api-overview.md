# API overview

`POST /api/speak`

```json
{ "text": "Hello. Pause after this.", "voice": "en_US-lessac-medium", "speed": 0.75, "sentence_pause": 0.6 }
```

Streams 16-bit little-endian mono PCM. Format headers:

- `X-Voice`
- `X-Sample-Rate` (usually 22050)
- `X-Sample-Width` (`2`), `X-Channels` (`1`)

Audio is produced one Piper sentence at a time and sent as it is ready.

`GET /api/voices` lists voices and the server defaults for new announcements (speed, sentence pause, busy retry / give-up). `POST /api/prepare` downloads/loads a model without speaking. Most `/api/*` routes require a login session cookie. Failed sign-ins for the same username from the same address return `429` after `TTS_LOGIN_MAX_FAILURES` attempts (default 5) within `TTS_LOGIN_WINDOW_SECONDS` (default 300) until the window passes; that limit is **in-memory** and clears on process restart (see [Process restart and in-memory state](#process-restart-and-in-memory-state)). Requests carrying an `X-Background-Poll` header (any value; only its presence is checked) are authenticated but do not reset the idle timer. User management is under `/api/users` (Admin only). Auth helpers: `/api/auth/status`, `/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`.

`GET /api/system/stats` returns `{"celsius", "thermal_state", "cpu_percent", "memory_percent"}` (authenticated). The UI uses `GET /api/events/stats` (SSE): an initial `snapshot` event, then `stats` events about every 15s from a single server broadcast (one sample shared across tabs). If the stream drops, the client falls back to REST polling every 15s. CPU% uses `psutil.cpu_percent(interval=None)` (average since the previous sample); memory uses psutil. Temperature is °C from Linux thermal sysfs, or macOS `ProcessInfo.thermalState` (`nominal` / `fair` / `serious` / `critical`) when Celsius is unavailable.

`GET /api/schedules` returns upcoming fire, today’s slot clock, warnings, settings, Newsline status, and schedule rows. **Earlier today** on that clock is simulated from the current baseline rotation (not a play log); see [Today’s schedule grid vs what played](docs/how-to-schedule.md#todays-schedule-grid-vs-what-played). Announcements are `GET/POST /api/announcements` and `PUT/DELETE /api/announcements/{id}`. Schedules are `GET/POST /api/schedules` and `PUT/PATCH/DELETE /api/schedules/{id}`. `PUT /api/settings` updates baseline shuffle, the slot window, the Thursday–Friday Newsline check times, and the repeater timeout. `POST /api/newsline/check` starts one Newsline download (admin). `POST /api/newsline/preview` streams the latest split report for the editor Play button.

```bash
uv run pytest -q
uv run basedpyright   # static types for app/ and tests/
```

Pushes and pull requests to `main` run the same default suite plus basedpyright in GitHub Actions (see the **Test** workflow; you can also re-run it manually from the Actions tab). basedpyright covers `app/` and `tests/` with no baseline, so any new diagnostic fails the check. Tests set `TTS_SKIP_DOTENV=1` (in `tests/conftest.py`) so your local `.env` cannot change their results. Browser tests for the manual-trigger countdown are opt-in locally: `uv run playwright install chromium` once, then `uv run pytest -m e2e`. On GitHub, run the **E2E** workflow manually from the Actions tab.

To catch Linux-only test paths before push (macOS dev machines skip some ALSA/PortAudio code), run `./scripts/ci-local.sh` (Docker + Ubuntu 24.04). It uses a temporary venv inside the container and does not change your Mac `.venv`.

