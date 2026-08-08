import sys, os
import time
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.database import connect_db
from src.data.sync import (download_ohlcv, extract_ticker_frame, records_from_frame, upsert_records,
                           needs_full_refresh, fetch_all_tickers)


logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

CHUNK_SIZE = 100
BACKFILL_YEARS = 10


def update_stock_data() -> list[str]:
    """Batch-download the latest prices from Yahoo Finance and upsert them into the local database.

    Returns the tickers a few days of new rows cannot bring up to date — those whose
    adjusted closes Yahoo has restated (dividend or split), and those whose stored
    history stops before the download window. Both need a full re-fetch.
    """
    logger.info("Starting daily stock data update...")
    stale_tickers: list[str] = []

    try:
        with connect_db() as conn:
            tickers = fetch_all_tickers(conn)
            total_stocks = len(tickers)
            logger.info(f"Loaded {total_stocks} stocks from the database, starting download...")

            total_success = 0

            # Request in batches to lower peak memory usage and avoid hitting the API rate limit
            for i in range(0, total_stocks, CHUNK_SIZE):
                success_count = 0
                chunk_tickers = tickers[i : i + CHUNK_SIZE]
                logger.info(f"Updating batch {i+1} ~ {min(i+CHUNK_SIZE, total_stocks)}...")

                # Request several days to work around holidays, market closures, or timezone gaps returning empty for today
                data = download_ohlcv(chunk_tickers, period="5d")

                for ticker in chunk_tickers:
                    try:
                        frame = extract_ticker_frame(data, ticker)
                        if frame.empty:
                            logger.warning(f"'{ticker}' download failed or has no valid latest data...")
                            continue

                        # Check before writing: the upsert would overwrite the very
                        # values the comparison relies on.
                        if needs_full_refresh(conn, ticker, frame):
                            stale_tickers.append(ticker)

                        upsert_records(conn, records_from_frame(ticker, frame))

                        success_count += 1
                        total_success += 1

                    except Exception as e:
                        logger.error(f"Failed to process '{ticker}': {e}")
                        continue

                conn.commit()
                logger.info(f"Batch write complete, wrote latest data for {success_count}/{len(chunk_tickers)} stocks!")
                # Rate-limit between batches to avoid getting blocked by yfinance from too many consecutive requests
                if i + CHUNK_SIZE < total_stocks:
                    time.sleep(3)

            logger.info(f"Daily update complete, wrote {total_success}/{total_stocks} stocks to the database!")

    except Exception as e:
        logger.error(f"Daily update failed: {e}")

    return stale_tickers

if __name__ == "__main__":
    stale = update_stock_data()

    # needs_full_refresh() flags a ticker for either reason: a dividend/split restated
    # Adj Close across the whole history, or the stored history has a gap the short
    # update window can't reach back far enough to close. Either way, only a full
    # re-fetch fixes it.
    if stale:
        from scripts.historical_backfill import backfill_history

        logger.info(f"{len(stale)} stocks have a stale or incomplete history, re-fetching in full...")
        backfill_history(BACKFILL_YEARS, tickers=stale)
