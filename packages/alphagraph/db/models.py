"""SQLAlchemy models.

Conventions:
  * UUID/string ids, UTC timestamps, append-only history for time-sensitive facts.
  * Anything derived from a versioned calculation stores that version, so a
    later change to the maths does not silently rewrite history.
  * `first_observed_at` is stored separately from event time everywhere it
    matters, because point-in-time queries depend on it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from alphagraph.core.timeutil import utcnow
from alphagraph.db.types import UTCDateTime


class Base(DeclarativeBase):
    type_annotation_map = {
        dict: JSON,
        list: JSON,
        Decimal: Numeric(38, 18),
    }


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# --------------------------------------------------------------------- chain


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # network:address
    network: Mapped[str] = mapped_column(String(24), index=True)
    address: Mapped[str] = mapped_column(String(80), index=True)
    symbol: Mapped[str | None] = mapped_column(String(40))
    decimals: Mapped[int] = mapped_column(Integer, default=9)
    first_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (UniqueConstraint("network", "address", name="uq_asset_network_address"),)


class Event(Base):
    """Canonical events. `event_id` is the idempotency key — replay-safe."""

    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    network: Mapped[str] = mapped_column(String(24), index=True)

    block_height: Mapped[int] = mapped_column(Integer)
    transaction_id: Mapped[str] = mapped_column(String(120), index=True)
    event_index: Mapped[int] = mapped_column(Integer, default=0)

    chain_time: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finality: Mapped[str] = mapped_column(String(16), default="confirmed")

    actor: Mapped[str] = mapped_column(String(80), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(80), index=True)
    counterparty: Mapped[str | None] = mapped_column(String(80))

    side: Mapped[str | None] = mapped_column(String(8))
    usd_value: Mapped[Decimal | None] = mapped_column(Numeric(38, 8))
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    leverage: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    limit_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    filled: Mapped[bool] = mapped_column(Boolean, default=True)

    provider: Mapped[str] = mapped_column(String(40), default="unknown")
    parser_version: Mapped[str] = mapped_column(String(16), default="v1")
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_events_actor_observed", "actor", "observed_at"),
        Index("ix_events_asset_observed", "asset_id", "observed_at"),
    )


class OutcomeRow(Base):
    __tablename__ = "outcomes"

    outcome_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    outcome_class: Mapped[str] = mapped_column(String(24), index=True)
    definition_version: Mapped[str] = mapped_column(String(16))
    asset_id: Mapped[str] = mapped_column(String(80), index=True)

    event_time: Mapped[datetime] = mapped_column(UTCDateTime)
    first_observed_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    t0: Mapped[datetime] = mapped_column(UTCDateTime, index=True)

    magnitude: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    venue: Mapped[str | None] = mapped_column(String(40))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


# -------------------------------------------------------------------- wallets


class WalletMetrics(Base):
    """Point-in-time wallet metrics. Append-only: one row per (wallet, as_of, version)."""

    __tablename__ = "wallet_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String(80), index=True)
    network: Mapped[str] = mapped_column(String(24))
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    calc_version: Mapped[str] = mapped_column(String(16))

    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    archetype: Mapped[str] = mapped_column(String(32), index=True)
    archetype_confidence: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    excluded_reason: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("wallet", "as_of", "calc_version", name="uq_wallet_metrics_pit"),
    )


class Candidate(Base, TimestampMixin):
    """A wallet surfaced by discovery, moving through the promotion lifecycle."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    network: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="surfaced", index=True)

    surfaced_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    shadow_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    promoted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    archetype: Mapped[str] = mapped_column(String(32), default="unclassified")
    hit_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    shrunk_hit_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    base_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    independent_events: Mapped[int] = mapped_column(Integer, default=0)
    edge_vs_base: Mapped[float] = mapped_column(Numeric(8, 4), default=0)

    discovery_provenance: Mapped[list] = mapped_column(JSON, default=list)
    rejection_reason: Mapped[str | None] = mapped_column(String(120))


# ------------------------------------------------------------------- entities


