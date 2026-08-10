# AlphaGraph — Insider Footprint Intelligence

**Document status:** Build-ready master specification, version 2
**Supersedes:** `docs/ALPHAGRAPH_SPEC_V1_ARCHIVED.md` (retained for provenance only)
**Product type:** Private crypto wallet-intelligence, discovery, alerting, and paper-trading platform
**Primary user:** A single non-technical investor/researcher operating this system for their own research
**Networks at launch:** Solana (spot), Hyperliquid-class on-chain perpetuals, BNB Smart Chain (spot), plus CEX listing feeds
**MVP boundary:** Research, discovery, alerts, watchlists, and paper trading only. No custody, no private keys, no transaction construction, no signing, no autonomous execution.

---

## 0. Why This Version Exists

Version 1 specified a **new-token launch discovery** product: watch fresh tokens, detect when several already-known "high quality" wallets buy one, score it, alert. That is the crowded, low-edge version of this problem, and it would have caught neither of the two events that motivated this project.

Two concrete failures of v1, documented here so they are not reintroduced:

1. **It ranked wallets by profit and loss.** The entity at the centre of the CASHCAT episode was *unprofitable* — it round-tripped its position and was liquidated. Under v1's §10.3 wallet-quality maths and §10.2 instruction to exclude suspected insiders from copy cohorts, that entity would have been ranked poorly or filtered out entirely. Its actual value was a track record against a **future event** (25 of 25 assets it touched later appeared on Robinhood), not against returns.

2. **It could only watch wallets it already knew.** V1 has no mechanism for *finding* an unknown wallet. Every signal family points forward from a token launch. The wallets worth following are discovered by working **backwards from outcomes**, which v1 never does.

Additional v1 gaps corrected in v2:

| Gap in v1 | Correction in v2 |
| --- | --- |
| Absolute USD thresholds (`liquidity_usd_min: 100000`) would have discarded the $500 probe trade that opened the CASHCAT sequence | Significance is **entity-relative**, measured against that wallet's own history (§5.4) |
| Only settled trades ingested | Open/unfilled orders, perp positions, and liquidations are first-class events (§4.3). The loudest CASHCAT signal was a limit order that **never filled** |
| Single-moment signals only | Stateful multi-week **playbook sequences** across a wallet cluster (§7) |
| Clustering had no ordering semantics | Edges carry sequence position; "side wallet moves *after* main wallet" is representable (§6.3) |
| Wallets were an auto-computed population | **Entity dossiers** are first-class objects with human notes and hypotheses (§6.1) |
| No registry of what actually happened | **Outcome registry** of pumps, listings, and revivals — the labels everything else is scored against (§3) |
| No self-improvement loop | Nightly evaluation, discovery sweep, and governed threshold proposals (§9) |

Everything in v1 concerning point-in-time correctness, evidence-first design, provider abstraction, uncertainty display, security, and the research-only boundary is **retained in full**. Those principles were sound. Only the product thesis changed.

---

## 1. Product Thesis

> Insiders and informed entities cannot transact without leaving a public record. The edge is not being the insider. It is finding their footprints, learning their procedure, and being prepared before they act again.

The system exists to answer four questions:

1. **Who knows something?** Which wallets repeatedly appear early and quiet in assets that later move violently — across unrelated events, at a rate that is not luck?
2. **What is their procedure?** For a given entity, what is the observed sequence — probe, dormancy, bid, size, side-wallet — and where in that sequence are they right now?
3. **What are they touching today?** Live alerting when a tracked entity acts, weighted by how significant the action is *for that entity*.
4. **Did any of this actually work?** Honest point-in-time measurement of every signal against baselines, with sample sizes and uncertainty.

### 1.1 What the system is not

It does not supply conviction, and it will not make you money by itself. In the episode that motivated this project, the alert-worthy moment lasted minutes; the edge was built over months of studying one entity. The system's job is to do that months-long labour continuously and at scale — maintain the dossiers, compute the track records, surface the candidates — so that when a moment arrives, the preparation already exists.

It also does not promise that a tracked entity will be profitable. The most predictive entity in the motivating case study lost money on the very trade that made the pattern famous. **Predictive value and profitability are different measurements and are stored separately.**

### 1.2 Non-goals

