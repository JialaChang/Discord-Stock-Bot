import asyncio
import logging
from fastapi import APIRouter, HTTPException

from src.data import StockDataFetcher
from src.bot import build_snapshot
from src.quant import InsufficientDataError
from src.api import SnapshotResponse


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

@router.get("/stock/{ticker}", response_model=SnapshotResponse)
async def get_stock_snapshot(ticker: str):
    try:
        # asyncio.to_thread offloads the blocking SQLite/yfinance calls to a thread pool so the event loop is not blocked.
        # The constructor itself hits SQLite to normalize the ticker, so it is offloaded too.
        fetcher = await asyncio.to_thread(StockDataFetcher, ticker)
        stock_name = await asyncio.to_thread(fetcher.fetch_stock_name)
        stock_ticker = fetcher.ticker

        # Run requests concurrently to improve responsiveness
        history_data, intraday_data = await asyncio.gather(
            asyncio.to_thread(fetcher.fetch_historical_data),
            asyncio.to_thread(fetcher.fetch_intraday_data)
        )

        if history_data.empty:
            logger.warning(f"Failed to retrieve data for '{stock_ticker}'...")
            raise HTTPException(status_code=404, detail=(f"Could not retrieve data for '{stock_ticker}'. "
                                                         "Check that the ticker is correct, or it may not be in the database yet."))
        # Intraday is optional
        if intraday_data.empty:
            logger.info(f"No intraday data for '{stock_ticker}', showing the daily chart only.")

        latest_time = await asyncio.to_thread(fetcher.fetch_latest_time)

        snapshot = await asyncio.to_thread(
            build_snapshot, stock_ticker, stock_name, history_data, intraday_data, latest_time
        )
        return SnapshotResponse.model_validate(snapshot)

    # Must precede the catch-all: the 404 raised above is an Exception too, and would
    # otherwise be swallowed and re-reported as a 500.
    except HTTPException:
        raise

    except InsufficientDataError as e:
        logger.warning(f"Insufficient data for '{ticker}': {e}")
        raise HTTPException(status_code=422, detail=f"Not enough historical data for '{ticker}'. {e}")

    except Exception as e:
        # Log the cause, but keep it out of the response: it may name internal paths.
        logger.error(f"Error building the snapshot for '{ticker}': {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred, please try again later.")