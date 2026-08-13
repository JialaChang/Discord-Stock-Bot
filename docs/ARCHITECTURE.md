# 架構與模組說明

各模組職責、關鍵設計規則與 Discord 指令資料流。模組相依關係見 [UML.md](./UML.md)，安裝與執行見 [README](../README.md)。

---

## 資料層

### `StockDataFetcher` (`src/data/fetcher.py`)

**讀取**門面，整合三個資料源：SQLite（歷史日線、股票名稱）、yfinance（當日 1 分鐘盤中）、twstock（台股市場別對照）。

`_format_ticker` 的解析順序為資料庫 → twstock → 原輸入。輸入先轉大寫，因 SQLite 的 `=` 區分大小寫。資料庫查詢候選包含原字串、`.TW`、`.TWO`，以及點號改為連字號的形式。

> **dash 形式僅為查詢候選，不作為回傳值。** Yahoo 將美股股別寫作 `BRK-B`，`seed_stocks` 亦以該形式收錄，故比對得到即回傳收錄值。

`fetch_historical_data` 讀出後即以 `AdjClose / Close` 比率回推 OHLC，消除配息與分割造成的跳空。收盤價缺值或非正數的列無可用比率，全列保持原值 —— 僅調整收盤會使其落在自身高低點之外。

### `sync.py` (`src/data/sync.py`)

yfinance → SQLite 的**寫入**路徑，由兩支回補腳本共用，統一批次資料的拆解與寫入方式。

| 函式 | 說明 |
|------|------|
| `download_ohlcv(tickers, period)` | 批次下載；失敗回傳空 DataFrame，不中斷後續批次 |
| `extract_ticker_frame(data, ticker)` | 依欄位是否為 MultiIndex 判斷拆法 |
| `records_from_frame` / `upsert_records` | 轉為參數 tuple 並批次 upsert |
| `fetch_all_tickers` / `fetch_pending_tickers(max_age_days)` | 全部股票／未回補或戳記過期者 |
| `mark_backfilled(conn, tickers)` | 蓋上回補戳記，僅限成功寫入者 |
| `needs_full_refresh(conn, ticker, frame)` | 判斷是否須重抓完整歷史 |

**`needs_full_refresh` 的兩種情境**：

1. **還原收盤價遭改寫** —— Yahoo 每逢除息或分割會回溯改寫整段歷史的 `Adj Close`。下載區間內的列由 upsert 修正，其餘較舊的列仍留在舊基準。
2. **歷史存在缺口** —— 更新腳本停擺超過下載區間長度時，期間資料未被取得。

> 兩項檢查有先後：缺口檢查須先於漂移比對。下載區間與既有資料無日期重疊時，漂移比對缺乏比對基準，而該情形正對應歷史最可能過期的狀況。
>
> 呼叫時機亦有先後：`needs_full_refresh` 必須在 upsert **之前**，否則寫入會覆蓋掉待比對的值。

### 排程腳本 (`scripts/`)

| 腳本 | 職責 |
|------|------|
| `seed_stocks.py` | 寫入台股、美股與全球指數清單；美股代碼寫入時將 `.` 換為 `-` |
| `historical_backfill.py` | 回補歷史 K 線；`force=True` 忽略回補戳記 |
| `daily_updater.py` | 更新每日行情，並回傳須重取完整歷史的股票清單 |

---

## 儲存層

### `database.py` + `sql/` (`src/database/`)

底層 SQLite CRUD，SQL 語句集中管理。

| 元件 | 說明 |
|------|------|
| `sql/*.sql` | 多行或跨模組共用的 SQL（單行的留在 Python 內） |
| `load_sql(name)` | 讀取 `sql/<name>.sql`，`lru_cache` 快取 |
| `connect_db()` | 開啟連線並設定 `PRAGMA foreign_keys = ON` |
| `get_daily_prices(ticker, limit)` | 取最近 `limit` **列** |

> **所有連線一律經由 `connect_db()` 取得。** `PRAGMA foreign_keys` 屬連線層級而非資料庫屬性，`schema.sql` 宣告的外鍵在未設定該 pragma 的連線上不生效，此類連線會靜默接受孤兒 `daily_prices` 列。

### 資料表約束

- `stocks.last_backfilled`：回補戳記。`upsert_stock` 僅更新 `name` / `market`，重新 seed 不會清除戳記。
- `daily_prices` 的 `UNIQUE(ticker, date)`：`ON CONFLICT(ticker, date)` 的前提。
- upsert 採 `ON CONFLICT ... DO UPDATE` 而非 `INSERT OR REPLACE`，以保留自增 `id` 並避免外鍵連鎖刪除。

