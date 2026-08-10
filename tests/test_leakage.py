"""Point-in-time correctness.

Look-ahead is the failure mode that makes a system feel brilliant and lose
money. These tests attack it directly.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from alphagraph.backtest.engine import LeakageAudit, SimulationClock
from alphagraph.db.models import Event, OutcomeRow
from alphagraph.discovery.engine import DiscoveryEngine
from alphagraph.ingestion.pipeline import observed_events
from alphagraph.wallets.metrics import WalletMetricsCalculator


def test_leakage_audit_passes_on_clean_data(session, as_of):
    findings = LeakageAudit(session).run(as_of - timedelta(days=365), as_of)
    failed = [f for f in findings if not f.passed]
    assert not failed, f"leakage audit failed: {[(f.check, f.detail) for f in failed]}"


def test_metrics_are_deterministic(session, world, as_of):
    """Same wallet, same as_of, same version → identical output, always."""
    calculator = WalletMetricsCalculator(session)
    wallet = world.wallet("insider_quiet")
    first = calculator.compute(wallet, as_of).as_dict()
    second = calculator.compute(wallet, as_of).as_dict()
    assert first == second


def test_earlier_as_of_never_sees_more(session, world, as_of):
    """Rewinding the clock can only reduce what is visible."""
    calculator = WalletMetricsCalculator(session)
    wallet = world.wallet("insider_quiet")
    late = calculator.compute(wallet, as_of)
    early = calculator.compute(wallet, as_of - timedelta(days=200))
    assert early.total_actions <= late.total_actions
    assert early.distinct_tokens <= late.distinct_tokens


def test_observed_events_respects_as_of(session, as_of):
    cutoff = as_of - timedelta(days=180)
    for event in observed_events(session, as_of=cutoff):
        assert event.observed_at <= cutoff


def test_outcomes_never_observable_before_they_happen(session):
    bad = session.execute(
        select(func.count(OutcomeRow.outcome_id)).where(
            OutcomeRow.first_observed_at < OutcomeRow.t0
        )
    ).scalar_one()
    assert bad == 0


def test_events_never_observed_before_chain_time(session):
    bad = session.execute(
        select(func.count(Event.event_id)).where(Event.observed_at < Event.chain_time)
    ).scalar_one()
    assert bad == 0


def test_discovery_at_past_date_ignores_future_outcomes(session, as_of):
    """A sweep frozen in the past must not use outcomes observed later.

    The strongest available check: a past sweep cannot cite an outcome whose
    first_observed_at is after the freeze point.
    """
    freeze = as_of - timedelta(days=200)
    engine = DiscoveryEngine(session)
    for score in engine.run_sweep(freeze):
        for appearance in score.appearances:
            outcome = session.get(OutcomeRow, appearance.outcome_id)
            assert outcome is not None
            assert outcome.first_observed_at <= freeze
            assert appearance.entered_at < outcome.t0


def test_simulation_clock_advances_monotonically():
    from datetime import UTC, datetime

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 10, tzinfo=UTC)
    ticks = list(SimulationClock(start, end, timedelta(days=1)))
    assert ticks[0] == start
    assert ticks == sorted(ticks)
    assert all(t <= end for t in ticks)
