"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from alphagraph.config import get_settings
from alphagraph.db.models import Base

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def get_engine(url: str | None = None, echo: bool = False) -> Engine:
    global _engine, _factory
    if _engine is None or url is not None:
        target = url or get_settings().database_url
        connect_args = {"check_same_thread": False} if target.startswith("sqlite") else {}
        _engine = create_engine(target, echo=echo, future=True, connect_args=connect_args)
        if target.startswith("sqlite"):
            # SQLite ignores FKs unless asked; without this, cascade deletes and
            # referential integrity silently do nothing in local development.
            @event.listens_for(_engine, "connect")
            def _fk_on(dbapi_conn, _record):  # type: ignore[no-untyped-def]
                dbapi_conn.execute("PRAGMA foreign_keys=ON")

        _factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _factory is None:
        get_engine()
    assert _factory is not None
    return _factory


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(url: str | None = None) -> Engine:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


def reset_engine() -> None:
    """Drop cached engine/factory. Used by tests switching databases."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _factory = None
