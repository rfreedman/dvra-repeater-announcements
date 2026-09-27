from __future__ import annotations

import argparse
import getpass
import sys

from app.config import DEFAULT_SENTENCE_PAUSE, DEFAULT_SPEED, HOST, PORT
from app.engines.base import VoiceError
from app.playback import play_chunks
from app.registry import get_registry


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"speak", "serve", "voices", "download", "users"}
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
        help="Deprecated: serve always preloads all Piper voices at startup",
    )

    sub.add_parser("voices", help="List Piper voices")

    download = sub.add_parser("download", help="Download / load a voice")
    download.add_argument("voices", nargs="*", help="Voice ids or aliases. Omit to prepare the default.")

    users = sub.add_parser("users", help="Manage web UI users")
    users_sub = users.add_subparsers(dest="users_command")
    users_sub.add_parser("list", help="List users")
    create = users_sub.add_parser("create", help="Create a user")
    create.add_argument("username")
    create.add_argument("--password", help="Password (prompted if omitted)")
    create.add_argument(
        "--role",
        choices=("admin", "readonly"),
        default="readonly",
        help="Role (default: readonly)",
    )
    create.add_argument("--admin", action="store_true", help="Shortcut for --role admin")
    passwd = users_sub.add_parser("passwd", help="Set a user's password")
    passwd.add_argument("username")
    passwd.add_argument("--password", help="Password (prompted if omitted)")
    delete = users_sub.add_parser("delete", help="Delete a user")
    delete.add_argument("username")

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
    if args.command == "users":
        return _cmd_users(args)
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
    import uvicorn

    from app.logging_setup import serve_log_config

    uvicorn.run(
        "app.server:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_config=serve_log_config(),
    )
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


def _read_password(explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise ValueError("Passwords do not match")
    return first


def _cmd_users(args: argparse.Namespace) -> int:
    from app.auth import hash_password, validate_password
    from app.users import get_user_store

    store = get_user_store()
    command = getattr(args, "users_command", None)
    if command == "list":
        rows = store.list_users()
        if not rows:
            print("No users yet. Open the web UI to create the first admin, or run:")
            print("  python -m app users create --admin USERNAME")
            return 0
        for user in sorted(rows, key=lambda item: item.username.lower()):
            print(f"{user.username}\t{user.role}\t{user.id}")
        return 0
    if command == "create":
        role = "admin" if args.admin else args.role
        try:
            password = validate_password(_read_password(args.password))
            user = store.create(
                username=args.username,
                password_hash=hash_password(password),
                role=role,
            )
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"Created {user.username} ({user.role})")
        return 0
    if command == "passwd":
        user = store.by_username(args.username)
        if user is None:
            print("User not found", file=sys.stderr)
            return 2
        try:
            password = validate_password(_read_password(args.password))
            store.update(user.id, password_hash=hash_password(password))
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"Updated password for {user.username}")
        return 0
    if command == "delete":
        user = store.by_username(args.username)
        if user is None:
            print("User not found", file=sys.stderr)
            return 2
        try:
            store.delete(user.id)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"Deleted {user.username}")
        return 0
    print("Usage: python -m app users {list|create|passwd|delete}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
