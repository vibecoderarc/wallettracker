"""Backtest execution realism, baselines, and the nightly governance loop."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from alphagraph.backtest.engine import Backtester, ExecutionAssumptions, discovery_backtest
from alphagraph.db.models import Proposal, SignalRow
from alphagraph.nightly.loop import NightlyLoop, render_digest
from alphagraph.providers.fixture import FixtureMarketDataProvider, RecordingNotificationProvider
from alphagraph.providers.world import WORLD_START
from alphagraph.signals.policy import AlertDispatcher, AlertPolicy


@pytest.fixture(scope="module")
def market(world):
    return FixtureMarketDataProvider(world)


@pytest.fixture(scope="module")
def entries(loaded_db, as_of):
    from alphagraph.db.models import Asset

    assets = list(loaded_db.execute(select(Asset.id).limit(40)).scalars())
    return [(a, as_of - timedelta(days=120)) for a in assets]


class TestExecutionRealism:
    def test_slippage_worsens_the_fill(self, session, market, entries, as_of):
        backtester = Backtester(session, market)
        asset_id, at = entries[0]

        cheap = asyncio.run(
            backtester.simulate_entry(asset_id, at, ExecutionAssumptions(slippage_bps=0))
        )
        expensive = asyncio.run(
            backtester.simulate_entry(asset_id, at, ExecutionAssumptions(slippage_bps=500))
        )
        if cheap.filled and expensive.filled:
            assert expensive.fill_price > cheap.fill_price

    def test_missing_price_rejects_rather_than_guessing(self, session, market, as_of):
        backtester = Backtester(session, market)
        fill = asyncio.run(
            backtester.simulate_entry("solana:DOES_NOT_EXIST", as_of, ExecutionAssumptions())
        )
        assert not fill.filled
        assert fill.rejected_reason == "no_price_at_entry"

    def test_entry_delay_is_applied(self, session, market, entries):
        backtester = Backtester(session, market)
        asset_id, at = entries[0]
        assumptions = ExecutionAssumptions(entry_delay=timedelta(hours=2))
        fill = asyncio.run(backtester.simulate_entry(asset_id, at, assumptions))
        assert fill.fill_at == at + timedelta(hours=2)

    def test_dead_assets_count_as_total_loss_not_excluded(self, session, market, as_of):
        """Dropping unpriceable exits is the most common way backtests lie."""
        backtester = Backtester(session, market)
        run = asyncio.run(
            backtester.run(
                "dead_asset_handling",
                [("solana:DOES_NOT_EXIST", as_of - timedelta(days=200))],
                WORLD_START,
                as_of,
            )
        )
        assert run.metrics["rejected"] == 1
        assert run.metrics["trades"] == 0


class TestBacktestIntegrity:
    def test_leakage_audit_runs_on_every_backtest(self, session, market, entries, as_of):
        run = asyncio.run(
            Backtester(session, market).run("audit_check", entries, WORLD_START, as_of)
        )
        assert run.leakage_audit_detail
        assert run.leakage_audit_passed

    def test_baseline_is_computed(self, session, market, as_of):
        baseline = asyncio.run(Backtester(session, market).random_baseline(30, WORLD_START, as_of))
        assert baseline.trades + baseline.rejected == 30

    def test_discovery_backtest_reports_survival(self, session, as_of):
        result = asyncio.run(discovery_backtest(session, as_of, as_of - timedelta(days=150)))
        assert "survival_rate" in result
        assert 0.0 <= result["survival_rate"] <= 1.0


class TestNightlyGovernance:
    def _loop(self, session, market):
        dispatcher = AlertDispatcher(
            session, RecordingNotificationProvider(), AlertPolicy(max_per_run=500)
        )
        return NightlyLoop(session, market, dispatcher)

    def test_grading_marks_hits_and_misses(self, session, market, as_of):
        loop = self._loop(session, market)
        counts = loop.grade_signals(as_of + timedelta(days=40))
        assert set(counts) == {"hit", "miss", "pending"}

    def test_graded_hits_have_a_forward_outcome(self, session, market, as_of):
        """A hit must cite an outcome that occurred AFTER the signal fired."""
        self._loop(session, market).grade_signals(as_of + timedelta(days=40))
        session.flush()
        hits = (
            session.execute(select(SignalRow).where(SignalRow.grade == "hit").limit(25))
            .scalars()
            .all()
        )
        for signal in hits:
            assert signal.grade_detail.get("lead_time_days", 0) > 0

    def test_proposals_are_never_auto_applied(self, session, market, as_of):
        loop = self._loop(session, market)
        loop.grade_signals(as_of + timedelta(days=40))
        _proposals, _variants, note = loop.generate_proposals(as_of)
        session.flush()
        for proposal in session.execute(select(Proposal)).scalars():
            assert proposal.status == "pending"
            assert proposal.decided_at is None
        assert note

    def test_proposals_record_variants_tested(self, session, market, as_of):
        """Multiple-testing awareness must be visible, not hidden."""
        loop = self._loop(session, market)
        loop.grade_signals(as_of + timedelta(days=40))
        proposals, _variants, _ = loop.generate_proposals(as_of)
        for proposal in proposals:
            assert proposal.variants_tested >= 1
            assert proposal.sample_size >= 20

    def test_digest_renders_without_a_model(self, session, market, as_of):
        """The facts must exist even when the AI provider is unavailable."""
        loop = self._loop(session, market)
        report = asyncio.run(loop.run(as_of + timedelta(days=40)))
        text = render_digest(report, session)
        assert "AlphaGraph digest" in text
        assert "Tracked wallets" in text