class Entity(Base, TimestampMixin):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(String(32), default="wallet_cluster")
    status: Mapped[str] = mapped_column(String(24), default="tracked")
    archetype: Mapped[str] = mapped_column(String(32), default="unclassified")
    summary: Mapped[str] = mapped_column(Text, default="")

    members: Mapped[list[EntityMember]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    notes: Mapped[list[EntityNote]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class EntityMember(Base, TimestampMixin):
    """Wallet↔entity membership. Versioned and time-bounded, never destructive.

    `valid_to` closes a membership rather than deleting the row, so a historical
    point-in-time query still sees the cluster as it was believed to be then.
    """

    __tablename__ = "entity_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    wallet: Mapped[str] = mapped_column(String(80), index=True)
    method: Mapped[str] = mapped_column(String(48))
    confidence: Mapped[float] = mapped_column(Numeric(6, 4), default=0.5)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime)

    entity: Mapped[Entity] = relationship(back_populates="members")


class EntityNote(Base, TimestampMixin):
    """Human research notes. The dossier is a research artifact, not a view."""

    __tablename__ = "entity_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    author: Mapped[str] = mapped_column(String(60), default="operator")
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24), default="note")  # note|hypothesis|question

    entity: Mapped[Entity] = relationship(back_populates="notes")


class GraphEdge(Base, TimestampMixin):
    """Sequence-aware relationship between wallets (spec §6.3).

    `median_lag_seconds` and `ordering_consistency` are what turn "these wallets
    are related" into "B reliably moves ~18h after A" — the difference between
    trivia and a tradeable confirmation.
    """

    __tablename__ = "graph_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str] = mapped_column(String(80), index=True)
    edge_type: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(48))
    confidence: Mapped[float] = mapped_column(Numeric(6, 4), default=0.5)

    observations: Mapped[int] = mapped_column(Integer, default=0)
    median_lag_seconds: Mapped[float | None] = mapped_column(Numeric(14, 2))
    ordering_consistency: Mapped[float | None] = mapped_column(Numeric(6, 4))

    false_positive_modes: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[str] = mapped_column(String(16), default="v1")

    __table_args__ = (
        UniqueConstraint("source", "target", "edge_type", name="uq_edge"),
        Index("ix_edge_source_type", "source", "edge_type"),
    )


class Playbook(Base, TimestampMixin):
    __tablename__ = "playbooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    stages: Mapped[list] = mapped_column(JSON, default=list)
    supporting_sequences: Mapped[int] = mapped_column(Integer, default=0)
    aborted_sequences: Mapped[int] = mapped_column(Integer, default=0)
    median_stage_lags: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_by_operator: Mapped[bool] = mapped_column(Boolean, default=False)


class PlaybookState(Base):
    __tablename__ = "playbook_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(60), index=True)
    asset_id: Mapped[str] = mapped_column(String(80), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    stage_index: Mapped[int] = mapped_column(Integer, default=0)
    entered_at: Mapped[datetime] = mapped_column(UTCDateTime)
    confidence: Mapped[float] = mapped_column(Numeric(6, 4), default=0)


# -------------------------------------------------------------------- signals


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    family: Mapped[str] = mapped_column(String(48), index=True)
    definition_version: Mapped[str] = mapped_column(String(16), default="v1")

    triggered_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    evidence_cutoff: Mapped[datetime] = mapped_column(UTCDateTime)

    asset_id: Mapped[str | None] = mapped_column(String(80), index=True)
    wallet: Mapped[str | None] = mapped_column(String(80), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(60))

    title: Mapped[str] = mapped_column(String(240))
    significance: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    confidence: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    risk_band: Mapped[str] = mapped_column(String(16), default="unknown")

    supporting_facts: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    unknowns: Mapped[list] = mapped_column(JSON, default=list)
    base_rate_context: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_vector: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)

    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    shadow_mode: Mapped[bool] = mapped_column(Boolean, default=True)

    graded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    grade: Mapped[str | None] = mapped_column(String(16))  # hit|miss|pending|expired
    grade_detail: Mapped[dict] = mapped_column(JSON, default=dict)