---

## 分析層

### `indicator.py` (`src/quant/indicator.py`)

單一入口 `compute_indicators(ticker, data, columns)`：依 `columns` 原地寫入指標，`None` 代表全算，回傳 `None`。回測是唯一使用者。

> **`MACD_z` 為 DIF 除以自身 60 列滾動標準差**，讀作「DIF 相當於自己近期的幾個標準差」，因而跨標的、跨價位皆可比較。刻意不減去滾動均值：零線本身即為有意義的絕對原點，中心化會將其抹去。

### `Rule` (`src/quant/rule.py`)

策略之下的一層。一條規則只回答「此刻偏向哪一側」，值域 -1（空）至 +1（多），0 代表沒有意見。規則不知當前部位，故無從表達反手 —— 那是策略的職責。介面沿用 `Strategy`：宣告 `name`、`required_columns`、`warmup`、`lookback`，路徑相依的狀態置於 `reset()`。

> **`name` 原樣進入 `Signal.conditions`，並隨之抵達 HTML 報表。** 故其為供人閱讀的敘述而非識別字，任何程式不得以其分支。

| 角色 | 規則 | 形狀 | 說明 |
|------|------|------|------|
| 趨勢 | `SMATrend(length, band)` | 連續 | 收盤價偏離長期均線的幅度，`band` 為滿分距離 |
| 趨勢 | `MACDZeroLine(full)` | 連續 | `MACD_z / full`，即 DIF 距零線幾個標準差 |
| 進場 | `RSIReversal(oversold, overbought)` | 事件 | 超賣看反彈、超買看回檔 |
| 進場 | `StochCross(low, high)` | 事件 | KD 交叉，僅在高低檔計入 |
| 進場 | `EMACross(fast, slow)` | 事件 | 快慢 EMA 交叉 |
| 出場 | `BollingerBand` | 事件 | 觸及軌道視為漲跌過度延伸 |
| 出場 | `MACDSignalCross` | 事件 | DIF 與 DEM 交叉，即柱狀體變號 |

> **趨勢規則採連續值而非二值。** 組合策略以零附近的帶狀區間開關閘門，階梯函數會使閘門在每根貼近均線的 K 上翻面。

> **`MACDZeroLine` 與 `MACDSignalCross` 讀的是兩件事。** 零線是趨勢讀數（快 EMA 位於慢 EMA 之上），兩線交叉則是動能事件；前者屬趨勢桶，後者屬出場桶。

`CrossRule` 為所有交叉規則的基底，其 `_CrossTracker` 記住兩線上次嚴格分開時何者在上 —— `低 → 相等 → 高`（真交叉）與 `高 → 相等 → 高`（貼合後折返）於相鄰兩列而言是相同的輸入。`lookback = 2` 僅用於在首根 K 補上初始記憶。

> **`precision` 決定兩線比較至小數第幾位，`None` 為精確比較。** 唯有以價格計價的線才有最小跳動單位，小於該單位的差距方可視為雜訊；`MACD_dif` 與 `MACD_dem` 隨價格縮放，取至分位會在低價標的上將大量真實交叉壓成相等。

### `Strategy` (`src/quant/strategy.py`)

抽象基底，子類實作 `signal(history, position)` 並宣告 `required_columns`、`warmup` 與 `lookback`。路徑相依的狀態置於 `reset()`，由引擎於每輪回測前呼叫。

| 類別 | `required_columns` | `warmup` | `lookback` | 策略 |
|------|--------------------|----------|------------|------|
| `RSIStrategy` | `["RSI"]` | 14 | 1 | RSI 超買 / 超賣 |
| `EMAStrategy` | `["EMA_5", "EMA_20"]` | 20 | 2 | EMA5/20 交叉 |
| `CompositeStrategy` | 所持規則的聯集 | 最深者 | 最長者 | 依角色組合多條規則 |

> **能寫成欄位者留在 `compute_indicators` 向量化計算一次。** `history` 供路徑相依的邏輯使用（進場後最高價、部位年齡），而非讓策略每根 K 重算一次本質上是指標的東西。

#### `CompositeStrategy`

規則只表達偏向，該偏向作何解讀則由其所屬的桶決定，而非全數併入同一張等權票：

| 桶 | 角色 | 判定 |
|----|------|------|
| `trend` | 閘門 | 分數決定可開的一側，另一側無論訊號多強皆否決 |
| `entry` | 觸發 | 閘門放行後，決定何時動作 |
| `exit` | 出場 | 偏向持倉的反方向即為離場理由 |

