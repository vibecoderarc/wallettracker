"""Signal engine, alert policy, and the safety rules around delivery."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from alphagraph.config import RunMode, Settings
from alphagraph.db.models import Candidate, SignalRow
from alphagraph.discovery.engine import CandidateStatus, DiscoveryEngine
from alphagraph.providers.fixture import RecordingNotificationProvider
from alphagraph.signals.engine import Family, Signal, SignalEngine
from alphagraph.signals.policy import AlertDispatcher, AlertPolicy, sanitize_for_notification
from alphagraph.wallets.metrics import entity_relative_significance


@pytest.fixture
def tracked(session, as_of):
    engine = DiscoveryEngine(session)
    engine.advance_lifecycle(as_of)
    engine.advance_lifecycle(as_of + timedelta(days=31))
    session.flush()
    return [
        c.wallet
        for c in session.execute(select(Candidate)).scalars()
        if c.status == CandidateStatus.TRACKED
    ]


def _signal(**kwargs) -> Signal:
    from alphagraph.providers.world import WORLD_END

    defaults = dict(
        family=Family.TRACKED_ENTITY_ACTION,
        triggered_at=WORLD_END,
        evidence_cutoff=WORLD_END,
        title="test signal",
        significance=0.9,
        confidence=0.5,
        asset_id="solana:AAA",
        wallet="W" * 40,
        supporting_facts=["fact"],
    )
    return Signal(**{**defaults, **kwargs})


class TestSignalGeneration:
    def test_engine_runs_and_carries_base_rates(self, session, as_of, tracked):
        signals = SignalEngine(session).run_all(as_of)
        assert signals
        for signal in signals:
            assert signal.evidence_cutoff == as_of
            assert signal.triggered_at <= as_of

    def test_resting_order_produces_a_signal(self, session, world, as_of, tracked):
        """An unfilled order moved no funds and must still alert."""
        from alphagraph.core.events import EventType
        from alphagraph.db.models import Event

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
        assert order is not None, "fixture should contain an unfilled resting order"

        engine = SignalEngine(session)
        signals = engine.tracked_entity_actions(order.observed_at + timedelta(hours=1))
        families = {s.family for s in signals}
        assert Family.RESTING_ORDER in families


class TestEntityRelativeSignificance:
    def test_small_probe_from_quiet_wallet_scores_high(self):
        """The rule that would have kept the $500 CASHCAT probe."""
        from alphagraph.providers.world import WORLD_END
        from alphagraph.wallets.metrics import WalletMetricSet

        quiet = WalletMetricSet(wallet="q", as_of=WORLD_END)
        quiet.trades_per_month = 0.5
        quiet.position_sizes_usd = [500.0]

        score, reasons = entity_relative_significance(
            quiet, usd_value=500, event_type="position_opened", days_since_last_action=90
        )
        assert score > 0.7
        assert reasons

    def test_large_buy_from_churner_scores_low(self):
        from alphagraph.providers.world import WORLD_END
        from alphagraph.wallets.metrics import WalletMetricSet

        churner = WalletMetricSet(wallet="c", as_of=WORLD_END)
        churner.trades_per_month = 200.0
        churner.position_sizes_usd = [1_000_000.0] * 50

        score, _ = entity_relative_significance(
            churner, usd_value=1000, event_type="swap", days_since_last_action=0.1
        )
        assert score < 0.3


class TestAlertPolicy:
    def _dispatcher(self, session, run_mode=RunMode.SHADOW, webhook=""):
        settings = Settings(run_mode=run_mode, notification_webhook=webhook)
        notifier = RecordingNotificationProvider()
        return (
            AlertDispatcher(session, notifier, AlertPolicy(cooldown=timedelta(hours=6)), settings),
            notifier,
        )

    def test_shadow_mode_sends_nothing(self, session):
        dispatcher, notifier = self._dispatcher(session)
        result = asyncio.run(dispatcher.dispatch([_signal()]))
        assert result.persisted == 1
        assert result.withheld_shadow == 1
        assert notifier.sent == []

    def test_live_mode_requires_a_destination(self, session):
        """run_mode alone must not enable delivery."""
        dispatcher, notifier = self._dispatcher(session, RunMode.LIVE_ALERTS, webhook="")
        result = asyncio.run(dispatcher.dispatch([_signal(asset_id="solana:NODEST")]))
        assert result.delivered == 0
        assert notifier.sent == []

    def test_live_mode_with_destination_delivers(self, session):
        dispatcher, notifier = self._dispatcher(
            session, RunMode.LIVE_ALERTS, webhook="https://example.invalid/hook"
        )
        result = asyncio.run(dispatcher.dispatch([_signal(asset_id="solana:LIVE")]))
        assert result.delivered == 1
        assert len(notifier.sent) == 1

    def test_duplicates_suppressed(self, session):
        dispatcher, _ = self._dispatcher(session)
        signal = _signal(asset_id="solana:DUPE")
        asyncio.run(dispatcher.dispatch([signal]))
        result = asyncio.run(dispatcher.dispatch([signal]))
        assert result.persisted == 0
        assert result.suppressed_duplicate == 1

    def test_distinct_wallets_do_not_suppress_each_other(self, session):
        """Wallet-scoped signals with no asset must not collide in the cooldown."""
        dispatcher, _ = self._dispatcher(session)
        signals = [
            _signal(family=Family.NEW_CANDIDATE, asset_id=None, wallet=f"wallet{i}")
            for i in range(4)
        ]
        result = asyncio.run(dispatcher.dispatch(signals))
        assert result.persisted == 4
        assert result.suppressed_cooldown == 0

    def test_critical_risk_survives_low_significance(self, session):
        """A high score must never be required to surface a serious risk."""
        dispatcher, _ = self._dispatcher(session)
        weak = _signal(
            family=Family.PUMP_OPERATOR,
            significance=0.01,
            asset_id="solana:RISKY",
        )
        result = asyncio.run(dispatcher.dispatch([weak]))
        assert result.persisted == 1
        assert result.risk_flagged == 1
        row = session.execute(
            select(SignalRow).where(SignalRow.asset_id == "solana:RISKY")
        ).scalar_one()
        assert row.risk_band == "critical"

    def test_low_significance_non_critical_suppressed(self, session):
        dispatcher, _ = self._dispatcher(session)
        result = asyncio.run(
            dispatcher.dispatch([_signal(significance=0.01, asset_id="solana:MEH")])
        )
        assert result.suppressed_threshold == 1


class TestPhishingSafety:
    def test_links_are_defanged(self):
        """Our own alert must not become a phishing delivery mechanism."""
        text = "Check https://evil.example/drain and http://also-bad.example"
        out = sanitize_for_notification(text)
        assert "https://" not in out
        assert "http://" not in out
        assert "hxxps://" in out

    def test_newlines_stripped(self):
        assert "\n" not in sanitize_for_notification("a\nb")
