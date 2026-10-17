"""Database engine and schema. SQLite on a laptop, MySQL on the OCI VM."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
)
from sqlalchemy.engine import Engine

from app.config import SETTINGS

METADATA = MetaData()

facilities = Table(
    "facilities",
    METADATA,
    Column("slug", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("country", String(100), nullable=False),
    Column("profile_json", Text, nullable=False),
)

devices = Table(
    "devices",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("label", String(200), nullable=False),
    Column("raw_input", Text, nullable=False, default=""),
    Column("facts_json", Text, nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
)

verdicts = Table(
    "verdicts",
    METADATA,
    Column("id", String(12), primary_key=True),
    Column("device_id", Integer, nullable=False),
    Column("device_label", String(200), nullable=False),
    Column("facility_slug", String(64), nullable=False),
    Column("facility_name", String(200), nullable=False),
    Column("outcome", String(40), nullable=False),
    Column("verdict_json", Text, nullable=False),
    Column("intel_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime, server_default=func.now()),
)

_engine: Engine | None = None


def get_engine() -> Engine:
    """One engine for the process, created on first use."""
    global _engine
    if _engine is None:
        url = SETTINGS.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
    return _engine


def init_db() -> Engine:
    """Create the tables if they are not there yet."""
    engine = get_engine()
    METADATA.create_all(engine)
    return engine
