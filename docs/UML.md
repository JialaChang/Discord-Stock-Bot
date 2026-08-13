# 模組相依關係

依目錄結構拆成 7 張圖，呈現類別與模組間的相依方向。

完整簽章、參數語意與行為規則見 [ARCHITECTURE.md](./ARCHITECTURE.md)。

跨圖引用以 `<<見「X」圖>>` 標註，僅保留關聯線。

## 1. 資料模型 (`src/models/`)

```mermaid
classDiagram
    direction TB

    class StockSnapshot {
        <<Data Transfer Object>>
        +str ticker
        +str name
        +float current_price
        +float previous_close
        +float_or_None rsi_value
        +datetime latest_time
        +float change_percent
        +str change_str
        +str rsi_str
        +str latest_time_str
    }

    class Signal {
        <<Data Transfer Object>>
        +Literal action
        +dict~str_bool~ conditions
        +dict~str_float~ values
    }

    class Position {
        <<Data Transfer Object>>
        +date entry_date
        +float entry_price
        +Signal entry_signal
        +Literal side
        +int shares
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
        +Literal side
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
```

## 2. 規則層 (`src/quant/rule.py`)

規則只表達偏向，不知道當前部位；`CompositeStrategy` 依角色將其分桶。

```mermaid
classDiagram
    direction TB

    class Rule {
        <<Abstract>>
        +str name
        +list~str~ required_columns
        +int warmup
        +int_or_None lookback
        +reset() None
        +bias(history) float
    }

    class CrossRule {
        <<Abstract>>
        +str fast_column
        +str slow_column
        +int_or_None precision
        +reset() None
    }

    class _CrossTracker {
        +reset() None
        +seed(fast, slow) None
        +update(fast, slow) int
    }

    class SMATrend {
        +int length
        +float band
        +bias(history) float
    }

    class MACDZeroLine {
        +float full
        +bias(history) float
    }

    class RSIReversal {
        +float oversold
        +float overbought
        +bias(history) float
    }

    class StochCross {
        +float low
        +float high
        +bias(history) float
    }

    class EMACross {
        +bias(history) float
    }

    class BollingerBand {
        +bias(history) float
    }

    class MACDSignalCross {
        +bias(history) float
    }

    CrossRule --|> Rule
    SMATrend --|> Rule
    MACDZeroLine --|> Rule
    RSIReversal --|> Rule
    BollingerBand --|> Rule
    StochCross --|> CrossRule
    EMACross --|> CrossRule
    MACDSignalCross --|> CrossRule
    CrossRule --> _CrossTracker : 持有
```

| 桶 | 規則 |
|----|------|
| 趨勢 | `SMATrend`、`MACDZeroLine` |
| 進場 | `RSIReversal`、`StochCross`、`EMACross` |
| 出場 | `BollingerBand`、`MACDSignalCross` |

## 3. 技術分析與回測引擎 (`src/quant/`)

