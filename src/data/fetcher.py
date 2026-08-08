import yfinance as yf
import pandas as pd
import twstock
import pytz
import logging
from datetime import datetime, timedelta

from src.database import connect_db, load_sql


logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)

TW_CODES = twstock.codes


class StockDataFetcher:
    """Stock query service that unifies three data sources: SQLite / yfinance / twstock."""
    def __init__(self, ticker: str):
        self._raw_ticker = ticker
        self.ticker = self._format_ticker(ticker)
        self.historical_data = None
        self.intraday_data = None

    def _format_ticker(self, ticker: str) -> str:
        """Normalize user input into Yahoo Finance format. Lookup order: local DB -> twstock -> raw input."""
        ticker = ticker.strip().upper()
        try:
            # Strip any existing suffix
            base_code = ticker.split(".")[0]

            # First try to find a matching full ticker in the database.
            # The dash form is a candidate only: Yahoo writes a US share class as BRK-B and seed_stocks stores it that way
            candidates = [ticker, f"{base_code}.TW", f"{base_code}.TWO"]
            if '.' in ticker:
                candidates.append(ticker.replace('.', '-'))
            with connect_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT ticker FROM stocks WHERE ticker IN ({','.join('?' * len(candidates))}) LIMIT 1",
                    candidates,
                )
                result = cursor.fetchone()
                if result:
                    return result[0]

            # If not found in the database, fall back to a twstock lookup
            if base_code in TW_CODES:
                market = TW_CODES[base_code].market
                if market == '上市':
                    return f"{base_code}.TW"
                if market == '上櫃':
                    return f"{base_code}.TWO"
                # Emerging-board and other boards have no Yahoo suffix to map to
            return ticker

        except Exception as e:
            logger.warning(f"Failed to process ticker '{ticker}': {e}")
            return ticker

    def check_stock_exist(self) -> bool:
        """Check whether the stock exists in the database."""
        try:
            with connect_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM stocks WHERE ticker = ? LIMIT 1", (self.ticker,))
                return cursor.fetchone() is not None

        except Exception as e:
            logger.error(f"Failed to check whether the stock exists: {e}")
            return False

    def fetch_stock_name(self) -> str:
        """Look up the stock name from the local database."""
        try:
            with connect_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM stocks WHERE ticker = ?", (self.ticker,))
                result = cursor.fetchone()

            if result:
                return result[0]

            return self.ticker

        except Exception as e:
            logger.warning(f"Failed to get name for '{self.ticker}': {e}")
            return self.ticker

    def fetch_historical_data(self, days: int = 365) -> pd.DataFrame:
        """Query daily historical data from SQLite and apply dividend/split adjustment."""
        try:
            cutoff_date = (datetime.now() - timedelta(days)).strftime('%Y-%m-%d')

            with connect_db() as conn:
                self.historical_data = pd.read_sql_query(
                    load_sql('select_historical_prices'),
                    conn,
                    params=(self.ticker, cutoff_date),
                    parse_dates=['date'],
                    index_col='date'
                )

            if self.historical_data.empty:
                logger.info(f"No historical data for '{self.ticker}' in the database...")
            else:
                # Back out Open/High/Low from the AdjClose/Close ratio to remove chart gaps caused by dividends and splits.
                # A row with a missing or non-positive close has no usable ratio, so it keeps its raw
                # values throughout: adjusting only the close would put it outside its own High/Low.
                close = self.historical_data['Close']
                adj_close = self.historical_data['AdjClose']
                usable = (close > 0) & adj_close.notna()
                adj_ratio = (adj_close / close).where(usable, 1.0)
                self.historical_data['Open'] = self.historical_data['Open'] * adj_ratio
                self.historical_data['High'] = self.historical_data['High'] * adj_ratio
                self.historical_data['Low'] = self.historical_data['Low'] * adj_ratio
                self.historical_data['Close'] = adj_close.where(usable, close)

                # Incomplete rows would silently poison indicators and charts downstream
                self.historical_data = self.historical_data.dropna(subset=['Open', 'High', 'Low', 'Close'])

                logger.info(f"Loaded {len(self.historical_data)} historical rows for '{self.ticker}' from the database!")

            return self.historical_data

        except Exception as e:
            logger.error(f"Failed to query historical data for '{self.ticker}': {e}")
            return pd.DataFrame()

    def fetch_intraday_data(self) -> pd.DataFrame:
        """Fetch today's 1-minute intraday data via yfinance."""
        try:
            stock = yf.Ticker(self.ticker)
            data = stock.history(period="1d", interval="1m", actions=False)

            if data.empty:
                logger.warning(f"Failed to download data for '{self.ticker}'...")
                return data

            # Drop non-OHLCV columns such as Dividends / Stock Splits
            core_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            data = data[core_cols]

            # Remove rows containing NaN
            self.intraday_data = data.dropna(subset=['Open', 'High', 'Low', 'Close'])

            if not self.intraday_data.empty:
                logger.info(f"Downloaded {len(self.intraday_data)} intraday rows for '{self.ticker}'.")

            return self.intraday_data

        except Exception as e:
            logger.error(f"Error while downloading data for '{self.ticker}': {e}")
            return pd.DataFrame()

    def fetch_latest_time(self) -> pd.Timestamp:
        """Return the latest data timestamp (Asia/Taipei). Priority: intraday > historical > system time."""
        if self.intraday_data is not None and not self.intraday_data.empty:
            latest_time = self.intraday_data.index[-1]
        elif self.historical_data is not None and not self.historical_data.empty:
            latest_time = self.historical_data.index[-1]
        else:
            latest_time = pd.Timestamp.now(tz=pytz.timezone('Asia/Taipei'))

        if latest_time.tz is None:
            latest_time = latest_time.tz_localize('UTC')

        return latest_time.astimezone(pytz.timezone('Asia/Taipei'))

    def get_data_count(self) -> dict:
        """Return the record count and date range for this stock in the database."""
        try:
            with connect_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*), MIN(date), MAX(date) FROM daily_prices WHERE ticker = ?",
                    (self.ticker,)
                )
                result = cursor.fetchone()

            if result and result[0] > 0:
                return {
                    "total_records": result[0],
                    "earliest_date": result[1],
                    "latest_date": result[2]
                }
        except Exception as e:
            logger.error(f"Failed to get data statistics for '{self.ticker}': {e}")

        return {"total_records": 0, "earliest_date": None, "latest_date": None}

    def _debug_info(self) -> dict:
        hist_data = self.fetch_historical_data()
        intra_data = self.fetch_intraday_data()
        data_count = self.get_data_count()
        print("-" * 50)

        return {
            "Ticker": self.ticker,
            "Name": self.fetch_stock_name(),
            "In database": self.check_stock_exist(),
            "DB total records": data_count["total_records"],
            "DB date range": f"{data_count['earliest_date']} ~ {data_count['latest_date']}",
            "Historical rows": len(hist_data),
            "Intraday rows": len(intra_data),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    while True:
        print("-" * 50)
        ticker = input("Enter the ticker (-1 to exit): ")
        print("-" * 50)
        if ticker == "-1":
            break
        s = StockDataFetcher(ticker)
        debug_report = s._debug_info()
        for key, value in debug_report.items():
            print(f"╎ {key}: {value}")
