import sqlite3
import twstock
import pandas as pd
import logging

from src.database import connect_db, init_database, load_sql


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


# Each of these articles marks its constituents table with id="constituents"; select on
# that, never on a positional index. A positional index breaks whenever an article gains
# an unrelated table, and it already has: the Nasdaq-100 constituents moved out to their
# own list page, leaving the original article with no constituents table at all.
#
# `min_rows` turns a silent structural change into a visible failure rather than a
# half-seeded stocks table.
WIKIPEDIA_INDICES = {
    "S&P 500": {
        "url": 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        "ticker_col": 'Symbol',
        "name_col": 'Security',
        "min_rows": 450,
    },
    "Dow Jones": {
        "url": 'https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average',
        "ticker_col": 'Symbol',
        "name_col": 'Company',
        "min_rows": 30,
    },
    "Nasdaq 100": {
        "url": 'https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies',
        "ticker_col": 'Ticker',
        "name_col": 'Company',
        "min_rows": 95,
    },
}


def import_us_stocks(conn: sqlite3.Connection):
    """Scrape the constituents of the three major US indices (S&P 500 / DJIA / NASDAQ 100) from Wikipedia's public tables."""
    logger.info("Importing US stocks...")
    cursor = conn.cursor()
    total_count = 0

    for index_name, config in WIKIPEDIA_INDICES.items():
        try:
            logger.info(f"Scraping {index_name} constituents...")
            # Wikipedia blocks default bots, so spoof a User-Agent header to get around it
            tables = pd.read_html(
                config["url"],
                attrs={'id': 'constituents'},
                storage_options={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            data = tables[0]

            if len(data) < config["min_rows"]:
                raise ValueError(
                    f"got {len(data)} rows, expected at least {config['min_rows']} "
                    f"- the page layout has probably changed"
                )

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

    # Names follow each index provider's own wording, not Yahoo's abbreviated shortName
    indices = {
        # US and volatility benchmarks
        '^GSPC': 'S&P 500 Index',
        '^DJI': 'Dow Jones Industrial Average',
        '^IXIC': 'NASDAQ Composite Index',
        '^NDX': 'NASDAQ-100 Index',
        '^RUT': 'Russell 2000 Index',
        '^SOX': 'PHLX Semiconductor Sector Index',
        '^VIX': 'CBOE Volatility Index',

        # Asia-Pacific indices
        '^TWII': 'TAIEX (TWSE Capitalization Weighted Stock Index)',
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
        with connect_db() as conn:
            import_taiwan_stocks(conn)
            import_us_stocks(conn)
            import_global_indices(conn)
            logger.info("Stock data import complete!")
    except Exception as e:
        logger.error(f"Error during stock data import: {e}")
