from __future__ import annotations

import argparse
import sys

from app.config import DEFAULT_SENTENCE_PAUSE, DEFAULT_SPEED, HOST, PORT
from app.engines.base import VoiceError
from app.playback import play_chunks
from app.registry import get_registry


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"speak", "serve", "voices", "download"}
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        argv = ["speak", *argv]

    parser = argparse.ArgumentParser(
        description="In-memory text-to-speech with Piper.",
    )
    sub = parser.add_subparsers(dest="command")

    speak = sub.add_parser("speak", help="Synthesize text and play it from memory")
    speak.add_argument("text", nargs="*", help="Text to speak")
    speak.add_argument("-v", "--voice", help="Voice id or alias")
    speak.add_argument(
        "--speed",
        type=float,
        default=DEFAULT_SPEED,
        help=f"Speaking speed (default: {DEFAULT_SPEED:g}; lower is slower)",
    )
    speak.add_argument(
        "--pause",
        "--sentence-pause",
        dest="sentence_pause",
        type=float,
        default=DEFAULT_SENTENCE_PAUSE,
        help=f"Silence between sentences in seconds (default: {DEFAULT_SENTENCE_PAUSE:g})",
    )
    speak.add_argument("--stdin", action="store_true", help="Read text from stdin")

    serve = sub.add_parser("serve", help="Run the FastAPI web UI and streaming API")
    serve.add_argument("--host", default=HOST)
    serve.add_argument("--port", type=int, default=PORT)
    serve.add_argument(
        "--preload",
        action="store_true",
        help="Download and load the default Piper voice before serving",
    )

    sub.add_parser("voices", help="List Piper voices")

    download = sub.add_parser("download", help="Download / load a voice")
    download.add_argument("voices", nargs="*", help="Voice ids or aliases. Omit to prepare the default.")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    if args.command == "speak":
        return _cmd_speak(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "voices":
        return _cmd_voices()
    if args.command == "download":
        return _cmd_download(args)
    parser.print_help()
    return 1


def _cmd_speak(args: argparse.Namespace) -> int:
    if args.stdin:
        text = sys.stdin.read().strip()
    else:
        text = " ".join(args.text).strip()
    if not text:
        print("Provide text to speak, or pass --stdin.", file=sys.stderr)
        return 2
    registry = get_registry()
    try:
        voice_id, _rate, chunks = registry.synthesize(
            text,
            voice=args.voice,
            speed=args.speed,
            sentence_pause=args.sentence_pause,
        )
    except VoiceError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(voice_id, file=sys.stderr)
    play_chunks(chunks)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.preload:
        registry = get_registry()
        voice_id = registry.prepare()
        print(f"Preloaded {voice_id}", file=sys.stderr)
    import uvicorn

    uvicorn.run("app.server:app", host=args.host, port=args.port, reload=False)
    return 0


def _cmd_voices() -> int:
    registry = get_registry()
    ready = "ready" if registry.engine.is_ready() else "not downloaded"
    print(f"Piper — {ready}")
    for voice in registry.list_voices():
        flags = []
        if voice.default:
            flags.append("default")
        flags.append("downloaded" if voice.downloaded else "download on first use")
        alias = f" alias={voice.alias}" if voice.alias else ""
        print(
            f"  - {voice.id}{alias}  {voice.name}  "
            f"({voice.gender}, {voice.locale}, {voice.quality})  [{', '.join(flags)}]"
        )
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    registry = get_registry()
    targets = args.voices or [None]
    try:
        for target in targets:
            voice_id = registry.prepare(target)
            print(f"Ready: {voice_id}")
    except VoiceError as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
