# ADR 0005 — A synthetic world with planted ground truth

**Status:** Accepted

## Context

Discovery makes a statistical claim: these wallets are informed, those are
noise. A claim like that cannot be verified against real data during
development, because the answer is unknown and paid providers are not wired up.

## Decision

`alphagraph.providers.world` builds a deterministic ecosystem (seeded, ~11k
events, 429 assets) containing planted archetypes with known properties, buried
in noise. `WORLD_TRUTH` records what each wallet is, and `tests/test_discovery.py`
asserts the engine finds every planted insider and rejects every planted decoy.

The decoys matter as much as the insiders:

- `sniper_bot` has an excellent hit rate and must be excluded anyway.
- `churner` hits by volume, not skill.
- `lucky_wallet` is 3-for-3 by chance and must fail the sample-size guard.
- `insider_listing` is unprofitable and must be found regardless.

The universe is ~93% duds so the population base rate lands near 6%, as it would
in reality. An earlier version had 25 duds, a 52% base rate, and correctly
surfaced nothing — no wallet can show an edge over a coin flip.

## Consequences

- Regressions in the discovery guards fail the suite immediately.
- The whole system runs end to end at zero cost, offline.
- Risk: the fixtures encode assumptions about how insiders behave. Those
  assumptions come from one documented case study and must be revalidated
  against real data before any of this is trusted with money.
