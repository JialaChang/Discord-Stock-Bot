# 系統架構與 UML 類別圖

依專案目錄結構拆成 5 張圖，若某類別的完整定義屬於其他圖，會以 `<<見「X」圖>>` 標註，只保留關聯線。

## 1. 資料模型 (Models)

`src/models/` 下的共用資料傳輸物件，被 Quant 與 Bot 層共用。

```mermaid
classDiagram
    direction TB

    class trade_module {
        <<Module: models/trade>>
        +pnl_ratio(side, entry_price, exit_price) float
    }
    note for trade_module "損益倍率統一由此計算；做空以 0 為下限，避免倍率轉負後反轉複利正負號"

    class StockSnapshot {
        <<Data Transfer Object>>
        +str ticker
        +str name
        +float current_price
        +float change_percent
        +float rsi_value
        +datetime latest_time
        +str change_str
        +str latest_time_str
    }

    class Signal {
        <<Data Transfer Object>>
        +Literal action: ENTER_LONG|EXIT_LONG|ENTER_SHORT|EXIT_SHORT|HOLD
        +dict~str_bool~ conditions
        +dict~str_float~ values
    }

    class Position {
        <<Data Transfer Object>>
        +date entry_date
        +float entry_price
        +Signal entry_signal
        +Literal side: LONG|SHORT
        +unrealized_pnl_ratio(price_now) float
    }

    class Trade {
        <<Data Transfer Object>>
        +str ticker
        +date entry_date
        +float entry_price
        +date exit_date
        +float exit_price
        +Signal entry_signal
        +Signal exit_signal
        +Literal side: LONG|SHORT
        +int shares
        +float profit_and_loss
        +float return_on_investment
        +bool is_profit
    }

    class BacktestResult {
        <<Data Transfer Object>>
        +str ticker
        +list~Trade~ trades
        +Series equity_curve
        +DataFrame data
        +float total_return
        +float win_rate
        +float max_drawdown
        +int trade_count
    }

    Trade --> Signal : 包含 entry/exit
    Position --> Signal : 包含 entry
    BacktestResult --> Trade : 包含 list
    Position ..> trade_module : unrealized_pnl_ratio() 委派
    Trade ..> trade_module : return_on_investment 委派
```

## 2. 技術分析與回測引擎 (Quant)

`src/quant/` 策略介面與回測引擎；`Strategy` 子類別透過 `required_columns` 告知引擎所需指標。

```mermaid
classDiagram
    direction TB

    class indicator {
        <<Module: quant/indicator>>
        +compute_indicators(ticker, history_data, columns) None
        +compute_indicators_for_discord(ticker, name, history_data, intraday_data, latest_time) StockSnapshot
    }
    note for indicator "columns=None 計算全套；否則只算指定欄位"

    class Strategy {
        <<Abstract>>
        +list~str~ required_columns
        +int warmup
        +signal(row, position) Signal
    }

    class RSIStrategy {
        +required_columns = ["RSI"]
        +warmup = 14
        +signal(row, position) Signal
    }

    class EMAStrategy {
        +required_columns = ["EMA_5", "EMA_20"]
        +warmup = 20
        +signal(row, position) Signal
    }

    class BacktestEngine {
        +Strategy strategy
        +float cumulative_multiplier
        +Position position
        +list~Trade~ trades
        +list~float~ equity
        +required_history_days(period_days) int
        +run(ticker, data, start) BacktestResult
        +print_backtest_result(result) None
        +export_backtest_result_html(result) str
        -_open_position(date, price, signal, side) None
        -_close_position(ticker, date, price, exit_signal) None
    }

    class InsufficientDataError {
        <<Exception: quant/errors>>
    }

    class StockDataFetcher {
        <<見「資料擷取、資料庫與排程腳本」圖>>
    }
    class html_report {
        <<見「Discord 機器人與圖表渲染」圖>>
    }
    class StockSnapshot {
        <<見「資料模型」圖>>
    }
    class Signal {
        <<見「資料模型」圖>>
    }
    class Position {
        <<見「資料模型」圖>>
    }
    class Trade {
        <<見「資料模型」圖>>
    }
    class BacktestResult {
        <<見「資料模型」圖>>
    }

    RSIStrategy --|> Strategy : 繼承
    EMAStrategy --|> Strategy : 繼承
    Strategy ..> Signal : 回傳
    indicator ..> StockSnapshot : 回傳股票快照
    BacktestEngine --> Strategy : 持有
    BacktestEngine --> indicator : 計算指標
    BacktestEngine --> StockDataFetcher : 取得歷史資料
    BacktestEngine --> html_report : 匯出 HTML 報表
    BacktestEngine ..> Position : 持倉追蹤
    BacktestEngine ..> Trade : 產生
    BacktestEngine ..> BacktestResult : 回傳
    BacktestEngine ..> InsufficientDataError : 資料不足時拋出
    indicator ..> InsufficientDataError : 資料不足時拋出
```

