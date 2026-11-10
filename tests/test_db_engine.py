"""The engine pings a pooled connection before use, so a connection the database
dropped while idle is replaced instead of failing the next request."""
from app.data import db


def test_engine_pings_pooled_connections_before_use(monkeypatch):
    monkeypatch.setattr(db, "_engine", None)
    engine = db.get_engine()
    try:
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()
        monkeypatch.setattr(db, "_engine", None)