- Autonomous trading, order construction, transaction signing, custody, or key handling
- Copy trading as a product feature
- Obtaining, soliciting, or acting on genuinely non-public information
- Coordinating, organising, promoting, or participating in the operation of a pump scheme
- Claims of certain wallet ownership or identity without authoritative public evidence
- Guarantees about listings, prices, or returns

---

## 2. Product Principles

Carried from v1 and extended.

1. **Evidence before narrative.** Every conclusion links to underlying transactions, orders, blocks, posts, and calculations.
2. **Point-in-time correctness.** Any historical evaluation may use only information that existed at the simulated decision time. This is the single most load-bearing rule in the document.
3. **Uncertainty is visible.** Identity, clustering, archetype classification, and outcome prediction are probabilistic and always display reasons, sample size, and confidence.
4. **Predictive skill ≠ profitability.** Tracked separately, displayed separately, never conflated.
5. **Significance is relative.** A $500 trade from a studied entity outranks a $1M trade from an unknown wallet.
6. **Absence of action is data.** Dormancy, unfilled orders, cancelled orders, and closed positions are recorded events.
7. **Discovery runs backwards.** Outcomes label wallets; wallets are not assumed in advance.
8. **The machine proposes, the human disposes.** Automated tuning is governed; no threshold changes itself into production.
9. **Risk is independent of opportunity.** A strong footprint signal never cancels contract, liquidity, concentration, or provenance risk.
10. **Provider portability, graceful degradation, cost as a constraint, security by design.** As v1.

---

## 3. Outcome Registry — The Labels

Nothing else in this system can be measured without this. Build it early.

The outcome registry records, for every asset, the **notable events that later happened to it**. These are the labels that turn a wallet's history into a track record.

### 3.1 Outcome classes

| Class | Definition (configurable, versioned) |
| --- | --- |
| `price_run` | Realised multiple from a defined baseline within a horizon — e.g. 3x/5x/10x within 7/30/90 days, measured on liquidity-adjusted prices |
| `cex_listing` | Asset listed on a tracked venue (Robinhood, Coinbase, Binance, Upbit, Kraken, and others), with announcement timestamp and first-observed timestamp recorded separately |
| `revival` | A dormant asset waking: sustained volume/price expansion after a defined dead period. Directly addresses the "coins that die and randomly pump" case |
| `rug_or_collapse` | Liquidity removal, transfer disablement, or terminal drawdown. **Required** — an entity whose picks pump then die is a different animal from one whose picks sustain |
| `pump_event` | Detected coordinated promotion episode, with start, peak, and decay timestamps |

### 3.2 Rules

- Every outcome stores `event_time` (when it happened on-chain / was announced) and `first_observed_at` (when this system saw it). Backtests may only use `first_observed_at`.
- Outcomes are **versioned definitions**. Changing the definition of "3x" creates a new version; it never mutates history.
- The universe must include **failures and dead assets**. Building the registry only from survivors is the fastest way to manufacture a fake edge.
- Listing feeds are ingested with strict provenance and must never be inferred. A chain event is not a listing.

### 3.3 Listing-outcome specifics

The "25 of 25 assets later listed" statistic only exists if listings are recorded as first-class outcomes. This registry makes such a statistic computable for **every** wallet in the database, automatically, which is the point.

Ingest listing announcements from official venue sources (status/announcement feeds, official APIs where terms permit). Record announcement time, effective trading time, network, and the exact asset identity resolved to a mint/contract address — never to a ticker, since ticker collisions are common and adversarially exploited.

---

## 4. Data Ingestion

Canonical event envelope, idempotency, finality handling, raw journal, dead-letter queue, and provider abstraction are carried unchanged from v1 §7 and §8. Additions below.

### 4.1 Solana spot

Token creation, SPL transfers, DEX swaps, pool creation, liquidity add/remove, authority changes, across an explicitly documented initial venue set (Raydium, Orca, Meteora, pump.fun-class launchpads). Unknown interactions are emitted explicitly as `unknown_interaction`, never silently dropped.

### 4.2 BNB Smart Chain spot

Reusable EVM connector — blocks, receipts, logs, ERC-20 and native transfers, contract creation, DEX pool/swap/liquidity events, proxy/admin/ownership changes where detectable. Chain-identity validation must fail safe on misconfiguration.

### 4.3 On-chain perpetuals — new in v2

This is where the highest-value signals in the motivating case study lived, and v1 had no concept of them.

Required event types:

