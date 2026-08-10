# ADR 0002 — A custom UTC datetime column type

**Status:** Accepted

## Context

SQLite discards timezone information on read. A value stored as aware UTC comes
back naive. During development this surfaced as `can't compare offset-naive and
offset-aware datetimes`, which dead-lettered 1,222 of 1,274 events on the first
ingest run.

The tempting fix — catching the error and coercing — would have been far worse
than the crash. A naive datetime treated as local time and compared against a
UTC one produces *silently wrong* point-in-time answers, and point-in-time
correctness is the load-bearing rule of the entire product.

## Decision

`alphagraph.db.types.UTCDateTime`, a `TypeDecorator` that refuses to store a
naive datetime and always returns an aware UTC one. Every timestamp column uses
it.

## Consequences

- The invariant holds at the type boundary rather than at dozens of call sites.
- The same code runs on SQLite locally and Postgres in production without a
  behavioural difference in time handling.
- Attempting to store a naive datetime raises immediately, at the write, rather
  than producing a wrong comparison months later in a backtest.
