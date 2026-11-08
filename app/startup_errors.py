"""Startup failure type (kept separate to avoid import cycles with DRA helpers)."""

from __future__ import annotations


class StartupError(Exception):
    """Fatal startup problem; message is safe to show operators."""
