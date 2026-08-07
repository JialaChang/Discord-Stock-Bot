# 架構與模組說明

各模組職責與 Discord 指令資料流。完整類別關聯見 [UML.md](./UML.md)。

---

## 模組說明

### `StockDataFetcher` (`src/data/fetcher.py`)

整合三個資料源的**讀取**門面：
- **SQLite**：歷史日線與股票名稱
- **yfinance**：當日 1 分鐘盤中資料
- **twstock**：台股代碼與市場別（上市 / 上櫃）對照

代碼正規化：輸入先轉大寫再解析 `.TW` / `.TWO` 後綴。

還原權值回推時，若收盤價缺值或非正數則保持原值不調整，避免產生 inf / NaN。

### `sync.py` (`src/data/sync.py`)

yfinance → SQLite 的**寫入**路徑，由兩支回補腳本共用，統一批次資料的拆解與寫入方式：

| 函式 | 說明 |
|------|------|
| `download_ohlcv(tickers, period)` | 批次下載，失敗回傳空 DataFrame 而不中斷後續批次 |
| `extract_ticker_frame(data, ticker)` | 依欄位是否為 MultiIndex 判斷拆法 |
| `records_from_frame` / `upsert_records` | 轉成參數 tuple 並批次 upsert |
| `fetch_all_tickers` / `fetch_pending_tickers(max_age_days)` | 全部股票／尚未回補或戳記過期的股票 |
| `mark_backfilled(conn, tickers)` | 蓋上回補時間戳，僅限成功寫入者 |
| `needs_full_refresh(conn, ticker, frame)` | 判斷此檔是否需要重抓完整歷史（見下） |

**`needs_full_refresh` 的兩種情境**：

1. **還原收盤價遭改寫**：Yahoo 每逢除息或分割會回溯改寫整段歷史的 `Adj Close`。下載區間內的列由 upsert 修正，其餘較舊的列仍留存於舊基準。
2. **歷史存在缺口**：更新腳本停擺超過下載區間長度時，期間資料未被取得。

兩項檢查具先後順序：缺口檢查須先於漂移比對執行。下載區間與既有資料無日期重疊時，漂移比對缺乏可資比對的基準，而該情形正對應歷史最可能過期的狀況。

### `compute_indicators` / `compute_indicators_for_discord` (`src/quant/indicator.py`)

刻意分離的兩個函式，對應不同情境：

| 函式 | 用途 | 回傳 |
|------|------|------|
| `compute_indicators(ticker, data, columns)` | 回測用：依 `columns` 原地寫入指標，`None` 代表全算 | `None` |
| `compute_indicators_for_discord()` | Discord 用：算 Embed 所需指標與現價漲跌幅 | `StockSnapshot` |

### `visualizer.py` (`src/utils/visualizer.py`)

生成 in-memory PNG：
- `generate_history_chart`：日線 K 線 + MA5/10/20 + 成交量
- `generate_intraday_chart`：盤中折線，以開盤價為界紅漲綠跌
- `generate_backtest_chart`：K 線 + 多空進出場標記 + 權益曲線

### `html_report.py` (`src/utils/html_report.py`)

共用 HTML 報表層，讓呼叫端只提供資料、不寫任何 HTML 標籤：

| 元件 | 說明 |
|------|------|
| `templates/report.html` | 靜態頁面外殼 + CSS，以 `$title` / `$meta` / `$body` 佔位符注入 |
| `html_document(title, body, subtitle)` | 以 `string.Template` 將內容注入模板 |
| `html_table(headers, rows)` | 由資料建表；儲存格為純文字或 `(文字, CSS class)` tuple 上色 |
| `fmt_num` / `fmt_int` | 數字格式化，`None` 顯示 `N/A` |

使用者：`database.py`（股票價格報表）、`BacktestEngine`（回測績效報表）。報表輸出至 `exports/`。

### `database.py` + `sql/` (`src/database/`)

底層 SQLite CRUD，SQL 語句集中管理：

| 元件 | 說明 |
|------|------|
| `sql/*.sql` | 多行或跨模組共用的 SQL：`schema`、`upsert_stock`、`upsert_daily_price`、`select_daily_prices`、`select_historical_prices` |
| `load_sql(name)` | 讀取 `sql/<name>.sql`（`lru_cache` 快取），自 `src.database` 匯出 |

### `dc_bot_view.py` (`src/bot/dc_bot_view.py`)

- `DiscordStockChart`：持有圖表 bytes 的 `View`，按鈕切換日線 / 分時圖，逾時 5 分鐘清理
- `send_stock_response`：股票資訊 Embed + 可切換圖表 View
- `send_backtest_response`：回測績效 Embed（報酬率／勝率／最大回撤／交易次數）+ 回測圖

### `BacktestEngine` (`src/quant/backtest.py`)

逐日迭代 OHLCV 的回測引擎，與策略解耦（初始化時注入 `Strategy`）：
- 只計算並 `dropna` 策略宣告的 `required_columns`
- **pending signal**：訊號於當日收盤產生，隔日開盤成交
- 以 `cumulative_multiplier` 累積收益倍率，支援做多 / 做空
- 結束時未平倉部位以最後一日收盤強制平倉，回傳 `BacktestResult`
- `export_backtest_result_html()`：匯出 HTML 績效報表

