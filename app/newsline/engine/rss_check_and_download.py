import argparse
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import feedparser
import requests
from feedparser import FeedParserDict

# Official ARNewsline RSS Feed URL
FEED_URL: str = "https://www.arnewsline.org/?format=rss"

# Project-root audio/ directory (script lives in src/app.newsline.engine/)
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DOWNLOAD_DIR: str = str(_PROJECT_ROOT / "audio")

# Squarespace site files for official scripts (nsln####.txt)
SCRIPT_URL_TEMPLATE: str = "https://www.arnewsline.org/s/nsln{report_number}.txt"

REPORT_NUMBER_RE: re.Pattern[str] = re.compile(r"Report\s*(\d+)", re.IGNORECASE)


def setup() -> None:
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)


def extract_report_number(entry: FeedParserDict) -> int | None:
    """Pull the report number from the enclosure URL or entry title."""
    if getattr(entry, "enclosures", None):
        path = unquote(urlparse(entry.enclosures[0].href).path)
        basename = os.path.basename(path)
        match = REPORT_NUMBER_RE.search(basename)
        if match:
            return int(match.group(1))

    title = getattr(entry, "title", "") or ""
    match = REPORT_NUMBER_RE.search(title)
    if match:
        return int(match.group(1))
    return None


def find_entry_for_report(
    feed: FeedParserDict, report_number: int
) -> FeedParserDict | None:
    """Return the first feed entry whose report number matches, or None."""
    for entry in feed.entries:
        if extract_report_number(entry) == report_number:
            return entry
    return None


def download_file(url: str, dest_path: str) -> bool:
    """Download url to dest_path. Returns True on success."""
    response = requests.get(url, stream=True)
    if response.status_code != 200:
        print(f"Failed to download {url} (HTTP {response.status_code})")
        return False

    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
    print(f"Successfully saved to {dest_path}")
    return True


def stories_dir_for(report_number: int) -> str:
    """Default splitter output dir: audio/Report####_stories/."""
    return os.path.join(DOWNLOAD_DIR, f"Report{report_number}_stories")


def ask_yes_no(prompt: str) -> bool:
    """Ask a yes/no question on stdin; default is no."""
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def on_report_ready(
    report_number: int,
    mp3_path: str,
    txt_path: str,
    *,
    repeater_timeout: int = 180,
) -> None:
    """Run the splitter when both MP3 and script are on disk."""
    from app.newsline.engine.cli import main as splitter_main

    print(f"Report {report_number} ready — splitting into stories...")
    splitter_main(
        [
            "--audio",
            mp3_path,
            "--transcript",
            txt_path,
            "--cache-dir",
            str(_PROJECT_ROOT / ".cache" / "arnewsline"),
            "--repeater-timeout",
            str(repeater_timeout),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check the ARNewsline RSS feed and download the latest report "
            "(or a specific report number)."
        ),
    )
    parser.add_argument(
        "-r",
        "--report",
        type=int,
        default=None,
        metavar="N",
        help="Download report number N instead of the latest episode",
    )
    parser.add_argument(
        "--from-report",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Download and process every feed report from the latest down to N "
            "(inclusive), non-interactively"
        ),
    )
    parser.add_argument(
        "--repeater-timeout",
        type=int,
        default=180,
        metavar="SEC",
        help="Repeater timeout passed to the splitter (default: 180)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Process without prompting when MP3/script exist but stories do not",
    )
    return parser


def _ensure_report_files(
    entry: FeedParserDict,
    report_number: int,
) -> tuple[str, str, bool]:
    """Download MP3/script if missing. Returns (mp3_path, txt_path, ok)."""
    mp3_path = os.path.join(DOWNLOAD_DIR, f"Report{report_number}.mp3")
    txt_path = os.path.join(DOWNLOAD_DIR, f"nsln{report_number}.txt")
    need_mp3 = not os.path.exists(mp3_path)
    need_txt = not os.path.exists(txt_path)

    print(f"Episode: {entry.title}")
    if need_mp3:
        if not getattr(entry, "enclosures", None):
            print(f"Entry has no audio enclosure: {entry.title!r}")
            return mp3_path, txt_path, False
        print("Downloading MP3...")
        if not download_file(entry.enclosures[0].href, mp3_path):
            return mp3_path, txt_path, False
    else:
        print(f"Already have MP3: {mp3_path}")

    if need_txt:
        script_url = SCRIPT_URL_TEMPLATE.format(report_number=report_number)
        print("Downloading script...")
        if not download_file(script_url, txt_path):
            return mp3_path, txt_path, False
    else:
        print(f"Already have script: {txt_path}")

    ok = os.path.exists(mp3_path) and os.path.exists(txt_path)
    return mp3_path, txt_path, ok


