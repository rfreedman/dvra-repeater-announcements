from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportBoundaryIssue:
    segment_index: int
    title: str
    code: str
    detail: str
