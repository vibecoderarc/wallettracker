# AlphaGraph — Full Product and Implementation Specification

**Document status:** Build-ready master specification  
**Product type:** Private crypto intelligence, research, alerting, and paper-trading platform  
**Initial networks:** Solana, BNB Smart Chain (BSC), and Robinhood Chain/Ecosystem subject to verified public data availability  
**Primary user:** A non-technical investor/researcher who wants earlier, evidence-backed awareness of potentially important token launches and wallet activity  
**MVP boundary:** Research, alerts, watchlists, and paper trading only. No autonomous order placement, custody, private-key handling, or promises of profit.

---

## 1. Executive Summary

AlphaGraph continuously watches public blockchain activity and selected public social sources, organizes addresses into explainable wallet/entity relationships, detects notable events, scores them, and presents concise evidence-backed alerts.

The product is designed to answer questions such as:

- Did historically early or high-quality wallets buy the same new token in a short window?
- Is a deployer, funder, buyer, or liquidity provider connected to previously successful launches?
- Did an official, authenticated public account announce a token or contract address?
- Is activity organic, or does it resemble coordinated wallets, wash trading, bundled supply, or a honeypot/rug pattern?
- How did similar signals perform historically when evaluated using only information available at the time?

AlphaGraph must not label a token as “safe,” “guaranteed,” or “the next winner.” It reports evidence, uncertainty, conflicts, and risk. The user makes every investment decision independently.

### 1.1 Product promise

> Turn fragmented public on-chain and social activity into timely, traceable research alerts with an honest confidence level.

### 1.2 Non-goals

- Autonomous trading or automated transaction signing in the MVP.
- Holding keys, seed phrases, exchange credentials with withdrawal rights, or user funds.
- Copy trading.
- Market manipulation, front-running, sandwiching, evasion, or use of non-public/private information.
- Claims that wallet ownership or identity is certain unless supported by authoritative public evidence.
- Guaranteed prediction of listings, prices, returns, or celebrity involvement.
- Replacing legal, tax, compliance, or financial advice.

---

## 2. Product Principles

1. **Evidence before narrative.** Every conclusion links to underlying transactions, blocks, posts, labels, and calculations.
2. **Point-in-time correctness.** Backtests may use only information that existed at the simulated decision time.
3. **Uncertainty is visible.** Identity, clustering, and outcome predictions are probabilistic and include reasons and confidence.
4. **Risk is independent of opportunity.** A strong momentum signal does not cancel contract, liquidity, concentration, or provenance risk.
5. **Human approval remains mandatory.** Alerts inform; they never transact.
6. **Provider portability.** Chain, labeling, social, price, notification, and AI vendors sit behind internal interfaces.
7. **Graceful degradation.** A provider outage should reduce coverage visibly, not fabricate normal operation.
8. **Cost is a product constraint.** Budgets, quotas, caching, sampling, and tiered analysis are built in from day one.
9. **Start narrow, validate, then expand.** Prove signal quality on a limited universe before adding networks and feeds.
10. **Security and compliance by design.** Public data, least privilege, clear retention, auditable access, and no secrets in source control.

---

## 3. Users and Core Workflows

### 3.1 Initial persona

The first user is comfortable interpreting crypto markets but has little or no coding experience. The interface must use plain language, disclose limitations, and make raw evidence available without requiring it for basic use.

### 3.2 Core workflows

#### Morning research

1. Open “Today.”
2. Review the highest-ranked new events.
3. Inspect opportunity, confidence, and risk separately.
4. Open an asset brief and evidence timeline.
5. Add it to a watchlist or create a paper position.

#### Real-time alert

1. The system detects a qualifying event.
2. Deterministic rules create a candidate.
3. Risk and data-quality gates run.
4. The scorer ranks the candidate.
5. If it crosses the user threshold, send a Telegram/Discord/email/web-push alert.
6. The alert deep-links to the evidence page.

#### Wallet investigation

1. Search an address, token, transaction, or entity.
2. View labels with sources and confidence.
3. Review funding lineage, counterparties, related wallets, prior tokens, and historical performance.
4. Expand graph edges and inspect the reason for each link.

#### Historical validation

1. Select a signal definition and date range.
2. Run a point-in-time backtest with fees, slippage assumptions, latency, and delisting/missing-price handling.
3. Compare against simple baselines.
4. Inspect returns, drawdowns, hit rate, sample size, calibration, and failure cases.

---

## 4. Scope and Assumptions

### 4.1 Initial network rollout

- **Solana:** First production connector because of high token-launch activity. Support native transactions, SPL tokens, token metadata, DEX swaps/pools, liquidity changes, holders, and program interactions relevant to supported protocols.
- **BNB Smart Chain:** EVM connector supporting blocks, transactions, logs, ERC-20 contracts, DEX pools/swaps, ownership/admin controls, liquidity, and holder distribution.
- **Robinhood Chain/Ecosystem:** Treat as a discovery and validation track until its exact public mainnet/testnet status, RPC/indexer coverage, canonical identifiers, and relevant token-listing semantics are verified. Never hard-code assumptions that “Robinhood Chain listing” is equivalent to a Robinhood brokerage/exchange listing. Implement it through a configurable EVM-compatible adapter if applicable.

### 4.2 Data availability principle

The system may integrate paid providers such as managed RPC/indexers, labeling platforms, price feeds, and social APIs, but the product must remain usable in a limited mode using public RPCs and open data. Provider terms must be reviewed before storing or redistributing labels/content.

### 4.3 Latency targets

- Tier A watched-wallet events: ingest within 10–30 seconds of provider availability when the chain/provider supports it.
- General discovery events: within 1–5 minutes.
- Alert dispatch after event normalization: p95 under 30 seconds.
- Dashboard freshness indicator: always visible.

Targets are service objectives, not guarantees; record actual end-to-end latency.

---

## 5. Functional Requirements

### 5.1 Live event feed

- Filter by network, event type, token age, score, confidence, risk, source, wallet cohort, and time.
- Pause/resume live updates.
- Show observed time, chain time, ingestion delay, and finality state.
- Group duplicate observations into one canonical event.
- Link every item to raw evidence and explorer URLs.

### 5.2 Watchlists

- Wallet, entity, token, deployer, social account, keyword, and contract-address watchlists.
- User-defined alert thresholds and quiet hours.
- Import/export CSV or JSON.
- Tags, notes, and provenance for manually added items.

### 5.3 Wallet intelligence

- Transaction and token history.
- Funding-source lineage with bounded depth.
- Common counterparties and behavioral similarity.
- Realized/unrealized performance estimates with explicit accounting method and data coverage.
- “Early buyer” metrics using point-in-time token age and liquidity/market-cap thresholds.
- Wallet quality metrics with minimum sample size and recency weighting.
- Sybil/coordinated-cluster indicators.
- Labels with source, evidence, author, timestamps, and confidence.

### 5.4 Entity graph

- Nodes: wallet, entity, token, contract/program, deployer, pool, exchange/service, social account, domain, repository, and event.
- Edges: funded-by, controls, likely-related, deployed, bought, sold, transferred, provided-liquidity, interacted-with, announced, mentions, belongs-to, and same-entity-as.
- Each inferred edge stores method, evidence, confidence, creation time, validity interval, and model/rule version.
- Never merge addresses irreversibly. Clusters are versioned hypotheses.

### 5.5 Token/asset intelligence

- Creation/deployment and metadata timeline.
- Deployer and funder history.
- Liquidity, volume, price, holder count, concentration, transferability, mint/freeze/admin privileges, and pool status.
- Top-holder analysis adjusted for known pools, burns, bridges, exchanges, and program accounts.
- Contract/source verification where relevant.
- Known scam/rug heuristics and simulation/vendor checks where legally and technically permitted.
- Social and website provenance.

### 5.6 Signals and alerts

Initial signal families:

