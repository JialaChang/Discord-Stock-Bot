import sqlite3
import pytest

from src.database import connect_db, get_stock, load_sql
from src.database import database as database_module


PRICE_ROW = ('GHOST', '2026-01-02', 10, 11, 9, 10, 10, 100)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point connect_db at a throwaway database carrying the production schema."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.executescript(load_sql('schema'))
    conn.commit()
    conn.close()
    monkeypatch.setattr(database_module, 'DB_PATH', str(path))
    return str(path)


def stored_stocks(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute('SELECT ticker FROM stocks').fetchall()
    finally:
        conn.close()


class TestConnectDb:
    def test_closes_the_connection_on_exit(self, db_path):
        with connect_db() as conn:
            pass

        # A closed connection is the point: sqlite3's own __exit__ ends the transaction
        # but leaves the connection open, and its reference cycles keep it alive until
        # the cyclic collector runs.
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute('SELECT 1')

    def test_closes_the_connection_when_the_body_raises(self, db_path):
        with pytest.raises(RuntimeError):
            with connect_db() as conn:
                raise RuntimeError('boom')

        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute('SELECT 1')

    def test_commits_on_a_clean_exit(self, db_path):
        with connect_db() as conn:
            conn.execute(load_sql('upsert_stock'), ('A.TW', 'AA', 'TW'))

        assert stored_stocks(db_path) == [('A.TW',)]

    def test_rolls_back_when_the_body_raises(self, db_path):
        with pytest.raises(RuntimeError):
            with connect_db() as conn:
                conn.execute(load_sql('upsert_stock'), ('A.TW', 'AA', 'TW'))
                raise RuntimeError('boom')

        assert stored_stocks(db_path) == []

    def test_enforces_foreign_keys(self, db_path):
        # PRAGMA foreign_keys is per connection, so it has to be set on every one:
        # without it daily_prices would accept a ticker absent from stocks.
        with pytest.raises(sqlite3.IntegrityError):
            with connect_db() as conn:
                conn.execute(load_sql('upsert_daily_price'), PRICE_ROW)


class TestCrudPersists:
    """The helpers rely on connect_db's exit to commit; none of them commit themselves."""

    def test_insert_stock_persists(self, db_path):
        database_module.insert_stock('A.TW', 'AA', 'TW')
        stock = get_stock('A.TW')

        assert stock is not None
        assert stock['name'] == 'AA'