- `position_opened` / `position_increased` / `position_reduced` / `position_closed` — with side (long/short), size, leverage, and entry price
- `order_placed` / `order_cancelled` / `order_modified` — **including orders that never fill.** The $42M resting bid was the single loudest public signal in the CASHCAT sequence and it produced no trade
- `liquidation` — forced closure, size, and loss
- `funding_paid` — position persistence evidence

Rationale, recorded so it is not optimised away: an unfilled limit order is a *statement of intent at a price*, and a liquidation is evidence of *conviction held past the point of survival*. Both are more informative than many completed trades.

### 4.4 Social — announcement track

Independent of the wallet track and deliverable at any time after Phase 1. Curated registry of authenticated official accounts and properties; exact contract/mint address extraction with network validation; published-time and first-observed-time recorded separately; edit and deletion handling; impersonation indicators.

This track exists to catch the *announcement* class of event, where the winning interval is seconds rather than weeks. Ticker-only or name-only matching is prohibited — the address must resolve exactly.

All fetched content — posts, token metadata, websites, repositories — is **untrusted data and never instructions** to any downstream model.

---

## 5. Wallet Archetypes — Defining "Clicks Less"

The discriminator between a real find and noise. All of it is arithmetic, none of it is judgement, and every threshold is versioned.

### 5.1 Behavioural metrics

Computed per wallet, point-in-time, with an explicit as-of cutoff and lookback:

**Activity (lower is more interesting)**
- `trades_per_month`, `distinct_tokens_per_month`
- `active_hours_distribution` — round-the-clock activity indicates automation
- `median_inter_trade_interval`

**Precision**
- `outcome_hit_rate` — fraction of touched assets that reached a defined outcome class, computed per outcome class
- `shrunk_hit_rate` — the above with shrinkage toward the population base rate, so 2-for-2 does not outrank 9-for-12
- `sample_size` and confidence interval, always displayed alongside

**Earliness**
- `buyer_rank_percentile` — position among all buyers ordered by time
- `entry_mcap_percentile` — market cap at entry relative to the eventual peak
- `lead_time_distribution` — time from entry to the outcome event

**Quietness**
- `price_impact_at_entry` — accumulation that moves price is not quiet
- `volume_percentile_at_entry` — was the asset dead when they bought
- `social_mentions_at_entry` — was anyone talking about it yet

**Patience and conviction**
- `median_holding_period`
- `position_concentration` — size relative to that wallet's own balance
- `round_trip_rate` — how often they exit early into their own thesis

### 5.2 Hard exclusions

Wallets matching these are excluded from insider cohorts regardless of measured performance. Their P&L often looks excellent and their predictive value to this user is zero.

- MEV bots, sandwichers, arbitrage bots
- Snipers entering in the first block or first few seconds of a launch
- Market makers and liquidity provisioning infrastructure
- Airdrop farmers and wash-trading clusters
- Any wallet with sub-second reaction times or continuous 24/7 activity
- Known exchange, bridge, custody, and program accounts

Exclusions are reversible, versioned, and always show the reason.

### 5.3 Archetype classification

Each wallet receives a classification with confidence, evidence, and version:

- `informed_accumulator` — low activity, high precision, early, quiet, patient. **The target archetype.**
- `listing_predictor` — specifically high hit-rate against `cex_listing` outcomes
- `revival_specialist` — repeatedly early in `revival` outcomes on dormant assets
- `pump_operator` — repeatedly present at the origin of `pump_event` outcomes, typically with `rug_or_collapse` following
- `high_frequency` / `bot` / `infrastructure` — excluded from insider cohorts
- `unclassified` — insufficient sample

A wallet may hold several classifications with separate confidences. Classification never destroys the underlying evidence.

### 5.4 Entity-relative significance

For every action by a tracked entity, compute a significance score **against that entity's own distribution**, not against absolute dollars:

- Size percentile within their own historical position sizes
- Deviation from their normal cadence — action after long dormancy scores higher
- Whether the action type is rare for them
- Whether it matches a known position in their playbook (§7)

A $500 probe from an entity that trades four times a year is a maximum-significance event. A $1M buy from an unknown wallet is not an event at all until that wallet has a record. This rule is why v1's absolute thresholds are removed.

---

## 6. Entities, Dossiers, and the Graph

### 6.1 Entity dossier — first-class object

Not a derived table. A durable research artifact per tracked entity:

