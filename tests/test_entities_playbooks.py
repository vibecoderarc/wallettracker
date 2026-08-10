"""Entity dossiers, sequence-aware graph edges, and playbook mining."""

from __future__ import annotations

from datetime import timedelta

import pytest

from alphagraph.entities.graph import DossierService, EdgeType, GraphBuilder
from alphagraph.playbooks.engine import PlaybookEngine, Stage


@pytest.fixture
def ring(session, world, as_of):
    primary = world.wallet("insider_listing")
    side = world.wallet("side_wallet")
    builder = GraphBuilder(session)
    builder.persist_edges(builder.build_edges([primary, side], as_of))
    session.flush()
    return primary, side


class TestSequenceGraph:
    def test_lead_follow_edge_detected(self, session, ring, as_of):
        primary, side = ring
        edges = GraphBuilder(session).neighborhood(primary, depth=1)
        assert edges, "no edge found between the primary and side wallet"
        lead = [e for e in edges if e.edge_type == EdgeType.LEADS]
        assert lead, "relationship should be directional, not merely co-acquisition"
        edge = lead[0]
        assert edge.source == primary
        assert edge.target == side
        assert edge.observations >= 3
        assert edge.median_lag_seconds and edge.median_lag_seconds > 0

    def test_edges_document_false_positive_modes(self, session, ring, as_of):
        primary, _ = ring
        for edge in GraphBuilder(session).neighborhood(primary, depth=1):
            assert edge.false_positive_modes, "an inferred edge must state how it can be wrong"

    def test_confidence_is_capped(self, session, ring):
        """No amount of behavioural correlation proves common control."""
        primary, _ = ring
        for edge in GraphBuilder(session).neighborhood(primary, depth=1):
            assert float(edge.confidence) <= 0.85

    def test_neighborhood_bounded(self, session, ring):
        primary, _ = ring
        assert len(GraphBuilder(session).neighborhood(primary, depth=99, limit=5)) <= 5


class TestDossiers:
    def test_retraction_preserves_history(self, session, world, as_of):
        """A user must be able to undo a claim without destroying the record."""
        service = DossierService(session)
        wallet = world.wallet("insider_quiet")
        entity = service.create(
            "ent_test_retract",
            "Test",
            [wallet],
            archetype="informed_accumulator",
            valid_from=as_of - timedelta(days=10),
        )
        session.flush()

        assert wallet in service.members_at(entity.id, as_of)
        assert service.retract_member(entity.id, wallet, as_of)
        session.flush()

        # Gone now...
        assert wallet not in service.members_at(entity.id, as_of + timedelta(days=1))
        # ...but the historical view is unchanged, and the row still exists.
        assert wallet in service.members_at(entity.id, as_of - timedelta(days=1))

        from sqlalchemy import select

        from alphagraph.db.models import EntityMember

        row = session.execute(
            select(EntityMember).where(
                EntityMember.entity_id == entity.id, EntityMember.wallet == wallet
            )
        ).scalar_one()
        assert row.valid_to == as_of, "retraction must close the row, not delete it"

    def test_notes_are_attached(self, session, world):
        service = DossierService(session)
        entity = service.create(
            "ent_test_notes", "Notes", [world.wallet("side_wallet")], archetype="listing_predictor"
        )
        service.add_note(entity.id, "Watch the side wallet, not the primary.", kind="hypothesis")
        session.flush()
        session.refresh(entity)
        assert any(n.kind == "hypothesis" for n in entity.notes)


class TestPlaybooks:
    def test_mines_the_listing_sequence(self, session, world, as_of):
        primary, side = world.wallet("insider_listing"), world.wallet("side_wallet")
        service = DossierService(session)
        entity = service.create(
            "ent_playbook", "Ring", [primary, side], archetype="listing_predictor"
        )
        session.flush()

        engine = PlaybookEngine(session)
        sequences = engine.mine(entity.id, [primary, side], as_of)
        completed = [s for s in sequences if s.completed]
        assert len(completed) >= 3

        playbook = engine.build_playbook(entity.id, sequences)
        assert playbook is not None
        # The stages that made the CASHCAT sequence readable.
        assert Stage.BIDDING in playbook.stages
        assert Stage.CONFIRMING in playbook.stages
        assert playbook.supporting_sequences >= 3
        assert playbook.confirmed_by_operator is False, "mined patterns need human confirmation"

    def test_withheld_below_minimum_sequences(self, session, world, as_of):
        """A pattern mined from two sequences is a coincidence."""
        from alphagraph.playbooks.engine import PlaybookConfig

        service = DossierService(session)
        entity = service.create(
            "ent_too_few", "Too few", [world.wallet("lucky_wallet")], archetype="unclassified"
        )
        session.flush()
        engine = PlaybookEngine(session, PlaybookConfig(min_sequences_to_publish=99))
        sequences = engine.mine(entity.id, [world.wallet("lucky_wallet")], as_of)
        assert engine.build_playbook(entity.id, sequences) is None

    def test_resting_order_maps_to_bidding_stage(self, session, world, as_of):
        from sqlalchemy import select

        from alphagraph.core.events import EventType
        from alphagraph.db.models import Event
        from alphagraph.playbooks.engine import classify_event

        order = (
            session.execute(
                select(Event).where(
                    Event.actor == world.wallet("insider_listing"),
                    Event.event_type == EventType.ORDER_PLACED.value,
                    Event.filled.is_(False),
                )
            )
            .scalars()
            .first()
        )
        assert classify_event(order, median_size=50_000) == Stage.BIDDING

    def test_small_probe_maps_to_probing_not_accumulating(self, session, world):
        from sqlalchemy import select

        from alphagraph.core.events import EventType
        from alphagraph.db.models import Event
        from alphagraph.playbooks.engine import classify_event

        probe = (
            session.execute(
                select(Event).where(
                    Event.actor == world.wallet("insider_listing"),
                    Event.event_type == EventType.POSITION_OPENED.value,
                )
            )
            .scalars()
            .first()
        )
        assert probe is not None
        # Relative to a wallet whose median size is 50k, a $500 open is a probe.
        assert classify_event(probe, median_size=50_000) == Stage.PROBING
        # Relative to a wallet that only ever trades $500, it is a real entry.
        assert classify_event(probe, median_size=500) == Stage.ACCUMULATING
