# AlphaGraph

Private crypto wallet-intelligence platform. Finds wallets that repeatedly appear early and
quiet in assets that later move hard, learns their behavioural procedure, and alerts when
they act again.

**Status:** Working end-to-end system running on deterministic fixtures. No paid provider is
connected — see [Connecting real data](#connecting-real-data).

```bash
pip install -e ".[dev]"
alphagraph demo        # ingest → outcomes → discovery → signals → backtest → nightly loop
alphagraph serve       # API on :8000
cd apps/web && npm install && npm run dev   # dashboard on :3000
```

---

## The idea

You cannot track a wallet you have not found, and the wallets worth finding do not announce
themselves. So discovery runs **backwards from outcomes**:

1. Take every asset that pumped, got listed on a CEX, or woke from dormancy.
2. Rewind to before the move and enumerate everyone who was buying during the quiet.
3. Score each buyer on how early, how quiet, and how large-relative-to-their-own-history.
4. Cross-tabulate across hundreds of unrelated outcomes. Wallets that keep appearing are
   the candidates.
5. Strip out the bots and churners.
6. Shadow-watch the survivors before trusting any of them.

Every pump that happens without you makes the system better at finding the next one.

### Two things this gets right that a naive version does not

**Profit is not skill.** The entity behind the case study that motivated this project
predicted 25 of 25 eventual Robinhood listings — and lost money, round-tripping its position
and getting liquidated. Ranking wallets by P&L discards exactly the wallets worth watching.
Ranking here uses predictive metrics only; P&L is displayed as context and never used.
([ADR 0003](docs/adr/0003-pnl-excluded-from-ranking.md))

**Significance is relative.** A $500 probe from a wallet that trades four times a year is a
scream. A $1M buy from a stranger is not an event at all. Absolute dollar thresholds would
have deleted the trade that opened the whole sequence.

---

## What is built

| Area | Status |
| --- | --- |
| Canonical event schemas, provider interfaces, capability negotiation | ✅ |
| Ingestion with idempotent replay, finality, dead-letter isolation | ✅ |
| Perps: positions, **unfilled resting orders**, liquidations | ✅ |
| Outcome registry: price runs, listings, revivals, collapses, pumps | ✅ |
| Wallet metrics, bot exclusions, shrinkage, archetype classification | ✅ |
| Reverse discovery engine with independence + base-rate guards | ✅ |
| Entity dossiers, sequence-aware graph edges, playbook mining | ✅ |
| Signal engine (9 families), alert policy, shadow mode | ✅ |
| Point-in-time backtesting, baselines, automated leakage audit | ✅ |
| Nightly grading, cohort maintenance, governed proposals, digest | ✅ |
| FastAPI surface + Next.js dashboard | ✅ |
| Live provider adapters (Helius, market data, Hyperliquid, BSC) | ⬜ needs credentials |
| Social / announcement track | ⬜ not started |
| Grounded AI research layer | ⬜ not started |

Two signal families are defined in the spec but not yet implemented: authenticated
announcements (needs the social track) and cross-venue confirmation (needs live perps).

---

## Verified behaviour

The discovery engine is checked against planted ground truth
([ADR 0005](docs/adr/0005-fixture-world-as-ground-truth.md)). From `alphagraph demo`:

```
Discovered:  insider_listing (unprofitable, 8/8 on listings)   12.1× base rate
             side_wallet     (confirms ~18h after primary)     12.1× base
             revival_hunter  (buys dead tokens weeks early)    11.1× base
             insider_quiet   (6 trades/year, all ran hard)      6.9× base
             pump_operator   (flagged as operator, not alpha)   6.3× base

Rejected (38): sniper_bot   — entered_within_30s_of_launch_10_times
               churner      — active_around_the_clock (428 assets touched)
               lucky_wallet — sample_size_3_below_minimum

Backtest:      2,241 simulated trades, hit rate 22.1% vs random baseline 14.9%
               mean return +7.7% vs baseline −1.3%
Leakage audit: PASSED (4 checks)
```

The 25 closest rejections are persisted and shown in the UI. A discovery screen that
only ever displays successes gives you no way to tell a working filter from a broken one.

And the nightly loop grading its own output, which is where the system tells you
uncomfortable things:

```
Precision by signal family:
  • sequence_confirmation:   100.0% (8/8)
  • resting_order_placed:    100.0% (8/8)
  • pump_operator_activity:  100.0% (5/5)
  • tracked_entity_action:    92.2% (59/64)
  • silent_accumulation:       1.5% (32/2151)   ← noise, and it says so
```

126 tests pass, including look-ahead, determinism, independence, phishing-safety,
authentication, and product-boundary checks.

---

## Safety boundary

Research, discovery, alerts, watchlists, and paper trading. **No custody, no private keys,
no transaction construction, no signing, no autonomous execution.** Public data only.

This is enforced by tests, not convention — `TestProductBoundary` greps the source for key
handling and transaction submission and fails CI if either appears. Shadow mode is the
default, and notifications require *both* `run_mode=live_alerts` and a configured
destination.

Every API endpoint except `/v1/health` requires a bearer token, and the service **refuses
to start** without one outside local development. Everything the system produces — tracked
wallets, dossier notes, the hypotheses behind them — is the product, and an unauthenticated
public hostname gives it away to whoever finds it.

The system detects coordinated pump activity and reports operator statistics. It will not
coordinate, promote, or amplify one, and reads only public data.

---

## Connecting real data

Everything currently runs on `alphagraph.providers.fixture`. To go live, implement the
interfaces in `alphagraph/providers/base.py` against real vendors and set
`ALPHAGRAPH_PROVIDER_MODE=live`. Config refuses to start in live mode without a chain
provider, rather than silently reporting empty coverage.

Budget envelope is $300–800/mo, dominated by the Solana archive access the discovery sweep
needs. Vendors are deliberately not named in code — evaluate current pricing and terms at
integration time and record the choice as an ADR.

---

## Reading order

- [`ALPHAGRAPH_SPEC.md`](./ALPHAGRAPH_SPEC.md) — source of truth
- [`docs/DEPLOY.md`](./docs/DEPLOY.md) — deploying to Render
- [`docs/adr/`](./docs/adr) — why the non-obvious decisions were made
- [`packages/alphagraph/discovery/engine.py`](./packages/alphagraph/discovery/engine.py) — the core loop
- [`packages/alphagraph/wallets/metrics.py`](./packages/alphagraph/wallets/metrics.py) — "clicks less", as arithmetic
- [`docs/ALPHAGRAPH_SPEC_V1_ARCHIVED.md`](./docs/ALPHAGRAPH_SPEC_V1_ARCHIVED.md) — superseded, kept for provenance

## Honest limitations

- **The fixture world encodes assumptions.** The planted archetypes come from one documented
  case study. Nothing here is validated against real chain data yet.
- **`silent_accumulation` is badly calibrated.** It fires 2,151 times and hits 32 — a 1.5%
  precision against a 6.8% base rate, so it is currently *worse than random*. Its
  significance score (0.3 + 0.1 × buyers) is an arbitrary formula that has never been fitted
  to anything. It has deliberately not been hand-tuned to make the demo look better; the
  nightly digest reports it, and it should be recalibrated against real data or removed.
- **The alert is not the edge.** By the time a wallet is public, dozens of people watch it.
  What this system provides is the months of preparation — dossiers, sequence models, track
  records — not a faster notification.
- **A tracked wallet is not a recommendation.** The most predictive entity in the case study
  lost money on the trade that made it famous.