四個門檻：`trend_enter` / `trend_exit` 為閘門的開關界線，`entry_threshold` / `exit_threshold` 為兩桶各自的成立強度。

> **一票否決無法以加權平均表達**，此即本組合方式與等權投票的分野。`voting_strategy` 作為對照組保留於同一檔案，供實測比較。

> **同桶規則的形狀須一致**（全連續或全事件），因為混合它們的是平均。每根 K 皆表態的規則會壓過偶爾才發言者，無論兩者權重為何。

> **閘門帶有遲滯。** `trend_exit` 與 `trend_enter` 之間維持上一次的決定。少了這段帶，分數每次擦過門檻閘門即翻面，而每次翻面都強制平倉。

> **`gate` 為 `None` 有兩種相反的意義。** 未給趨勢規則是「沒有意見」，應放行兩側；已給而尚未表態是「現在不行」，應擋住兩側。兩者僅能由 `self._trend` 是否為空區分，否則無閘門的策略一筆倉都開不了。

> **每條規則每根 K 僅求值一次，且先於任何決定。** 帶狀態的規則必須看過每一根，不因某個桶當下用不到而略過；同一實例置入兩個桶時，第二次 `bias()` 會使交叉追蹤器越過其正要回報的交叉，故桶內存放的是規則索引而非規則本身。

### `BacktestEngine` (`src/quant/backtest.py`)

逐日迭代 OHLCV，與策略解耦（初始化時注入 `Strategy`），亦與資料來源解耦 —— 引擎不接觸 `StockDataFetcher`，`run()` 接收呼叫端備妥的 DataFrame。

```
每日迴圈：
  1. 執行昨日 pending signal → 今日開盤成交
  2. 盤中停損（當日即時，跳空則以開盤價成交）
  3. 以今日收盤記錄 equity
  4. 依今日收盤產生明日 pending signal
```

- 只計算並 `dropna` 策略宣告的 `required_columns`
- 以現金與整股部位記帳（`cash` + `_mark_to_market()`），支援做多 / 做空
- `REVERSE_LONG` / `REVERSE_SHORT` 於同一筆開盤成交中平掉反向倉並開立新倉
- 結束時未平倉部位以最後一日收盤強制平倉

> **`run()` 同時持有兩個 DataFrame。** `data` 為算完指標並 `dropna` 後的全部列，`window` 為 `start` 之後實際回測並寫入報表的區間；策略每根 K 收到的 `history_window(data, i, lookback)` 切自前者，區間首根 K 因而仍能回看 `start` 之前。

> **策略可見的歷史右端恆為當日。** `history_window` 取 `i + 1` 為開區間，前視偏誤於構造上即不可能發生，無須仰賴策略自律。

> **指標暖身另行取得，不佔用回測區間。** 呼叫端依 `required_history_days(period_days)` 決定取得天數並將 `start` 傳入 `run()`，待指標計算完成後才截去暖身段。若僅取得使用者指定的區間，短週期回測的可用列數將被暖身消耗殆盡。資料不足時拋出 `InsufficientDataError`，其訊息會原文回覆使用者，故須寫明處置方式。

`PERIOD_DAYS` 為回測區間長度的**唯一定義來源**；Discord 指令僅引用其部分 key，不另行定義天數。

> **帳戶僅由現金與一筆整股部位構成。** `cash` 為唯一的價值儲存處，每根 K 的權益即 `_mark_to_market()` 算出的 `cash ± shares × price`；做空的賣出價金亦存入 `cash`，負債為欠還的股票，故同一條估值式涵蓋多空兩側，且以現金定量使其無槓桿而全額擔保。`_open_position()` 買進 `int(cash // price)` 股，零頭閒置於現金形成真實的資金拖累，股價高於整個帳戶時不成交而非成交零股。`Trade.profit_and_loss` 因而恰為該筆交易使 `cash` 變動的金額，`INITIAL_CAPITAL + Σ 損益` 於構造上等於最終權益。

### `Signal` / `Position` / `Trade` / `BacktestResult` (`src/models/trade.py`)

| 類別 | 說明 |
|------|------|
| `Signal` | 策略訊號：`action`、`conditions`、觸發時 `values`。報表由 `conditions` 的真值鍵導出進出場原因 |
| `Position` | 進場快照；含 `shares`（持有或借券賣出的整股數） |
| `Trade` | 單筆交易；`shares`、`profit_and_loss`、`return_on_investment`、`is_profit` |
| `BacktestResult` | 回測彙總；`total_return`、`win_rate`、`max_drawdown`、`trade_count` |

