# Booth

Local text-to-speech with a FastAPI UI and a CLI. Audio is generated and played **entirely in memory** — nothing is written to disk as a WAV/MP3.

Two engines are available:

| Engine | Best for | Voices |
| --- | --- | --- |
| **Piper** (default) | Speed, sentence streaming, Raspberry Pi | Many English voices, plus any [Piper voice](https://rhasspy.github.io/piper-samples/) |
| **KittenTTS** | More natural English | 8 built-in voices in one small model |

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

The first run of each engine downloads its model(s) into `voices/` (gitignored).

```bash
python -m app download                 # default Piper voice
python -m app download --engine kitten # KittenTTS model (all 8 voices)
```

## Web UI

```bash
python -m app serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Pick Piper or KittenTTS, choose a voice, adjust **rate** and **sentence pause** if you like, and press **Speak**. PCM audio is streamed to the browser and played with the Web Audio API.

## CLI

```bash
python -m app voices
python -m app speak "Hello from the booth."
python -m app speak -e kitten -v Jasper "This is KittenTTS."
python -m app speak --voice amy "Piper voice aliases work too."
python -m app speak --speed 0.75 --pause 0.6 "Hello. Take your time with this."
echo "From a pipe" | python -m app speak --stdin
```

If you pass `--voice` without `--engine`, the voice name is matched against both catalogs.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TTS_DEFAULT_ENGINE` | `piper` | `piper` or `kitten` |
| `TTS_DEFAULT_VOICE` | `en_US-lessac-medium` | Default Piper voice |
| `TTS_DEFAULT_KITTEN_VOICE` | `Bella` | Default KittenTTS voice |
| `TTS_KITTEN_MODEL` | `KittenML/kitten-tts-mini-0.8` | Hugging Face model id |
| `TTS_VOICES_DIR` | `./voices` | Model cache |
| `TTS_SPEED` | `1.0` | Speaking rate (`< 1` is slower) |
| `TTS_SENTENCE_PAUSE` | `0.25` | Silence between sentences, in seconds |
| `TTS_HOST` / `TTS_PORT` | `0.0.0.0` / `8000` | Bind address |

On a Raspberry Pi, keep Piper on a **medium** voice. KittenTTS 0.8 currently pulls a heavier Python stack (including PyTorch via its phonemizer extras), so Piper is the practical Pi default. If you still want KittenTTS on a Pi 5, use the smaller model:

```bash
export TTS_KITTEN_MODEL=KittenML/kitten-tts-nano-0.8
```

## API

`POST /api/speak`

```json
{ "text": "Hello. Pause after this.", "engine": "piper", "voice": "en_US-lessac-medium", "speed": 0.75, "sentence_pause": 0.6 }
```

Streams 16-bit little-endian mono PCM. Format headers:

- `X-Engine`, `X-Voice`
- `X-Sample-Rate` (Piper usually 22050, KittenTTS 24000)
- `X-Sample-Width` (`2`), `X-Channels` (`1`)

`GET /api/voices` lists engines and voices. `POST /api/prepare` downloads/loads a model without speaking.
