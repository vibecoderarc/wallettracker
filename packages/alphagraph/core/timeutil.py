"""UTC time helpers.

Every timestamp in this system is timezone-aware UTC. Naive datetimes are a
correctness hazard for a product whose central rule is point-in-time
correctness, so they are rejected at the boundary rather than coerced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = ["days", "ensure_utc", "hours", "iso", "minutes", "parse_utc", "utcnow"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return `value` as UTC, rejecting naive datetimes.

    Naive datetimes are ambiguous. Silently attaching UTC to one that was
    actually local time would corrupt point-in-time comparisons in a way that is
    invisible until a backtest quietly reads the future.
    """
    if value.tzinfo is None:
        raise ValueError(f"naive datetime rejected: {value!r} (attach a timezone)")
    return value.astimezone(UTC)


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp missing timezone: {value!r}")
    return parsed.astimezone(UTC)


def iso(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def days(n: float) -> timedelta:
    return timedelta(days=n)


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)
