"""Populate a fresh database so the dashboard has something to show.

Shared by the CLI (`alphagraph demo`) and by the API's automatic first-boot
seed, so both produce the same state and there is only one definition of "a
populated system".

Deliberately not a migration or a fixture-loader: it runs the real pipeline —
ingest, detect outcomes, sweep for candidates, build the graph, fire signals —
because a dashboard filled by a shortcut would not prove the pipeline works.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphagraph.db.models import Candidate, Event
from alphagraph.discovery.engine import CandidateStatus, DiscoveryEngine
from alphagraph.entities.graph import DossierService, GraphBuilder
from alphagraph.pipeline import bootstrap
from alphagraph.playbooks.engine import PlaybookEngine
from alphagraph.providers.fixture import RecordingNotificationProvider
from alphagraph.providers.world import WORLD_END, WORLD_START, World, build_world
from alphagraph.signals.policy import AlertDispatcher, AlertPolicy

log = logging.getLogger(__name__)

#: Clock step for the historical signal replay. The full demo walks in 5-day
#: steps; the automatic seed uses a coarser step so a small cloud instance
#: finishes in minutes rather than tens of minutes. Same code path, fewer ticks.
QUICK_REPLAY_STEP = timedelta(days=15)
FULL_REPLAY_STEP = timedelta(days=5)


@dataclass
class SeedResult:
    ingest: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)
    listings: int = 0
    discovery: dict[str, int] = field(default_factory=dict)
    tracked: list[str] = field(default_factory=list)
    graph_edges: int = 0
    playbook_stages: list[str] = field(default_factory=list)
    signals_fired: int = 0
    signals_persisted: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "ingest": self.ingest,
            "outcomes": self.outcomes,
            "listings": self.listings,
            "discovery": self.discovery,
            "tracked": len(self.tracked),
            "graph_edges": self.graph_edges,
            "signals_persisted": self.signals_persisted,
        }


def is_empty(session: Session) -> bool:
    return session.execute(select(func.count(Event.event_id))).scalar_one() == 0


async def seed_demo(
    session: Session, world: World | None = None, quick: bool = False
) -> SeedResult:
    """Run the pipeline end to end against the fixture world."""
    from alphagraph.backtest.engine import replay_signals

    world = world or build_world()
    result = SeedResult()

    boot = await bootstrap(session, world)
    result.ingest = boot.ingest
    result.outcomes = boot.outcomes
    result.listings = boot.listings
    result.discovery = boot.discovery

    engine = DiscoveryEngine(session)
    engine.advance_lifecycle(WORLD_END)
    # Second pass with the clock advanced past the shadow period, so the seeded
    # system arrives with wallets actually tracked rather than all pending.
    engine.advance_lifecycle(WORLD_END + timedelta(days=31))

    result.tracked = [
        c.wallet
        for c in session.execute(select(Candidate)).scalars()
        if c.status == CandidateStatus.TRACKED
    ]

    builder = GraphBuilder(session)
    result.graph_edges = builder.persist_edges(builder.build_edges(result.tracked, WORLD_END))

    primary = world.wallet("insider_listing")
    side = world.wallet("side_wallet")
    dossiers = DossierService(session)
    entity = dossiers.create(
        "ent_listing_ring",
        "Listing ring (primary + side wallet)",
        [primary, side],
        archetype="listing_predictor",
        summary="Probes small, aborts, rests a bid, then sizes in. Side wallet confirms.",
        method="co_acquisition_sequence",
        valid_from=WORLD_START,
    )
    dossiers.add_note(
        entity.id,
        "Unprofitable overall but 8/8 on listings. Track the sequence, not the P&L.",
        kind="hypothesis",
    )

    playbooks = PlaybookEngine(session)
    playbook = playbooks.build_playbook(
        entity.id, playbooks.mine(entity.id, [primary, side], WORLD_END)
    )
    if playbook:
        result.playbook_stages = list(playbook.stages)

    step = QUICK_REPLAY_STEP if quick else FULL_REPLAY_STEP
    signals = replay_signals(session, WORLD_START, WORLD_END, step)
    result.signals_fired = len(signals)

    dispatcher = AlertDispatcher(
        session,
        RecordingNotificationProvider(),
        AlertPolicy(max_per_run=len(signals) + 10),
    )
    dispatch = await dispatcher.dispatch(signals)
    result.signals_persisted = dispatch.persisted
    return result


def seed_if_empty_blocking() -> None:
    """Seed a fresh database on first boot, if configured to.

    Runs in a background thread from the API's startup hook so the service
    becomes healthy immediately and the dashboard is reachable while data fills
    in. Without this, a fresh deploy would require the operator to open a shell
    and run a command by hand before seeing anything.
    """
    import asyncio

    from alphagraph.config import ProviderMode, get_settings
    from alphagraph.db.session import session_scope

    settings = get_settings()
    if not settings.auto_seed:
        return
    if settings.provider_mode is not ProviderMode.FIXTURE:
        # Seeding writes the synthetic world. Doing that into a database that is
        # meant to hold real chain data would silently corrupt every metric.
        log.info("auto-seed skipped: provider_mode is not fixture")
        return

    try:
        with session_scope() as session:
            if not is_empty(session):
                log.info("auto-seed skipped: database already has events")
                return
            log.info("auto-seed starting — this takes several minutes")

            async def run() -> None:
                result = await seed_demo(session, quick=True)
                log.info("auto-seed complete: %s", result.summary())

            asyncio.run(run())
    except Exception:  # must never take the API down with it
        log.exception("auto-seed failed; the API stays up and the dashboard shows empty states")
