import sqlite3
import os
import sys
import logging
from datetime import datetime
from functools import lru_cache
from typing import Any


logger = logging.getLogger(__name__)

# Absolute path to the project root
BASE_DIR = os.path.dirname(os.path.dirname((os.path.dirname(os.path.abspath(__file__)))))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

DB_PATH = os.path.join(BASE_DIR, 'stock_data.db')
SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sql')


@lru_cache
def load_sql(name: str) -> str:
    """Load a SQL statement from src/database/sql/<name>.sql."""
    with open(os.path.join(SQL_DIR, f'{name}.sql'), encoding='utf-8') as f:
        return f.read()


def connect_db() -> sqlite3.Connection:
    """Open the project database with foreign keys enforced."""
    connect = sqlite3.connect(DB_PATH)
    connect.execute("PRAGMA foreign_keys = ON")
    return connect


def init_database():
    """Initialize the SQLite database tables."""
    with connect_db() as connect:
        connect.executescript(load_sql('schema'))
        connect.commit()

    logger.info(f"Database created at {DB_PATH}")


def insert_stock(ticker: str, name: str, market: str) -> None:
    """Insert or update a single stock's master record."""
    with connect_db() as connect:
        cursor = connect.cursor()
        cursor.execute(load_sql('upsert_stock'), (ticker, name, market))
        connect.commit()


def delete_stock(ticker: str) -> None:
    """Delete a single stock's master record."""
    with connect_db() as connect:
        cursor = connect.cursor()
        cursor.execute('''
            DELETE FROM daily_prices WHERE ticker = ?
        ''', (ticker,))
        cursor.execute('''
            DELETE FROM stocks WHERE ticker = ?
        ''', (ticker,))
        connect.commit()


def get_stock(ticker: str) -> dict[str, Any] | None:
    """Query a single stock's master record."""
    with connect_db() as connect:
        connect.row_factory = sqlite3.Row  # Allow accessing columns by name like a dict
        cursor = connect.cursor()
        cursor.execute('SELECT * FROM stocks WHERE ticker = ?', (ticker,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_daily_prices(ticker: str, days: int = 30) -> list[dict[str, Any]]:
    """Get historical prices for the given stock."""
    with connect_db() as connect:
        connect.row_factory = sqlite3.Row
        cursor = connect.cursor()
        cursor.execute(load_sql('select_daily_prices'), (ticker, days))
        return [dict(row) for row in cursor.fetchall()]


def _menu_init_database():
    init_database()
    print("Database initialization complete.")


def _menu_insert_stock():
    ticker = input("Enter ticker (e.g. 2330.TW): ").strip()
    name   = input("Enter stock name: ").strip()
    # Listed vs. OTC is not a market: it lives in the .TW / .TWO suffix of the ticker.
    market = input("Enter market (TW / US / INDEX): ").strip()
    insert_stock(ticker, name, market)
    print(f"Inserted/Updated: {ticker} {name}")


def _menu_delete_stock():
    ticker = input("Enter the ticker to delete: ").strip()
    confirm = input(f"Delete {ticker} and all its historical prices? (y/N) ").strip().lower()
    if confirm == 'y':
        delete_stock(ticker)
        print(f"Deleted {ticker}!")
    else:
        print("Cancelled...")


def _menu_get_stock():
    ticker = input("Enter ticker: ").strip()
    info = get_stock(ticker)
    if info:
        print("\n[Stock master record]")
        for key, value in info.items():
            print(f"  {key:<8} : {value}")
    else:
        print(f"No master record found for {ticker}...")


def _menu_get_prices():
    ticker = input("Enter ticker: ").strip()
    try:
        days = int(input("Number of records (default 10): ").strip() or "10")
    except ValueError:
        days = 10
    prices = get_daily_prices(ticker, days=days)
    if not prices:
        print(f"No price data found for {ticker}...")
        return

    # Export to an HTML report instead of flooding the terminal when the result set is large.
    if len(prices) > 50:
        _export_prices_html(ticker, prices)
        return

    print(f"\n[{ticker} latest {len(prices)} prices]")
    print(f"{'Date':<12} | {'Open':>8} | {'Close':>8} | {'AdjClose':>8} | {'Volume':>12}")
    print("-" * 60)
    for p in prices:
        open_p  = p.get('open_price')
        close_p = p.get('close_price')
        adj_close_p = p.get('adjust_close_price')
        vol     = p.get('volume')
        open_s  = f'{open_p:.2f}'  if open_p  is not None else 'N/A'
        close_s = f'{close_p:.2f}' if close_p is not None else 'N/A'
        adj_close_s = f'{adj_close_p:.2f}' if adj_close_p is not None else 'N/A'
        vol_s   = f'{vol:.0f}'     if vol     is not None else 'N/A'
        print(f"{p['date']:<12} | {open_s:>8} | {close_s:>8} | {adj_close_s:>8} | {vol_s:>12}")


def _export_prices_html(ticker: str, prices: list[dict[str, Any]]):
    """Write price records to a HTML report under the project's exports/ directory."""
    from src.utils.html_report import html_document, html_table, fmt_num, fmt_int

    export_dir = os.path.join(BASE_DIR, 'exports')
    os.makedirs(export_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(export_dir, f'{ticker}_prices_{timestamp}.html')

    rows = []
    # Rows are newest-first; the previous trading day is the next row, used to color the change.
    for i, p in enumerate(prices):
        close_p = p.get('close_price')
        prev_close = prices[i + 1].get('close_price') if i + 1 < len(prices) else None
        if close_p is not None and prev_close is not None:
            cls = 'up' if close_p > prev_close else 'down' if close_p < prev_close else 'flat'
        else:
            cls = 'flat'
        rows.append([
            p.get('date', ''),
            fmt_num(p.get('open_price')),
            fmt_num(p.get('high_price')),
            fmt_num(p.get('low_price')),
            (fmt_num(close_p), cls),  # colored by daily change
            fmt_num(p.get('adjust_close_price')),
            fmt_int(p.get('volume')),
        ])

    table = html_table(['Date', 'Open', 'High', 'Low', 'Close', 'AdjClose', 'Volume'], rows)
    data_range = f"{prices[-1].get('date', '?')} ~ {prices[0].get('date', '?')}"
    html = html_document(
        f'{ticker} &mdash; {len(prices)} records',
        table,
        subtitle=f"Data {data_range} · Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    )
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n>> {len(prices)} records exported to: {filepath}")


_MENU = [
    ("Initialize database", _menu_init_database),
    ("Insert / Update stock master record", _menu_insert_stock),
    ("Delete stock", _menu_delete_stock),
    ("Query stock master record", _menu_get_stock),
    ("Query historical prices", _menu_get_prices),
]

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    while True:
        print("\n========== Stock Database Manager ==========")
        for i, (label, _) in enumerate(_MENU, start=1):
            print(f"  {i}. {label}")
        print("  0. Exit")
        print("============================================")

        choice = input("Choose an option: ").strip()
        if choice == "0":
            print("Goodbye!")
            break
        elif choice.isdigit() and 1 <= int(choice) <= len(_MENU):
            print()
            _MENU[int(choice) - 1][1]()
        else:
            print("Invalid option, please try again...")
