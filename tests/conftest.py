from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session

from alphagraph.db.session import create_all, get_session_factory, reset_engine
from alphagraph.pipeline import bootstrap
from alphagraph.providers.world import WORLD_END, build_world


@pytest.fixture(scope="session")
def world():
    return build_world()


@pytest.fixture(scope="session")
def engine(world, tmp_path_factory):
    """One fully-populated database for the whole suite.

    Building the world and running the pipeline takes ~15s; doing it per test
    would make the suite too slow to run often, and a suite nobody runs catches
    nothing.
    """
    import os

    url = os.environ.get("ALPHAGRAPH_TEST_DATABASE_URL")
    if not url:
        path = tmp_path_factory.mktemp("db") / "test.db"
        url = f"sqlite+pysqlite:///{path}"
    reset_engine()
    created = create_all(url)

    session = get_session_factory()()
    asyncio.run(bootstrap(session, world, WORLD_END))
    session.commit()
    session.close()

    yield created
    reset_engine()


@pytest.fixture
def session(engine):
    """A session whose writes are rolled back after each test.

    Tests that write (dossiers, edges, alerts, lifecycle transitions) must not
    leak state into their neighbours — and without this, one failed flush
    poisons every subsequent test that shares the session.
    """
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="session")
def loaded_db(engine):
    """Read-only session shared across the suite, for queries that never write."""
    db = get_session_factory()()
    yield db
    db.close()


@pytest.fixture(scope="session")
def as_of():
    return WORLD_END
