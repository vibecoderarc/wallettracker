"""The Phase A sweep, especially the two-pass structure.

The single most important property here is that pass two runs. Without it every
wallet's hit rate is 100% by construction, because the only assets ever ingested
are the ones that won. These tests exist so that structure cannot be quietly
"optimised" away.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from alphagraph.bootstrap import BootstrapSweep, SweepBudget, SweepReport
from alphagraph.db.models import Asset, Event
from alphagraph.db.session import create_all, reset_engine
from alphagraph.providers.geckoterminal import GeckoTerminalProvider
from alphagraph.providers.helius import HeliusChainProvider
from alphagraph.providers.http import HttpClient

MEME = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
POOL = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
WALLET = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"


@pytest.fixture
def fresh_session(tmp_path):
    from sqlalchemy.orm import Session

    reset_engine()
    engine = create_all(f"sqlite+pysqlite:///{tmp_path / 'sweep.db'}")
    db = Session(bind=engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        reset_engine()


class ScriptedTransport(httpx.AsyncBaseTransport):
    """Serves responses by URL substring, so ordering does not matter."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        for fragment, payload in self.routes.items():
            if fragment in url:
                return httpx.Response(200, json=payload)
        return httpx.Response(200, json=[] if "helius" in url else {"data": []})


def _client(provider: str, base: str, transport: ScriptedTransport) -> HttpClient:
    return HttpClient(
        provider=provider,
        base_url=base,
        requests_per_second=0,
        client=httpx.AsyncClient(transport=transport),
    )


class TestBudget:
    def test_caps_are_explicit(self):
        budget = SweepBudget()
        assert budget.max_helius_requests > 0
        assert budget.max_pages_per_address > 0
        assert budget.max_candidates_to_qualify > 0

    def test_estimate_does_not_touch_the_chain_provider(self, fresh_session):
        """Pricing a sweep must not spend the allowance being priced."""
        market_transport = ScriptedTransport({})
        chain_transport = ScriptedTransport({})
        market = GeckoTerminalProvider(
            client=_client("gecko", "https://api.geckoterminal.com/api/v2", market_transport)
        )
        chain = HeliusChainProvider(
            "k", client=_client("helius", "https://api.helius.xyz", chain_transport)
        )
        sweep = BootstrapSweep(fresh_session, chain, market)

        asyncio.run(sweep.estimate(pages=1))
        assert chain_transport.calls == []
        assert market_transport.calls != []

    def test_request_cap_stops_ingestion_and_is_reported(self, fresh_session):
        """A reached limit must appear in the report, not pass silently."""
        chain_transport = ScriptedTransport({})
        chain = HeliusChainProvider(
            "k", client=_client("helius", "https://api.helius.xyz", chain_transport)
        )
        market = GeckoTerminalProvider(
            client=_client("gecko", "https://api.geckoterminal.com/api/v2", ScriptedTransport({}))
        )
        sweep = BootstrapSweep(fresh_session, chain, market, SweepBudget(max_helius_requests=0))

        async def run():
            return await sweep._ingest_address(
                WALLET, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )

        assert asyncio.run(run()) == []
        assert "helius_request_cap" in sweep.report.truncated


class TestUniverseIncludesLosers:
    def test_pools_without_outcomes_are_still_registered(self, fresh_session):
        """The base rate's denominator is every asset examined, not the winners.

        Registering only tokens that moved drives the base rate toward 100% and
        makes every wallet's edge vanish — the failure already seen once in the
        fixture world.
        """
        flat = [
            [int(datetime(2026, 6, 1, tzinfo=UTC).timestamp()) + i * 3600, 1, 1, 1, 1, 50]
            for i in range(60)
        ]
        market_transport = ScriptedTransport(
            {
                "/pools?": {
                    "data": [
                        {
                            "id": f"solana_{POOL}",
                            "attributes": {
                                "address": POOL,
                                "name": "DUD / SOL",
                                "reserve_in_usd": "1000",
                                "volume_usd": {"h24": "10"},
                                "pool_created_at": "2026-05-01T00:00:00Z",
                            },
                            "relationships": {"base_token": {"data": {"id": f"solana_{MEME}"}}},
                        }
                    ]
                },
                "/ohlcv/": {"data": {"attributes": {"ohlcv_list": flat}}},
            }
        )
        market = GeckoTerminalProvider(
            client=_client("gecko", "https://api.geckoterminal.com/api/v2", market_transport)
        )
        chain = HeliusChainProvider(
            "k", client=_client("helius", "https://api.helius.xyz", ScriptedTransport({}))
        )
        sweep = BootstrapSweep(fresh_session, chain, market)

        asyncio.run(sweep.build_universe(pages=1))
        fresh_session.flush()

        assets = list(fresh_session.execute(select(Asset)).scalars())
        assert len(assets) == 1, "a flat token must still enter the universe"
        assert sweep.report.outcomes_detected == {}