1. Multiple high-quality wallets acquire a young token within a defined window.
2. A watched wallet/entity first acquires or deploys a token.
3. A deployer/funder resembles a historically successful or risky cluster.
4. New pool/liquidity event combined with verified social confirmation.
5. Unusual accumulation relative to wallet history and available liquidity.
6. Significant liquidity addition/removal.
7. Concentration or admin-risk state changes.
8. Authenticated public announcement containing a contract address.
9. Cross-source convergence: on-chain, social, and code/domain evidence agree.
10. Data-quality/risk alert: provider conflict, chain reorg/finality reversal, suspicious holder clustering, or transfer restriction.

### 5.7 Paper trading

- Create manual or alert-derived simulated positions.
- Configurable entry delay, slippage, fees, price source, and position size.
- Mark-to-market snapshots and performance dashboard.
- Exits remain manual or rule-simulated; no transaction construction or signing.
- Paper results must be labeled prominently and never presented as executable fills.

---

## 6. System Architecture

### 6.1 Recommended stack

Use current stable versions at implementation time; pin exact versions in lockfiles and architecture records.

- **Frontend:** Next.js + TypeScript, a maintainable component library, accessible charts, and server-side authentication.
- **API/application layer:** Python FastAPI or TypeScript service. Prefer Python for analytics/data work unless the initial team strongly favors a single-language TypeScript stack.
- **Workers:** Python async workers for ingestion, normalization, enrichment, scoring, and backtests.
- **Operational database:** PostgreSQL.
- **Time-series:** PostgreSQL partitioning/Timescale-compatible extension if available; do not require it for local development.
- **Graph:** Start with relational node/edge tables and recursive queries. Add a graph database only after measured query pressure justifies it.
- **Queue/cache:** Redis-compatible queue and cache for MVP; introduce a durable streaming platform only when throughput/replay requirements warrant it.
- **Object storage:** S3-compatible storage for raw payloads, backtest artifacts, and exports.
- **Observability:** OpenTelemetry-compatible traces/metrics/logs plus an error tracker.
- **Infrastructure:** Containerized local environment; infrastructure as code for hosted environments.

### 6.2 Logical services

```text
Chain/Social/Price/Label Providers
              |
        Connector Adapters
              |
        Raw Event Journal  ----> Dead-letter Queue
              |
     Normalization + Finality
              |
        Canonical Event Bus
        /       |         \
 Enrichment   Graph     Market Data
        \       |         /
         Signal Candidates
                 |
       Risk + Confidence + Rank
                 |
        Alert Policy / Dedupe
          /       |       \
 Dashboard   Notifications   Research Agent
                 |
         Paper Trading / Backtests
```

### 6.3 Repository shape

```text
/apps/web                 User dashboard
/apps/api                 Authenticated API
/services/ingestion       Chain/social/provider workers
/services/enrichment      Wallet, token, label, and graph enrichment
/services/signals         Rules, features, scoring, and alert policy
/services/backtesting     Point-in-time simulations
/services/research        Evidence retrieval and AI summaries
/packages/contracts       Shared schemas and generated clients
/packages/provider-sdk    Provider interfaces and adapters
/db                       Migrations, seeds, fixtures
/infra                    Local and hosted infrastructure definitions
/docs                     ADRs, runbooks, data dictionary, threat model
/tests                    Cross-service integration and end-to-end tests
```

### 6.4 Event flow and guarantees

- Preserve immutable raw observations before normalization when licensing permits.
- Assign an internal observation ID and provider cursor.
- Normalize to versioned canonical schemas.
- Use idempotency keys based on network, transaction/log coordinates, event type, and semantic index.
- Consumers are at-least-once; writes must be idempotent.
- Track chain finality/confirmation and support reversals.
- Store both chain timestamp and system-observed timestamp.
- Replay a bounded event range without duplicating state or alerts.
- Failed messages go to a visible dead-letter queue with retry controls.

---

## 7. Provider Abstraction Contracts

No business logic may depend directly on a provider-specific response. Each adapter maps into internal contracts and reports capabilities.

### 7.1 Chain provider

```ts
interface ChainProvider {
  network(): NetworkDescriptor;
  capabilities(): ChainCapabilities;
  subscribeAddresses(addresses: string[], cursor?: string): AsyncIterable<RawObservation>;
  subscribeBlocks(cursor?: string): AsyncIterable<RawBlock>;
  getTransaction(id: string): Promise<RawTransaction>;
  getToken(tokenAddress: string, at?: PointInTime): Promise<TokenSnapshot>;
  getBalances(address: string, at?: PointInTime): Promise<BalanceSnapshot[]>;
  getLogs(query: LogQuery): AsyncIterable<RawLog>;
  health(): Promise<ProviderHealth>;
}
```

### 7.2 Market-data provider

```ts
interface MarketDataProvider {
  quote(asset: AssetRef, at?: PointInTime): Promise<Quote | MissingQuote>;
  candles(asset: AssetRef, range: TimeRange, resolution: string): Promise<Candle[]>;
  liquidity(asset: AssetRef, at?: PointInTime): Promise<LiquiditySnapshot>;
  resolvePairs(asset: AssetRef): Promise<MarketPair[]>;
}
```

### 7.3 Label/entity provider

```ts
interface LabelProvider {
  labels(addresses: AddressRef[]): Promise<ExternalLabel[]>;
  entity(address: AddressRef): Promise<ExternalEntityClaim[]>;
  usagePolicy(): ProviderUsagePolicy;
}
```

### 7.4 Social provider

```ts
interface SocialProvider {
  search(query: SocialQuery, cursor?: string): Promise<SocialPage>;
  accounts(ids: string[]): Promise<SocialAccount[]>;
  stream?(rules: SocialRule[]): AsyncIterable<SocialObservation>;
}
```

### 7.5 Notification provider

```ts
interface NotificationProvider {
  validateDestination(destination: Destination): Promise<ValidationResult>;
  send(message: AlertMessage, destination: Destination): Promise<DeliveryReceipt>;
}
```

### 7.6 AI provider

```ts
interface AIProvider {
  summarize(evidenceBundle: EvidenceBundle, policy: ResearchPolicy): Promise<GroundedSummary>;
  answer(question: string, evidenceBundle: EvidenceBundle, policy: ResearchPolicy): Promise<GroundedAnswer>;
}
```

Every provider call records provider, operation, latency, status, cost estimate, quota state, cache status, request correlation ID, and response timestamp. Do not log credentials or unnecessary personal data.

---

## 8. Data Ingestion and Normalization

### 8.1 Ingestion modes

- **WebSocket/stream:** watched addresses and selected contracts/programs.
- **Polling:** provider fallback and sources without streams.
- **Webhook:** supported provider events with signature verification and replay protection.
- **Batch backfill:** historical blocks, transactions, prices, labels, and posts.

### 8.2 Canonical event envelope

Required fields:

- `event_id`, `schema_version`, `event_type`
- `network_id`, `chain_id`, `block_height_or_slot`, `block_hash`
- `transaction_id`, `event_index`
- `chain_time`, `observed_at`, `normalized_at`
- `finality_status`
- `subjects[]` and `objects[]`
- normalized quantities plus original atomic units and decimals
- `provider`, `provider_cursor`, `raw_object_uri`, `raw_hash`
- `parser_version`, `quality_flags[]`, `correlation_id`

### 8.3 Canonical event types

- block observed/finalized/reverted
- native/token transfer
- swap
- token created/minted/burned
- contract/program deployed
- pool created
- liquidity added/removed
- authority/admin change
- metadata change
- social post/account change
- label claim created/changed
- price/liquidity/holder snapshot

### 8.4 Network-specific parsing

Solana and EVM transactions are materially different. Share the canonical output schemas, not brittle parsing code. Maintain protocol parsers as versioned plug-ins, with fixtures from real historical transactions and explicit “unknown interaction” output instead of silent drops.