```mermaid
classDiagram
    direction TB

    class indicator {
        <<Module: quant/indicator>>
        +int MACD_Z_WINDOW
        +compute_indicators(ticker, data, columns) None
    }

    class Strategy {
        <<Abstract>>
        +list~str~ required_columns
        +int warmup
        +int_or_None lookback
        +reset() None
        +signal(history, position) Signal
    }

    class RSIStrategy {
        +signal(history, position) Signal
    }

    class EMAStrategy {
        +reset() None
        +signal(history, position) Signal
    }

    class CompositeStrategy {
        +float trend_enter
        +float trend_exit
        +float entry_threshold
        +float exit_threshold
        +reset() None
        +signal(history, position) Signal
    }

    class strategy_module {
        <<Module: quant/strategy>>
        +gated_strategy() CompositeStrategy
        +voting_strategy() CompositeStrategy
    }

    class backtest_module {
        <<Module: quant/backtest>>
        +dict~str_int~ PERIOD_DAYS
        +int INITIAL_CAPITAL
        +float STOP_LOSS
        +history_window(data, i, lookback) DataFrame
    }

    class BacktestEngine {
        +Strategy strategy
        +float cash
        +Position position
        +list~Trade~ trades
        +list~float~ equity
        +required_history_days(period_days) int
        +run(ticker, data, start) BacktestResult
        +print_backtest_result(result) None
        +export_backtest_result_html(result) str
    }

    class InsufficientDataError {
        <<Exception: quant/errors>>
    }

    class html_report {
        <<見「Bot 與輸出工具」圖>>
    }
    class Rule {
        <<見「規則層」圖>>
    }
    class EMACross {
        <<見「規則層」圖>>
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

    RSIStrategy --|> Strategy
    EMAStrategy --|> Strategy
    CompositeStrategy --|> Strategy
    EMAStrategy --> EMACross : 持有
    CompositeStrategy --> Rule : 依角色分桶持有
    strategy_module ..> CompositeStrategy : 預設組合
    Strategy ..> Signal : 回傳
    indicator ..> InsufficientDataError : 拋出
    BacktestEngine --> Strategy : 持有
    BacktestEngine --> indicator : 計算指標
    BacktestEngine --> backtest_module : 讀取常數 / 切歷史區間
    BacktestEngine --> html_report : 匯出報表
    BacktestEngine ..> Position : 追蹤
    BacktestEngine ..> Trade : 產生
    BacktestEngine ..> BacktestResult : 回傳
    BacktestEngine ..> InsufficientDataError : 拋出
```

引擎不相依於任何資料來源，`run()` 接收呼叫端備妥的 DataFrame。

## 4. Bot 與輸出工具 (`src/bot/`、`src/utils/`)

`html_report` 不相依於 Discord，其使用者為 `BacktestEngine` 與 `database`；列於此圖僅因同屬 `src/utils/`。

```mermaid
classDiagram
    direction TB

    class dc_bot {
        <<Module: bot/dc_bot>>
        +commands.Bot bot
        +dict~str_type~ STRATEGIES
        +list~str~ BOT_PERIODS
        +analyze_stock(interaction, ticker)
        +backtest_stock(interaction, ticker, strategy, period)
    }

    class dc_bot_view {
        <<Module: bot/dc_bot_view>>
        +display_name(name, ticker) str
        +build_snapshot(ticker, name, history_data, intraday_data, latest_time) StockSnapshot
        +send_stock_response(interaction, snapshot, history_bytes, intraday_bytes)
        +send_backtest_response(interaction, result, stock_name, strategy_label, chart_bytes)
    }

    class DiscordStockChart {
        <<discord.ui.View>>
        +bytes history_bytes
        +bytes_or_None intraday_bytes
        +bool is_history
        +on_timeout()
        +btn_toggle(interaction, button)
    }

    class visualizer {
        <<Module: utils/visualizer>>
        +generate_history_chart(ticker, data, days) BytesIO
        +generate_intraday_chart(ticker, data, baseline) BytesIO
        +generate_backtest_chart(ticker, result) BytesIO
    }

    class html_report {
        <<Module: utils/html_report>>
        +html_document(title, body, subtitle) str
        +html_table(headers, rows) str
        +fmt_num(v, decimals) str
        +fmt_int(v) str
        +write_report(html_text, name) str
    }

    class StockDataFetcher {
        <<見「資料存取與排程腳本」圖>>
    }
    class strategy_module {
        <<見「技術分析與回測引擎」圖>>
    }
    class BacktestEngine {
        <<見「技術分析與回測引擎」圖>>
    }
    class InsufficientDataError {
        <<見「技術分析與回測引擎」圖>>
    }
    class StockSnapshot {
        <<見「資料模型」圖>>
    }
    class BacktestResult {
        <<見「資料模型」圖>>
    }

    dc_bot --> StockDataFetcher : 實例化
    dc_bot --> visualizer : 繪製圖表
    dc_bot --> dc_bot_view : 組裝快照 / 送出回覆
    dc_bot --> BacktestEngine : 執行回測
    dc_bot --> strategy_module : STRATEGIES 對照表
    dc_bot_view --> DiscordStockChart : 實例化
    dc_bot_view ..> StockSnapshot : 組裝與回傳
    dc_bot_view ..> BacktestResult : 讀取
    dc_bot_view ..> InsufficientDataError : 拋出
```

