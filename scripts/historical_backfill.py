import sys, os
import time
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.database import connect_db
from src.data.sync import download_ohlcv, extract_ticker_frame, records_from_frame, upsert_records, fetch_all_tickers, fetch_pending_tickers, mark_backfilled


logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

CHUNK_SIZE = 50
BACKFILL_YEARS = 10
REFRESH_AFTER_DAYS = 180  # Safety net: redo a backfill this long after the last one


def backfill_history(period: int, tickers: list[str] | None = None, force: bool = False):
    """Historical backfill: fetch long-period history when the DB is first built or new tickers are added.

    Tickers are skipped by `stocks.last_backfilled`, not by how many rows they have:
    a row count cannot tell a fully backfilled ticker from a recently listed one, so
    it both marks partial histories as done and re-downloads young tickers forever.

    `tickers` re-fetches exactly those, bypassing the skip filter. The daily updater
    uses it to restate a history after a dividend or split. `force` re-fetches
    everything regardless of when it was last backfilled.
    """
    logger.info(f"Starting historical backfill for the past {period} years...")

    try:
        with connect_db() as conn:
            if tickers is None:
                tickers = fetch_all_tickers(conn) if force else fetch_pending_tickers(conn, REFRESH_AFTER_DAYS)
            total_stocks = len(tickers)
            total_success = 0

            for i in range(0, total_stocks, CHUNK_SIZE):
                chunk_tickers = tickers[i : i + CHUNK_SIZE]
                logger.info(f"Downloading batch {i+1} ~ {min(i + CHUNK_SIZE, total_stocks)}...")

                data = download_ohlcv(chunk_tickers, period=f"{period}y")

                written = []
                for ticker in chunk_tickers:
                    try:
                        frame = extract_ticker_frame(data, ticker)
                        if frame.empty:
                            logger.warning(f"'{ticker}' download failed or has no valid historical data...")
                            continue

                        # Use executemany for an efficient bulk parameterized write
                        upsert_records(conn, records_from_frame(ticker, frame))
                        written.append(ticker)
                        total_success += 1

                    except Exception as e:
                        logger.error(f"Failed to process '{ticker}': {e}")
                        continue

                # Stamp and write in the same transaction, so a ticker is never
                # recorded as backfilled without its prices landing too.
                mark_backfilled(conn, written)
                conn.commit()
                logger.info(f"Batch write complete, wrote historical data for {len(written)}/{len(chunk_tickers)} stocks!")
                # Long-period payloads are large, so use a longer sleep to avoid the YF server refusing connections
                if i + CHUNK_SIZE < total_stocks:
                    time.sleep(10)

            logger.info(f"Historical backfill complete, wrote historical data for {total_success}/{total_stocks} stocks!")

    except Exception as e:
        logger.error(f"Historical backfill failed: {e}")

if __name__ == "__main__":
    backfill_history(BACKFILL_YEARS)
