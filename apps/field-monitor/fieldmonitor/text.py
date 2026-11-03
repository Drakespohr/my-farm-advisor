"""Plain-language formatting shared by alerts, scores, and the watch list."""

from __future__ import annotations

from datetime import date, datetime, timezone


def nice_date(iso: str, today: date | None = None) -> str:
    """'2025-07-01' -> 'Jul 1' this year, 'Jul 1, 2025' otherwise."""
    d = date.fromisoformat(iso[:10])
    today = today or datetime.now(timezone.utc).date()
    return f"{d:%b} {d.day}" if d.year == today.year else f"{d:%b} {d.day}, {d.year}"


def ago(days: int) -> str:
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


def km_to_mi(km: float) -> str:
    mi = km * 0.621371
    return f"{mi:.1f} miles" if mi < 10 else f"{mi:.0f} miles"
