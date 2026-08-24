# 架構與模組說明

說明各模組的責任、重要設計決策與 Discord 指令資料流。完整類別關係請見 [UML.md](./UML.md)，安裝與執行方式請見 [README](../README.md)。

---

## 資料層

### `StockDataFetcher` (`src/data/fetcher.py`)

整合三個資料來源：SQLite 的歷史日線與股票名稱、yfinance 的當日 1 分鐘資料，以及 twstock 的台股市場別對照。

`_format_ticker` 會依序從資料庫、twstock 與原始輸入解析代碼。輸入先轉為大寫，資料庫查詢則同時嘗試原字串、`.TW`、`.TWO` 與點號改為連字號的形式，以對應台股、美股及 Yahoo 的代碼格式。

`fetch_historical_data` 以 `AdjClose / Close` 回推 OHLC，消除除權息與分割造成的跳空。收盤價缺值或非正數時，該列維持原值，避免只調整收盤價而破壞 OHLC 關係。

### `sync.py` (`src/data/sync.py`)

負責 yfinance 到 SQLite 的批次寫入，供兩支回補腳本共用。主要處理下載結果拆解、資料列轉換、批次 upsert、待回補股票篩選與完整歷史重抓判斷。

`needs_full_refresh` 在 upsert 前執行，檢查兩種情況：Yahoo 回溯改寫 `Adj Close` 導致調整基準漂移，或既有歷史資料出現缺口。缺口檢查優先於漂移比對，因為沒有日期重疊時無法取得後者的比對基準。

### 排程腳本 (`scripts/`)

- `seed_stocks.py`：建立或更新台股、美股與全球指數清單，美股代碼以 `-` 取代 `.`。
- `historical_backfill.py`：回補完整歷史 K 線，`force=True` 忽略回補戳記。
- `daily_updater.py`：更新每日行情，並回報需要完整重抓的股票。

---

## 儲存層

### `database.py` + `sql/` (`src/database/`)

封裝 SQLite CRUD，並集中管理跨模組或較長的 SQL。所有連線都必須透過 `connect_db()` 建立，因為 `PRAGMA foreign_keys = ON` 是連線層級設定；未啟用時，`daily_prices` 可能接受不存在於 `stocks` 的代碼。

資料表的重要約束如下：

- `stocks.last_backfilled` 記錄最後一次成功回補的時間，upsert 股票資料不會清除它。
- `daily_prices` 以 `UNIQUE(ticker, date)` 保證每日每檔股票只有一列，這也是 `ON CONFLICT(ticker, date)` 的依據。
- 價格資料使用 `ON CONFLICT ... DO UPDATE`，而非 `INSERT OR REPLACE`，以保留自增 `id`，避免刪除舊列時觸發外鍵連鎖影響。

---

## 分析層

### 指標 (`src/quant/indicator.py`)

`compute_indicators(ticker, data, columns)` 是唯一入口，將指定指標直接寫入 DataFrame；`columns=None` 代表計算全部指標。

`MACD_z` 定義為 DIF 除以自身 60 列的滾動標準差，用來表示 DIF 相對近期波動的程度。計算時不扣除滾動平均值，因為 MACD 零線是固定且有意義的趨勢基準。

### 規則 (`src/quant/rule.py`)

規則只回報目前的方向偏向：-1 為空、+1 為多、0 為沒有意見，不掌握部位，也不負責反手。規則宣告 `name`、`required_columns`、`warmup` 與 `lookback`，依序列變化的狀態在 `reset()` 中重設。

| 角色 | 規則 | 形式 | 用途 |
|------|------|------|------|
| 趨勢 | `SMATrend`、`MACDZeroLine` | 連續 | 衡量趨勢方向與強度 |
| 進場 | `RSIReversal`、`StochCross`、`EMACross` | 事件 | 提供進場時機 |
| 出場 | `BollingerBand`、`MACDSignalCross` | 事件 | 提供離場理由 |
| 包裝 | `Decay` | 依內層 | 使事件訊號逐步減弱 |

趨勢規則使用連續值，而非只回報多空二值。組合策略在零點附近設緩衝帶，避免價格在均線附近的小幅波動造成閘門反覆切換。

`CrossRule` 以 `_CrossTracker` 記錄兩線上次嚴格分開時的相對位置，因此相等後折返不會被誤判為新的交叉。`precision` 只適合用於有最小跳動單位的價格線；MACD 線隨價格尺度變化，不應任意四捨五入。

`Decay(rule, half_life, floor)` 只應包裝事件規則，不應包裝持續表態的狀態規則。沉默一根 K 棒後，意見乘以 `0.5 ** (1 / half_life)`；低於 `floor` 時歸零。`floor` 同時避免微弱殘留訊號被列為報表理由。每條規則只包裝一次，並共用同一個包裝實例，以免帶狀態的內層規則被重複求值。

### 策略 (`src/quant/strategy.py`)

`Strategy` 宣告指標需求、暖身列數與可見歷史長度，子類別實作 `signal(history, position)`。能以欄位向量化計算的內容留在 `compute_indicators`；策略只處理依序列或部位變化的邏輯。