- Member wallets with the evidence and confidence for each membership
- Human notes, hypotheses, and open questions — free text, versioned, authored, timestamped
- Observed playbook (§7) and current state within it
- Track record per outcome class with sample size and uncertainty
- Profitability, recorded **separately** from predictive skill
- Full timeline of actions with entity-relative significance
- Discovery provenance — which outcome events surfaced this entity, and when

### 6.2 Clustering

Carried from v1 §10.2 — common funder, synchronised behaviour, common deployer, recurring transfer paths, shared withdrawal batches (treated cautiously), common social/domain evidence. Every heuristic documents its false-positive modes. Clusters are versioned, reversible hypotheses and are never irreversibly merged.

### 6.3 Sequence semantics — new in v2

Graph edges additionally carry **ordering**: typical lag between wallet A acting and wallet B acting, direction, and the consistency of that ordering across observations.

This is what makes "the side wallet accumulating is the real confirmation" a representable, alertable fact rather than a piece of folklore. The confirmation was not that a related wallet acted — it was that it acted *in a specific position in a repeated sequence*.

---

## 7. Playbook / Sequence Engine

Insider behaviour in the motivating case was **procedural**, not discretionary: probe, abort, dormancy, resting bid, real entry, side-wallet confirmation, event. Point-in-time single-event detection cannot express that.

### 7.1 Model

A playbook is a versioned state machine per entity:

- **States** — observed stages such as `dormant`, `probing`, `aborted`, `bidding`, `accumulating`, `confirming`, `distributing`
- **Transitions** — the events that move between states, with observed time distributions
- **Historical fit** — how many past sequences support this playbook, and how many diverged
- **Current position** — which state this entity is in right now, for which asset, with confidence

### 7.2 Alerting on sequences

The valuable alert is not "wallet bought token." It is:

> *Entity X entered stage 4 of its 5-stage sequence on asset Y. Historically this stage precedes a listing by 2–4 weeks, observed in 6 of 8 prior sequences. Two prior sequences aborted at this stage.*

Alerts must state the historical base rate **and the failure count**. The abort case is part of the pattern — in the motivating episode the entity had a documented habit of opening, aborting the same day, and returning weeks later.

### 7.3 Discovery of playbooks

Playbooks are mined from an entity's history by aligning its action sequences preceding confirmed outcomes, then proposed to the user for confirmation. They are never silently auto-applied — a mined sequence on a sample of three is a coincidence with extra steps.

---

## 8. Signal Families

Deterministic rules only. No language model participates in detection or scoring.

1. **Silent accumulation** — sustained net buying with flat price, dead volume, and no social mentions. The dormant-coin-wakes-up case. Rare, and usually somebody who knows something.
2. **Tracked entity action** — any action by a dossier entity, ranked by entity-relative significance (§5.4).
3. **Playbook stage transition** — an entity advancing within a known sequence (§7.2).
4. **Sequence confirmation** — a side/related wallet acting in its historical ordering position relative to the primary wallet.
5. **Resting order placed** — an unfilled limit order or perp bid from a tracked entity, below market. Explicitly a signal despite no trade occurring.
6. **Convergence** — multiple independent `informed_accumulator` wallets, with no detected relationship to each other, entering the same asset within a window. Independence is required; a cluster acting together is one opinion, not several.
7. **Cross-venue confirmation** — spot accumulation plus perp positioning in the same direction on the same asset.
8. **Newly surfaced candidate** — the nightly discovery sweep promoted a new wallet worth reviewing.
9. **Pump-operator activity** — a classified `pump_operator` initiating a new episode, delivered with their historical timing statistics (§8.1).
10. **Authenticated announcement** — an official account published an exact contract address, confirmed on-chain.
11. **Risk / data-quality** — reorg, finality reversal, provider conflict, transfer restriction, deleted social evidence, or severe new contract risk.

### 8.1 Pump-operator signals — scope and honesty

Detecting a coordinated promotion episode from public data is legitimate research and is in scope. What the system provides is the **operator dossier**: historical episode duration, typical peak timing, drawdown profile, and measured time from initiation to distribution.

Recorded plainly because it should inform how this feature is used: entering a detected pump means the exit depends on beating participants who already know the distribution schedule, and it is frequently the losing side. The system therefore presents measured operator statistics, never an inducement, and always alongside the `rug_or_collapse` history of that operator's prior assets.

