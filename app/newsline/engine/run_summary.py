"""Durable end-of-run status for after-the-fact success checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.newsline.engine.progress import format_elapsed, log
from app.newsline.engine.verify import ExportBoundaryIssue


@dataclass
class RunSummary:
    report_id: int
    status: str  # SUCCESS | FAILED
    wall_clock_sec: float
    qa_passes: int
    qa_max_passes: int
    segment_count: int
    output_dir: Path
    errors: list[ExportBoundaryIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS"

    def format_table(self) -> str:
        lines = [
            "======== RUN SUMMARY ========",
            f"Report:     {self.report_id}",
            f"Status:     {self.status}",
            f"Wall clock: {format_elapsed(self.wall_clock_sec)}",
            f"QA passes:  {self.qa_passes} / {self.qa_max_passes}",
            f"Segments:   {self.segment_count}",
            f"Output:     {self.output_dir.name}/",
        ]
        if self.errors:
            lines.append(f"Errors:     {len(self.errors)} critical")
            for it in self.errors:
                title = it.title[:52]
                detail = it.detail[:80]
                lines.append(f"  - [{it.code}] {title}: {detail}")
        else:
            lines.append("Errors:     (none)")
        lines.append("=============================")
        return "\n".join(lines)


def write_run_summary(summary: RunSummary) -> Path:
    """
    Write ``run_summary.txt`` next to the stories and tee the table into ``run.log``.
    """
    path = summary.output_dir / "run_summary.txt"
    text = summary.format_table()
    path.write_text(text + "\n", encoding="utf-8")
    for line in text.splitlines():
        log(line)
    return path
