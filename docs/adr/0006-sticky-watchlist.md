# ADR 0006 — The collector's watchlist is sticky

**Status:** Accepted

## Context

Discovery works backwards from outcomes: find what pumped, collapsed, or
revived, then look at who was early. That requires knowing what a token looked
like *before* it was obvious — and that history is the one thing no affordable
provider sells.

Every market API answers a current-state question. "Top tokens by 24h volume"
cannot return a token that ran in March and is a corpse today, because a corpse
does no volume. New-listing feeds reach further back, but only to their own
horizon, and they never say what a token looked like on the specific day a
wallet was accumulating it.

This has already bitten twice. GeckoTerminal's volume-sorted pool listing
produced a universe with no `rug_or_collapse` outcomes in it at all — every
token that could have produced one had been filtered out for being dead. Adding
Birdeye improved the listing quality but not this: a better current-state query
is still a current-state query.

## Decision

`alphagraph.collector.DailyCollector` runs every day and records the market
state of everything it is watching into `asset_observations`, stamped with our
own clock in `observed_at`.

The watchlist is **sticky**: once an asset clears the traction floor, it is
observed permanently, whether or not it still appears in any listing. Each
observation records `in_universe` — whether the asset was in that day's listing,
or whether we only saw it because we kept watching.

Assets that never qualified are retired after a run of quiet days, because cost
has to be bounded somewhere and a token nobody ever traded carries no footprint
worth finding. Qualified assets are never retired.

## Why sticky is the whole point

The obvious implementation — record today's universe each day — would be
worthless in a way that would not show up for months. It would hold a record of
every token *on the days it was big*, and no record of any collapse. That is
survivorship bias rebuilt inside our own archive, where no provider could be
blamed for it and no amount of budget would fix it.

`rug_or_collapse` is the outcome class that separates a wallet early into things
that last from one early into things that die. Without it, "was early to a
pump" and "was early to a rug" are the same signal, and the system would
recommend the second while claiming the first.

## Consequences

- The archive is small at first and grows without further work. `archive_span`
  is surfaced in the CLI because for the first months it is the honest answer to
  "why is discovery finding so little".
- Storage grows monotonically for qualified assets. One row per asset per day is
  cheap; the retirement rule bounds the rest.
- A provider outage must not be read as silence. `_update_quiet_streak`
  distinguishes a missing figure on a *listed* asset (a gap — streak untouched)
  from an asset absent from the listing with no price series either (silence).
  Treating every gap as silence would retire assets during an outage and leave
  holes that read as real quiet periods later.
- Absent values are stored as NULL, never zero. A fabricated zero volume ages
  into a fake quiet period that the revival detector would read as dormancy.
- The collector runs before the nightly loop on the same cron, so evaluation
  reads a database that already contains today's observations.