The system will not coordinate, organise, promote, or amplify such an episode, and will not integrate any feature that does.

---

## 9. Nightly Evaluation and Improvement Loop

The requested "AI that improves daily," specified so that it actually compounds rather than overfits.

### 9.1 What runs every night

1. **Outcome update** — refresh the registry; label yesterday's assets against every outcome class.
2. **Alert scoring** — grade every alert issued in the trailing window against what subsequently happened. Record hits, misses, and near-misses.
3. **Cohort maintenance** — recompute every tracked wallet's metrics. Flag decaying wallets for demotion and improving candidates for promotion.
4. **Discovery sweep** — the reverse lookup (§10) over newly confirmed outcomes.
5. **Proposal generation** — where evidence supports a threshold or definition change, generate a proposal with a point-in-time backtest attached.
6. **Morning digest** — new candidates, promotions, demotions, alerts that worked, alerts that failed, coverage, and spend.

### 9.2 Governance — the part that prevents rot

Being explicit, because this is where systems of this kind usually fail:

- **A language model does not tune thresholds.** It writes the digest and explains evidence. Numerical tuning is statistical code.
- **No proposal auto-applies.** Every change is presented to the user with its backtest, sample size, and uncertainty, and requires approval.
- **Every proposal is validated point-in-time** on validation data only.
- **A locked holdout period is never touched** until final evaluation.
- **Multiple-testing awareness** — proposals are counted; a system testing hundreds of variants nightly will find spurious winners, and the digest reports how many were tried.
- **Change log** — every accepted change is versioned with its justification and its subsequent measured effect.

Auto-tuning on small samples is how you build something that looks brilliant on history and loses money live. The bot does the labour and proposes; the user decides.

### 9.3 AI research layer

Carried from v1 §14 unchanged in principle. The model summarises structured evidence into readable briefs and dossier updates, with every claim mapped to evidence IDs, plus certainty, conflicts, risks, and unknowns. It never detects events, never scores, never trades. Prompt-injection isolation is mandatory; deterministic templates are the fallback when the provider is unavailable or the budget is exhausted.

---

## 10. Reverse Discovery Engine

The core loop, and the component with no equivalent in v1. Everything else in the system either feeds this or consumes its output.

### 10.1 Algorithm

```text
For each newly confirmed outcome event E (pump, listing, revival):
  1. Establish t0 = the moment the move became public
  2. Enumerate every buyer in the windows [t0-1d, t0], [t0-7d, t0], [t0-30d, t0]
  3. For each buyer, compute:
       - earliness (rank and market-cap percentile)
       - quietness (price impact, volume percentile, social silence)
       - size relative to that wallet's own history
  4. Apply hard exclusions (§5.2)
  5. Emit (wallet, outcome_event, scores) rows

Then, across the accumulated corpus:
  6. Aggregate per wallet across ALL outcome events
  7. Require a minimum number of independent events
  8. Verify events are genuinely independent — not one cluster, one week,
     one launchpad, or one correlated market regime
  9. Compute shrunk hit rate vs the population base rate
  10. Apply archetype classification (§5.3)
  11. Promote survivors to CANDIDATE status for user review
```

### 10.2 Guards against fake discoveries

Given enough historical assets, some wallets will look extraordinary purely by chance. Mandatory controls:

- **Population base rate** must be computed and displayed alongside every hit rate. If 5% of all assets 3x, a wallet at 8% over 12 events has found nothing.
- **Independence testing** — repeated appearances within one cluster, one time window, or one launchpad count as approximately one event, not many.
- **Minimum sample size** with shrinkage, enforced by default.
- **Forward shadow test** — a candidate is watched live, without trading, for a defined period before promotion to tracked status. This is the only real test.
- **Negative controls** — random-wallet and momentum baselines run against the same pipeline. If the baselines look good too, the pipeline is broken.
- **Survivorship audit** — the asset universe is reconstructed as it existed historically, including everything that later died.

### 10.3 Promotion lifecycle

```text
surfaced → candidate → shadow-watched → tracked → (decaying) → retired
```

Every transition is timestamped, evidence-backed, reversible, and reviewable. Retired entities keep their full history; nothing is deleted.

---

## 11. Dashboard