`visualizer` 自行計算圖上的均線，不相依於 `indicator`；`dc_bot` 亦不再直接計算指標，Embed 所需的一項由 `dc_bot_view.build_snapshot` 自行取得。

## 5. 儲存層 (`src/database/`)

```mermaid
classDiagram
    direction TB

    class database {
        <<Module: database/database>>
        +str DB_PATH
        +connect_db() Connection
        +load_sql(name) str
        +init_database()
        +insert_stock(ticker, name, market)
        +delete_stock(ticker)
        +get_stock(ticker) dict
        +get_daily_prices(ticker, limit) list
    }

    class sql_files {
        <<SQL Files: database/sql>>
        schema.sql
        upsert_stock.sql
        upsert_daily_price.sql
        select_daily_prices.sql
        select_historical_prices.sql
    }

    class stocks {
        <<Database Table>>
        +ticker TEXT PK
        +name TEXT NOT NULL
        +market TEXT
        +last_backfilled TEXT
    }

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
        UNIQUE ticker + date
    }

    class html_report {
        <<見「Bot 與輸出工具」圖>>
    }

    database --> sql_files : 載入
    database --> html_report : 匯出價格報表
    database ..> stocks : CRUD
    database ..> daily_prices : CRUD
    daily_prices --> stocks : FK (ticker)
```

## 6. 資料存取與排程腳本 (`src/data/`、`scripts/`)

`fetcher` 為讀取門面，`sync` 為寫入路徑，三支腳本一律經由 `sync` 寫入。

```mermaid
classDiagram
    direction TB

    class StockDataFetcher {
        +str ticker
        +DataFrame historical_data
        +DataFrame intraday_data
        +check_stock_exist() bool
        +fetch_stock_name() str
        +fetch_historical_data(days) DataFrame
        +fetch_intraday_data() DataFrame
        +fetch_latest_time() Timestamp
        +get_data_count() dict
        -_format_ticker(ticker) str
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

    class daily_updater {
        <<Script>>
        +update_stock_data() list~str~
    }

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

    class twstock {
        <<External>>
    }
    class yfinance {
        <<External>>
    }
    class database {
        <<見「儲存層」圖>>
    }
    class stocks {
        <<見「儲存層」圖>>
    }
    class daily_prices {
        <<見「儲存層」圖>>
    }

    StockDataFetcher --> database : connect_db() / load_sql()
    StockDataFetcher --> twstock : 台股市場別對照
    StockDataFetcher --> yfinance : 盤中資料
    StockDataFetcher ..> stocks : 讀取
    StockDataFetcher ..> daily_prices : 讀取
    sync --> database : load_sql()
    sync --> yfinance : 批次下載
    sync ..> stocks : 讀寫 last_backfilled
    sync ..> daily_prices : 寫入
    daily_updater --> sync
    daily_updater --> historical_backfill : 觸發完整重取
    daily_updater --> database : connect_db()
    historical_backfill --> sync
    historical_backfill --> database : connect_db()
    seed_stocks --> database : 初始化 / 寫入
    seed_stocks --> twstock : 台股清單
    seed_stocks ..> stocks : 寫入
```

## 7. 測試 (`tests/`)

同時作為覆蓋範圍對照。