### 8.5 Quality controls

- Validate decimals and raw integer preservation.
- Detect provider gaps, duplicates, out-of-order data, and cursor regressions.
- Reconcile a sample against an independent source.
- Record unsupported protocols and unparsed transaction ratios.
- Mark stale snapshots; never silently carry them forward as current.
- Apply finality-aware alert policies.

---

## 9. Database Model

Use UUIDs internally, UTC timestamps, append-only history for time-sensitive facts, and migrations for all schema changes.

### 9.1 Identity and configuration

- `users`: identity reference, status, locale, timezone.
- `workspaces`: ownership and plan.
- `memberships`: role and audit metadata.
- `watchlists`, `watchlist_items`.
- `alert_rules`, `alert_destinations`, `notification_deliveries`.
- `provider_connections`: encrypted secret reference, not plaintext secret.
- `feature_flags`, `budget_policies`.

### 9.2 Chain and asset data

- `networks`: canonical name, chain ID/genesis identifier, native asset, finality policy, status.
- `blocks`: network, height/slot, hash, parent, timestamps, finality.
- `transactions`: network, hash/signature, block, sender/fee payer, status, fee, raw reference.
- `addresses`: normalized address, network, type, first/last seen.
- `assets`: network, address/mint, symbol/name, decimals, asset type, creation event.
- `asset_metadata_history`: values, source, valid/observed intervals.
- `transfers`, `swaps`, `pools`, `liquidity_events`.
- `balance_snapshots`, `holder_snapshots`, `market_snapshots`, `candles`.
- `contracts_programs`: code/program identity and verification state.
- `admin_authority_history`.

### 9.3 Entity intelligence

- `entities`: type, display name, status.
- `entity_labels`: label, source, evidence URL/reference, confidence, validity, review status.
- `entity_memberships`: entity/address link, method, confidence, validity, version.
- `graph_nodes`, `graph_edges` or relational views over canonical tables.
- `wallet_features`: feature name/value, as-of time, lookback, calculation version.
- `wallet_cohorts`: definition and version.
- `wallet_cohort_memberships`: score, rank, as-of time.

### 9.4 Signals and research

- `signal_definitions`: immutable versioned configuration.
- `signal_candidates`: trigger time, feature vector, evidence bundle ID, state.
- `signal_scores`: opportunity, confidence, risk dimensions, model/rule versions.
- `alerts`: candidate, policy, status, dedupe key, sent time.
- `evidence_bundles`: manifest and point-in-time cutoff.
- `evidence_items`: source type, source ID, observed time, excerpt/hash, URI.
- `research_reports`: structured claims, citations, generated time, model and prompt versions.
- `user_feedback`: useful/not useful, reason, optional outcome note.

### 9.5 Backtesting and paper trading

- `backtest_definitions`, `backtest_runs`, `backtest_events`, `backtest_positions`, `backtest_metrics`.
- `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `paper_equity_snapshots`.
- `evaluation_labels`: outcome definitions and point-in-time eligibility.

### 9.6 Operations and audit

- `raw_observations`, `ingestion_cursors`, `provider_health_samples`.
- `jobs`, `job_attempts`, `dead_letters`.
- `audit_log`: actor, action, resource, before/after hashes, timestamp, request ID.
- `cost_ledger`: provider/service, units, estimated cost, workspace, operation, time.
- `data_quality_incidents`.

### 9.7 Important indexes and retention

- Unique transaction/log and event idempotency indexes.
- Time/network/address composite indexes for transfers and swaps.
- Candidate score/time indexes.
- Graph source/destination/type indexes.
- Partition high-volume event/snapshot tables by time and optionally network.
- Define retention by data class; retain compact canonical facts longer than large raw payloads.
- Legal/contractual deletion rules override convenience.

---

## 10. Wallet and Entity Intelligence

### 10.1 Label confidence hierarchy

Example source weighting, configurable and never treated as absolute truth:

1. Authoritative self-disclosure or verified organization documentation.
2. Multiple reputable independent public sources.
3. A licensed third-party label provider.
4. Strong deterministic linkage such as direct creation/control evidence.
5. Behavioral or funding heuristic.
6. Unverified community/manual assertion.

Conflicting claims coexist and reduce confidence until resolved. UI language should say “likely linked” or “provider-labeled,” not “owned by,” unless evidence warrants it.

### 10.2 Clustering heuristics

- Common funder with temporal proximity.
- Repeated synchronized buys/sells with similarity above a threshold.
- Common deployer/admin relationships.
- Recurring transfer paths and sweep behavior.
- Shared exchange withdrawal batch, treated cautiously because it is not proof of common ownership.
- Common social/domain/repository evidence.
- Reused transaction patterns or infrastructure, where reliable.

Each heuristic must document false-positive modes. Cluster membership is versioned, reversible, and reviewable.

### 10.3 Wallet performance

Calculate with explicit assumptions:

- Cost basis method.
- Known and unknown transfers.
- Realized versus marked returns.
- Fees and estimated slippage.
- Price availability and stale pricing.
- Token age at entry.
- Liquidity at entry and realistic executable size.
- Maximum adverse/favorable excursion.
- Time-to-2x/5x or loss thresholds, used descriptively rather than as promises.

Wallet rank must use shrinkage/minimum samples so one lucky trade cannot create a “smart money” label. Weight recent performance while preserving stability. Exclude suspected insiders/manipulators from ordinary copy-signal cohorts or label them separately.

---

## 11. Signal Engine

### 11.1 Pipeline

1. Canonical event arrives.
2. Cheap deterministic eligibility filters run.
3. Required point-in-time features are assembled.
4. Candidate is created with immutable evidence cutoff.
5. Contract/token and market risk gates run.
6. Opportunity, confidence, and risk scores are calculated.
7. Alert policy applies threshold, dedupe, cooldown, rate limit, and user preferences.
8. Optional AI summary is generated only after the structured candidate exists.
9. Alert and all versions are logged for evaluation.

The language model must not be the primary event detector or numerical scorer. It summarizes structured evidence and may suggest research questions.

### 11.2 Example signal definition

```yaml
id: coordinated_early_accumulation
version: 1
networks: [solana, bsc]
window: 30m
eligibility:
  token_age_max: 72h
  liquidity_usd_min: 100000
  distinct_wallets_min: 3
  wallet_quality_percentile_min: 90
features:
  - distinct_high_quality_buyers
  - buyer_independence_score
  - net_buy_usd
  - liquidity_usd
  - holder_concentration_adjusted
  - deployer_history_score
  - verified_social_evidence
risk_gates:
  - transferable
  - no_critical_admin_or_honeypot_flag
alert_policy:
  minimum_confidence: 0.70
  cooldown: 6h
