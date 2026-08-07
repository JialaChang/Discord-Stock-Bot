# Stock Bot

![Python Version](https://img.shields.io/badge/python-3.13%2B-blue)
![Managed by uv](https://img.shields.io/badge/managed%20by-uv-purple)
![License](https://img.shields.io/badge/license-MIT-green)

量化交易回測框架，用於驗證股票交易策略。支援**雙向交易**（做多／做空）、**自訂策略**、**止損機制**與績效指標（報酬率、勝率、最大回撤）。

核心特性：
- **累積倍率資產追蹤**：避免複利誤差，精確模擬資金曲線
- **策略與引擎解耦**：新增策略無需改動回測邏輯
- **完整交易紀錄**：每筆交易的進出場、價格、訊號條件與損益
- **Discord 機器人**：可直接於 Discord 中執行指令並輸出 Embed

<div>
  
  [![邀請機器人](https://img.shields.io/badge/邀請機器人到伺服器-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1494994206425612399)
  
  <img src="./docs/stock.png" height="300" alt="">
  <img src="./docs/backtest.png" height="300" alt="">
</div>

---

## 技術架構

```
stock-bot/
├── src/
│   ├── bot/          # Discord 斜線指令 & UI 元件
│   ├── data/         # 股票資料查詢（fetcher）與寫入同步（sync）
│   ├── quant/        # 技術指標計算 & 回測引擎
│   ├── database/     # 資料庫初始化與 CRUD（SQL 語句集中於 sql/*.sql）
│   ├── models/       # 共用資料類別
│   └── utils/        # 圖表生成 & HTML 報表
├── scripts/
│   ├── seed_stocks.py           # 匯入台美股與全球指數基本清單
│   ├── historical_backfill.py   # 回補歷史 K 線至資料庫
│   └── daily_updater.py         # 更新每日數據至資料庫
├── tests/            # 離線回歸測試（pytest）
└── main.py           # 啟動入口
```

各模組職責請見 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)，完整類別關聯與方法簽章請見 [docs/UML.md](./docs/UML.md)

---

## 資料庫結構

```sql
-- 股票基本資料
stocks (
    ticker           TEXT PRIMARY KEY,
    name             TEXT,
    market           TEXT,
    last_backfilled  TEXT   -- 最後一次成功回補的時間戳，NULL 代表未回補
)

-- 歷史日線（OHLCV）
daily_prices (
    id                  INTEGER PRIMARY KEY,
    ticker              TEXT REFERENCES stocks(ticker),
    date                TEXT,
    open_price          REAL,
    high_price          REAL,
    low_price           REAL,
    close_price         REAL,
    adjust_close_price  REAL,  -- 除權息調整後收盤價
    volume              REAL,
    UNIQUE(ticker, date)
)
```

> **除權息調整**：查詢歷史資料時，系統以 `AdjClose / Close` 的比率回推開高低價，消除配息或股票分割造成的圖表跳空缺口。

> **還原權值基準一致性**：Yahoo 每次除息或分割都會回頭改寫**整段歷史**的 `Adj Close`。因此 `daily_updater.py` 在寫入前會比對新舊還原收盤價，若偵測到基準改變或既有歷史的結束日早於下載區間，則將該檔股票交給 `historical_backfill.py` 重抓完整歷史。

> 完整 schema 定義於 [src/database/sql/schema.sql](./src/database/sql/schema.sql)；跨模組共用的 SQL 皆以 `.sql` 檔集中於該目錄，由 `load_sql()` 載入。

---

## 快速開始

### 環境需求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) 套件管理器

### 安裝

```bash
# 安裝依賴
uv sync

# 建立 .env（參考下方欄位說明）
cp .env.example .env
```

`.env` 變數欄位：

| 變數 | 說明 |
|------|------|
| `DISCORD_TOKEN` | Discord Bot Token（從 [Discord Developer Portal](https://discord.com/developers/applications) 取得） |
| `GUILD` | （選填）測試伺服器 ID，設定後斜線指令立即生效，省去全域同步的 1 小時等待 |

### 初始化資料庫

```bash
python scripts/seed_stocks.py          # 寫入股票基本清單
python scripts/historical_backfill.py  # 回補歷史 K 線（需一段時間）
```

> 回補以 `stocks.last_backfilled` 判斷是否略過，只有成功寫入的股票才會蓋上時間戳，下載失敗的下次執行會自動重試。`backfill_history(force=True)` 可忽略時間戳強制全部重補。

### 每日更新資料庫（建議使用工具排程）

```bash
python scripts/daily_updater.py
```

### 執行

```bash
python src/bot/dc_bot.py         # 啟動 Discord 機器人
python src/quant/backtest.py     # 執行回測（CLI界面）
python src/database/database.py  # 操作資料庫（CLI界面）
```

> **HTML 報表**：回測完成後可選擇匯出 HTML 績效報表；資料庫查詢超過 50 筆時自動改為匯出 HTML 報表。檔案輸出至 `exports/`

### 測試

```bash
uv sync --dev   # 安裝含 pytest 的開發依賴
uv run pytest   # 執行全部測試
```

測試全程離線：以 in-memory SQLite 與合成 OHLCV 資料驗證，不呼叫 yfinance，也不依賴本機的 `stock_data.db`，因此重建資料庫不影響測試結果。

| 檔案 | 涵蓋範圍 |
|------|----------|
| `tests/test_models.py` | 損益倍率、單筆交易報酬率、回測績效指標 |
| `tests/test_sync.py` | 批次資料拆解、還原收盤價漂移與歷史缺口偵測、回補時間戳 |
| `tests/test_fetcher.py` | 代碼正規化、還原權值回推、異常列處理 |
| `tests/test_backtest.py` | 訊號隔日開盤成交、多空停損成交價、指標暖身、資料不足例外 |

<div>
  <img src="./docs/backtest_html.png" height="400" alt="回測績效 HTML 報表：績效指標與逐筆交易明細">
  <img src="./docs/price_html.png" height="400" alt="歷史價格 HTML 報表：OHLCV 明細，收盤價依漲跌上色">
</div>

---

## Discord 指令說明

| 指令 | 說明 |
|------|------|
| `/stock <ticker>` | 查詢股票資訊、技術指標與 K 線圖 |
| `/backtest <ticker> <strategy> <period>` | 對指定股票執行策略回測，回傳績效指標與圖表（K 線 + 進出場標記 + 權益曲線） |

> `/stock` 的盤中資料為選用：若遇到限流、網路錯誤或本身不提供分時的標的，仍會顯示日線圖與各項指標，切換按鈕標示為無法使用。
>
> `/backtest` 會額外抓取指標暖身所需的歷史資料，暖身不佔用指定的回測區間；倘若資料不足時會回覆錯誤原因。

輸入格式範例：

| 輸入 | 自動解析為 | 說明 |
|------|-----------|------|
| `2330` | `2330.TW` | 台積電（台股上市） |
| `5274` | `5274.TWO` | 信驊（台股上櫃） |
| `SPCX` | `SPCX` | SpaceX 美股 |
| `nvda` | `NVDA` | 一律轉大寫後查詢，大小寫皆可 |
| `BRK.B` | `BRK-B` | 波克夏 B 股（Yahoo Finance 格式轉換） |
| `^GSPC` | `^GSPC` | S&P 500 指數 |

> 各指令的內部資料流與模組職責請見 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)

---

## 支援市場

### 台灣股票

- **上市（TSE）**：輸入純代碼，自動補齊 `.TW`
- **上櫃（TPEX）**：輸入純代碼，自動補齊 `.TWO`

### 美國股票

- 直接輸入代碼（`SPCX`、`AAPL`、`NVDA` 等）
- 含小數點代碼（如 `BRK.B`）自動轉換為 Yahoo Finance 格式 `BRK-B`

### 全球主要指數

`scripts/seed_stocks.py` 會一併寫入下列指數：

| 區域 | 代碼 | 指數 |
|------|------|------|
| 美國 | `^GSPC` | S&P 500 |
| 美國 | `^DJI` | 道瓊工業 |
| 美國 | `^IXIC` | 那斯達克綜合 |
| 美國 | `^NDX` | 那斯達克 100 |
| 美國 | `^RUT` | 羅素 2000 |
| 美國 | `^SOX` | 費城半導體 |
| 美國 | `^VIX` | 恐慌指數（VIX） |
| 亞太 | `^TWII` | 台灣加權 |
| 亞太 | `^HSI` | 恆生 |
| 亞太 | `000001.SS` | 上證綜合 |
| 亞太 | `399001.SZ` | 深證成指 |
| 亞太 | `^KS11` | 韓國綜合（KOSPI） |
| 亞太 | `^N225` | 日經 225 |
| 歐洲 | `^FTSE` | 富時 100 |
| 歐洲 | `^GDAXI` | 德國 DAX |
| 歐洲 | `^FCHI` | 法國 CAC 40 |
| 歐洲 | `^STOXX50E` | 歐洲 STOXX 50 |
