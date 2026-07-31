-- Stock master table
CREATE TABLE IF NOT EXISTS stocks (
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT,
    last_backfilled TEXT
);

-- Daily historical price table
CREATE TABLE IF NOT EXISTS daily_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    adjust_close_price REAL,
    volume REAL,
    FOREIGN KEY (ticker) REFERENCES stocks (ticker),
    UNIQUE(ticker, date)
);

-- Composite index to speed up queries
CREATE INDEX IF NOT EXISTS index_ticker_date
ON daily_prices (ticker, date);