def check_and_download(
    report_number: int | None = None,
    *,
    repeater_timeout: int = 180,
    yes: bool = False,
) -> None:
    setup()

    feed: FeedParserDict = feedparser.parse(FEED_URL)
    if not feed.entries:
        print("RSS feed has no entries.")
        return

    # Entries are ordered descending by report number (therefore by date);
    # [0] is the latest when no specific report is requested.
    if report_number is None:
        entry: FeedParserDict = feed.entries[0]
        resolved: int | None = extract_report_number(entry)
        if resolved is None:
            print(f"Could not determine report number for latest entry: {entry.title!r}")
            return
        report_number = resolved
    else:
        found = find_entry_for_report(feed, report_number)
        if found is None:
            print(f"Report {report_number} not found in RSS feed")
            return
        entry = found

    mp3_path: str = os.path.join(DOWNLOAD_DIR, f"Report{report_number}.mp3")
    txt_path: str = os.path.join(DOWNLOAD_DIR, f"nsln{report_number}.txt")
    stories_path: str = stories_dir_for(report_number)
    need_mp3: bool = not os.path.exists(mp3_path)
    need_txt: bool = not os.path.exists(txt_path)
    have_stories: bool = os.path.isdir(stories_path)

    if not need_mp3 and not need_txt:
        print(f"Already have report: {mp3_path} and {txt_path}")
        if have_stories:
            print(f"Stories already present: {stories_path}")
            return
        if yes or ask_yes_no(
            f"Stories directory missing ({stories_path}). Process report {report_number}?"
        ):
            on_report_ready(
                report_number,
                mp3_path,
                txt_path,
                repeater_timeout=repeater_timeout,
            )
        else:
            print("Skipping processing.")
        return

    mp3_path, txt_path, ok = _ensure_report_files(entry, report_number)
    if ok:
        on_report_ready(
            report_number,
            mp3_path,
            txt_path,
            repeater_timeout=repeater_timeout,
        )


def download_and_process_range(
    from_report: int,
    *,
    repeater_timeout: int = 180,
) -> list[dict[str, object]]:
    """
    Download and process every feed report from the latest down to ``from_report``.

    Returns a list of per-report status dicts (rid, status, wall_sec, exit).
    """
    import time

    setup()
    feed: FeedParserDict = feedparser.parse(FEED_URL)
    if not feed.entries:
        print("RSS feed has no entries.")
        return []

    # Unique report numbers present in the feed, descending.
    seen: set[int] = set()
    ordered: list[tuple[int, FeedParserDict]] = []
    for entry in feed.entries:
        rid = extract_report_number(entry)
        if rid is None or rid in seen:
            continue
        seen.add(rid)
        if rid >= from_report:
            ordered.append((rid, entry))
    ordered.sort(key=lambda t: t[0], reverse=True)

    if not ordered:
        print(f"No feed entries at or above report {from_report}")
        return []

    latest = ordered[0][0]
    print(
        f"Batch: reports {latest} → {from_report} "
        f"({len(ordered)} episodes), repeater-timeout={repeater_timeout}"
    )
    results: list[dict[str, object]] = []
    for rid, entry in ordered:
        print("")
        print(f"======== Report {rid} START ========")
        t0 = time.perf_counter()
        status = "FAILED"
        exit_code = 1
        try:
            mp3_path, txt_path, ok = _ensure_report_files(entry, rid)
            if not ok:
                status = "DOWNLOAD_FAILED"
            else:
                stories = stories_dir_for(rid)
                if os.path.isdir(stories):
                    # Fresh process each batch run when re-invoked; leave existing
                    # unless missing — user deleted audio/, so normally missing.
                    pass
                on_report_ready(
                    rid, mp3_path, txt_path, repeater_timeout=repeater_timeout
                )
                summary = Path(stories) / "run_summary.txt"
                if summary.is_file():
                    for line in summary.read_text(errors="replace").splitlines():
                        if line.startswith("Status:"):
                            status = line.split(":", 1)[1].strip()
                            break
                    else:
                        status = "UNKNOWN"
                else:
                    status = "NO_SUMMARY"
                exit_code = 0 if status == "SUCCESS" else 1
        except SystemExit as e:
            exit_code = int(e.code) if isinstance(e.code, int) else 1
            status = "SUCCESS" if exit_code == 0 else "FAILED"
        except Exception as e:
            print(f"Report {rid} error: {e}")
            status = "ERROR"
            exit_code = 1
        wall = time.perf_counter() - t0
        row = {
            "rid": rid,
            "status": status,
            "wall_sec": round(wall, 1),
            "exit": exit_code,
        }
        results.append(row)
        print(
            f"======== Report {rid} DONE status={status} "
            f"wall={wall:.1f}s exit={exit_code} ========"
        )
    print("")
    print("======== BATCH SUMMARY ========")
    for row in results:
        print(
            f"  {row['rid']}: {row['status']}  "
            f"{row['wall_sec']}s  exit={row['exit']}"
        )
    return results


if __name__ == "__main__":
    args = build_parser().parse_args()
    if args.from_report is not None:
        download_and_process_range(
            args.from_report,
            repeater_timeout=args.repeater_timeout,
        )
    else:
        check_and_download(
            report_number=args.report,
            repeater_timeout=args.repeater_timeout,
            yes=args.yes,
        )
