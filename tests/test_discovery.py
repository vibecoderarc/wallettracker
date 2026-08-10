"""Discovery must find the planted insiders and reject the planted decoys.

These are the load-bearing tests for the whole product. If discovery stops
finding `insider_quiet` or starts accepting `sniper_bot`, the system is broken
regardless of what anything else reports.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from alphagraph.db.models import Candidate
from alphagraph.discovery.engine import DiscoveryEngine, negative_controls
from alphagraph.providers.world import WORLD_TRUTH
from alphagraph.wallets.metrics import EXCLUDED_ARCHETYPES


@pytest.fixture(scope="module")
def scores(loaded_db, as_of):
    return {s.wallet: s for s in DiscoveryEngine(loaded_db).run_sweep(as_of)}


@pytest.mark.parametrize("handle", [h for h, truth in WORLD_TRUTH.items() if truth["discover"]])
def test_planted_insiders_are_discovered(handle, world, scores):
    address = world.wallet(handle)
    assert address in scores, f"{handle} never surfaced in the sweep at all"
    score = scores[address]
    assert score.passed, f"{handle} was rejected: {score.rejection_reason}"


@pytest.mark.parametrize("handle", [h for h, truth in WORLD_TRUTH.items() if not truth["discover"]])
def test_planted_decoys_are_rejected(handle, world, scores):
    address = world.wallet(handle)
    score = scores.get(address)
    if score is None:
        return  # never surfaced at all, which is a stronger form of rejection
    assert not score.passed, (
        f"{handle} passed discovery but should have been rejected "
        f"(edge={score.edge_ratio:.2f}, hits={score.hits}, "
        f"independent={score.independent_events})"
    )


def test_unprofitable_insider_still_discovered(world, scores):
    """The central claim of the product.

    `insider_listing` loses money — it round-trips and gets liquidated — but
    predicts listings reliably. Ranking on P&L would discard it.
    """
    score = scores[world.wallet("insider_listing")]
    assert score.passed
    assert WORLD_TRUTH["insider_listing"]["profitable"] is False


def test_sniper_excluded_despite_good_record(world, scores, loaded_db, as_of):
    """A wallet with a superb hit rate must still be excluded if it is a bot."""
    from alphagraph.wallets.metrics import WalletMetricsCalculator

    address = world.wallet("sniper_bot")
    metrics = WalletMetricsCalculator(loaded_db).compute(address, as_of)
    assert metrics.archetype in EXCLUDED_ARCHETYPES
    assert metrics.excluded_reason is not None
    score = scores.get(address)
    if score is not None:
        assert not score.passed
        assert "excluded_archetype" in (score.rejection_reason or "")


def test_lucky_wallet_fails_sample_size(world, scores):
    """3-for-3 by chance must not survive the minimum-sample guard."""
    score = scores.get(world.wallet("lucky_wallet"))
    if score is None:
        return
    assert not score.passed
    assert score.hits <= 3


def test_independence_collapses_correlated_appearances(loaded_db, as_of):
    """Appearances clustered in one window count as ~one observation."""
    from datetime import timedelta

    from alphagraph.discovery.engine import Appearance

    engine = DiscoveryEngine(loaded_db)
    base = as_of - timedelta(days=200)
    clustered = [
        Appearance(
            wallet="w",
            outcome_id=f"o{i}",
            asset_id=f"a{i}",
            outcome_class="price_run",
            t0=base + timedelta(days=i),
            entered_at=base,
            lead_time_days=1,
            window="7d",
            usd_value=100.0,
        )
        for i in range(8)
    ]
    assert engine.count_independent(clustered) == 1

    spread = [
        Appearance(
            wallet="w",
            outcome_id=f"o{i}",
            asset_id=f"b{i}",
            outcome_class="price_run",
            t0=base + timedelta(days=i * 60),
            entered_at=base,
            lead_time_days=1,
            window="7d",
            usd_value=100.0,
        )
        for i in range(5)
    ]
    assert engine.count_independent(spread) == 5


def test_negative_control_surfaces_nothing(loaded_db, as_of):
    """Random wallets run through the same pipeline must not pass.

    If they do, the pipeline is a random number generator and every candidate it
    produces is meaningless.
    """
    results = negative_controls(loaded_db, DiscoveryEngine(loaded_db), as_of, sample=40)
    assert results
    assert results[0].surfaced == 0


def test_base_rate_is_realistic(loaded_db, as_of):
    """A universe where most assets win makes every hit rate meaningless."""
    from alphagraph.core.outcomes import OutcomeClass
    from alphagraph.outcomes.registry import OutcomeRegistry

    rate, _hits, total = OutcomeRegistry(loaded_db).base_rate(OutcomeClass.PRICE_RUN, as_of)
    assert total > 100
    assert 0.0 < rate < 0.25, f"base rate {rate:.1%} is implausible for a real universe"


def test_persisted_candidates_record_provenance(loaded_db):
    candidates = list(loaded_db.execute(select(Candidate)).scalars())
    assert candidates
    for candidate in candidates:
        if candidate.rejection_reason is None:
            assert candidate.discovery_provenance, "a passing candidate must cite its outcomes"
            for entry in candidate.discovery_provenance:
                assert {"outcome_id", "asset_id", "t0", "entered_at"} <= set(entry)


class TestBotExclusion:
    """Entry latency measures wallet behaviour, not our ingestion lag."""

    def test_sniper_caught_by_entry_latency(self, loaded_db, world, as_of):
        from alphagraph.wallets.metrics import Archetype, WalletMetricsCalculator

        metrics = WalletMetricsCalculator(loaded_db).compute(world.wallet("sniper_bot"), as_of)
        assert metrics.archetype == Archetype.BOT
        assert metrics.sniper_entries >= 3
        assert "within_30s_of_launch" in (metrics.excluded_reason or "")

    @pytest.mark.parametrize(
        "handle", ["insider_quiet", "insider_listing", "side_wallet", "revival_hunter"]
    )
    def test_humans_have_no_false_sniper_entries(self, loaded_db, world, as_of, handle):
        """A false sniper count would misclassify a real find as infrastructure."""
        from alphagraph.wallets.metrics import WalletMetricsCalculator

        metrics = WalletMetricsCalculator(loaded_db).compute(world.wallet(handle), as_of)
        assert metrics.sniper_entries == 0
        assert metrics.min_entry_latency_seconds is None or (
            metrics.min_entry_latency_seconds > 3600
        )

    def test_latency_skipped_when_creation_was_never_ingested(self, loaded_db, as_of):
        """Tokens created before the backfill window must not be scored.

        Otherwise latency is measured against whatever event we happened to
        ingest first, which understates it and manufactures sniper counts at
        every backfill boundary.
        """
        from alphagraph.wallets.metrics import WalletMetricsCalculator

        calculator = WalletMetricsCalculator(loaded_db)
        assert calculator._creation_time("solana:NEVER_INGESTED") is None

    def test_rejections_are_recorded_for_audit(self, loaded_db):
        """The guards must be inspectable, not just effective."""
        recorded = list(
            loaded_db.execute(
                select(Candidate).where(Candidate.rejection_reason.is_not(None))
            ).scalars()
        )
        assert recorded, "no rejections persisted — the discovery guards are invisible"
        assert any("excluded_archetype" in (c.rejection_reason or "") for c in recorded)
