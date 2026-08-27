from __future__ import annotations

import argparse
import sys

from app.config import HOST, PORT
from app.engines.base import VoiceError
from app.playback import play_chunks
from app.registry import get_registry


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"speak", "serve", "voices", "download"}
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        argv = ["speak", *argv]

    parser = argparse.ArgumentParser(
        description="In-memory text-to-speech with Piper and KittenTTS.",
    )
    sub = parser.add_subparsers(dest="command")

    speak = sub.add_parser("speak", help="Synthesize text and play it from memory")
    speak.add_argument("text", nargs="*", help="Text to speak")
    speak.add_argument("-e", "--engine", help="piper or kitten (inferred from --voice when omitted)")
    speak.add_argument("-v", "--voice", help="Voice id or alias")
    speak.add_argument("--speed", type=float, default=1.0, help="Speaking speed (1.0 is default)")
    speak.add_argument("--stdin", action="store_true", help="Read text from stdin")

    serve = sub.add_parser("serve", help="Run the FastAPI web UI and streaming API")
    serve.add_argument("--host", default=HOST)
    serve.add_argument("--port", type=int, default=PORT)
    serve.add_argument(
        "--preload",
        action="store_true",
        help="Download and load the default Piper voice before serving",
    )

    voices = sub.add_parser("voices", help="List engines and voices")
    voices.add_argument("-e", "--engine", help="Limit to one engine")

    download = sub.add_parser("download", help="Download / load a voice or model")
    download.add_argument("voices", nargs="*", help="Voice ids or aliases. Omit to prepare the default.")
    download.add_argument("-e", "--engine", help="Engine to download for")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    if args.command == "speak":
        return _cmd_speak(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "voices":
        return _cmd_voices(args)
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
        engine, voice_id, _rate, chunks = registry.synthesize(
            text,
            engine_id=args.engine,
            voice=args.voice,
            speed=args.speed,
        )
    except VoiceError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"{engine.name} · {voice_id}", file=sys.stderr)
    play_chunks(chunks)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.preload:
        registry = get_registry()
        engine_id, voice_id = registry.prepare(None, None)
        print(f"Preloaded {engine_id}:{voice_id}", file=sys.stderr)
    import uvicorn

    uvicorn.run("app.server:app", host=args.host, port=args.port, reload=False)
    return 0


def _cmd_voices(args: argparse.Namespace) -> int:
    registry = get_registry()
    engines = registry.list_engines()
    if args.engine:
        engines = [registry.get(args.engine)]
    for engine in engines:
        marker = " (default engine)" if engine.id == registry.default_engine_id else ""
        ready = "ready" if engine.is_ready() else "not downloaded"
        print(f"{engine.name} [{engine.id}]{marker} — {ready}")
        print(f"  {engine.blurb}")
        for voice in engine.list_voices():
            flags = []
            if voice.default:
                flags.append("default")
            flags.append("downloaded" if voice.downloaded else "download on first use")
            alias = f" alias={voice.alias}" if voice.alias else ""
            print(
                f"  - {voice.id}{alias}  {voice.name}  "
                f"({voice.gender}, {voice.locale}, {voice.quality})  [{', '.join(flags)}]"
            )
        print()
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    registry = get_registry()
    targets = args.voices or [None]
    try:
        for target in targets:
            engine_id, voice_id = registry.prepare(args.engine, target)
            print(f"Ready: {engine_id}:{voice_id}")
    except VoiceError as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