## 3. Discord 機器人與圖表渲染 (Bot & Utils)

`src/bot/` 斜線指令與 View 元件；`src/utils/visualizer.py` 負責產生圖表 bytes。

```mermaid
classDiagram
    direction TB

    class dc_bot {
        <<Module: bot/dc_bot>>
        +commands.Bot bot
        +list~str~ BOT_PERIODS
        +on_ready()
        +on_disconnect()
        +on_resumed()
        +analyze_stock(interaction, ticker)
        +backtest_stock(interaction, ticker, strategy, period)
    }
    note for dc_bot "BOT_PERIODS 僅引用 PERIOD_DAYS 的 key"

    class dc_bot_view {
        <<Module: bot/dc_bot_view>>
        +send_stock_response(interaction, snapshot, history_bytes, intraday_bytes)
        +send_backtest_response(interaction, result, strategy_label, chart_bytes)
    }

    class DiscordStockChart {
        <<discord.ui.View>>
        +str stock_ticker
        +bytes history_bytes
        +bytes_or_None intraday_bytes
        +bool is_history
        +Message message
        +on_timeout()
        +btn_toggle(interaction, button)
    }

    class visualizer {
        <<Module: utils/visualizer>>
        +generate_history_chart(ticker, data, days) BytesIO
        +generate_intraday_chart(ticker, data) BytesIO
        +generate_backtest_chart(ticker, result) BytesIO
    }
    note for visualizer "需要的指標已由 indicator 寫入"

    class html_report {
        <<Module: utils/html_report>>
        +html_document(title, body, subtitle) str
        +html_table(headers, rows) str
        +fmt_num(v, decimals) str
        +fmt_int(v) str
    }
    note for html_report "外殼與 CSS 在 templates/report.html"

    class StockDataFetcher {
        <<見「資料擷取、資料庫與排程腳本」圖>>
    }
    class indicator {
        <<見「技術分析與回測引擎」圖>>
    }
    class BacktestEngine {
        <<見「技術分析與回測引擎」圖>>
    }
    class BacktestResult {
        <<見「資料模型」圖>>
    }

    dc_bot --> StockDataFetcher : 實例化
    dc_bot --> indicator : 計算指標
    dc_bot --> visualizer : 繪製圖表
    dc_bot --> dc_bot_view : 封裝 View 訊息物件
    dc_bot --> BacktestEngine : 執行回測
    dc_bot_view --> DiscordStockChart : 實例化 View
    dc_bot_view ..> BacktestResult : 讀取回測結果
```

## 4. 資料擷取、資料庫與排程腳本 (Data & Database & Scripts)

`src/data/fetcher.py` 為讀取門面、`src/data/sync.py` 為寫入路徑；`src/database/` 為底層 CRUD 與 SQL 語句集中地；`scripts/` 為獨立排程腳本。

