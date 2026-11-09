#!/usr/bin/env python3
"""Estimate active work time from git commit timestamps.

Commits close together count as one sitting. A gap longer than --threshold
(default 2 hours) starts a new sitting. In this repository most gaps are
under an hour, and the next common gaps are overnight, so 2 hours is the
break between "still working" and "came back later."

Time strictly between the first and last commit of a sitting is a lower
bound: it misses work done before the opening commit, and a one-commit
sitting measures as zero. The adjusted figures add a lead-in per sitting
(shown at 30, 45, and 60 minutes). Forty-five minutes is the figure to
quote; the other two are the range around it.

  python scripts/work_time.py
  python scripts/work_time.py --threshold 3
  python scripts/work_time.py --repo /path/to/other/clone

Times are each commit's author-date clock. Gaps are absolute.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_THRESHOLD_HOURS = 2.0
LEAD_MINUTES = (30, 45, 60)
CENTER_LEAD_MINUTES = 45
SHORT_SITTING = timedelta(minutes=30)
LARGE_DIFF_LINES = 400
SENSITIVITY_HOURS = (1.5, 2.0, 2.5, 3.0, 4.0)

# Upper bound is exclusive. Minutes.
GAP_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0, 15, "under 15 min"),
    (15, 30, "15–30 min"),
    (30, 60, "30–60 min"),
    (60, 120, "1–2 h"),
    (120, 240, "2–4 h"),
    (240, 480, "4–8 h"),
    (480, 720, "8–12 h"),
    (720, 1440, "12–24 h"),
    (1440, 10_080, "1–7 days"),
    (10_080, math.inf, "over 7 days"),
)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
RECORD = "\x1e"
FIELD = "\x1f"


@dataclass(frozen=True)
class DiffStat:
    insertions: int
    deletions: int
    files: int

    @property
    def lines(self) -> int:
        return self.insertions + self.deletions


@dataclass(frozen=True)
class Commit:
    sha: str
    when: datetime
    author: str
    subject: str
    diff: DiffStat


@dataclass(frozen=True)
class Sitting:
    commits: tuple[Commit, ...]

    @property
    def start(self) -> datetime:
        return self.commits[0].when

    @property
    def end(self) -> datetime:
        return self.commits[-1].when

    @property
    def span(self) -> timedelta:
        return self.end - self.start

    @property
    def diff(self) -> DiffStat:
        return DiffStat(
            insertions=sum(c.diff.insertions for c in self.commits),
            deletions=sum(c.diff.deletions for c in self.commits),
            files=sum(c.diff.files for c in self.commits),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_HOURS,
        help="hours; a longer gap between commits starts a new sitting (default: %(default)s)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="repository to measure (default: the repo that contains this script)",
    )
    parser.add_argument(
        "--all-branches",
        action="store_true",
        help="include every branch, not only the current HEAD history",
    )
    args = parser.parse_args(argv)
    if args.threshold <= 0:
        print("--threshold must be positive", file=sys.stderr)
        return 1

    start = args.repo if args.repo is not None else Path(__file__).resolve().parent
    repo = git_root(start)
    commits = load_commits(repo, all_branches=args.all_branches)
    if not commits:
        print(f"No commits in {repo}", file=sys.stderr)
        return 1

    commits.sort(key=lambda c: (c.when, c.sha))
    threshold = timedelta(hours=args.threshold)
    sittings = cluster(commits, threshold)
    report(repo, commits, sittings, threshold)
    return 0


def git_root(start: Path) -> Path:
    proc = git(start, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0:
        message = proc.stderr.strip() or f"not a git repository: {start}"
        print(message, file=sys.stderr)
        raise SystemExit(1)
    return Path(proc.stdout.strip())


def load_commits(repo: Path, *, all_branches: bool) -> list[Commit]:
    rev = "--all" if all_branches else "HEAD"
    proc = git(
        repo,
        "log",
        rev,
        "--reverse",
        f"--pretty=format:{RECORD}%H{FIELD}%aI{FIELD}%an{FIELD}%s",
        "--numstat",
    )
    commits: list[Commit] = []
    for record in proc.stdout.split(RECORD):
        record = record.strip("\n")
        if not record.strip():
            continue
        header, _, body = record.partition("\n")
        sha, when_s, author, subject = header.split(FIELD, 3)
        commits.append(
            Commit(
                sha=sha,
                when=datetime.fromisoformat(when_s),
                author=author,
                subject=subject,
                diff=parse_numstat(body),
            )
        )
    return commits


def parse_numstat(body: str) -> DiffStat:
    insertions = 0
    deletions = 0
    files = 0
    for line in body.splitlines():
        if not line.strip():
            continue
        added, deleted, _path = line.split("\t", 2)
        files += 1
        if added == "-" or deleted == "-":
            continue
        insertions += int(added)
        deletions += int(deleted)
    return DiffStat(insertions, deletions, files)


def cluster(commits: list[Commit], threshold: timedelta) -> list[Sitting]:
    groups: list[list[Commit]] = [[commits[0]]]
    for previous, current in zip(commits, commits[1:], strict=False):
        if current.when - previous.when > threshold:
            groups.append([current])
        else:
            groups[-1].append(current)
    return [Sitting(tuple(group)) for group in groups]


def report(repo: Path, commits: list[Commit], sittings: list[Sitting], threshold: timedelta) -> None:
    first, last = commits[0], commits[-1]
    span = last.when - first.when
    days = {c.when.date() for c in commits}
    measured = sum((s.span for s in sittings), timedelta())
    gaps = gap_minutes(commits)
    authors = Counter(c.author for c in commits)

    print(f"Repository: {repo}")
    if len(authors) == 1:
        name, count = authors.most_common(1)[0]
        print(f"{count} commits by {name}")
    else:
        print(f"{len(commits)} commits")
        for name, count in authors.most_common():
            print(f"  {count:4}  {name}")
    print(
        f"{fmt_clock(first.when)}  →  {fmt_clock(last.when)}"
        f"   ({span.days} days, {len(days)} days with commits)"
    )
    print("Times are author-date local clocks. Gaps are absolute.")
    print()

    print("Gaps between consecutive commits")
    for lo, hi, label in GAP_BUCKETS:
        count = sum(1 for gap in gaps if lo <= gap < hi)
        if count:
            print(f"  {label:<14} {count:4}")
    if gaps:
        under = sum(1 for gap in gaps if gap <= threshold.total_seconds() / 60)
        print(
            f"  median {percentile(gaps, 0.50):.0f} min"
            f"   p75 {percentile(gaps, 0.75):.0f} min"
            f"   {under} of {len(gaps)} gaps are within {fmt_threshold(threshold)}"
        )
    print()

    single = sum(1 for s in sittings if len(s.commits) == 1)
    print(f"Sittings (a gap over {fmt_threshold(threshold)} starts a new one): {len(sittings)}")
    print(f"  one-commit sittings: {single}")
    print(f"  time between first and last commit: {fmt_hours(measured)}")
    for minutes in LEAD_MINUTES:
        adjusted = measured + timedelta(minutes=minutes) * len(sittings)
        mark = "  ← quote this" if minutes == CENTER_LEAD_MINUTES else ""
        print(f"  +{minutes} min lead-in per sitting: {fmt_hours(adjusted)}{mark}")
    low = measured + timedelta(minutes=LEAD_MINUTES[0]) * len(sittings)
    high = measured + timedelta(minutes=LEAD_MINUTES[-1]) * len(sittings)
    center = measured + timedelta(minutes=CENTER_LEAD_MINUTES) * len(sittings)
    print()
    print(f"Reading: {fmt_hours(center)}  (lead-in band {fmt_hours(low)} – {fmt_hours(high)})")
    print("The lead-in is the work before each sitting's first commit, which the gaps do not contain.")
    print()

    print("Other gap cutoffs (same 30 / 45 / 60 min lead-in)")
    hours = list(SENSITIVITY_HOURS)
    chosen = threshold.total_seconds() / 3600
    if not any(math.isclose(chosen, item, abs_tol=1e-9) for item in hours):
        hours.append(chosen)
        hours.sort()
    print(f"  {'cutoff':>8} {'sittings':>9} {'between':>10} {'+30m':>10} {'+45m':>10} {'+60m':>10}")
    for item in hours:
        grouped = cluster(commits, timedelta(hours=item))
        raw = sum((s.span for s in grouped), timedelta())
        pads = [raw + timedelta(minutes=m) * len(grouped) for m in LEAD_MINUTES]
        marker = "*" if math.isclose(item, chosen, abs_tol=1e-9) else " "
        print(
            f"  {marker}{item:>6.1f}h {len(grouped):>9}"
            f" {fmt_hours(raw):>10} {fmt_hours(pads[0]):>10}"
            f" {fmt_hours(pads[1]):>10} {fmt_hours(pads[2]):>10}"
        )
    print()

    print("By weekday (time between commits)")
    by_dow: dict[int, timedelta] = defaultdict(timedelta)
    for sitting in sittings:
        by_dow[sitting.start.weekday()] += sitting.span
    for index, name in enumerate(WEEKDAYS):
        print(f"  {name}  {fmt_hours(by_dow[index])}")
    print()

    print("By week (time between commits, assigned to the sitting's start)")
    by_week: dict[tuple[int, int], timedelta] = defaultdict(timedelta)
    week_commits: dict[tuple[int, int], int] = defaultdict(int)
    for sitting in sittings:
        key = iso_week(sitting.start.date())
        by_week[key] += sitting.span
        week_commits[key] += len(sitting.commits)
    for key in weeks_from(first.when.date(), last.when.date()):
        monday = date.fromisocalendar(key[0], key[1], 1)
        sunday = monday + timedelta(days=6)
        label = f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d')}"
        commits_here = week_commits[key]
        suffix = f"  ({commits_here} {plural(commits_here, 'commit')})" if commits_here else ""
        print(f"  {label:<22} {fmt_hours(by_week[key])}{suffix}")
    print()

    print("Sittings")
    for sitting in sittings:
        subject = sitting.commits[0].subject
        if len(subject) > 72:
            subject = subject[:69] + "..."
        print(
            f"  {fmt_sitting(sitting.start, sitting.end)}"
            f"  {fmt_hours(sitting.span):>8}"
            f"  {len(sitting.commits):>3} {plural(len(sitting.commits), 'commit'):<7}"
            f"  {subject}"
        )
    print()

    understated = [
        s for s in sittings if s.span < SHORT_SITTING and s.diff.lines >= LARGE_DIFF_LINES
    ]
    if understated:
        print(
            f"Large diffs in sittings under {int(SHORT_SITTING.total_seconds() // 60)} minutes"
        )
        print("The lead-in is most of the estimate for these, and the diff may be more work than that.")
        for sitting in understated:
            subject = sitting.commits[0].subject
            if len(subject) > 64:
                subject = subject[:61] + "..."
            diff = sitting.diff
            print(
                f"  {sitting.start.strftime('%Y-%m-%d %H:%M')}"
                f"  {fmt_hours(sitting.span):>8}"
                f"  +{diff.insertions}/-{diff.deletions}"
                f"  {subject}"
            )
        print()

    if is_root(repo, first.sha):
        print(
            f"First commit {first.sha[:9]} ({fmt_clock(first.when)}) is the start of history:"
            f" {first.diff.files} files, +{first.diff.insertions}/-{first.diff.deletions}."
        )
        print("Time before that commit is not included.")

    if work_tree_dirty(repo):
        print(f"The work tree has uncommitted changes. Time after {fmt_clock(last.when)} is not included.")


def gap_minutes(commits: list[Commit]) -> list[float]:
    return [(b.when - a.when).total_seconds() / 60 for a, b in zip(commits, commits[1:], strict=False)]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def iso_week(day: date) -> tuple[int, int]:
    year, week, _day = day.isocalendar()
    return year, week


def weeks_from(start: date, end: date) -> list[tuple[int, int]]:
    cursor = date.fromisocalendar(*iso_week(start), 1)
    last = date.fromisocalendar(*iso_week(end), 1)
    keys: list[tuple[int, int]] = []
    while cursor <= last:
        keys.append(iso_week(cursor))
        cursor += timedelta(days=7)
    return keys


def is_root(repo: Path, sha: str) -> bool:
    proc = git(repo, "rev-list", "--max-parents=0", "HEAD")
    return sha in proc.stdout.split()


def work_tree_dirty(repo: Path) -> bool:
    proc = git(repo, "status", "--porcelain")
    return bool(proc.stdout.strip())


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "--no-pager", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and proc.returncode != 0:
        message = proc.stderr.strip() or f"git {' '.join(args)} failed"
        print(message, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def fmt_hours(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    sign = "-" if minutes < 0 else ""
    hours, mins = divmod(abs(minutes), 60)
    return f"{sign}{hours}h {mins:02d}m"


def fmt_threshold(threshold: timedelta) -> str:
    hours = threshold.total_seconds() / 3600
    if math.isclose(hours, round(hours)):
        text = f"{hours:.0f}"
    else:
        text = f"{hours:.1f}"
    return f"{text}h"


def plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def fmt_clock(when: datetime) -> str:
    return when.strftime("%Y-%m-%d %H:%M")


def fmt_sitting(start: datetime, end: datetime) -> str:
    left = start.strftime("%a %Y-%m-%d %H:%M")
    if start.date() == end.date():
        right = end.strftime("%H:%M")
    else:
        right = end.strftime("%a %H:%M")
    return f"{left} – {right}"


if __name__ == "__main__":
    raise SystemExit(main())
