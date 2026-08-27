# Booth

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

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Choose a voice, adjust **rate** and **sentence pause** if you like, and press **Speak**. PCM audio is streamed to the browser and played with the Web Audio API.

Insert a timed silence in the script with `[pause:SECONDS]` (optional trailing `s`). The tag is not spoken. Duration is capped at 10 seconds; invalid tags such as `[pause]` are left as ordinary text.

```text
This is w2-zee-q. [pause:2] The Delaware Valley Radio Association...
```

`[pause:1.5]` and `[pause:1.5s]` both pause 1.5 seconds. Tagged pauses are extra silence at that location; the **Sentence pause** slider still applies between Piper’s sentence chunks.

## CLI

```bash
python -m app voices
python -m app speak "Hello from the booth."
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
