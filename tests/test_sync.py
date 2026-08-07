from datetime import datetime, timedelta
import pandas as pd
import pytest

from src.data.sync import (TIMESTAMP_FORMAT, extract_ticker_frame, fetch_all_tickers,
                           fetch_pending_tickers, mark_backfilled, needs_full_refresh,
                           records_from_frame, upsert_records)
from src.database import load_sql


DATES = ['2026-01-02', '2026-01-05', '2026-01-06']


def frame(adj_closes, dates=DATES):
    """Minimal yfinance-shaped frame carrying just the adjusted closes."""
    return pd.DataFrame({'Adj Close': adj_closes}, index=pd.to_datetime(dates))


def store_prices(conn, ticker, adj_closes, dates=DATES):
    conn.executemany(load_sql('upsert_daily_price'),
                     [(ticker, d, 10, 11, 9, 10, adj, 100)
                      for d, adj in zip(dates, adj_closes)])
    conn.commit()


class TestExtractTickerFrame:
    def test_reads_a_multiindex_batch(self):
        columns = pd.MultiIndex.from_product([['AAA', 'BBB'], ['Open', 'Adj Close']])
        data = pd.DataFrame([[1, 2, 3, 4]], columns=columns, index=pd.to_datetime(['2026-01-02']))

        assert extract_ticker_frame(data, 'AAA')['Adj Close'].iloc[0] == 2

    def test_reads_flat_columns(self):
        data = pd.DataFrame({'Open': [1], 'Adj Close': [2]}, index=pd.to_datetime(['2026-01-02']))

        assert extract_ticker_frame(data, 'AAA')['Adj Close'].iloc[0] == 2

    def test_ticker_absent_from_the_batch(self):
        columns = pd.MultiIndex.from_product([['AAA'], ['Adj Close']])
        data = pd.DataFrame([[1]], columns=columns, index=pd.to_datetime(['2026-01-02']))

        assert extract_ticker_frame(data, 'ZZZ').empty

    def test_empty_download(self):
        assert extract_ticker_frame(pd.DataFrame(), 'AAA').empty

    def test_rows_without_an_adjusted_close_are_dropped(self):
        data = pd.DataFrame({'Adj Close': [1.0, None]}, index=pd.to_datetime(DATES[:2]))

        assert len(extract_ticker_frame(data, 'AAA')) == 1

    def test_frame_of_only_empty_rows(self):
        data = pd.DataFrame({'Adj Close': [None]}, index=pd.to_datetime(DATES[:1]))

        assert extract_ticker_frame(data, 'AAA').empty


class TestRecordsFromFrame:
    def test_builds_upsert_tuples_in_column_order(self):
        data = pd.DataFrame(
            {'Open': [1.0], 'High': [2.0], 'Low': [0.5], 'Close': [1.5],
             'Adj Close': [1.4], 'Volume': [10.0]},
            index=pd.to_datetime(['2026-01-02']),
        )

        assert records_from_frame('AAA', data) == [
            ('AAA', '2026-01-02', 1.0, 2.0, 0.5, 1.5, 1.4, 10.0)
        ]


class TestNeedsFullRefresh:
    def test_matching_adjusted_closes_need_nothing(self, seeded_db):
        store_prices(seeded_db, 'C', [9.0, 9.1, 9.2])

        assert needs_full_refresh(seeded_db, 'C', frame([9.0, 9.1, 9.2])) is False

    def test_restated_adjusted_closes_trigger_a_refresh(self, seeded_db):
        store_prices(seeded_db, 'C', [9.0, 9.1, 9.2])
        # A dividend makes Yahoo rewrite the older rows onto a new basis.
        assert needs_full_refresh(seeded_db, 'C', frame([8.94, 9.04, 9.2])) is True

    def test_row_order_does_not_hide_the_drift(self, seeded_db):
        store_prices(seeded_db, 'C', [9.0, 9.1, 9.2])
        shuffled = frame([9.2, 8.94, 9.04], dates=[DATES[2], DATES[0], DATES[1]])

        assert needs_full_refresh(seeded_db, 'C', shuffled) is True

    def test_a_window_that_merely_extends_history_is_fine(self, seeded_db):
        store_prices(seeded_db, 'C', [9.0, 9.1, 9.2])
        extending = frame([9.2, 9.3], dates=[DATES[2], '2026-01-07'])

        assert needs_full_refresh(seeded_db, 'C', extending) is False

    def test_a_gap_between_history_and_the_window_triggers_a_refresh(self, seeded_db):
        # No overlap means the drift check has nothing to compare, and the days in
        # between would never be fetched by a later short update.
        store_prices(seeded_db, 'C', [9.0, 9.1, 9.2])

        assert needs_full_refresh(seeded_db, 'C', frame([5.0], dates=['2026-03-02'])) is True

    def test_a_ticker_with_no_history_needs_nothing(self, seeded_db):
        assert needs_full_refresh(seeded_db, 'C', frame([9.0, 9.1, 9.2])) is False

    def test_an_empty_frame_needs_nothing(self, seeded_db):
        store_prices(seeded_db, 'C', [9.0, 9.1, 9.2])

        assert needs_full_refresh(seeded_db, 'C', frame([], dates=[])) is False

    def test_tiny_float_noise_is_not_drift(self, seeded_db):
        store_prices(seeded_db, 'C', [9.0, 9.1, 9.2])

        assert needs_full_refresh(seeded_db, 'C', frame([9.0000001, 9.1, 9.2])) is False