```

### 11.3 Dedupe and correlation

- Correlate events for the same asset and thesis into one evolving incident.
- Do not send repeated alerts for minor score changes.
- Send a material update when new evidence changes the thesis, risk category, or confidence beyond a configured delta.
- Explicitly notify reversals, reorgs, deleted/invalidated social evidence, and severe new risk.

---

## 12. Scoring, Risk, and Confidence

Never compress everything into one misleading number. Display at least three dimensions.

### 12.1 Opportunity score (0–100)

Measures how unusual and potentially interesting the observed setup is. Candidate feature groups:

- Quality and independence of participating wallets.
- Token age and timing.
- Net accumulation normalized by liquidity.
- Deployer/funder historical behavior.
- Liquidity growth and market participation.
- Cross-source confirmation.
- Novelty versus already-crowded price/volume movement.

### 12.2 Confidence score (0–100)

Measures confidence that the event and interpretation are supported:

- Source reliability and agreement.
- Data completeness and freshness.
- Finality/confirmation.
- Entity-link confidence.
- Sample size.
- Feature stability across providers.
- Model calibration for similar historical cases.

### 12.3 Risk dimensions

Show category scores plus an overall risk band:

- Contract/program and transfer risk.
- Admin/mint/freeze/upgrade authority risk.
- Liquidity depth, lock/burn status where verifiable, and removal risk.
- Holder concentration adjusted for known infrastructure.
- Wallet coordination/Sybil risk.
- Deployer/funder history.
- Market manipulation and wash-trading indicators.
- Social impersonation/provenance risk.
- Data-quality and unknown-protocol risk.

A critical risk may block a promotional opportunity alert while still generating a high-priority risk alert.

### 12.4 Score governance

- Version every rule, feature, threshold, model, and calibration map.
- Store the full feature vector used at decision time.
- Define missing-data behavior per feature; do not silently substitute zero.
- Calibrate confidence against observed frequency, with reliability plots and Brier/log-loss where applicable.
- Require minimum sample sizes and show uncertainty intervals.
- Compare each version to baselines before promotion.
- Changes pass offline evaluation and shadow mode before production.

---

## 13. Social Intelligence

### 13.1 Sources

Begin only with sources that have lawful, stable access and clear terms. Possible categories include official project sites/RSS, authenticated social APIs, public repositories, domain/DNS/website metadata where permitted, and manually curated official accounts.

### 13.2 Processing

- Preserve post ID, author/account ID, published time, first-observed time, source, and content hash.
- Resolve contract addresses exactly and validate checksum/network format.
- Track edits/deletions where the provider exposes them.
- Distinguish original posts from replies, reposts, screenshots, and quotations.
- Validate account identity using multiple signals; a display name or badge alone is insufficient.
- Detect duplicate campaigns and likely bot amplification as risk context, not proof.
- Store only what provider terms permit; prefer IDs and derived facts over full content when required.

### 13.3 Announcement verification

For a claim like “Person/organization X launched token Y,” require:

- A source attributable to an official or strongly authenticated account/property.
- An exact contract/mint address or unambiguous canonical link.
- On-chain confirmation on the claimed network.
- Time ordering that proves the evidence existed at alert time.
- Explicit uncertainty if any step is inferred.

Social buzz alone must never override severe on-chain risk.

---

## 14. AI Research Agent

### 14.1 Role

The AI layer turns structured evidence into readable research. It does not invent facts, make trades, or assign the core deterministic score.

### 14.2 Capabilities

- Generate an alert explanation: what happened, why it triggered, risks, unknowns, and what to verify.
- Answer questions about a token, wallet, entity, or prior signal using retrieved evidence.
- Compare the current event to historical analogs selected by deterministic similarity features.
- Build a timeline of on-chain and social facts.
- Produce a daily digest and post-mortem.

### 14.3 Grounding contract

Every generated claim must map to one or more evidence item IDs. Output structured JSON before rendering:

```json
{
  "summary": "...",
  "claims": [
    {"text": "...", "evidence_ids": ["..."], "certainty": "high|medium|low"}
  ],
  "risks": [],
  "unknowns": [],
  "conflicts": [],
  "not_financial_advice": true
}
```

Reject unsupported claims, prompt injection found in fetched content, instructions to reveal secrets, and requests to transact. Treat social posts, token metadata, websites, and repository text as untrusted data, never system instructions.

### 14.4 Evaluation

- Citation correctness and completeness.
- Unsupported-claim rate.
- Numerical fidelity.
- Conflict disclosure.
- Usefulness ratings.
- Latency and cost.
- Adversarial prompt-injection suite.

Use templates as a fallback when the AI provider is unavailable or budget-limited.

---

## 15. Backtesting and Evaluation

### 15.1 Point-in-time rules

- A run has a strict simulation clock.
- Entity labels, wallet ranks, metadata, social posts, prices, and holder data are visible only after their recorded first-observed time.
- Feature calculation uses versioned code and an as-of cutoff.
- Do not use current token survivorship lists to define the historical universe.
- Record ingestion latency assumptions and simulate realistic alert delay.

### 15.2 Execution simulation

- Use available pool liquidity and price at entry time.
- Configure entry delay, fees, gas, priority fees, slippage, and maximum participation.
- Treat missing quotes explicitly; do not interpolate through dead/illiquid assets without a declared method.
- Include rug-to-zero, delisted, abandoned, and failed tokens.
- Separate signal quality from position-sizing strategy.

### 15.3 Metrics

- Coverage and alert frequency.
- Precision/recall for clearly defined outcomes.
- Median and distribution of forward returns by horizon.
- Hit rate, expectancy, drawdown, volatility, and tail loss.
- Time-to-peak and maximum adverse excursion.
- Calibration by confidence bucket.
- Performance by chain, liquidity band, token age, signal version, and market regime.
- Baselines: random eligible asset, liquidity/volume momentum, and simple watched-wallet rules.
- Sample size and uncertainty intervals.

### 15.4 Anti-overfitting controls

- Train/validation/test time splits.
- Walk-forward evaluation.
- Locked holdout period.
- Multiple-testing awareness.
- No threshold promotion based solely on one exceptional token.
- Maintain a signal registry containing experiments, failures, and retirement reasons.

---

## 16. Dashboard UX

### 16.1 Navigation

- **Today:** ranked alerts and coverage health.
- **Live:** real-time canonical event stream.
- **Assets:** token search and research pages.
- **Wallets & Entities:** profiles, cohorts, and graph explorer.
- **Watchlists:** tracked assets/accounts and alert rules.
- **Research:** conversational evidence-based analysis and reports.
- **Backtests:** definitions, runs, comparisons, and diagnostics.
- **Paper Portfolio:** simulated positions and attribution.
- **System:** provider health, data freshness, budgets, and incidents.
- **Settings:** destinations, thresholds, privacy, and account settings.

### 16.2 Alert card

Must show:

- Plain-language title and event time.
- Network and asset address with copy action.
- Opportunity, confidence, and risk displayed separately.
- Three strongest supporting facts.
- Top risks and unknowns.
- Data freshness/finality.
- “Why this fired,” “View evidence,” “Watch,” “Paper trade,” and feedback actions.

### 16.3 Asset page

- Header with address, network, verified metadata, age, and risk banner.
- Price/liquidity/volume/holders with source and timestamp.
- Event and social timeline.
- Deployer/funder and wallet-cohort participation.
- Holder and liquidity concentration.
- Admin/contract controls.
- Similar historical cases.
- Evidence-backed AI brief.

### 16.4 Graph explorer

- Begin with a focused neighborhood, not an unreadable global graph.
- Edge legend and confidence filter.
- Click an edge to show why it exists.
- Time slider/as-of view.
- Expand one hop at a time with query-cost guardrails.
- Table alternative for accessibility.

### 16.5 UX requirements

- Responsive web app and installable PWA where practical.
- WCAG-oriented keyboard navigation, contrast, labels, and reduced motion.
- Empty, loading, stale, partial-data, provider-outage, and error states are designed explicitly.
- Never use color alone to communicate risk.
- All dates display user timezone plus UTC on detail.

---

## 17. API Surface

Version APIs and generate an OpenAPI contract.

Representative endpoints:

- `GET /v1/health` and `GET /v1/system/coverage`
- `GET /v1/events`
- `GET /v1/signals` and `GET /v1/signals/{id}`
- `GET /v1/assets/{network}/{address}`
- `GET /v1/wallets/{network}/{address}`
- `GET /v1/entities/{id}` and `GET /v1/graph/neighborhood`
- `POST /v1/watchlists`, `POST /v1/alert-rules`
- `POST /v1/backtests`, `GET /v1/backtests/{id}`
- `POST /v1/research/query`
- `POST /v1/paper/orders`, `GET /v1/paper/portfolio`
- `POST /v1/feedback`

Requirements:

- Authenticated by default; health endpoint exposes no sensitive detail.
- Cursor pagination for streams.
- Rate limits and query complexity limits.
- Stable error schema with request ID.
- Idempotency keys for mutations.
- Point-in-time `as_of` parameters where applicable.
- Evidence IDs and provenance included in research responses.

---

## 18. Security, Privacy, and Compliance

### 18.1 Threat model priorities

- Credential theft and secret leakage.
- Malicious webhooks and replay.
- Prompt injection from untrusted token/social/web content.
- Poisoned labels or provider data.
- Broken authorization between workspaces.
- Dependency/supply-chain compromise.
- Denial-of-service through expensive graph/backtest queries.
- Fraudulent token links and phishing in notifications.

### 18.2 Controls

- Managed authentication with MFA support.
- Workspace-scoped authorization on every resource.
- Secrets manager; encrypted in transit and at rest; rotation and revocation runbook.
- Provider keys are read-only/minimum scope. Never accept seed phrases or private keys.
- Webhook HMAC/signature verification, timestamp tolerance, nonce/replay protection.
- Strict input validation and canonical address/network handling.
- Parameterized queries/ORM safeguards.
- CSP, secure cookies, CSRF protection where applicable, and output encoding.
- SSRF-safe retrieval with allowlists, timeouts, size limits, and no access to internal metadata endpoints.
- Sandboxed/untrusted-content treatment for AI retrieval.
- Dependency scanning, secret scanning, signed builds where feasible, and locked dependencies.
- Immutable security/audit events with retention policy.
- Encrypted backups and tested restoration.

### 18.3 Product/legal posture

- Prominent research-only and risk disclosures.
- No guarantees or personalized fiduciary language.
- Track provider licensing/redistribution restrictions.
- Provide privacy notice, retention policy, deletion/export mechanism, and incident process before external users.
- Seek qualified counsel before commercialization, paid recommendations, copy trading, or execution features.

---

## 19. Observability and Operations

### 19.1 Service-level indicators

- Provider availability and error rate.
- Block/slot lag and event ingestion lag.
- Normalization failure and unknown-protocol rates.
- Queue depth and oldest-message age.
- Alert computation and delivery latency.
- Notification success rate.
- API latency/error rate.
- AI citation and fallback rates.
- Daily provider/AI/infrastructure spend.

### 19.2 Operational requirements

- Structured logs with correlation IDs.
- Distributed traces across ingest → signal → alert.
- Metrics dashboards by network/provider.
- Alert on sustained coverage gaps, not transient single errors.
- Runbooks for provider outage, cursor corruption, reorg, backlog, bad deployment, cost spike, secret exposure, and incorrect alert burst.
- Status page inside the app showing actual coverage.
- Safe replay and backfill tools with dry-run, bounded ranges, and idempotency.

---

## 20. Cost Controls

### 20.1 Design

- Tier A: real-time watched wallets/contracts.
- Tier B: high-priority discovery universe.
- Tier C: batch enrichment and historical backfill.
- Cache immutable transactions and stable metadata.
- Batch provider calls and deduplicate concurrent requests.
- Use cheap deterministic filters before enrichment and AI.
- Generate AI summaries only for displayed or alerted candidates.
- Cap graph depth, backtest ranges, and per-user query complexity.
- Store estimated unit cost for every provider operation.

### 20.2 Budget behavior

- Workspace daily/monthly budget and warning thresholds.
- Forecast spend based on trailing usage.
- Soft limit degrades optional enrichment and AI first.
- Hard limit pauses nonessential backfills; critical watched-wallet ingestion continues if configured.
- The UI explains any reduced coverage caused by budget limits.

### 20.3 Planning ranges

Do not encode vendor prices in product logic. Maintain a configurable cost model and verify current pricing before purchase. A local/demo environment should run with fixtures at near-zero variable cost. A private MVP should be designed to start with modest infrastructure and one managed data source, then scale based on measured request volume and signal value.

---

## 21. Testing Strategy

### 21.1 Test pyramid

- **Unit:** parsers, address normalization, scoring math, risk rules, dedupe, accounting, point-in-time filters.
- **Contract:** provider adapters against recorded sanitized responses and schemas.
- **Integration:** database, queue, object storage, migrations, replay, finality/reorg handling.
- **End-to-end:** event fixture → normalized event → candidate → alert → UI detail.
- **Backtest golden tests:** fixed dataset and expected features/metrics.
- **Security:** authorization matrix, webhook replay, injection, SSRF, secret scanning, dependency checks.
- **AI evaluation:** grounding, citations, adversarial instructions, numerical fidelity, fallback behavior.
- **Performance:** sustained ingest, event bursts, graph query limits, backtest concurrency.

### 21.2 Required fixtures

- Successful and failed Solana transactions.
- EVM swaps, token deployments, liquidity changes, proxy/admin changes, and reverts.
- Duplicate/out-of-order events.
- Reorg/finality reversal.
- Malformed metadata and extreme decimals.
- Honeypot/admin-risk examples.
- Coordinated wallet cluster and false-positive counterexample.
- Social impersonation, edited/deleted announcement, and prompt injection.
- Dead token and missing-price histories.

### 21.3 Release gates

- Migrations tested up and down where safe.
- No high-severity security findings.
- Critical paths covered by integration/E2E tests.
- Parser reconciliation meets an agreed threshold on sampled data.
- Backtest point-in-time audit passes.
- Alert shadow run reviewed before enabling notifications.
- Rollback procedure verified.

---

## 22. Delivery Rules for Codex

For every phase:

1. Read this file and existing architecture decisions.
2. Inspect the repository before changing it.
3. Write a short implementation plan and list assumptions.
4. Implement only the requested phase and its prerequisites.
5. Never invent API credentials, wallet labels, provider capabilities, or network details.
6. Use fixtures/mocks when credentials are unavailable.
7. Add migrations, tests, documentation, and sample configuration.
8. Run relevant checks and report exact results.
9. Update the phase checklist and architecture decision records.
10. Stop at the phase boundary and ask for review before starting the next phase.

Do not commit, push, deploy, purchase services, or create external accounts unless the user explicitly asks. Never add autonomous trading as an incidental feature.

---

# 23. Implementation Roadmap: Phases 0–15

## Phase dependency map

```text
0 Foundation
└── 1 Data contracts & provider abstractions
    ├── 2 Solana ingestion
    ├── 3 EVM/BSC ingestion
    └── 4 Robinhood ecosystem validation/adapter
         \        |        /
          5 Market/token enrichment
                    |
          6 Wallet intelligence
                    |
          7 Entity graph & labels
                    |
          8 Signal engine
                    |
          9 Risk, confidence & alerting
               /          \
      10 Social intel    11 Backtesting
               \          /
            12 AI research agent
                    |
            13 Dashboard & paper trading
                    |
            14 Security, ops & cost hardening
                    |
            15 Pilot, calibration & release
