"""Shared fixtures.

Every test here is deterministic and offline: no yfinance calls, and no reliance on
the developer's `stock_data.db`, which is rebuilt from scratch periodically.
"""
import sqlite3
import pandas as pd
import pytest

from src.database import load_sql


@pytest.fixture
def db():
    """In-memory database carrying the production schema."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(load_sql('schema'))
    yield conn
    conn.close()


@pytest.fixture
def seeded_db(db):
    """Database with three tickers and no backfill stamps."""
    db.executemany(load_sql('upsert_stock'),
                   [('A.TW', 'AA', 'TW'), ('B.TW', 'BB', 'TW'), ('C', 'CC', 'US')])
    db.commit()
    return db


def make_ohlcv(closes, *, opens=None, highs=None, lows=None, start='2026-01-01'):
    """Build a daily OHLCV frame indexed by business days.

    Unspecified opens track the close, and highs/lows sit 1% away from it, which
    keeps rows clear of the 15% stop-loss unless a test sets them deliberately.
    """
    index = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({
        'Open': list(opens) if opens is not None else list(closes),
        'High': list(highs) if highs is not None else [c * 1.01 for c in closes],
        'Low': list(lows) if lows is not None else [c * 0.99 for c in closes],
        'Close': list(closes),
        'Volume': [1_000] * len(closes),
    }, index=index)


@pytest.fixture
def ohlcv():
    return make_ohlcv
