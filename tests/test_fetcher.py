import sqlite3
import pytest

from src.data import StockDataFetcher
from src.database import database as database_module
from src.database import load_sql


STOCKS = [('SPCX', 'SpaceX', 'US'), ('2330.TW', 'TSMC', 'TW'), ('5274.TWO', 'Aspeed', 'TW'),
          ('BRK-B', 'Berkshire Hathaway B', 'US')]


@pytest.fixture
def stock_db(tmp_path, monkeypatch):
    """Point the fetcher at a throwaway database seeded with a few tickers."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.executescript(load_sql('schema'))
    conn.executemany(load_sql('upsert_stock'), STOCKS)
    conn.commit()
    conn.close()
    monkeypatch.setattr(database_module, 'DB_PATH', str(path))
    return str(path)


def store_prices(path, rows):
    conn = sqlite3.connect(path)
    conn.executemany(load_sql('upsert_daily_price'), rows)
    conn.commit()
    conn.close()


class TestTickerNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ('SPCX', 'SPCX'),
        ('spcx', 'SPCX'),          # SQLite '=' is case-sensitive
        ('  spcx  ', 'SPCX'),
        ('2330', '2330.TW'),       # bare TW code resolves to its listed suffix
        ('2330.TW', '2330.TW'),
        ('5274', '5274.TWO'),      # OTC codes resolve to .TWO
        ('BRK.B', 'BRK-B'),        # Yahoo writes a US share class with a dash
        ('brk.b', 'BRK-B'),
        ('BRK-B', 'BRK-B'),
    ])
    def test_resolves_to_the_stored_ticker(self, stock_db, raw, expected):
        assert StockDataFetcher(raw).ticker == expected

    def test_unknown_ticker_is_passed_through_upper_cased(self, stock_db):
        assert StockDataFetcher('zzzz').ticker == 'ZZZZ'

    @pytest.mark.parametrize("raw", ['9999.TW', '9999.TWO', '000001.SS', '399001.SZ', '7203.T', 'FOO.B'])
    def test_an_unseeded_dotted_ticker_is_passed_through_untouched(self, stock_db, raw):
        # A dot is far more often a market suffix than a US share class. Rewriting it to a
        # dash on a miss would corrupt every non-US market, the seeded SS/SZ indices included.
        assert StockDataFetcher(raw).ticker == raw


class TestExistenceLookup:
    def test_known_ticker(self, stock_db):
        assert StockDataFetcher('spcx').check_stock_exist() is True

    def test_unknown_ticker(self, stock_db):
        assert StockDataFetcher('zzzz').check_stock_exist() is False

    def test_name_falls_back_to_the_ticker(self, stock_db):
        assert StockDataFetcher('spcx').fetch_stock_name() == 'SpaceX'
        assert StockDataFetcher('zzzz').fetch_stock_name() == 'ZZZZ'


class TestAdjustedPriceReconstruction:
    def test_open_high_low_are_scaled_by_the_adjustment_ratio(self, stock_db):
        # AdjClose 50 against Close 100 halves the whole bar.
        store_prices(stock_db, [('SPCX', '2026-01-02', 100, 110, 90, 100, 50, 1_000)])

        row = StockDataFetcher('SPCX').fetch_historical_data(days=3650).iloc[0]

        assert (row['Open'], row['High'], row['Low'], row['Close']) == (50, 55, 45, 50)

    def test_an_unadjusted_row_is_unchanged(self, stock_db):
        store_prices(stock_db, [('SPCX', '2026-01-02', 100, 110, 90, 100, 100, 1_000)])

        row = StockDataFetcher('SPCX').fetch_historical_data(days=3650).iloc[0]

        assert (row['Open'], row['High'], row['Low'], row['Close']) == (100, 110, 90, 100)

    def test_a_zero_close_keeps_the_bar_raw_instead_of_producing_inf(self, stock_db):
        # There is no ratio to apply, and adjusting only the close would push it
        # outside the bar's own High/Low.
        store_prices(stock_db, [('SPCX', '2026-01-02', 100, 110, 90, 0, 50, 1_000)])

        row = StockDataFetcher('SPCX').fetch_historical_data(days=3650).iloc[0]

        assert (row['Open'], row['High'], row['Low'], row['Close']) == (100, 110, 90, 0)

    def test_a_missing_adjusted_close_falls_back_to_the_raw_close(self, stock_db):
        store_prices(stock_db, [('SPCX', '2026-01-02', 100, 110, 90, 100, None, 1_000)])

        row = StockDataFetcher('SPCX').fetch_historical_data(days=3650).iloc[0]

        assert (row['Open'], row['High'], row['Low'], row['Close']) == (100, 110, 90, 100)

    def test_rows_with_missing_prices_are_dropped(self, stock_db):
        store_prices(stock_db, [
            ('SPCX', '2026-01-02', 100, 110, 90, 100, 100, 1_000),
            ('SPCX', '2026-01-05', None, 110, 90, 100, 100, 1_000),
        ])

        assert len(StockDataFetcher('SPCX').fetch_historical_data(days=3650)) == 1

    def test_missing_ticker_returns_an_empty_frame(self, stock_db):
        assert StockDataFetcher('SPCX').fetch_historical_data(days=3650).empty