```mermaid
classDiagram
    direction TB

    class StockDataFetcher {
        -str _raw_ticker
        +str ticker
        +DataFrame historical_data
        +DataFrame intraday_data
        +check_stock_exist() bool
        +fetch_stock_name() str
        +fetch_historical_data(days) DataFrame
        +fetch_intraday_data() DataFrame
        +fetch_latest_time() Timestamp
        +get_data_count() dict
    }

    class sync {
        <<Module: data/sync>>
        +download_ohlcv(tickers, period) DataFrame
        +extract_ticker_frame(data, ticker) DataFrame
        +records_from_frame(ticker, frame) list
        +upsert_records(conn, records) None
        +fetch_all_tickers(conn) list
        +fetch_pending_tickers(conn, max_age_days) list
        +mark_backfilled(conn, tickers) None
        +needs_full_refresh(conn, ticker, frame) bool
    }
    note for sync "needs_full_refresh 偵測 Adj Close 遭改寫或歷史存在缺口"

    class database {
        <<Module: database/database>>
        +str DB_PATH
        +load_sql(name) str
        +init_database()
        +insert_stock(ticker, name, market)
        +delete_stock(ticker)
        +get_stock(ticker) dict
        +get_daily_prices(ticker, limit) list
        -_export_prices_html(ticker, prices)
    }

    class sql_files {
        <<SQL Files: database/sql>>
        schema.sql
        upsert_stock.sql
        upsert_daily_price.sql
        select_daily_prices.sql
        select_historical_prices.sql
    }
    note for sql_files "SQL 集中於此，由 load_sql() 載入並快取"

    class html_report {
        <<見「Discord 機器人與圖表渲染」圖>>
    }

    class stocks {
        <<Database Table>>
        +ticker TEXT PK
        +name TEXT NOT NULL
        +market TEXT
        +last_backfilled TEXT
    }
    note for stocks "last_backfilled 決定回補是否略過"

    class daily_prices {
        <<Database Table>>
        +id INTEGER PK AUTOINCREMENT
        +ticker TEXT FK
        +date TEXT NOT NULL
        +open_price REAL
        +high_price REAL
        +low_price REAL
        +close_price REAL
        +adjust_close_price REAL
        +volume REAL
    }

    class daily_updater {
        <<Script>>
        +update_stock_data() list~str~
    }
    note for daily_updater "回傳須重取完整歷史的股票"

    class historical_backfill {
        <<Script>>
        +backfill_history(period, tickers, force)
    }

    class seed_stocks {
        <<Script>>
        +import_taiwan_stocks(conn)
        +import_us_stocks(conn)
        +import_global_indices(conn)
    }

    StockDataFetcher ..> stocks : 讀取
    StockDataFetcher ..> daily_prices : 讀取
    StockDataFetcher --> database : load_sql()
    sync --> database : load_sql()
    sync ..> stocks : 讀寫 last_backfilled 欄位
    sync ..> daily_prices : 寫入
    database --> sql_files : load_sql() 載入
    database ..> stocks : CURD
    database ..> daily_prices : CURD
    daily_updater --> sync : 下載 / 寫入 / 偵測
    daily_updater --> historical_backfill : 觸發完整歷史重取
    historical_backfill --> sync : 下載 / 寫入 / 標記回補時間
    seed_stocks --> database : 初始化 / load_sql()
    seed_stocks ..> stocks : 寫入
    daily_prices --> stocks : FK (ticker)
```

## 5. 測試 (Tests)

`tests/` 為離線回歸測試：資料庫為 in-memory SQLite，行情為 `conftest.py` 產生的合成 OHLCV，不觸及 yfinance 或本機 `stock_data.db`。下圖同時作為覆蓋範圍對照，標示各測試模組驗證哪些正式單元。

```mermaid
classDiagram
    direction TB

    class conftest {
        <<Module: tests/conftest>>
        +db() Connection
        +seeded_db() Connection
        +make_ohlcv(closes, opens, highs, lows, start) DataFrame
    }
    note for conftest "make_ohlcv 預設高低點距收盤 1%，不會誤觸 15% 停損"

    class ScriptedStrategy {
        <<Test Double: tests/test_backtest>>
        +list~str~ actions
        +int bar
        +signal(row, position) Signal
    }

    class test_models {
        <<Test Module>>
        TestPnlRatio
        TestTrade
        TestBacktestResult
    }

    class test_backtest {
        <<Test Module>>
        TestOrderExecution
        TestStopLoss
        TestEquityCurve
        TestIndicatorWarmup
    }

    class test_sync {
        <<Test Module>>
        TestExtractTickerFrame
        TestRecordsFromFrame
        TestNeedsFullRefresh
        TestBackfillStamps
        TestUpsertRecords
    }

    class test_fetcher {
        <<Test Module>>
        +stock_db() Path
        TestTickerNormalization
        TestExistenceLookup
        TestAdjustedPriceReconstruction
    }
    note for test_fetcher "自建暫存資料庫並改寫 fetcher.DB_PATH"

    class Strategy {
        <<見「技術分析與回測引擎」圖>>
    }
    class BacktestEngine {
        <<見「技術分析與回測引擎」圖>>
    }
    class trade_module {
        <<見「資料模型」圖>>
    }
    class BacktestResult {
        <<見「資料模型」圖>>
    }
    class sync {
        <<見「資料擷取、資料庫與排程腳本」圖>>
    }
    class StockDataFetcher {
        <<見「資料擷取、資料庫與排程腳本」圖>>
    }

    ScriptedStrategy --|> Strategy : 繼承
    test_backtest --> ScriptedStrategy : 驅動引擎
    test_backtest --> conftest : make_ohlcv
    test_sync --> conftest : seeded_db

    test_models ..> trade_module : 驗證損益倍率下限
    test_models ..> BacktestResult : 驗證績效指標與空曲線防護
    test_backtest ..> BacktestEngine : 驗證成交規則、停損與暖身
    test_sync ..> sync : 驗證漂移／缺口偵測與回補戳記
    test_fetcher ..> StockDataFetcher : 驗證代碼正規化與還原權值回推
```
