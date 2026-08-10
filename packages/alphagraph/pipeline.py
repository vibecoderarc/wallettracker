"""End-to-end orchestration: ingest → outcomes → discovery → signals.

One place that knows the order of operations, so the CLI, the API, the nightly
loop, and the tests all drive the system identically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef
from alphagraph.db.models import Asset
from alphagraph.discovery.engine import DiscoveryEngine
from alphagraph.ingestion.pipeline import Ingestor
from alphagraph.outcomes.registry import OutcomeRegistry
from alphagraph.providers.fixture import (
    FixtureChainProvider,
    FixtureListingProvider,
    FixtureMarketDataProvider,
)
from alphagraph.providers.world import WORLD_END, WORLD_START, World, build_world


@dataclass
class BootstrapResult:
    ingest: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)
    listings: int = 0
    discovery: dict[str, int] = field(default_factory=dict)
    passed_candidates: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "ingest": self.ingest,
            "outcomes": self.outcomes,
            "listings": self.listings,
            "discovery": self.discovery,
            "passed_candidates": len(self.passed_candidates),
        }


def assets_from_db(session: Session) -> list[AssetRef]:
    rows = session.execute(select(Asset)).scalars()
    return [
        AssetRef(
            network=Network(row.network),
            address=row.address,
            symbol=row.symbol,
            decimals=row.decimals,
        )
        for row in rows
    ]


async def bootstrap(
    session: Session,
    world: World | None = None,
    as_of: datetime | None = None,
) -> BootstrapResult:
    """Load the fixture world and run the full pipeline once."""
    world = world or build_world()
    as_of = as_of or WORLD_END
    result = BootstrapResult()

    chain = FixtureChainProvider(world)
    market = FixtureMarketDataProvider(world)
    listings = FixtureListingProvider(world)

    ingestor = Ingestor(session)
    report = await ingestor.backfill(chain, WORLD_START, as_of)
    result.ingest = report.as_dict()
    session.flush()

    registry = OutcomeRegistry(session)
    result.outcomes = await registry.detect_all(market, assets_from_db(session))

    # Listings cannot be inferred from chain data — they arrive from a venue
    # feed with its own provenance (spec §3.3).
    written = 0
    for outcome in await listings.listings(WORLD_START):
        if registry.upsert(outcome):
            written += 1
    result.listings = written
    session.flush()

    engine = DiscoveryEngine(session)
    scores = engine.run_sweep(as_of)
    result.discovery = engine.persist(scores, as_of)
    result.passed_candidates = [s.wallet for s in scores if s.passed]
    return result