**指標暖身資料另行取得，不佔用回測區間**：呼叫端依 `required_history_days(period_days)` 決定取得天數，並將 `start` 傳入 `run()`，待指標計算完成後才截去暖身區段。若僅取得使用者指定的區間，短週期回測的可用列數將被暖身消耗殆盡。資料量不足時拋出 `InsufficientDataError`（`src/quant/errors.py`）。

`PERIOD_DAYS` 為回測區間長度的**唯一定義來源**，Discord 指令僅引用其部分 key，不另行定義天數。

```
每日迴圈：
  1. 執行昨日 pending signal → 今日開盤成交
  2. 盤中止損（當日即時，跳空則以開盤價成交）
  3. 以今日收盤記錄 equity
  4. 依今日收盤產生明日 pending signal
```

### `Strategy` (`src/quant/strategy.py`)

抽象基底，子類實作 `signal()` 並宣告 `required_columns` 與 `warmup`：

| 類別 | `required_columns` | `warmup` | 策略 |
|------|--------------------|----------|------|
| `RSIStrategy` | `["RSI"]` | 14 | RSI 超買 / 超賣 |
| `EMAStrategy` | `["EMA_5", "EMA_20"]` | 20 | EMA5/20 黃金 / 死亡交叉 |

### `Signal` / `Position` / `Trade` / `BacktestResult` (`src/models/trade.py`)

| 類別 | 說明 |
|------|------|
| `Signal` | 策略訊號：`action`、`conditions`、觸發時 `values` |
| `Position` | 進場快照（日期／價格／方向）；`unrealized_pnl_ratio()` 回傳浮動損益倍率 |
| `Trade` | 單筆交易；屬性 `profit_and_loss`、`return_on_investment`、`is_profit` |
| `BacktestResult` | 回測彙總（`trades`／`equity_curve`／`data`）；屬性 `total_return`、`win_rate`、`max_drawdown`、`trade_count` |

損益倍率一律由 `pnl_ratio(side, entry_price, exit_price)` 計算，做空以 0 為下限。未設下限時，`2 - exit/entry` 於股價漲逾一倍後轉為負值，並反轉其後每次複利的正負號，導致權益曲線失真且不易察覺。`Trade.return_on_investment` 由同一函式導出，確保報表與權益曲線一致。

---

## Discord 指令資料流

### `/stock`

```
使用者輸入 ticker
    → StockDataFetcher._format_ticker()   # 轉大寫、補齊 .TW / .TWO 後綴（在執行緒中）
    → asyncio.gather()                    # 並發：SQLite 歷史 + yfinance 盤中
    → compute_indicators_for_discord()    # RSI(14)、MA5/10/20、漲跌幅 → StockSnapshot
    → asyncio.gather()                    # 並發：歷史 K 線圖 +（有分時資料才畫）分時圖
    → send_stock_response()               # 組裝 Embed 送出
    → DiscordStockChart View              # Embed + 可切換按鈕（5 分鐘逾時）
```

僅歷史資料為空時視為查詢失敗；無分時資料時仍正常回覆，切換按鈕則停用並標示無法使用。

### `/backtest`

```
使用者輸入 ticker、strategy、period
    → PERIOD_DAYS[period]                             # 區間長度的唯一來源
    → engine.required_history_days(period_days)       # 區間 + 指標暖身
    → StockDataFetcher.fetch_historical_data(days)    # 歷史 OHLCV
    → BacktestEngine.run(ticker, data, start)         # 算完指標後切掉暖身段 → BacktestResult
    → generate_backtest_chart()                       # K 線 + 進出場標記 + 權益曲線
    → send_backtest_response()                        # 績效 Embed 附圖送出
```

資料不足時 `run()` 拋出 `InsufficientDataError`，指令將其訊息原文回覆使用者。

---

## 測試

`tests/` 以 pytest 撰寫，全程離線：資料庫為 in-memory SQLite，行情為 `conftest.py` 產生的合成 OHLCV，不呼叫 yfinance，亦不讀取本機 `stock_data.db`。執行方式見 [README](../README.md#測試)。

回測執行規則的測試以 `ScriptedStrategy` 逐根 K 棒驅動引擎，使進出場時點與成交價可被精確指定，不受任何真實指標的數值影響。

本文所述的設計規則各有對應測試守護，修改前可先確認會觸動哪一項：

| 設計規則 | 守護測試 |
|----------|----------|
| 做空損益倍率以 0 為下限，且與 `return_on_investment` 一致 | `test_models.py::TestPnlRatio` / `TestTrade` |
| 訊號於當日收盤產生、隔日開盤成交 | `test_backtest.py::TestOrderExecution` |
| 停損以停損價成交，跳空則以開盤價成交 | `test_backtest.py::TestStopLoss` |
| 指標暖身另行取得，不佔用回測區間 | `test_backtest.py::TestIndicatorWarmup` |
| `needs_full_refresh` 的缺口檢查先於漂移比對 | `test_sync.py::TestNeedsFullRefresh` |
| 僅成功寫入者蓋上回補戳記，失敗者保持待補 | `test_sync.py::TestBackfillStamps` |
| 代碼轉大寫後查詢；還原權值回推略過不可用的列 | `test_fetcher.py` |