- **Today** — what needs attention now: live alerts ranked by entity-relative significance, with the three strongest supporting facts and the top risks
- **Insider Radar** — tracked entities, current playbook state, what they are holding, hit rate with sample size and confidence interval
- **Discovery** — newly surfaced candidates awaiting review, each with the outcome events that surfaced them and the baseline comparison
- **Entity Dossier** — notes, member wallets, fingerprint, playbook, track record, full timeline, discovery provenance
- **Asset page** — who bought early and quietly, quiet-accumulation score, holder concentration, contract and admin risk, liquidity, event and social timeline
- **Backtests** — definitions, runs, baselines, calibration, segment analysis
- **Paper Portfolio** — clearly simulated positions with configurable delay, fees, and slippage
- **System** — provider health, data freshness, coverage gaps, budget and spend
- **Settings** — destinations, thresholds, quiet hours

Requirements carried from v1 §16: separate display of opportunity, confidence, and risk; explicit empty, loading, stale, partial, and outage states; never colour alone for risk; user timezone plus UTC; keyboard accessibility; responsive and mobile-capable for receiving and investigating an alert.

---

## 12. Providers and Cost

Budget envelope: **$300–800/month**, which makes historical discovery sweeps genuinely viable rather than rate-limited to a trickle.

Provider selection is deliberately not fixed in this document. Candidates must be evaluated at implementation time against current pricing, current capability, and current terms of service, and recorded in an architecture decision record. Categories required:

- **Solana archive/indexer** with historical transaction access sufficient for backfill sweeps
- **DEX/price/liquidity data** with historical OHLCV and pool state
- **Perpetuals data** — public APIs where available
- **EVM/BSC RPC and log access**
- **CEX listing announcement sources** — official feeds, with terms reviewed before storage or redistribution
- **AI provider** for the research layer only

Cost controls carried from v1 §20: tiered ingestion (real-time watched entities / priority discovery universe / batch backfill), caching of immutable data, request batching and deduplication, cheap deterministic filters before expensive enrichment, AI summaries only for displayed or alerted candidates, per-operation cost ledger, budget caps with graceful degradation, and a UI that explains any reduced coverage.

Never encode vendor prices in product logic. A local development environment must run entirely on fixtures at near-zero cost.

---

## 13. Backtesting

Carried from v1 §15 in full, with these additions specific to v2:

- **Discovery backtests** — freeze the corpus at a past date, run the discovery engine, and measure whether the wallets it would have surfaced then went on to perform. This validates the engine itself, not just individual signals.
- **Playbook backtests** — measure how often a mined sequence completed versus aborted.
- **Listing-prediction metrics** — precision, recall, and lead-time distribution against `cex_listing` outcomes, separate from return-based metrics.

Non-negotiable: automated look-ahead audit; dead, rugged, and delisted assets retained in the universe; baselines and sample sizes shown beside every result; walk-forward evaluation; a locked holdout untouched until final evaluation; and no threshold promoted on the strength of a single exceptional asset.

---

## 14. Security, Legal, and Safety Posture

Carried from v1 §18 in full: managed authentication, workspace-scoped authorization, secrets management, webhook signature verification with replay protection, strict input validation, SSRF-safe retrieval, sandboxed treatment of untrusted content, dependency and secret scanning, immutable audit log, encrypted and tested backups.

Version 2 additions:

- **Public data only.** The system reads public blockchains and lawfully accessible public sources. It must never solicit, ingest, purchase, or act upon genuinely non-public information, and must never present itself as a channel for such information.
- **No manipulation participation.** Detection and analysis of coordinated activity is in scope. Coordination, organisation, promotion, or amplification of it is not, and no feature that facilitates it will be built.
- **Provider terms tracked** per source for retention and redistribution limits.
- **Research-only disclosure** displayed prominently. No guarantees, no fiduciary language, no personalised advice.
- **Identity claims are evidenced.** Language is "likely linked" or "provider-labeled," never "owned by," unless authoritative public evidence supports it.
- Qualified counsel before any commercialisation, paid recommendation, copy-trading, or execution feature.

---

## 15. Roadmap

Phases marked **∥** may be developed in parallel once their prerequisites are met.

