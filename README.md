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

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The home screen shows the next run, today’s half-hour slots, schedules, and the announcement library. **How to schedule** (in the header, and in [docs/how-to-schedule.md](docs/how-to-schedule.md)) is the operator guide.

Write the spoken text under **New announcement**. Timing lives on **schedules**, which point at that text:

- A **baseline** fills every hour and half-hour unless something else claims the slot. Several baselines take turns.
- An **overlay** (weekly net, monthly net, event countdown, once, silence, or emergency) replaces the baseline for the slots it matches. You can slide an overlay a few minutes early or late inside the slot window; it still occupies that slot. Example: the 15:30 slot at −5 minutes fires at 15:25, and 15:30 stays silent.

Scheduled playback happens on the **server speakers** (with stub PTT key-up and a lead-in delay), even if the browser is closed. The Speak button is preview only and does not key the radio.

Insert a timed silence in the script with `[pause:SECONDS]` (optional trailing `s`). The tag is not spoken. Duration is capped at 10 seconds; invalid tags such as `[pause]` are left as ordinary text.

```text
This is w2-zee-q. [pause:2] The Delaware Valley Radio Association...
```

`[pause:1.5]` and `[pause:1.5s]` both pause 1.5 seconds. Tagged pauses are extra silence at that location; the **Sentence pause** slider still applies between Piper’s sentence chunks.

## CLI

```bash
python -m app voices
python -m app speak "Hello from the announcements."
python -m app speak --voice amy "Piper voice aliases work too."
python -m app speak --speed 0.75 --pause 0.6 "Hello. Take your time with this."
echo "From a pipe" | python -m app speak --stdin
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TTS_DEFAULT_VOICE` | `en_US-lessac-medium` | Default Piper voice |
| `TTS_VOICES_DIR` | `./voices` | Model cache |
| `TTS_SPEED` | `1.0` | Speaking rate (`< 1` is slower) |
| `TTS_SENTENCE_PAUSE` | `0.25` | Silence between sentences, in seconds |
| `TTS_HOST` / `TTS_PORT` | `0.0.0.0` / `8000` | Bind address |
| `TTS_TIMEZONE` | `America/New_York` | Clock for schedules |
| `TTS_DATA_DIR` | `./data` | Saved announcements JSON |
| `TTS_LOG_DIR` | `./data/logs` | Daily `announcements.log` files (30 days; uvicorn access lines are not written here) |
| `TTS_PTT_LEAD_SECONDS` | `0.4` | Delay after PTT before audio |
| `TTS_BUSY_RETRY_SECONDS` | `5` | Default retry while the channel is busy |
| `TTS_BUSY_GIVE_UP_SECONDS` | `45` | Default drop this fire if still busy |
| `TTS_SCHEDULER_MAX_WAIT_SECONDS` | `5` | How long the scheduler may sleep before checking the clock again |
| `TTS_SLOT_HALF_WINDOW_MINUTES` | `10` | How far an overlay may shift from its slot center |

On a Raspberry Pi, keep a **medium** quality voice.

## API

`POST /api/speak`

```json
{ "text": "Hello. Pause after this.", "voice": "en_US-lessac-medium", "speed": 0.75, "sentence_pause": 0.6 }
```

Streams 16-bit little-endian mono PCM. Format headers:

- `X-Voice`
- `X-Sample-Rate` (usually 22050)
- `X-Sample-Width` (`2`), `X-Channels` (`1`)

`GET /api/voices` lists voices. `POST /api/prepare` downloads/loads a model without speaking.

`GET /api/schedules` returns upcoming fire, today’s slot clock, warnings, settings, and schedule rows. Announcements are `GET/POST /api/announcements` and `PUT/DELETE /api/announcements/{id}`. Schedules are `GET/POST /api/schedules` and `PUT/PATCH/DELETE /api/schedules/{id}`. `PUT /api/settings` updates baseline shuffle and the slot window.

```bash
pytest
```