# ------------------------------------------------------------ backtest, paper


class BacktestRun(Base, TimestampMixin):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime)
    code_version: Mapped[str] = mapped_column(String(40), default="dev")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    baselines: Mapped[dict] = mapped_column(JSON, default=dict)
    leakage_audit_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    leakage_audit_detail: Mapped[dict] = mapped_column(JSON, default=dict)


class PaperPosition(Base, TimestampMixin):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(80), index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    entry_price_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    exit_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    size_usd: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    fees_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal(0))
    slippage_bps: Mapped[int] = mapped_column(Integer, default=0)
    entry_delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)  # always True
    notes: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- ops, govern


class Proposal(Base, TimestampMixin):
    """A tuning change the nightly loop wants to make. Never auto-applied."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(80))
    current_value: Mapped[str] = mapped_column(String(80))
    proposed_value: Mapped[str] = mapped_column(String(80))
    justification: Mapped[str] = mapped_column(Text)
    backtest_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    variants_tested: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decided_by: Mapped[str | None] = mapped_column(String(60))


class Digest(Base, TimestampMixin):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    body: Mapped[dict] = mapped_column(JSON, default=dict)


class WatchedAsset(Base, TimestampMixin):
    """An asset the daily collector keeps observing, and why.

    The watchlist is deliberately *sticky*. An asset joins on the day it is
    first seen and, once it has cleared the traction floor even once, it is
    never retired — because the days that matter most are the ones after it
    stops being interesting to a volume-sorted listing.

    Dropping an asset when it goes quiet would rebuild the survivorship problem
    inside our own archive: we would hold a record of every token on the days it
    was big, and no record at all of any collapse. `rug_or_collapse` is the
    outcome class that separates a wallet early into things that last from one
    early into things that die, so losing it costs more than the storage.
    """

    __tablename__ = "watched_assets"

    asset_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    #: How the market provider addresses this asset's price series.
    series_key: Mapped[str] = mapped_column(String(80))
    symbol: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(40), default="")

    #: When *we* first recorded it. Not when it was created on chain — that is
    #: on `Asset.first_seen_at` and may be unknown.
    first_watched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    #: The first day it cleared the traction floor. None means never; such an
    #: asset is a retirement candidate, a qualified one never is.
    qualified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    last_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    peak_volume_24h_usd: Mapped[Decimal] = mapped_column(Numeric(38, 6), default=Decimal(0))
    #: Consecutive collection days below the "anyone could trade this" line.
    quiet_days: Mapped[int] = mapped_column(Integer, default=0)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class AssetObservation(Base):
    """One day's market state for one asset, stamped with when we saw it.

    This is the archive no provider sells: a point-in-time record built forward
    from the day collection starts. `observed_at` is our clock, and every
    analytic filters on it, so a value written today can never leak into a
    backtest of last month.
    """

    __tablename__ = "asset_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(80), index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    #: Collection day as YYYY-MM-DD. Carries the idempotency: re-running the
    #: collector on the same day overwrites rather than duplicating.
    observed_on: Mapped[str] = mapped_column(String(10), index=True)

    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    liquidity_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 6))
    volume_24h_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 6))
    market_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 6))
    #: True when the asset was in today's listing; False when we only kept
    #: observing it because it is on the watchlist. The difference is exactly
    #: what a survivor-selected universe would have thrown away.
    in_universe: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(40), default="")

    __table_args__ = (
        UniqueConstraint("asset_id", "observed_on", name="uq_observation_asset_day"),
        Index("ix_observation_asset_time", "asset_id", "observed_at"),
    )


class CostLedger(Base):
    __tablename__ = "cost_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(60))
    units: Mapped[int] = mapped_column(Integer, default=1)
    estimated_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)


class ProviderHealthSample(Base):
    __tablename__ = "provider_health_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    lag_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    detail: Mapped[str] = mapped_column(String(240), default="")


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    stage: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, default=0)