class TestTwoPassStructure:
    """Pass two is what makes the numbers mean anything."""

    def _sweep(self, session, helius_payload):
        chain_transport = ScriptedTransport({"/transactions": helius_payload})
        chain = HeliusChainProvider(
            "k", client=_client("helius", "https://api.helius.xyz", chain_transport)
        )
        market = GeckoTerminalProvider(
            client=_client("gecko", "https://api.geckoterminal.com/api/v2", ScriptedTransport({}))
        )
        return BootstrapSweep(session, chain, market), chain_transport

    def _swap(self, signature: str, when: datetime) -> dict:
        return {
            "signature": signature,
            "timestamp": int(when.timestamp()),
            "slot": 1,
            "type": "SWAP",
            "source": "RAYDIUM",
            "feePayer": WALLET,
            "tokenTransfers": [
                {
                    "mint": MEME,
                    "tokenAmount": 10,
                    "fromUserAccount": "pool",
                    "toUserAccount": WALLET,
                },
                {
                    "mint": "So11111111111111111111111111111111111111112",
                    "tokenAmount": 1,
                    "fromUserAccount": WALLET,
                    "toUserAccount": "pool",
                },
            ],
        }

    def test_pass_two_fetches_each_suspects_own_history(self, fresh_session):
        """Without this the denominator equals the numerator for every wallet."""
        when = datetime(2026, 6, 1, tzinfo=UTC)
        sweep, transport = self._sweep(fresh_session, [self._swap("s1", when)])

        async def run():
            await sweep.pass_two_qualify(
                {WALLET}, when - timedelta(days=180), when + timedelta(days=1)
            )

        asyncio.run(run())
        assert sweep.report.suspects_qualified == 1
        assert any(WALLET in call for call in transport.calls), (
            "pass two must query the wallet's own address, not just outcome windows"
        )

    def test_qualification_is_capped_and_the_shortfall_reported(self, fresh_session):
        when = datetime(2026, 6, 1, tzinfo=UTC)
        sweep, _ = self._sweep(fresh_session, [])
        sweep.budget = SweepBudget(max_candidates_to_qualify=2)

        async def run():
            await sweep.pass_two_qualify(
                {f"wallet{i}" for i in range(10)}, when - timedelta(days=30), when
            )

        asyncio.run(run())
        assert sweep.report.suspects_qualified == 2
        assert any("qualified 2 of 10" in note for note in sweep.report.truncated)

    def test_ingested_events_are_persisted(self, fresh_session):
        when = datetime(2026, 6, 1, tzinfo=UTC)
        sweep, _ = self._sweep(fresh_session, [self._swap("s1", when)])

        async def run():
            await sweep._ingest_address(WALLET, when - timedelta(days=30), when + timedelta(days=1))

        asyncio.run(run())
        fresh_session.flush()
        assert fresh_session.execute(select(Event)).scalars().all()


class TestCoverageReporting:
    def test_parse_coverage_is_surfaced(self):
        """Low coverage means the candidate list rests on partial history."""
        report = SweepReport()
        report.events_written = 10
        report.unknown_interactions = 4
        assert report.parse_coverage == pytest.approx(0.6)

    def test_coverage_is_zero_when_nothing_was_written(self):
        assert SweepReport().parse_coverage == 0.0

    def test_report_serialises_for_the_cli(self):
        payload = SweepReport().as_dict()
        for key in ("parse_coverage", "base_rate", "truncated", "provider_usage"):
            assert key in payload