`CompositeStrategy` 將規則分為三組：

| 分組 | 作用 |
|------|------|
| `trend` | 閘門，決定允許開倉的方向；另一側會被否決 |
| `entry` | 觸發，決定何時進場 |
| `exit` | 出場，持倉的反方向視為離場理由 |

閘門採 `trend_enter` / `trend_exit` 遲滯切換，進出場則分別使用 `entry_threshold` / `exit_threshold`。未提供趨勢規則代表放行兩側；已提供但尚未表態則暫時擋住兩側。每條規則每根 K 棒只求值一次，規則索引而非規則實例放入分組，以避免跨組重複推進狀態。

`gated_strategy()` 使用長週期 `SMATrend(200)` 作閘門，並以 RSI、KD、EMA 事件觸發進場；`voting_strategy()` 則保留為等權投票的比較基準。

---

## 回測與模型

### `BacktestEngine` (`src/quant/backtest.py`)

引擎只接收呼叫端準備好的 DataFrame，不直接依賴資料來源。每日處理順序為：

1. 執行前一日收盤產生的訊號，於今日開盤成交。
2. 檢查盤中停損，跳空時以開盤價成交。
3. 以今日收盤計算並記錄權益。
4. 依今日收盤產生下一交易日的訊號。

`run()` 先計算策略需要的指標並移除無效列，再從 `start` 切出實際回測區間。策略取得的 `history_window` 截至當日，且可回看區間開始前的資料，因此不會產生前視偏誤。指標暖身資料由 `required_history_days(period_days)` 額外取得，不佔用指定區間；資料不足時拋出 `InsufficientDataError`。

帳戶由現金與一筆整股部位組成，支援做多與做空。零頭保留在現金中；若無法買入一股則不成交。權益以 `cash ± shares × price` 計算，逐筆 `Trade.profit_and_loss` 的總和與最終權益一致。`PERIOD_DAYS` 是回測區間的唯一定義來源，Discord 指令只取用其中的部分項目。

### 模型 (`src/models/`)

- `Signal`：交易動作、觸發條件與當下指標值；`conditions` 的鍵是供報表顯示的描述文字，不是程式識別字。
- `Position`：進場日期、價格、方向、股數與進場訊號。
- `Trade`：完整的一次進出場，損益與報酬率依同一筆交易及方向計算。
- `BacktestResult`：交易清單、權益曲線與總報酬率、勝率、最大回撤等彙總結果。

---

## 呈現層

### 圖表與報表

`visualizer.py` 以 `Agg` backend 產生日線、盤中與回測 PNG。均線先計算完整歷史再切片；盤中圖以前收 `baseline` 判定漲跌，配色遵循台股的紅漲綠跌慣例。

`html_report.py` 提供共用的 HTML 文件、表格與數值格式化功能。表頭、儲存格、標題與副標題都由本層負責 HTML 跳脫；`body` 只接受由 `html_table` 產生的內容。`write_report` 統一組合匯出路徑並過濾檔名，避免輸出離開 `exports/`。

### `dc_bot_view.py` (`src/bot/dc_bot_view.py`)

`build_snapshot` 組合歷史與盤中資料，計算 Embed 所需的 RSI 與前收基準；有盤中資料時現價取最後一筆，否則取最後收盤。`DiscordStockChart` 提供日線／分時圖切換，5 分鐘後清理訊息。`send_*` 函式負責組合 Embed 與圖表附件。

---

## Discord 指令資料流

阻塞操作（SQLite、yfinance、指標計算與繪圖）透過 `asyncio.to_thread` 執行，彼此獨立的工作以 `asyncio.gather` 並行。

### `/stock`

```
輸入 ticker
  → StockDataFetcher 正規化代碼
  → 並行取得歷史與盤中資料
  → build_snapshot 組合價格、RSI 與前收
  → 並行產生日線與分時圖
  → send_stock_response 回覆 Embed
```

歷史資料為空才視為查詢失敗；沒有盤中資料時仍回覆日線與指標，分時切換按鈕停用。

### `/backtest`

```
輸入 ticker、strategy、period
  → 從 PERIOD_DAYS 取得區間長度
  → 額外取得指標暖身資料
  → BacktestEngine.run(ticker, data, start)
  → 產生回測圖並回覆績效 Embed
```

資料不足時，指令將 `InsufficientDataError` 的訊息原文回覆。

---

## 測試

測試全程離線，使用記憶體內 SQLite 與合成 OHLCV，不呼叫 yfinance，也不依賴本機資料庫。測試重點包括：

- 回測時序、隔日開盤成交、反手、停損、整股部位與權益曲線。
- 指標暖身、歷史視窗、前視偏誤防護與策略狀態重設。
- 規則的方向讀值、交叉事件、意見衰減與宣告的欄位／暖身。
- 組合策略的閘門否決、遲滯、門檻、跨組求值與預設組合。
- 資料同步的歷史缺口、調整基準漂移與回補戳記。
- 代碼正規化、OHLC 調整、報表跳脫與匯出檔名安全性。

執行方式請見 [README](../README.md#測試)。