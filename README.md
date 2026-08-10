# AlphaGraph

Private crypto wallet-intelligence platform. Finds wallets that repeatedly appear early and quiet in assets
that later move hard, learns their behavioural procedure, and alerts when they act again.

**Status:** Specification complete. No code yet.

## Read this first

- [`ALPHAGRAPH_SPEC.md`](./ALPHAGRAPH_SPEC.md) — the source of truth (v2)
- [`docs/ALPHAGRAPH_SPEC_V1_ARCHIVED.md`](./docs/ALPHAGRAPH_SPEC_V1_ARCHIVED.md) — superseded v1, retained for provenance only

## The core idea

You cannot track a wallet you have not found. So discovery runs **backwards from outcomes**: every night, take
the assets that pumped, got listed, or woke from dormancy; rewind to before the move; find who was buying during
the quiet; and look for the wallets that keep showing up across unrelated events. Filter out the bots and the
churners. Shadow-watch the survivors. Only then track them.

## Scope boundary

Research, discovery, alerts, watchlists, and paper trading. No custody, no private keys, no transaction
construction, no signing, no autonomous execution. Public data only.

## Roadmap

18 phases, two milestones. See §15 of the spec.

- **MVP-1 (Phase 6)** — discovery finds candidate wallets from history, and alerts fire when they act
- **MVP-2 (Phase 14)** — the full researchable dashboard with paper trading
