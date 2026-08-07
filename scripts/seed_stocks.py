import sys, os
import sqlite3
import twstock
import pandas as pd
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.database import DB_PATH, init_database, load_sql


logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def import_taiwan_stocks(conn: sqlite3.Connection):
    """Import all Taiwan listed/OTC stocks from the twstock package's static list."""
    logger.info("Importing Taiwan stocks...")
    cursor = conn.cursor()
    count = 0

    for code, info in twstock.codes.items():
        # Filter out unrelated financial products; keep only regular stocks and ETFs
        if info.type in ['股票', 'ETF']:
            if info.market == '上市':
                ticker = f"{code}.TW"
            elif info.market == '上櫃':
                ticker = f"{code}.TWO"
            else:
                continue
            cursor.execute(load_sql('upsert_stock'), (ticker, info.name, 'TW'))
            count += 1

    conn.commit()
    logger.info(f"Imported {count} Taiwan stocks!")


def import_us_stocks(conn: sqlite3.Connection):
    """Scrape the constituents of the three major US indices (S&P 500 / DJIA / NASDAQ 100) from Wikipedia's public tables."""
    logger.info("Importing US stocks...")
    cursor = conn.cursor()
    total_count = 0

    # Map each target Wikipedia URL to the table index within its DOM
    urls = {
        "S&P 500": {
            "url": 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
            "table_index": 0,
            "ticker_col": 'Symbol',
            "name_col": 'Security'
        },
        "Dow Jones": {
            "url": 'https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average',
            "table_index": 1,
            "ticker_col": 'Symbol',
            "name_col": 'Company',
        },
        "Nasdaq 100": {
            "url": 'https://en.wikipedia.org/wiki/Nasdaq-100',
            "table_index": 5,
            "ticker_col": 'Ticker',
            "name_col": 'Company'
        }
    }

    for index_name, config in urls.items():
        try:
            logger.info(f"Scraping {index_name} constituents...")
            # Wikipedia blocks default bots, so spoof a User-Agent header to get around it
            tables = pd.read_html(
                config["url"],
                storage_options={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            data = tables[config["table_index"]]

            # Clean the data: convert US special tickers like BRK.B into the Yahoo-compatible BRK-B
            records = [
                (str(row[config["ticker_col"]]).replace('.', '-'), str(row[config["name_col"]]), 'US')
                for _, row in data.iterrows()
            ]

            cursor.executemany(load_sql('upsert_stock'), records)

            conn.commit()
            count = len(records)
            total_count += count
            logger.info(f"Imported {count} stocks from {index_name}...")

        except Exception as e:
            logger.error(f"Failed to import {index_name}: {e}")

    logger.info(f"Imported {total_count} US stocks!")


def import_global_indices(conn: sqlite3.Connection):
    """Hard-code the major global market indices."""
    logger.info("Importing major global and core market indices...")
    cursor = conn.cursor()

    indices = {
        # US and volatility benchmarks
        '^GSPC': 'S&P 500 Index',
        '^DJI': 'Dow Jones Industrial Average',
        '^IXIC': 'NASDAQ Composite Index',
        '^SOX': 'PHLX Semiconductor Index',
        '^VIX': 'CBOE Volatility Index',

        # Asia-Pacific indices
        '^TWII': 'TAIEX (Taiwan Weighted Index)',
        '^TWOII': 'Taipei Exchange (TPEx) Index',
        '^HSI': 'Hang Seng Index',
        '000001.SS': 'SSE Composite Index',
        '399001.SZ': 'SZSE Component Index',
        '^KS11': 'KOSPI Composite Index',
        '^N225': 'Nikkei 225 Index',

        # European indices
        '^FTSE': 'FTSE 100 Index',
        '^GDAXI': 'DAX Index',
        '^FCHI': 'CAC 40 Index',
        '^STOXX50E': 'EURO STOXX 50 Index'
    }

    records = [(ticker, name, 'INDEX') for ticker, name in indices.items()]

    cursor.executemany(load_sql('upsert_stock'), records)

    conn.commit()
    logger.info(f"Imported {len(records)} global market indices!")

if __name__ == "__main__":
    # Ensure the database exists
    init_database()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            import_taiwan_stocks(conn)
            import_us_stocks(conn)
            import_global_indices(conn)
            logger.info("Stock data import complete!")
    except Exception as e:
        logger.error(f"Error during stock data import: {e}")