```mermaid
classDiagram
    direction TB

    class conftest {
        <<Module: tests/conftest>>
        +db() Connection
        +seeded_db() Connection
        +ohlcv() callable
        +make_ohlcv(closes, opens, highs, lows, start) DataFrame
    }

    class ScriptedStrategy {
        <<Test Double: tests/test_backtest>>
        +reset() None
        +signal(history, position) Signal
    }

    class RecordingStrategy {
        <<Test Double: tests/test_backtest>>
        +list~DataFrame~ seen
        +reset() None
        +signal(history, position) Signal
    }

    class test_models {
        <<Test Module>>
        TestStockSnapshot
        TestTrade
        TestBacktestResult
    }

    class test_backtest {
        <<Test Module>>
        TestOrderExecution
        TestReversal
        TestPositionSizing
        TestStopLoss
        TestEquityCurve
        TestHistoryWindow
        TestStrategyHistory
        TestStrategyLifecycle
        TestIndicatorWarmup
    }

    class StubRule {
        <<Test Double: tests/test_composite>>
        +int calls
        +list~int~ views
        +reset() None
        +bias(history) float
    }

    class test_strategy {
        <<Test Module>>
        TestEMACrossDetection
        TestEMAReversal
        TestEMALifecycle
        TestRSIReadsTheLatestBar
    }

    class test_rule {
        <<Test Module>>
        TestRSIReversal
        TestSMATrend
        TestMACDZeroLine
        TestBollingerBand
        TestMACDSignalCross
        TestEMACross
        TestStochCross
        TestDeclaredColumnsAndWarmup
    }

    class test_composite {
        <<Test Module>>
        TestEntryNeedsGateAndTrigger
        TestNoTrendRules
        TestExit
        TestGateHysteresis
        TestEveryRuleSeesEveryBar
        TestSignalPayload
        TestDeclarationsAggregate
        TestPresets
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
        TestTickerNormalization
        TestExistenceLookup
        TestAdjustedPriceReconstruction
    }

    class test_html_report {
        <<Test Module>>
        TestEscaping
        TestWriteReport
        TestFormatting
    }

    class test_bot_view {
        <<Test Module>>
        TestDisplayName
        TestSnapshotCurrentPrice
        TestSnapshotPreviousClose
        TestSnapshotFields
        TestSnapshotInputs
    }

    class Strategy {
        <<見「技術分析與回測引擎」圖>>
    }
    class CompositeStrategy {
        <<見「技術分析與回測引擎」圖>>
    }
    class BacktestEngine {
        <<見「技術分析與回測引擎」圖>>
    }
    class backtest_module {
        <<見「技術分析與回測引擎」圖>>
    }
    class Rule {
        <<見「規則層」圖>>
    }
    class indicator {
        <<見「技術分析與回測引擎」圖>>
    }
    class Trade {
        <<見「資料模型」圖>>
    }
    class StockSnapshot {
        <<見「資料模型」圖>>
    }
    class BacktestResult {
        <<見「資料模型」圖>>
    }
    class sync {
        <<見「資料存取與排程腳本」圖>>
    }
    class StockDataFetcher {
        <<見「資料存取與排程腳本」圖>>
    }
    class html_report {
        <<見「Bot 與輸出工具」圖>>
    }
    class dc_bot_view {
        <<見「Bot 與輸出工具」圖>>
    }

    ScriptedStrategy --|> Strategy
    RecordingStrategy --|> Strategy
    StubRule --|> Rule
    test_backtest --> ScriptedStrategy : 驅動引擎
    test_backtest --> RecordingStrategy : 記錄可見歷史
    test_backtest --> conftest
    test_composite --> StubRule : 給定偏向
    test_strategy --> backtest_module : 沿用 history_window 切片
    test_rule --> backtest_module : 沿用 history_window 切片
    test_rule --> conftest
    test_sync --> conftest
    test_fetcher --> conftest

    test_models ..> Trade : 金額損益與報酬率一致
    test_models ..> StockSnapshot : 漲跌幅導出與指標缺值
    test_models ..> BacktestResult : 績效指標與空曲線防護
    test_backtest ..> BacktestEngine : 成交規則、反手、停損、可見歷史、暖身
    test_strategy ..> Strategy : 交叉方向到訊號的映射
    test_rule ..> Rule : 偏向讀值與宣告正確性
    test_rule ..> indicator : 宣告的欄位確實算得出
    test_composite ..> CompositeStrategy : 分桶、閘門、出場與宣告合併
    test_sync ..> sync : 漂移／缺口偵測與回補戳記
    test_fetcher ..> StockDataFetcher : 代碼正規化與還原權值回推
    test_html_report ..> html_report : 逃脫與匯出路徑過濾
    test_bot_view ..> dc_bot_view : 標題組裝與快照組裝
```