> **`conditions` 的鍵為敘述文字，非識別字。** 其原樣抵達 HTML 報表的「進出場原因」欄，故一律寫成可讀的敘述（`"EMA golden cross"`、`"Stop loss"`），任何程式不得以其分支。組合策略於此填入促成該決定的規則 `name`，未表態或表態相反者不列名。

> **`Trade.profit_and_loss` 是方向語意的唯一定義處。** `return_on_investment` 以 `entry_price × shares` 除之導出，故同一列報表中的金額與百分比不可能描述不同的交易。兩者皆不設下限：跳空穿越擔保品的空單確實虧損超過其投入（停損以開盤價成交），截斷將使報酬率與金額互相矛盾。現金因而可能轉負，此時 `int(cash // price)` ≤ 0，帳戶自行停止開倉。

---

## 呈現層

### `StockSnapshot` (`src/models/stock.py`)

Embed 所需的最小快照。`previous_close` 亦供盤中圖著色使用。`rsi_value` 為 `None` 時 `rsi_str` 顯示 `N/A`。

### `visualizer.py` (`src/utils/visualizer.py`)

生成 in-memory PNG，使用 `Agg` backend。

| 函式 | 說明 |
|------|------|
| `generate_history_chart` | 日線 K 線 + MA5/10/20 + 成交量 |
| `generate_intraday_chart` | 盤中折線，以 `baseline` 為界紅漲綠跌 |
| `generate_backtest_chart` | K 線 + 多空進出場標記 + 權益曲線 |

- 均線由本模組自行計算，先算全歷史再切片 —— 反序會使最長均線在圖左緣缺值。
- `baseline`（前收）為必填參數，台股慣例以前收為準。

> **配色慣例：紅漲綠跌**（台股慣例）。

### `dc_bot_view.py` (`src/bot/dc_bot_view.py`)

- `build_snapshot`：由 fetcher 取得的兩個 frame 組出 `StockSnapshot`
- `DiscordStockChart`：持有圖表 bytes 的 `View`，按鈕切換日線 / 分時，逾時 5 分鐘清理
- `send_stock_response` / `send_backtest_response`：組裝 Embed

`build_snapshot` 自行計算 Embed 唯一需要的 RSI(14)，現價於有盤中資料時取盤中最後一筆，否則取最後收盤；前收固定取「現價日期之前」最後一個收盤，Embed 的漲跌幅與分時圖配色都以它為基準。

`send_*` 兩者標題均由 `display_name(name, ticker)` 組裝為 `name (ticker)`。`fetch_stock_name()` 查無名稱時回傳代碼本身，此時只顯示代碼不重複括號。

### `html_report.py` (`src/utils/html_report.py`)

共用 HTML 報表層，呼叫端只提供資料、不寫任何標籤。使用者為 `database.py`（價格報表）與 `BacktestEngine`（績效報表）。

| 元件 | 說明 |
|------|------|
| `templates/report.html` | 靜態外殼 + CSS，以 `$title` / `$meta` / `$body` 注入 |
| `html_document(title, body, subtitle)` | 注入模板 |
| `html_table(headers, rows)` | 由資料建表；儲存格為純文字或 `(文字, CSS class)` tuple |
| `fmt_num` / `fmt_int` | 數值格式化，`None` 顯示 `N/A` |
| `write_report(html_text, name)` | 寫入 `exports/<name>_<時間戳>.html`，回傳路徑 |

> **逃脫由本層負責。** `html_table` 的表頭與儲存格、`html_document` 的 `title` 與 `subtitle` 均經 `html.escape`，`body` 是唯一以原始 HTML 處理的參數，其來源應為 `html_table`。
>
> **`write_report` 是組裝匯出路徑的唯一位置。** 檔名以白名單 `[^A-Za-z0-9._-]` 過濾並剝除開頭點號，代碼因而無法將輸出導向 `exports/` 之外。

---

## Discord 指令資料流

所有阻塞呼叫（SQLite、yfinance、指標計算、繪圖）均以 `asyncio.to_thread` 卸載，彼此獨立者以 `asyncio.gather` 併發。

### `/stock`

```
使用者輸入 ticker
    → StockDataFetcher._format_ticker()   # 代碼正規化
    → gather()                            # 併發：SQLite 歷史 + yfinance 盤中
    → build_snapshot()                    # RSI(14)、前收基準 → StockSnapshot
    → gather()                            # 併發：日線圖 +（有盤中資料才畫）分時圖
    → send_stock_response()               # Embed + 可切換 View（5 分鐘逾時）
```