```

Phases 2–4 may be developed independently after Phase 1. Phase 4 must not block the validated Solana/BSC MVP if the target Robinhood environment lacks sufficient public infrastructure.

---

## Phase 0 — Foundation and Guardrails

### Objective

Create a reproducible repository, local development environment, architecture baseline, and safety boundaries.

### Deliverables

- Monorepo structure, README, contribution guide, and coding conventions.
- Local containers/services for PostgreSQL, Redis-compatible queue/cache, and object storage or lightweight substitutes.
- Web/API/worker health endpoints and starter apps.
- Configuration validation and `.env.example` with no secrets.
- CI for formatting, linting, type checks, tests, migration validation, secret/dependency scanning.
- Architecture decision records for language/stack, database, queue, graph approach, authentication, and raw-data retention.
- Threat-model skeleton and research-only product disclaimer.
- Seeded demo user/workspace and deterministic fixtures.

### Acceptance criteria

- A new developer can start the stack from documented steps.
- Health page reports each dependency without leaking configuration.
- CI passes from a clean checkout.
- No credentials, private keys, or autonomous execution code exists.
- Repository contains clear MVP/non-goal boundaries.

### Copy/paste Codex prompt

```text
Implement Phase 0 of ALPHAGRAPH_FULL_SPEC.md only. Inspect the repository first, then create the foundation, local environment, starter web/API/worker services, configuration validation, CI, architecture decisions, threat-model skeleton, and deterministic fixtures. Preserve the research/alerts/paper-trading-only boundary. Do not connect paid providers, deploy, commit, push, or begin Phase 1. Run all relevant checks, update the phase checklist, summarize files changed and test results, then stop for review.
```

---

## Phase 1 — Canonical Data Contracts and Provider Abstractions

### Objective

Define stable internal schemas and adapter interfaces before integrating vendors.

### Deliverables

- Versioned schemas for networks, raw observations, canonical events, assets, prices, labels, social evidence, and provider health.
- Provider interfaces described in Section 7.
- Capability negotiation and explicit unsupported-operation results.
- Idempotency key library, address normalization, units/decimal handling, and UTC time helpers.
- Raw journal, cursor, job, dead-letter, and cost-ledger migrations.
- Mock providers and contract-test harness.
- Schema compatibility and replay tests.

### Acceptance criteria

- The same normalized fixture can be produced from two mock provider formats.
- Duplicate delivery causes no duplicate canonical event.
- Missing capabilities and missing data are explicit.
- Raw-to-canonical provenance is traceable.
- Cost and latency metadata are recorded for mock calls.

### Copy/paste Codex prompt

```text
Implement Phase 1 of ALPHAGRAPH_FULL_SPEC.md only, assuming Phase 0 is complete. Build versioned canonical schemas, migrations, provider interfaces, capability reporting, idempotency/address/unit/time utilities, raw journal and dead-letter flow, cost instrumentation, mock adapters, and contract/replay tests. Do not integrate a live provider yet. Run checks, document decisions and schema fields, update the checklist, and stop for review.
```

---

## Phase 2 — Solana Ingestion

### Objective

Ingest and normalize Solana activity for watched addresses and supported launch/DEX protocols.

### Deliverables

- Solana provider adapter with stream/poll fallback.
- Slot/block, transaction, SPL transfer, token creation/mint, swap, pool, liquidity, and authority parsing for an explicitly documented initial protocol set.
- Confirmation/finality progression and rollback handling.
- Watch-address subscription manager and bounded historical backfill.
- Real transaction fixtures and explorer reconciliation report.
- Coverage/unknown-interaction metrics.

### Acceptance criteria

- Watched-wallet fixtures flow end-to-end into canonical events.
- Retry/replay creates no duplicates.
- Failed and partially parsed transactions are represented correctly.
- Finality updates and reversal fixtures pass.
- Initial protocol coverage and limitations are visible in documentation/UI health data.

### Copy/paste Codex prompt

```text
Implement Phase 2 of ALPHAGRAPH_FULL_SPEC.md only. Add a Solana adapter behind the Phase 1 interfaces, watched-address ingestion, bounded backfill, finality handling, and parsers for the documented initial SPL token and DEX/launch event set. Use fixtures when live credentials are absent. Add reconciliation, replay, duplicate, failure, and finality tests; expose coverage metrics; document unsupported interactions. Do not start scoring, alerts, or another network. Stop after reporting checks and limitations.
```

---

## Phase 3 — EVM and BNB Smart Chain Ingestion

### Objective

Build the reusable EVM connector and configure BSC as the first supported EVM network.

### Deliverables

- Chain-ID/genesis-aware EVM provider adapter.
- Blocks, transactions, receipts/logs, native/ERC-20 transfers, contract creation, swaps, pools, liquidity, proxy/admin/ownership events where detectable.
- ABI/event signature registry with parser versioning.
- Reorg and confirmation policy.
- BSC configuration, fixtures, reconciliation, and coverage metrics.

### Acceptance criteria

- Wrong-network provider configuration fails safely.
- Logs are decoded only when signatures/configuration support it; unknown logs remain accessible.
- Reverted transactions do not create false successful actions.
- Reorg test removes/reverses derived state correctly.
- Replay is idempotent and BSC sample reconciliation passes the agreed threshold.

### Copy/paste Codex prompt

```text
Implement Phase 3 of ALPHAGRAPH_FULL_SPEC.md only. Create a reusable EVM ingestion adapter and configure BNB Smart Chain, including chain identity validation, receipts/logs, ERC-20/native transfers, deployments, initial DEX pool/swap/liquidity parsers, supported admin/proxy events, finality/reorg handling, bounded backfill, fixtures, reconciliation, and metrics. Keep unknown interactions explicit. Do not add signal logic. Run checks and stop for review.
```

---

## Phase 4 — Robinhood Ecosystem Discovery and Adapter

### Objective

Verify the exact target environment and add support only from authoritative, technically confirmed network information.

### Deliverables

- A dated discovery note covering canonical name, status, chain ID/genesis, RPC/indexer options, explorers, finality, token standards, DEX/launch venues, and the distinction between chain activity and Robinhood platform listings.
- Go/no-go decision with evidence.
- If supported: configuration through the EVM abstraction, fixtures, health checks, parsers for verified protocols, and reconciliation.
- If unsupported/incomplete: disabled feature flag, clear UI status, and a monitoring/revisit checklist without fabricated functionality.

### Acceptance criteria

- No unverified endpoints, chain identifiers, labels, or listing claims are shipped.
- The app clearly distinguishes observed on-chain events from exchange/brokerage listing evidence.
- Adapter tests pass if implemented; otherwise the documented no-go state is graceful and does not block other networks.

### Copy/paste Codex prompt

```text
Implement Phase 4 of ALPHAGRAPH_FULL_SPEC.md only. First verify the current Robinhood chain/ecosystem details using authoritative sources and record a dated discovery note with citations. Do not assume a chain event implies a Robinhood platform listing. If public infrastructure is sufficiently verified, configure it through the existing EVM abstraction and add fixtures/tests/coverage; otherwise implement a clean disabled state and revisit checklist. Do not fabricate support or begin later phases. Report the go/no-go evidence and stop.
```

---

## Phase 5 — Market, Token, and Contract Enrichment

### Objective

Add point-in-time asset metadata, price, liquidity, holder, and contract/control context.

### Deliverables

- Market-data and token-security adapter implementations behind internal interfaces.
- Asset identity resolution and pair/pool selection rules.
- Price, liquidity, volume, holder, and metadata snapshots with freshness/provenance.
- Admin/mint/freeze/upgrade and transferability checks where technically meaningful.
- Adjusted holder-concentration calculations.
- Cache, quotas, stale/missing-data semantics, and provider-conflict handling.

### Acceptance criteria

- Every displayed metric includes source and as-of time.
- Missing/stale price is not represented as zero or current.
- Pool/exchange/burn classifications do not inflate holder concentration when supported by evidence.
- Conflicting providers create quality flags.
- Unit, decimal, illiquidity, and malformed-token tests pass.

### Copy/paste Codex prompt

```text
Implement Phase 5 of ALPHAGRAPH_FULL_SPEC.md only. Add provider-neutral market/token enrichment for prices, pools, liquidity, volume, holders, metadata, and applicable contract/admin/transferability checks. Implement point-in-time snapshots, provenance, freshness, cache/quota behavior, conflicts, adjusted concentration, and explicit missing-data semantics. Use mocks where credentials are absent. Add edge-case and integration tests, document limitations, and stop before wallet scoring.
```

---

## Phase 6 — Wallet Intelligence and Cohorts

### Objective

Build explainable wallet features and statistically responsible cohorts.

### Deliverables

- Wallet timelines, funding lineage, counterparties, token entries/exits, and performance accounting.
- Point-in-time features for early entry, hit rate, expectancy, drawdown, recency, liquidity realism, and sample size.
- Versioned cohort definitions such as watched, consistently early, deployer, liquidity provider, high-risk, or possible coordinated wallet.
- Shrinkage/minimum-sample ranking and uncertainty.
- Wallet profile API and basic internal UI.

### Acceptance criteria

- Transferred-in tokens are not automatically counted as purchases.
- Results disclose unknown cost basis and missing prices.
- One lucky trade cannot qualify a wallet as high quality under defaults.
- Features reproduce for the same as-of time and version.
- Tests cover partial history, transfers, dead tokens, and illiquid fills.

### Copy/paste Codex prompt

```text
Implement Phase 6 of ALPHAGRAPH_FULL_SPEC.md only. Build point-in-time wallet timelines, funding lineage, accounting, performance features, uncertainty-aware rankings, and versioned cohorts. Treat transfers, unknown basis, liquidity, fees, stale/missing prices, and small samples correctly. Add APIs and a minimal internal profile view for verification, plus deterministic tests. Do not build entity merging or production signals yet. Report assumptions and stop.
```

---

## Phase 7 — Entity Graph and Label Provenance

### Objective

Create a reversible, evidence-based graph of wallets, entities, assets, and public identities.

### Deliverables

- Graph node/edge and label/membership schemas.
- External-label import with licensing/provenance metadata.
- Manual labels and review workflow.
- Initial clustering heuristics with documented false positives.
- Confidence/conflict logic and versioned validity intervals.
- Bounded neighborhood query and graph/table explorer.

### Acceptance criteria

- Every inferred link explains method, evidence, version, and confidence.
- Conflicting labels remain visible.
- Cluster changes do not rewrite historical point-in-time membership.
- A user can retract/override a manual claim without deleting audit history.
- Graph queries enforce depth/result limits and workspace authorization.

### Copy/paste Codex prompt

```text
Implement Phase 7 of ALPHAGRAPH_FULL_SPEC.md only. Add the versioned entity graph, label provenance, manual review, conflict handling, reversible clustering heuristics, validity intervals, bounded neighborhood API, and a small graph/table verification UI. Document false-positive modes and provider licensing fields. Do not assert identities without evidence and do not start signal scoring. Add authorization and point-in-time tests, then stop.
```

---

## Phase 8 — Deterministic Signal Engine

### Objective

Turn canonical events and point-in-time features into reproducible signal candidates.

### Deliverables

- Versioned declarative signal definitions.
- Feature assembler with as-of cutoff and missing-data policies.
- Initial signal families from Section 5.6, excluding social-dependent rules until Phase 10.
- Candidate/evidence-bundle persistence.
- Correlation, dedupe, cooldown, replay, and shadow modes.
- Signal registry and diagnostics.

### Acceptance criteria

- Given identical data, cutoff, and versions, candidates are identical.
- Future labels/prices cannot leak into features.
- Replays do not duplicate candidates.
- Each candidate explains every rule pass/fail and stores its feature vector.
- Shadow mode produces no external notification.

### Copy/paste Codex prompt

```text
Implement Phase 8 of ALPHAGRAPH_FULL_SPEC.md only. Build a deterministic, versioned signal engine with declarative definitions, point-in-time feature assembly, evidence bundles, missing-data policies, correlation/deduplication/cooldowns, replay, shadow mode, diagnostics, and the initial on-chain signal families. Do not use an LLM for detection or scoring and do not send real notifications. Add leakage and determinism tests, then stop.
```

---

## Phase 9 — Opportunity, Confidence, Risk, and Notifications

### Objective

Rank candidates transparently, gate severe risks, and deliver controlled alerts.

### Deliverables

- Separate opportunity, confidence, and risk-category calculations.
- Feature contribution explanations and score versioning.
- Critical risk gates and material-update logic.
- Alert policy engine with per-user thresholds, quiet hours, rate limits, and test mode.
- Notification adapters for selected initial channels, using mocks unless configured.
- Delivery receipts, retries, failure handling, and deep links.

### Acceptance criteria

- A high opportunity score cannot hide a critical risk.
- Missing evidence reduces confidence according to documented rules.
- Duplicate events do not spam users.
- Test notifications are clearly labeled.
- No notification includes an unverified clickable contract/domain link without warning/sanitization.
- All score versions and delivery outcomes are auditable.

### Copy/paste Codex prompt

```text
Implement Phase 9 of ALPHAGRAPH_FULL_SPEC.md only. Add separate opportunity, confidence, and category-level risk scores with explanations and versioning; critical risk gates; alert policy/deduplication/material updates; and safe notification adapters with test mode, retries, receipts, and deep links. Use no autonomous actions or trading. Add scoring, missing-data, spam-control, phishing-link, and delivery tests. Run in shadow/test mode and stop for review.
```

---

## Phase 10 — Social Intelligence and Announcement Verification

### Objective

Add lawful public social evidence and cross-source verification without treating hype as truth.

### Deliverables

- Social provider abstraction implementation for an approved initial source set.
- Curated official-account registry and provenance.
- Post/account observations with published and first-observed timestamps.
- Contract-address extraction and network validation.
- Announcement-verification workflow, edit/delete handling, impersonation and duplication indicators.
- Social/on-chain convergence signals and UI evidence.

### Acceptance criteria

- Historical simulations cannot see a post before first observation.
- Exact address and network checks prevent ticker/name-only matching.
- Deleted/edited evidence produces a material update.
- Untrusted content cannot change system/AI instructions.
- Provider retention and redistribution restrictions are enforced/documented.

### Copy/paste Codex prompt

```text
Implement Phase 10 of ALPHAGRAPH_FULL_SPEC.md only. Integrate an approved public social source behind the abstraction, build official-account provenance, point-in-time post observations, exact contract/network extraction, announcement verification, edit/delete and impersonation handling, and cross-source convergence signals. Treat all fetched content as untrusted and obey provider terms. Add prompt-injection and time-ordering tests. Do not scrape prohibited sources or begin the AI agent. Stop for review.
```

---

## Phase 11 — Backtesting and Signal Evaluation

### Objective

Measure whether signals historically added value using honest point-in-time simulation.

### Deliverables

- Backtest definition/run engine and isolated worker queue.
- As-of feature reconstruction.
- Execution simulation with fees, delay, slippage, liquidity, missing prices, and dead assets.
- Baselines, metrics, confidence intervals, calibration plots, and segment analysis.
- Walk-forward and locked-holdout support.
- Results UI and downloadable artifact.

### Acceptance criteria

- Automated leakage audit passes.
- Failed/dead/illiquid tokens remain in the universe.
- Changing latency/slippage materially affects fills as expected.
- Baselines and sample sizes display beside strategy results.
- Runs are reproducible from definition, dataset/version manifest, and code version.

### Copy/paste Codex prompt

```text
Implement Phase 11 of ALPHAGRAPH_FULL_SPEC.md only. Build a reproducible point-in-time backtesting service, as-of feature reconstruction, realistic execution assumptions, dead/missing-price handling, baselines, uncertainty, calibration, walk-forward/holdout support, artifacts, and a results view. Add explicit look-ahead and survivorship-bias tests. Do not tune on the locked holdout or imply profitability. Stop after documenting results and limitations.
```

---

## Phase 12 — Grounded AI Research Agent

### Objective

Explain signals and answer research questions using only permissioned evidence bundles.

### Deliverables

- Evidence retrieval service with workspace and point-in-time authorization.
- Structured grounded answer/summary schema.
- Citations, certainty, conflicts, risks, and unknowns.
- Prompt-injection isolation and content sanitization.
- Template fallback, caching, token/cost limits, and model/provider abstraction.
- Evaluation dataset and quality dashboard.

### Acceptance criteria

- Every factual claim cites evidence or the response refuses/qualifies it.
- Numerical values match cited structured records.
- Adversarial token metadata/social content cannot redirect the agent or expose secrets.
- Provider outage/budget exhaustion returns a useful deterministic summary.
- Agent cannot construct, sign, or submit trades.

### Copy/paste Codex prompt

```text
Implement Phase 12 of ALPHAGRAPH_FULL_SPEC.md only. Create the grounded research layer over authorized evidence bundles, with structured claims/citations/certainty/conflicts/risks/unknowns, point-in-time retrieval, prompt-injection defenses, provider abstraction, caching, cost limits, deterministic fallback templates, and an evaluation suite. The agent must not perform or facilitate autonomous execution. Run grounding, numeric-fidelity, authorization, and adversarial tests, then stop.
```

---

## Phase 13 — Complete Dashboard and Paper Trading

### Objective

Deliver the coherent non-technical user experience and safe simulated portfolio workflow.

### Deliverables

- Today, Live, Assets, Wallets/Entities, Watchlists, Research, Backtests, Paper Portfolio, System, and Settings screens.
- Responsive accessible alert cards, timelines, charts, graph/table explorer, search, filters, and saved views.
- Paper accounts/orders/fills/positions with configurable delay, fees, and slippage.
- Feedback capture and onboarding.
- Explicit stale/partial/outage states and research-only disclaimers.

### Acceptance criteria

- A non-technical tester can configure a watchlist, understand an alert, inspect evidence, and create/close a paper position without help.
- Paper fills are visibly simulated and never call a wallet/exchange execution API.
- Keyboard/accessibility checks pass for critical workflows.
- Mobile layout supports receiving and investigating an alert.
- Empty/error/stale/partial states are tested.

### Copy/paste Codex prompt

```text
Implement Phase 13 of ALPHAGRAPH_FULL_SPEC.md only. Complete the accessible responsive dashboard and onboarding across all specified screens, then add clearly simulated paper trading with configurable fees, slippage, and delay. Preserve evidence links, score separation, freshness, outage states, and research-only disclosures. Do not add wallet connection, key storage, transaction construction, or live execution. Run component/E2E/accessibility tests and stop for user acceptance review.
```

---

## Phase 14 — Security, Reliability, Observability, and Cost Hardening

### Objective

Prepare the private pilot for sustained operation and controlled spend.

### Deliverables

- Completed threat model and authorization review.
- Secrets, webhook, SSRF, CSP, rate-limit, audit, backup/restore, retention/deletion, and dependency controls.
- End-to-end tracing, dashboards, service-level indicators, provider coverage/status page, and alerts.
- Runbooks and incident drills.
- Budget policies, cost ledger, forecasts, caps, and graceful degradation.
- Load, burst, queue-recovery, disaster-recovery, and security tests.

### Acceptance criteria

- Cross-workspace access tests pass.
- Backup restore and bounded event replay are demonstrated.
- Provider outage and cost-cap drills degrade visibly and recover without duplicates.
- No high-severity security findings remain.
- Sustained load meets documented latency/lag targets or limitations are accepted explicitly.

### Copy/paste Codex prompt

```text
Implement Phase 14 of ALPHAGRAPH_FULL_SPEC.md only. Harden authorization, secrets, webhooks, SSRF and untrusted content, browser controls, audits, retention/deletion, backups, dependencies, observability, service objectives, runbooks, provider health, cost ledger/budgets, graceful degradation, and recovery. Run security, load, outage, replay, and restore drills; record evidence and unresolved risks. Do not deploy or begin the pilot without explicit approval. Stop for review.
```

---

## Phase 15 — Private Pilot, Calibration, and Release Gate

### Objective

Operate in shadow mode, calibrate with real outcomes, and decide whether the private MVP is trustworthy enough for alert use.

### Deliverables

- Time-bounded shadow pilot across approved networks and wallet universe.
- Daily coverage/cost/quality review and incident log.
- Human review sample for entity links, risks, social claims, and AI citations.
- Signal outcome report versus baselines, including false positives and missed cases.
- Threshold calibration using validation data; locked holdout remains untouched until final evaluation.
- User acceptance test and operational readiness checklist.
- Go/no-go decision for private notifications; roadmap for post-MVP improvements.

### Acceptance criteria

- No unresolved critical security, data-integrity, or alert-spam issue.
- Provider coverage and measured latency are visible and acceptable for the declared scope.
- Confidence calibration and signal results include sample sizes and uncertainty.
- The user can trace sampled alerts to original evidence.
- Notifications are enabled only by explicit user approval after shadow review.
- Live/autonomous trading remains out of scope.

### Copy/paste Codex prompt

```text
Execute Phase 15 of ALPHAGRAPH_FULL_SPEC.md only. Run a bounded private shadow pilot using the approved providers/networks, monitor coverage, latency, cost, data quality, signal outcomes, risk flags, entity links, and AI citations; collect human review and user acceptance evidence; calibrate only on allowed validation data; then produce an honest go/no-go report with limitations and a post-MVP roadmap. Do not enable notifications, deploy changes, spend beyond configured budgets, or add live trading without explicit user approval. Stop at the release gate.
```

---

## 24. Phase Status Checklist

- [ ] Phase 0 — Foundation and guardrails
- [ ] Phase 1 — Data contracts and provider abstractions
- [ ] Phase 2 — Solana ingestion
- [ ] Phase 3 — EVM/BSC ingestion
- [ ] Phase 4 — Robinhood ecosystem validation/adapter
- [ ] Phase 5 — Market/token enrichment
- [ ] Phase 6 — Wallet intelligence
- [ ] Phase 7 — Entity graph and labels
- [ ] Phase 8 — Signal engine
- [ ] Phase 9 — Risk/confidence/scoring and notifications
- [ ] Phase 10 — Social intelligence
- [ ] Phase 11 — Backtesting
- [ ] Phase 12 — AI research agent
- [ ] Phase 13 — Dashboard and paper trading
- [ ] Phase 14 — Security/operations/cost hardening
- [ ] Phase 15 — Private pilot and release gate

---

## 25. Product Success Measures

### Reliability and coverage

- Measured ingest/alert latency by network and provider.
- Percentage of watched activity successfully parsed.
- Provider gap duration and recovery time.
- Duplicate and false-alert rate.

### Research quality

- Percentage of alert claims with valid evidence.
- Entity-link precision from reviewed samples.
- User-rated usefulness.
- Risk flags caught before simulated entry.
- Confidence calibration.

### Signal value

- Performance against declared baselines with uncertainty.
- Stability across time, networks, liquidity bands, and market regimes.
- Sample size and alert frequency.
- False-positive and severe-tail-loss analysis.

### Cost

- Cost per watched wallet, normalized event, enriched candidate, alert, and AI report.
- Cache hit rate and optional-enrichment deferral.
- Budget forecast accuracy.

Success is not “finding every moonshot.” Success is a reliable, auditable system that improves the speed and quality of research while making uncertainty and risk unmistakable.

---

## 26. Deferred Post-MVP Ideas

Consider only after the Phase 15 release gate:

- Additional networks and protocols.
- Team collaboration and analyst review queues.
- User-defined visual signal builder.
- More sophisticated graph embeddings/anomaly detection after labeled evaluation data exists.
- Mobile-native app.
- Commercial multi-tenant plans and compliance work.
- Broker/wallet integrations limited to read-only portfolio import.

Any transaction preparation, signing, automated execution, copy trading, or custody requires a separate product specification, threat model, legal/compliance review, permission model, and explicit user authorization. It is not an extension of this MVP by default.

---

## 27. First Instruction to Codex

Paste the following after adding this file to a project:

```text
Read ALPHAGRAPH_FULL_SPEC.md in full. Treat it as the source of truth. Inspect the current repository and report what already exists, what conflicts with the specification, and what Phase 0 requires. Then implement Phase 0 only, including its tests and documentation. Use mock/fixture data; do not purchase or connect paid services, create external accounts, commit, push, deploy, or begin Phase 1. Keep autonomous trading, private keys, transaction signing, and custody entirely out of scope. At completion, provide a concise change summary, exact verification results, remaining risks/assumptions, and stop for my review.
```

---

**End of specification.**
