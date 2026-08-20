"""Database connection and session management."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def _database_url() -> str:
    return (
        f"postgresql+psycopg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_database}"
    )


# Lazy connection: create_engine() does not open a connection, so importing
# this module is safe even when Postgres isn't running yet.
_engine = create_engine(
    _database_url(),
    pool_size=settings.postgres_pool_size,
    max_overflow=settings.postgres_max_overflow,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create all tables that do not already exist."""
    from . import models  # noqa: F401 - import registers models on Base.metadata

    Base.metadata.create_all(bind=_engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context-managed session for use outside FastAPI's dependency system."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    with get_session() as session:
        yield session
