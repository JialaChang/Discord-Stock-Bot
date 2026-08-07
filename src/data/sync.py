import logging
import sqlite3
from datetime import datetime, timedelta
from typing import cast
import pandas as pd
import yfinance as yf

from src.database import load_sql


logger = logging.getLogger(__name__)

TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'  # ISO order, so string comparison is time comparison

# Yahoo rewrites Adj Close for the whole history on every dividend and split,
# so a stored value that no longer matches a freshly downloaded one for the same date
# means everything older than the download window is on a stale basis.
ADJ_DRIFT_TOLERANCE = 1e-4  # relative

OHLCV_COLUMNS = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']


def download_ohlcv(tickers: list[str], period: str) -> pd.DataFrame:
    """Download unadjusted OHLCV plus Adj Close for a batch of tickers.

    Network errors and rate limits are caught here rather than left to propagate:
    both callers batch this over many chunks, and one failed chunk shouldn't abort
    every later chunk. `extract_ticker_frame` already treats an empty frame as "no
    data for this ticker", so returning one on failure needs no change downstream.
    """
    try:
        data = yf.download(
            tickers,
            period=period,
            interval="1d",
            group_by='ticker',
            actions=False,
            progress=False,
            auto_adjust=False,
            threads=True,
        )
        return data if data is not None else pd.DataFrame()
    except Exception as e:
        logger.warning(f"Download failed for batch of {len(tickers)} tickers: {e}")
        return pd.DataFrame()


def extract_ticker_frame(data: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Pull one ticker's rows out of a yfinance batch download.

    Returns an empty frame so callers check `.empty`.

    yfinance returns flat columns for a single ticker and a MultiIndex for a batch,
    but which one you get also depends on the version, so branch on the frame itself
    rather than on how many tickers were requested.
    """
    if data is None or data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        if ticker not in data.columns.get_level_values(0):
            return pd.DataFrame()
        # A MultiIndex top-level lookup returns a DataFrame cross-section at runtime,
        # but the type stubs only model a plain-Hashable key as a single-column Series.
        frame = cast(pd.DataFrame, data[ticker])
    else:
        frame = data

    if 'Adj Close' not in frame.columns:
        return pd.DataFrame()

    return frame.dropna(subset=['Adj Close'])


def records_from_frame(ticker: str, frame: pd.DataFrame) -> list[tuple]:
    """Convert a ticker's OHLCV frame into upsert parameter tuples."""
    return [
        (
            ticker,
            pd.Timestamp(index).strftime('%Y-%m-%d'), # pyright: ignore[reportArgumentType]
            float(row['Open']), # pyright: ignore[reportArgumentType]
            float(row['High']), # pyright: ignore[reportArgumentType]
            float(row['Low']), # pyright: ignore[reportArgumentType]
            float(row['Close']), # pyright: ignore[reportArgumentType]
            float(row['Adj Close']), # pyright: ignore[reportArgumentType]
            float(row['Volume']), # pyright: ignore[reportArgumentType]
        )
        for index, row in frame.iterrows()
    ]


def upsert_records(conn: sqlite3.Connection, records: list[tuple]) -> None:
    """Bulk-write price rows, updating in place on (ticker, date) conflicts."""
    conn.cursor().executemany(load_sql('upsert_daily_price'), records)


def fetch_all_tickers(conn: sqlite3.Connection) -> list[str]:
    """Every ticker in the stock master table."""
    return [row[0] for row in conn.execute("SELECT ticker FROM stocks")]


def fetch_pending_tickers(conn: sqlite3.Connection, max_age_days: int) -> list[str]:
    """Tickers never backfilled, or last backfilled longer than `max_age_days` ago."""
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime(TIMESTAMP_FORMAT)
    return [
        row[0] for row in conn.execute(
            "SELECT ticker FROM stocks WHERE last_backfilled IS NULL OR last_backfilled < ?",
            (cutoff,),
        )
    ]


def mark_backfilled(conn: sqlite3.Connection, tickers: list[str]) -> None:
    """Stamp tickers whose history was written successfully.

    Only stamp on success: an unstamped ticker is retried on the next run, which is
    how a failed download recovers on its own.
    """
    if not tickers:
        return
    now = datetime.now().strftime(TIMESTAMP_FORMAT)
    conn.cursor().executemany(
        "UPDATE stocks SET last_backfilled = ? WHERE ticker = ?",
        [(now, ticker) for ticker in tickers],
    )


def needs_full_refresh(conn: sqlite3.Connection, ticker: str, frame: pd.DataFrame) -> bool:
    """Report whether a short update leaves this ticker's stored history inconsistent.

    Two cases, both of which a few days of new rows cannot repair:

    * **Restated adjusted closes.** A dividend or split makes Yahoo rewrite Adj Close
      all the way back through the history. Rows inside the download window get
      corrected by the upsert, but every older row keeps the pre-event basis, which
      shows up as a fake gap in the adjusted price series.
    * **A hole in the history.** If the updater has not run for longer than the
      download window, the days in between are missing entirely, and no later run
      will ever go back for them.

    Note the drift check only sees dates the download and the database have in
    common, which is why the hole check has to come first: with no overlap there is
    nothing to compare, and that is exactly when the data is most likely stale.
    """
    dates = sorted(index.strftime('%Y-%m-%d') for index in frame.index)
    if not dates:
        return False

    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM daily_prices WHERE ticker = ?", (ticker,))
    row = cursor.fetchone()
    latest_stored = row[0] if row else None
    if latest_stored is None:
        return False  # Nothing stored yet; a plain insert is all this ticker needs

    if latest_stored < dates[0]:
        logger.info(
            f"'{ticker}' history ends at {latest_stored} but the download starts at {dates[0]};"
            f"the days in between are missing..."
        )
        return True

    placeholders = ','.join('?' * len(dates))
    cursor.execute(
        f"SELECT date, adjust_close_price FROM daily_prices WHERE ticker = ? AND date IN ({placeholders})",
        (ticker, *dates),
    )
    stored = {row[0]: row[1] for row in cursor.fetchall() if row[1] is not None}

    fresh = {pd.Timestamp(index).strftime('%Y-%m-%d'): float(value) for index, value in frame['Adj Close'].items()} # pyright: ignore[reportArgumentType]
    for date, stored_adj in stored.items():
        fresh_adj = fresh.get(date)
        if fresh_adj is None or stored_adj <= 0:
            continue
        if abs(fresh_adj - stored_adj) / stored_adj > ADJ_DRIFT_TOLERANCE:
            logger.info(
                f"'{ticker}' adjusted close for {date} changed {stored_adj:.6f} -> {fresh_adj:.6f};"
                f"older rows are on a stale basis."
            )
            return True
    return False
