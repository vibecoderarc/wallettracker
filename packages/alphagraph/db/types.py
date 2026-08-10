"""Custom column types.

`UTCDateTime` exists because SQLite discards timezone information on read, so a
value stored as aware UTC comes back naive. Comparing a naive value to an aware
one raises; worse, code that defends against the raise by coercing would compare
a local-looking timestamp against a UTC one and silently produce wrong
point-in-time answers.

Since point-in-time correctness is the load-bearing rule of this system, the fix
belongs at the type boundary rather than at each of the dozens of call sites.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"refusing to store naive datetime: {value!r}")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
