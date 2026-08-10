"""API surface, and the product boundary that must never be crossed.

The boundary tests are not decoration. The single most dangerous change anyone
could make to this codebase is adding transaction construction or key handling,
and these assertions are designed to fail loudly if that ever happens.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from alphagraph.api.app import app

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "packages" / "alphagraph"


@pytest.fixture(scope="module")
def client(loaded_db):
    return TestClient(app)


class TestApi:
    def test_health_leaks_no_configuration(self, client):
        payload = client.get("/v1/health").json()
        assert payload["status"] == "ok"
        for leaky in ("database_url", "solana_rpc_url", "ai_provider_key", "notification_webhook"):
            assert leaky not in payload

    def test_coverage_reports_freshness(self, client):
        payload = client.get("/v1/system/coverage").json()
        assert payload["events"] > 0
        assert payload["latest_event_observed_at"]
        assert payload["notifications_enabled"] is False
        assert "disclaimer" in payload

    def test_base_rates_exposed(self, client):
        payload = client.get("/v1/system/base-rates").json()
        assert "price_run" in payload["base_rates"]
        assert payload["base_rates"]["price_run"]["total_assets"] > 100

    def test_candidates_include_base_rate_context(self, client):
        candidates = client.get("/v1/candidates").json()["candidates"]
        assert candidates
        for candidate in candidates:
            assert "base_rate" in candidate
            assert "independent_events" in candidate
            assert "edge_vs_base" in candidate

    def test_wallet_profile_separates_pnl_from_skill(self, client, world):
        payload = client.get(f"/v1/wallets/{world.wallet('insider_listing')}").json()
        assert "metrics" in payload
        assert "ranking never uses this figure" in payload["profitability_note"]

    def test_graph_neighborhood_depth_is_capped(self, client, world):
        response = client.get(
            "/v1/graph/neighborhood",
            params={"wallet": world.wallet("insider_listing"), "depth": 99},
        )
        assert response.status_code == 422  # rejected by validation, not silently clamped

    def test_paper_order_is_labelled_simulated(self, client):
        response = client.post(
            "/v1/paper/orders",
            json={"asset_id": "solana:TEST", "size_usd": 100.0, "entry_price_usd": 0.01},
        )
        payload = response.json()
        assert payload["simulated"] is True
        assert "No order was placed" in payload["warning"]

    def test_portfolio_warns_positions_are_simulated(self, client):
        payload = client.get("/v1/paper/portfolio").json()
        assert "simulated" in payload["warning"].lower()

    def test_proposals_state_they_do_not_auto_apply(self, client):
        payload = client.get("/v1/proposals").json()
        assert "never auto-apply" in payload["note"]


class TestProductBoundary:
    """Static checks against the non-goals in spec §1.2."""

    def _sources(self) -> list[tuple[pathlib.Path, str]]:
        return [(p, p.read_text()) for p in PACKAGE_ROOT.rglob("*.py")]

    def test_no_private_key_handling(self):
        forbidden = re.compile(
            r"\b(private_key|privatekey|seed_phrase|mnemonic|secret_key|keypair)\b",
            re.IGNORECASE,
        )
        offenders = [
            str(path)
            for path, text in self._sources()
            # The config module names these only to state they are never accepted.
            if forbidden.search(text) and "config.py" not in str(path)
        ]
        assert not offenders, f"private-key handling appeared in: {offenders}"

    def test_no_transaction_signing_or_submission(self):
        forbidden = re.compile(
            r"\b(sign_transaction|sendTransaction|send_transaction|signAndSend|"
            r"submit_transaction|broadcast_tx)\b"
        )
        offenders = [str(p) for p, text in self._sources() if forbidden.search(text)]
        assert not offenders, f"transaction submission appeared in: {offenders}"

    def test_no_exchange_trading_endpoints(self):
        forbidden = re.compile(r"\b(place_order|create_order|market_buy|market_sell)\b")
        offenders = [
            str(p)
            for p, text in self._sources()
            # Paper trading legitimately creates simulated positions.
            if forbidden.search(text) and "paper" not in text[:400].lower()
        ]
        assert not offenders, f"live order placement appeared in: {offenders}"

    def test_paper_positions_are_always_simulated(self, loaded_db):
        from sqlalchemy import select

        from alphagraph.db.models import PaperPosition

        for position in loaded_db.execute(select(PaperPosition)).scalars():
            assert position.simulated is True

    def test_shadow_mode_is_the_default(self):
        from alphagraph.config import RunMode, Settings

        assert Settings().run_mode is RunMode.SHADOW
        assert Settings().notifications_enabled is False

    def test_live_provider_mode_requires_credentials(self):
        from alphagraph.config import ProviderMode, Settings

        with pytest.raises(ValueError):
            Settings(provider_mode=ProviderMode.LIVE, solana_rpc_url="")