class TestBackfillStamps:
    def test_everything_is_pending_before_the_first_backfill(self, seeded_db):
        assert sorted(fetch_pending_tickers(seeded_db, 180)) == ['A.TW', 'B.TW', 'C']

    def test_stamped_tickers_are_skipped(self, seeded_db):
        mark_backfilled(seeded_db, ['A.TW', 'C'])
        seeded_db.commit()

        assert fetch_pending_tickers(seeded_db, 180) == ['B.TW']

    def test_all_tickers_are_still_reachable_for_a_forced_run(self, seeded_db):
        mark_backfilled(seeded_db, ['A.TW', 'C'])
        seeded_db.commit()

        assert sorted(fetch_all_tickers(seeded_db)) == ['A.TW', 'B.TW', 'C']

    def test_a_failed_download_stays_pending(self, seeded_db):
        # Nothing was written this batch, so nothing may be stamped.
        mark_backfilled(seeded_db, [])
        seeded_db.commit()

        assert sorted(fetch_pending_tickers(seeded_db, 180)) == ['A.TW', 'B.TW', 'C']

    def test_a_stale_stamp_becomes_pending_again(self, seeded_db):
        stale = (datetime.now() - timedelta(days=200)).strftime(TIMESTAMP_FORMAT)
        mark_backfilled(seeded_db, ['A.TW', 'B.TW', 'C'])
        seeded_db.execute("UPDATE stocks SET last_backfilled = ? WHERE ticker = 'A.TW'", (stale,))
        seeded_db.commit()

        assert fetch_pending_tickers(seeded_db, 180) == ['A.TW']
        assert fetch_pending_tickers(seeded_db, 365) == []

    def test_row_count_does_not_decide_completion(self, seeded_db):
        # A newly listed ticker holds few rows yet is finished once stamped;
        # the old row-count filter re-downloaded these forever.
        store_prices(seeded_db, 'B.TW', [1.0, 1.1, 1.2])
        mark_backfilled(seeded_db, ['A.TW', 'B.TW', 'C'])
        seeded_db.commit()

        assert fetch_pending_tickers(seeded_db, 365) == []

    def test_reseeding_the_stock_list_preserves_the_stamps(self, seeded_db):
        mark_backfilled(seeded_db, ['C'])
        seeded_db.commit()
        before = seeded_db.execute("SELECT last_backfilled FROM stocks WHERE ticker='C'").fetchone()[0]

        seeded_db.execute(load_sql('upsert_stock'), ('C', 'CC renamed', 'US'))
        seeded_db.commit()

        row = seeded_db.execute("SELECT name, last_backfilled FROM stocks WHERE ticker='C'").fetchone()
        assert row == ('CC renamed', before)


class TestUpsertRecords:
    def test_rewriting_a_date_updates_it_in_place(self, seeded_db):
        upsert_records(seeded_db, [('C', '2026-01-02', 1, 2, 0.5, 1.5, 1.4, 10)])
        upsert_records(seeded_db, [('C', '2026-01-02', 9, 9, 9, 9, 8.8, 20)])
        seeded_db.commit()

        rows = seeded_db.execute(
            "SELECT COUNT(*), MAX(adjust_close_price) FROM daily_prices WHERE ticker='C'"
        ).fetchone()
        assert rows == (1, 8.8)
