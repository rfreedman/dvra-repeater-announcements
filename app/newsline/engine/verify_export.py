from __future__ import annotations

import subprocess
from pathlib import Path

from app.newsline.engine.models import Boundary, Segment, WordTiming
from app.newsline.engine.split import plan_export_slots, probe_duration
from app.newsline.engine.verify import ExportBoundaryIssue


def verify_post_export(
    output_dir: Path,
    report_id: int,
    boundaries: tuple[Boundary, ...],
    segments: tuple[Segment, ...],
    words: list[WordTiming],
    *,
    duration_tolerance: float = 0.35,
    repeater_timeout: int = 180,
) -> list[ExportBoundaryIssue]:
    """Check exported MP3 files and durations against boundaries."""
    del words  # duration comes from slot start/end (inspect already applied)
    issues: list[ExportBoundaryIssue] = []

    if not output_dir.is_dir():
        issues.append(
            ExportBoundaryIssue(
                segment_index=-1,
                title="(output)",
                code="missing_file",
                detail=f"output directory not found: {output_dir}",
            )
        )
        return issues

    for slot in plan_export_slots(
        output_dir,
        report_id,
        boundaries,
        segments,
        repeater_timeout=repeater_timeout,
    ):
        path = slot.path
        boundary = slot.primary
        if slot.is_station_id:
            if not path.is_file():
                issues.append(
                    ExportBoundaryIssue(
                        boundary.segment_index,
                        "station-id",
                        "missing_file",
                        f"missing station-id placeholder: {path.name}",
                    )
                )
            # Zero-byte is intentional; do not treat as missing/empty export.
            continue
        if not path.is_file() or path.stat().st_size == 0:
            issues.append(
                ExportBoundaryIssue(
                    boundary.segment_index,
                    slot.title,
                    "missing_file",
                    f"missing or empty export: {path.name}",
                )
            )
            continue

        expected = slot.end - slot.start
        try:
            actual = probe_duration(path)
        except (subprocess.CalledProcessError, ValueError) as e:
            issues.append(
                ExportBoundaryIssue(
                    boundary.segment_index,
                    slot.title,
                    "duration_mismatch",
                    f"ffprobe failed for {path.name}: {e}",
                )
            )
            continue

        if abs(actual - expected) > duration_tolerance:
            issues.append(
                ExportBoundaryIssue(
                    boundary.segment_index,
                    slot.title,
                    "duration_mismatch",
                    f"{path.name}: expected {expected:.2f}s, got {actual:.2f}s",
                )
            )

    return issues
