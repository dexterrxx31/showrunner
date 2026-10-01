"""SQLAlchemy engine + session management.

One code path serves both dev/test (SQLite) and production (Postgres); the
only difference is the DATABASE_URL. Engine and sessionmaker are cached but
resettable so tests can point at a temp database.
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app import config

_engine = None
_SessionLocal: sessionmaker | None = None


class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


def get_engine():
    global _engine
    if _engine is None:
        url = config.DATABASE_URL
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            if path and "/" in path:
                from pathlib import Path

                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, connect_args=_connect_args(url), future=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), expire_on_commit=False, future=True, class_=Session
        )
    return _SessionLocal


def reset_engine() -> None:
    """Drop cached engine/sessionmaker: used by tests switching databases."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def init_db() -> None:
    from app import models  # noqa: F401  # register tables

    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope():
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
