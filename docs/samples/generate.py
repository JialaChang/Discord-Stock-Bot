"""
Regenerate the sample reports linked from the README.
Writes to a throwaway database, so the developer's own stock_data.db is left untouched.
"""
import os
import shutil
import tempfile

from src.data import StockDataFetcher
from src.data.sync import download_ohlcv, extract_ticker_frame, records_from_frame, upsert_records
from src.database import connect_db, init_database, load_sql
from src.database import database as database_module
from src.database.database import _export_prices_html, get_daily_prices
from src.quant import BacktestEngine, EMAStrategy
from src.utils import html_report

SAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKER, NAME, MARKET = '2330.TW', '台積電', 'TW'
BACKTEST_PERIOD_DAYS = 730  # 2y
PRICE_ROWS = 100


def seed(db_path: str) -> None:
    """Download `TICKER` into a throwaway database via the production write path."""
    database_module.DB_PATH = db_path
    init_database()
    with connect_db() as conn:
        conn.execute(load_sql('upsert_stock'), (TICKER, NAME, MARKET))
        conn.commit()

    frame = extract_ticker_frame(download_ohlcv([TICKER], '5y'), TICKER)
    if frame.empty:
        raise SystemExit(f"No data returned for '{TICKER}'; check the network and retry...")
    with connect_db() as conn:
        upsert_records(conn, records_from_frame(TICKER, frame))
        conn.commit()
    print(f"Downloaded {len(frame)} rows for '{TICKER}'!")


def backtest_sample() -> None:
    fetcher = StockDataFetcher(TICKER)
    engine = BacktestEngine(EMAStrategy())
    data = fetcher.fetch_historical_data(days=engine.required_history_days(BACKTEST_PERIOD_DAYS))
    result = engine.run(TICKER, data)
    shutil.copy(engine.export_backtest_result_html(result), os.path.join(SAMPLE_DIR, 'backtest.html'))


def prices_sample() -> None:
    _export_prices_html(TICKER, get_daily_prices(TICKER, limit=PRICE_ROWS))
    newest = max((os.path.join(html_report._EXPORT_DIR, f)
                  for f in os.listdir(html_report._EXPORT_DIR)), key=os.path.getmtime)
    shutil.copy(newest, os.path.join(SAMPLE_DIR, 'prices.html'))


if __name__ == '__main__':
    print('Regenerating sample reports...')
    with tempfile.TemporaryDirectory() as tmp:
        html_report._EXPORT_DIR = os.path.join(tmp, 'exports')  # keep exports/ clean
        seed(os.path.join(tmp, 'sample.db'))
        backtest_sample()
        prices_sample()