僅歷史資料為空視為查詢失敗。無盤中資料時仍正常回覆，切換按鈕停用並標示無法使用。

### `/backtest`

```
使用者輸入 ticker、strategy、period
    → PERIOD_DAYS[period]                             # 區間長度唯一來源
    → engine.required_history_days(period_days)       # 區間 + 指標暖身
    → StockDataFetcher.fetch_historical_data(days)
    → BacktestEngine.run(ticker, data, start)         # 算完指標後截去暖身段
    → generate_backtest_chart()
    → send_backtest_response()
```

資料不足時 `run()` 拋出 `InsufficientDataError`，指令將其訊息原文回覆。

---

## 測試

`tests/` 全程離線：資料庫為 in-memory SQLite，行情為 `conftest.py` 產生的合成 OHLCV，不呼叫 yfinance，亦不讀取本機 `stock_data.db`。執行方式見 [README](../README.md#測試)。

回測執行規則以 `ScriptedStrategy` 逐根 K 棒驅動引擎，使進出場時點與成交價可精確指定，不受任何真實指標數值影響。

規則與策略的決策則於 `test_rule.py`、`test_strategy.py` 直接餵入手寫的指標列驗證。有狀態者無法由單次呼叫判定，故以 `replay()` 逐根重播；其切片一律取自引擎的 `history_window`，測試因而不會漂移成驗證一個引擎已不再提供的輸入。

組合邏輯另於 `test_composite.py` 以 `StubRule` 隔離驗證：偏向由劇本給定，全程不涉及指標，受測者僅為意見到動作的映射。

本文所述設計規則各有對應測試守護：

| 設計規則 | 守護測試 |
|----------|----------|
| 金額損益與報酬率描述同一筆交易，且皆不設下限 | `test_models.py::TestTrade` |
| 倉位以整股計、零頭留為現金、買不起則不成交 | `test_backtest.py::TestPositionSizing` |
| 逐筆損益總和等於最終權益 | `test_backtest.py::TestEquityCurve` |
| 漲跌幅由前收導出；RSI 缺值顯示 `N/A` | `test_models.py::TestStockSnapshot` |
| 訊號於當日收盤產生、隔日開盤成交 | `test_backtest.py::TestOrderExecution` |
| 反手於同一開盤價平舊倉並開新倉 | `test_backtest.py::TestReversal` |
| 停損以停損價成交，跳空則以開盤價成交 | `test_backtest.py::TestStopLoss` |
| 指標暖身另行取得，不佔用回測區間 | `test_backtest.py::TestIndicatorWarmup` |
| 策略僅見至多 `lookback` 列且右端恆為當日 | `test_backtest.py::TestHistoryWindow`、`TestStrategyHistory` |
| 單輪策略狀態不跨輪殘留 | `test_backtest.py::TestStrategyLifecycle` |
| 交叉為事件，貼合後折返不觸發訊號 | `test_rule.py::TestEMACross` |
| 規則宣告的欄位算得出、`warmup` 不樂觀、`bias` 不越界 | `test_rule.py::TestDeclaredColumnsAndWarmup` |
| 閘門為否決而非投票，未決時擋住兩側 | `test_composite.py::TestEntryNeedsGateAndTrigger`、`TestNoTrendRules` |
| 閘門帶遲滯，帶內維持上一次的決定 | `test_composite.py::TestGateHysteresis` |
| 出場相對持倉判讀，且永不轉為反手 | `test_composite.py::TestExit` |
| 每條規則每根 K 求值一次，跨桶共用者不重複推進 | `test_composite.py::TestEveryRuleSeesEveryBar` |
| 桶內宣告合併為聯集／最深／最長 | `test_composite.py::TestDeclarationsAggregate` |
| `needs_full_refresh` 的缺口檢查先於漂移比對 | `test_sync.py::TestNeedsFullRefresh` |
| 僅成功寫入者蓋上回補戳記 | `test_sync.py::TestBackfillStamps` |
| 代碼正規化；未收錄的帶點代碼原樣送出 | `test_fetcher.py::TestTickerNormalization` |
| 還原權值回推略過不可用的列 | `test_fetcher.py::TestAdjustedPriceReconstruction` |
| 報表逃脫；匯出檔名無法脫離 `exports/` | `test_html_report.py` |
| 標題組裝；無名稱時不重複顯示代碼 | `test_bot_view.py::TestDisplayName` |
