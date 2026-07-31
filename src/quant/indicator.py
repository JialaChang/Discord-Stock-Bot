# pyright: reportCallIssue=false, reportPrivateImportUsage=false, reportArgumentType=false
import pandas as pd
import pandas_ta as pta
from src.models import StockSnapshot
from src.quant.errors import InsufficientDataError


def compute_indicators(ticker: str, data: pd.DataFrame, columns: list[str] | None = None) -> None:
    """
    For quantitative backtesting: write technical indicators in place into the given DataFrame.\n
    `columns` selects which indicators to compute; None means compute everything.
    """
    if len(data) < 2:
        raise InsufficientDataError(f"'{ticker}' has fewer than two historical rows and cannot be processed...")

    close = data['Close']
    high = data['High']
    low = data['Low']

    need = set(columns) if columns is not None else None

    def should(*cols: str) -> bool:
        return need is None or bool(need & set(cols))

    # ── RSI ───────────────────────────────────────────────────
    # Relative strength of recent gains vs. losses, ranges 0–100.
    # > 70 overbought, < 30 oversold.
    if should('RSI'):
        data['RSI'] = pta.rsi(close, length=14)

    # ── SMA (moving average) ──────────────────────────────────
    # Simple moving average, smooths short-term noise and shows trend direction.
    # MA5 short-term, MA10 mid-term, MA20 monthly, MA60 quarterly.
    if should('SMA_5'):
        data['SMA_5'] = pta.sma(close, length=5)
    if should('SMA_10'):
        data['SMA_10'] = pta.sma(close, length=10)
    if should('SMA_20'):
        data['SMA_20'] = pta.sma(close, length=20)
    if should('SMA_60'):
        data['SMA_60'] = pta.sma(close, length=60)

    # ── EMA (moving average) ──────────────────────────────────
    # Exponential moving average, weights recent prices more and reacts faster to moves.
    if should('EMA_5'):
        data['EMA_5'] = pta.ema(close, length=5)
    if should('EMA_10'):
        data['EMA_10'] = pta.ema(close, length=10)
    if should('EMA_20'):
        data['EMA_20'] = pta.ema(close, length=20)
    if should('EMA_60'):
        data['EMA_60'] = pta.ema(close, length=60)

    # ── MACD ───────────────────────────────────────────────────
    # Difference between two EMAs, measures momentum strength and direction changes.
    # DIF Line = EMA(12) - EMA(26)
    # DEM Line = EMA(MACD, 9)
    # OSC (histogram) = DEM - DIF
    # Golden cross (DIF crosses above DEM) is a buy signal; death cross (DIF crosses below DEM) is a sell signal.
    # Crosses above the zero line are stronger than below; an OSC bar flipping negative to positive means momentum turns bullish.
    if should('MACD_dif', 'MACD_dem', 'MACD_osc'):
        macd_df = pta.macd(close, fast=12, slow=26, signal=9)
        data['MACD_dif'] = macd_df['MACD_12_26_9']
        data['MACD_dem'] = macd_df['MACDs_12_26_9']
        data['MACD_osc'] = macd_df['MACDh_12_26_9']

    # ── Stochastic Oscillator (KD) ──────────────────────────────
    # Position of the close within the recent 9-day high/low range.
    # K > 80 overbought, K < 20 oversold.
    # A golden cross (K crosses above D) is only meaningful at lows; a death cross only at highs.
    if should('STOCH_K', 'STOCH_D'):
        stoch_df = pta.stoch(high, low, close, k=9, d=3, smooth_k=3)
        data['STOCH_K'] = stoch_df['STOCHk_9_3_3']
        data['STOCH_D'] = stoch_df['STOCHd_9_3_3']

    # ── Bollinger Bands ─────────────────────────────────────────
    # Middle band = SMA(20); upper/lower bands are ±2 standard deviations.
    # Touching the lower band hints at a bounce, touching the upper band at a pullback.
    # A narrowing bandwidth signals an imminent large move.
    if should('BB_U', 'BB_M', 'BB_L', 'BB_W'):
        bb_df = pta.bbands(close, length=20, std=2)
        data['BB_U'] = bb_df['BBU_20_2.0_2.0']
        data['BB_M'] = bb_df['BBM_20_2.0_2.0']
        data['BB_L'] = bb_df['BBL_20_2.0_2.0']
        data['BB_W'] = bb_df['BBB_20_2.0_2.0']


def compute_indicators_for_discord(ticker: str, name: str, history_data: pd.DataFrame, intraday_data: pd.DataFrame, latest_time) -> StockSnapshot:
    """
    For Discord display: compute the minimal indicator set needed by the Embed and assemble a StockSnapshot.
    """
    if len(history_data) < 2:
        raise InsufficientDataError(f"'{ticker}' has fewer than two historical rows and cannot be processed...")

    prices = history_data['Close']
    history_data['RSI'] = pta.rsi(prices, length=14)
    history_data['SMA_5'] = pta.sma(prices, length=5)
    history_data['SMA_10'] = pta.sma(prices, length=10)
    history_data['SMA_20'] = pta.sma(prices, length=20)

    # ── Current price and change percentage ──────────────────────
    if not intraday_data.empty:
        curr_price = intraday_data['Close'].iloc[-1]
        curr_date = pd.to_datetime(intraday_data.index[-1]).date()
    else:
        curr_price = prices.iloc[-1]
        curr_date = pd.to_datetime(history_data.index[-1]).date()

    # Find the reference price: the most recent close strictly before today
    past_data = history_data[pd.to_datetime(history_data.index).date < curr_date]
    prev_price = past_data['Close'].iloc[-1] if not past_data.empty else prices.iloc[-2]
    change_percent = (curr_price - prev_price) / prev_price * 100

    # ── Take the latest value, return 0.0 when missing ───────────
    def last(col):
        if col not in history_data.columns:
            return 0.0
        val = history_data[col].iloc[-1]
        return float(val) if pd.notna(val) else 0.0

    return StockSnapshot(
        ticker=ticker,
        name=name,
        current_price=float(curr_price),
        change_percent=float(change_percent),
        rsi_value=last('RSI'),
        latest_time=latest_time
    )
