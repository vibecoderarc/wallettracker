"""Ingestion pipeline: provider events → canonical rows.

Guarantees:
  * Idempotent. Re-running any range writes no duplicates, because `event_id`
    is derived from chain coordinates only.
  * Failures are isolated. One malformed event goes to the dead-letter table;
    it does not abort the batch.
  * Reorgs reverse derived state rather than deleting history.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.events import CanonicalEvent, Finality
from alphagraph.db.models import Asset, DeadLetter, Event, ProviderHealthSample
from alphagraph.providers.base import ChainProvider


@dataclass
class IngestReport:
    seen: int = 0
    written: int = 0
    duplicates: int = 0
    dead_lettered: int = 0
    reverted: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "seen": self.seen,
            "written": self.written,
            "duplicates": self.duplicates,
            "dead_lettered": self.dead_lettered,
            "reverted": self.reverted,
        }


def asset_id(network: str, address: str) -> str:
    return f"{network}:{address}"


class Ingestor:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _ensure_asset(self, event: CanonicalEvent) -> str | None:
        if event.asset is None:
            return None
        aid = asset_id(event.asset.network.value, event.asset.address)
        existing = self.session.get(Asset, aid)
        if existing is None:
            self.session.add(
                Asset(
                    id=aid,
                    network=event.asset.network.value,
                    address=event.asset.address,
                    symbol=event.asset.symbol,
                    decimals=event.asset.decimals,
                    first_seen_at=event.chain_time,
                )
            )
        elif existing.first_seen_at is None or event.chain_time < existing.first_seen_at:
            existing.first_seen_at = event.chain_time
        return aid

    def write(self, event: CanonicalEvent, report: IngestReport) -> None:
        report.seen += 1
        try:
            existing = self.session.get(Event, event.event_id)
            if existing is not None:
                report.duplicates += 1
                # Finality can advance on a known event; that is an update, not
                # a duplicate write. A reversal must be recorded, not dropped.
                if existing.finality != event.finality.value:
                    existing.finality = event.finality.value
                    if event.finality is Finality.REVERTED:
                        report.reverted += 1
                return

            aid = self._ensure_asset(event)
            self.session.add(
                Event(
                    event_id=event.event_id,
                    schema_version=event.schema_version,
                    event_type=event.event_type.value,
                    network=event.network.value,
                    block_height=event.block_height,
                    transaction_id=event.transaction_id,
                    event_index=event.event_index,
                    chain_time=event.chain_time,
                    observed_at=event.observed_at,
                    finality=event.finality.value,
                    actor=event.actor,
                    asset_id=aid,
                    counterparty=event.counterparty,
                    side=event.side.value if event.side else None,
                    usd_value=event.usd_value,
                    price_usd=event.price_usd,
                    leverage=event.leverage,
                    limit_price_usd=event.limit_price_usd,
                    filled=event.filled,
                    provider=event.provider,
                    parser_version=event.parser_version,
                    quality_flags=[f.value for f in event.quality_flags],
                    extra=event.extra,
                )
            )
            report.written += 1
        except Exception as exc:
            report.dead_lettered += 1
            report.errors.append(f"{event.event_id}: {exc}")
            self.session.add(
                DeadLetter(
                    stage="ingest",
                    payload={"event_id": event.event_id, "type": event.event_type.value},
                    error=str(exc),
                )
            )

    async def consume(self, stream: AsyncIterator[CanonicalEvent]) -> IngestReport:
        report = IngestReport()
        async for event in stream:
            self.write(event, report)
        self.session.flush()
        return report

    async def backfill(
        self, provider: ChainProvider, start: datetime, end: datetime
    ) -> IngestReport:
        health = await provider.health()
        self.session.add(
            ProviderHealthSample(
                provider=health.provider,
                healthy=health.healthy,
                lag_seconds=health.lag_seconds,
                detail=health.detail,
            )
        )
        if not health.healthy:
            # Degrade visibly. Returning an empty report that looks like a
            # successful no-op is how coverage gaps become invisible.
            report = IngestReport()
            report.errors.append(f"provider {health.provider} unhealthy: {health.detail}")
            return report
        return await self.consume(provider.backfill(start, end))


def observed_events(
    session: Session,
    *,
    as_of: datetime | None = None,
    actor: str | None = None,
    asset: str | None = None,
    since: datetime | None = None,
) -> list[Event]:
    """Query events by OBSERVED time.

    Every read path used by analytics goes through here. Filtering on
    `observed_at` rather than `chain_time` is what makes point-in-time
    correctness the default instead of something each caller has to remember.
    """
    stmt = select(Event).where(Event.finality != Finality.REVERTED.value)
    if as_of is not None:
        stmt = stmt.where(Event.observed_at <= as_of)
    if since is not None:
        stmt = stmt.where(Event.observed_at >= since)
    if actor is not None:
        stmt = stmt.where(Event.actor == actor)
    if asset is not None:
        stmt = stmt.where(Event.asset_id == asset)
    return list(session.execute(stmt.order_by(Event.observed_at)).scalars())
