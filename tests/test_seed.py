"""First-boot auto-seed: it must fill an empty database and never touch a full
or live one.

A seed that ran twice, or ran against real chain data, would silently corrupt
every metric in the system.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from alphagraph.config import ProviderMode, Settings
from alphagraph.db.models import Candidate, Event, SignalRow
from alphagraph.discovery.engine import CandidateStatus
from alphagraph.seed import is_empty, seed_demo


class TestSeedGuards:
    def test_populated_database_is_not_empty(self, loaded_db):
        assert is_empty(loaded_db) is False

    def test_auto_seed_defaults_on(self):
        assert Settings().auto_seed is True

    def test_auto_seed_can_be_disabled(self):
        assert Settings(auto_seed=False).auto_seed is False

    def test_live_mode_is_a_separate_guard(self):
        """Seeding writes synthetic data; it must never run against real data."""
        settings = Settings(provider_mode=ProviderMode.LIVE, solana_rpc_url="https://rpc.invalid")
        assert settings.provider_mode is not ProviderMode.FIXTURE

    def test_seed_skips_when_not_fixture_mode(self, monkeypatch):
        """The skip happens before any database work."""
        import alphagraph.seed as seed_module

        settings = Settings(provider_mode=ProviderMode.LIVE, solana_rpc_url="https://rpc.invalid")
        monkeypatch.setattr("alphagraph.config.get_settings", lambda: settings)

        called = False

        def _boom(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("must not open a session in live mode")

        monkeypatch.setattr("alphagraph.db.session.session_scope", _boom)
        seed_module.seed_if_empty_blocking()
        assert called is False


@pytest.fixture
def empty_session(tmp_path, world):
    """A genuinely empty database — the state a fresh deploy starts from.

    The suite-wide fixture is already populated, and seeding into it would
    write nothing but duplicates, which is the opposite of what this verifies.
    """
    from sqlalchemy.orm import Session

    from alphagraph.db.session import create_all, reset_engine

    reset_engine()
    engine = create_all(f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}")
    db = Session(bind=engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        reset_engine()


class TestSeedProducesAWorkingSystem:
    """The seeded state is what a fresh deploy shows on the dashboard."""

    def test_empty_database_is_detected(self, empty_session):
        assert is_empty(empty_session) is True

    def test_seed_populates_everything_the_dashboard_reads(self, empty_session, world):
        session = empty_session
        result = asyncio.run(seed_demo(session, world, quick=True))

        assert result.ingest["written"] > 10_000
        assert result.outcomes
        assert result.listings > 0
        assert result.signals_persisted > 0
        assert result.graph_edges >= 1
        assert result.playbook_stages, "dossier playbook should be mined"

        tracked = [
            c
            for c in session.execute(select(Candidate)).scalars()
            if c.status == CandidateStatus.TRACKED
        ]
        assert len(tracked) == 5, "all five planted insiders should end up tracked"

        assert session.execute(select(func.count(Event.event_id))).scalar_one() > 10_000
        assert session.execute(select(func.count(SignalRow.id))).scalar_one() > 0

    def test_quick_seed_is_cheaper_than_the_full_demo(self):
        from alphagraph.seed import FULL_REPLAY_STEP, QUICK_REPLAY_STEP

        assert QUICK_REPLAY_STEP > FULL_REPLAY_STEP


class TestDatabaseUrlNormalization:
    """Hosting platforms hand out driver-less Postgres URLs.

    SQLAlchemy resolves `postgresql://` to psycopg2, which this project does not
    install. The failure is a ModuleNotFoundError at engine creation — after
    config validation has already passed — so it looks like a deploy bug rather
    than a URL problem.
    """

    def test_bare_postgresql_scheme_gets_the_installed_driver(self):
        from alphagraph.db.session import normalize_database_url

        assert (
            normalize_database_url("postgresql://u:p@host:5432/db")
            == "postgresql+psycopg://u:p@host:5432/db"
        )

    def test_legacy_postgres_scheme_is_handled(self):
        """Some platforms still emit the deprecated `postgres://` form."""
        from alphagraph.db.session import normalize_database_url

        assert (
            normalize_database_url("postgres://u:p@host:5432/db")
            == "postgresql+psycopg://u:p@host:5432/db"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "postgresql+psycopg://u:p@host/db",
            "postgresql+asyncpg://u:p@host/db",
            "sqlite+pysqlite:///./local.db",
        ],
    )
    def test_explicit_drivers_are_left_alone(self, url):
        from alphagraph.db.session import normalize_database_url

        assert normalize_database_url(url) == url

    def test_credentials_survive_rewriting(self):
        from alphagraph.db.session import normalize_database_url

        url = "postgresql://user:p%40ss word@host.internal:5432/alphagraph?sslmode=require"
        out = normalize_database_url(url)
        assert out.startswith("postgresql+psycopg://")
        assert out.endswith("user:p%40ss word@host.internal:5432/alphagraph?sslmode=require")
