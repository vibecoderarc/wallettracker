"""API authentication and the refusal to deploy unauthenticated.

A public hostname with no auth hands over every tracked wallet, dossier note,
and hypothesis — the entire product. These tests exist so that cannot regress
into a deploy.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from alphagraph.api.app import app
from alphagraph.config import Settings, get_settings

TOKEN = "test-token-abc123"

PROTECTED_PATHS = [
    "/v1/system/coverage",
    "/v1/system/base-rates",
    "/v1/signals",
    "/v1/candidates",
    "/v1/entities",
    "/v1/proposals",
    "/v1/digests",
    "/v1/paper/portfolio",
]


@pytest.fixture
def secured(loaded_db, monkeypatch):
    """Run the app as if a token were configured."""
    settings = Settings(api_token=TOKEN, environment="production")
    monkeypatch.setattr("alphagraph.api.app.get_settings", lambda: settings)
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


class TestConfigRefusesUnauthenticatedDeploy:
    def test_non_local_environment_requires_token(self):
        with pytest.raises(ValueError, match="ALPHAGRAPH_API_TOKEN is required"):
            Settings(environment="production", api_token="")

    @pytest.mark.parametrize("env", ["local", "dev", "development", "test"])
    def test_local_environments_may_run_open(self, env):
        assert Settings(environment=env, api_token="").auth_required is False

    @pytest.mark.parametrize("env", ["production", "staging", "prod"])
    def test_deployed_environments_are_not_treated_as_local(self, env):
        assert Settings(environment=env, api_token=TOKEN).is_local is False


class TestBearerGate:
    @pytest.mark.parametrize("path", PROTECTED_PATHS)
    def test_rejected_without_token(self, secured, path):
        assert secured.get(path).status_code == 401

    @pytest.mark.parametrize("path", PROTECTED_PATHS)
    def test_accepted_with_token(self, secured, path):
        response = secured.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200

    def test_wrong_token_forbidden(self, secured):
        response = secured.get("/v1/candidates", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 403

    def test_wrong_scheme_rejected(self, secured):
        response = secured.get("/v1/candidates", headers={"Authorization": f"Basic {TOKEN}"})
        assert response.status_code == 401

    def test_mutations_are_gated_too(self, secured):
        """A writable endpoint left open is worse than a readable one."""
        body = {"asset_id": "solana:X", "size_usd": 1.0, "entry_price_usd": 0.01}
        assert secured.post("/v1/paper/orders", json=body).status_code == 401
        assert (
            secured.post(
                "/v1/paper/orders", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
            ).status_code
            == 200
        )

    def test_health_stays_open_for_platform_checks(self, secured):
        """Render's health check has no way to present a token."""
        response = secured.get("/v1/health")
        assert response.status_code == 200
        assert set(response.json()) == {"status", "run_mode"}


class TestTokenNeverReachesBrowser:
    def test_web_client_does_not_use_next_public_prefix(self):
        """NEXT_PUBLIC_ variables are inlined into the client bundle."""
        import pathlib

        source = (pathlib.Path(__file__).resolve().parents[1] / "apps/web/lib/api.ts").read_text()
        assert "NEXT_PUBLIC_API_TOKEN" not in source
        assert "process.env.ALPHAGRAPH_API_TOKEN" in source


class TestRootSignpost:
    """The engine's root must explain itself rather than return "Not Found".

    A bare 404 at the root reads like a broken deploy, which is what it looked
    like in practice when the service was working correctly.
    """

    def test_root_is_reachable_without_a_token(self, secured):
        """It is a signpost, so it cannot require the credential it explains."""
        response = secured.get("/")
        assert response.status_code == 200

    def test_root_points_at_the_dashboard(self, secured):
        body = secured.get("/").json()
        assert "dashboard" in body["message"].lower()
        assert body["service"] == "alphagraph-api"

    def test_root_leaks_no_state(self, secured):
        """Unauthenticated, so it must not reveal anything about the data."""
        body = secured.get("/").json()
        assert set(body) == {"service", "status", "message", "health", "docs"}
        serialized = str(body).lower()
        for leaky in ("token", "database", "postgres", "wallet", "candidate"):
            assert leaky not in serialized