| # | Phase | Delivers |
| --- | --- | --- |
| 0 | Foundation and guardrails | Monorepo, local stack, CI, config validation, ADRs, threat model, fixtures |
| 1 | Canonical contracts and provider abstractions | Versioned schemas, provider interfaces, idempotency, address/unit/time utilities, raw journal, dead-letter, cost ledger, mock adapters |
| 2 | Solana spot ingestion | Streaming and backfill, DEX/token/liquidity parsers, finality and reversal handling, coverage metrics |
| 3 | Outcome registry ∥ | Price-run, listing, revival, collapse, and pump outcome detection; CEX listing feeds; point-in-time observation timestamps |
| 4 | Reverse discovery engine | The backwards sweep, independence testing, base-rate comparison, negative controls, candidate promotion lifecycle |
| 5 | Wallet archetypes and cohorts | Behavioural metrics, hard exclusions, shrinkage, classification, entity-relative significance |
| 6 | **Watchlist and live alerting — MVP-1** | Tracked-entity ingestion, significance-ranked alerts, notification adapters, deep links, shadow mode |
| 7 | Perpetuals ingestion ∥ | Positions, unfilled orders, cancellations, liquidations, funding; cross-venue confirmation |
| 8 | Announcement track ∥ | Official-account registry, exact address extraction, on-chain confirmation, impersonation handling |
| 9 | Entity dossiers and graph | Dossier objects, human notes, clustering with false-positive documentation, sequence-aware edges |
| 10 | Playbook / sequence engine | State machines, mining, stage-transition alerts with base rates and abort counts |
| 11 | Token enrichment and risk | Price, liquidity, holders, adjusted concentration, admin/mint/freeze, transferability, provenance, freshness |
| 12 | Point-in-time backtesting | Simulation clock, execution assumptions, baselines, calibration, discovery and playbook backtests, leakage audit |
| 13 | Nightly evaluation and improvement loop | Outcome refresh, alert grading, cohort maintenance, governed proposals, morning digest |
| 14 | **Dashboard and paper trading — MVP-2** | All screens, accessible and responsive, simulated portfolio, feedback capture, onboarding |
| 15 | BSC / EVM expansion ∥ | Reusable EVM connector, BSC configuration, reconciliation |
| 16 | Grounded AI research layer | Evidence retrieval, structured claims and citations, injection defences, deterministic fallback, evaluation suite |
| 17 | Security, ops, and cost hardening | Authorization review, drills, tracing, SLIs, runbooks, budgets, recovery |
| 18 | Private pilot, calibration, release gate | Shadow pilot, human review sample, outcome report vs baselines, calibration, go/no-go |

**MVP-1 at Phase 6** is the first point of real standalone value: the system finds candidate insider wallets from history and alerts when they act. **MVP-2 at Phase 14** is the full researchable product.

### 15.1 Delivery rules

Carried from v1 §22 and binding:

1. Read this file and existing ADRs before starting.
2. Inspect the repository before changing it.
3. Write a short implementation plan and list assumptions.
4. Implement only the requested phase and its prerequisites.
5. Never invent API credentials, wallet labels, provider capabilities, or network details.
6. Use fixtures and mocks when credentials are unavailable.
7. Add migrations, tests, documentation, and sample configuration.
8. Run relevant checks and report exact results.
9. Update the phase checklist and ADRs.
10. Stop at the phase boundary and ask for review.

Do not deploy, purchase services, or create external accounts unless explicitly asked. Never add autonomous trading as an incidental feature.

---

## 16. Success Measures

**Discovery quality** — candidates surfaced per month; fraction surviving shadow watch; precision against the population base rate; independence of the events supporting each candidate.

**Predictive value** — hit rate and lead time per outcome class, with sample size and confidence interval, per entity and in aggregate; performance versus random and momentum baselines; calibration by confidence bucket.

**Alert quality** — precision, false-positive rate, duplicate rate, and user-rated usefulness; risk flags raised before simulated entry.

**Operational** — ingest and alert latency, parse coverage, provider gap duration, cost per watched entity and per surfaced candidate, budget forecast accuracy.

**The honesty measure** — fraction of surfaced candidates that later fail. If this number is near zero, the guards in §10.2 are not working.

Success is not catching every move. Success is a system that finds real footprints faster than a human could, states honestly how confident it is, and is measurably better at it each quarter.

---

## 17. Deferred Beyond MVP

Additional networks, team collaboration and review queues, a visual signal builder, graph embeddings and anomaly detection once labeled evaluation data exists, a native mobile app, and read-only portfolio import.

Any transaction preparation, signing, automated execution, copy trading, or custody requires a separate specification, threat model, legal review, permission model, and explicit authorization. It is not an extension of this MVP by default.

---

**End of specification.**
