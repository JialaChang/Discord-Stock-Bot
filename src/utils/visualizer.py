import mplfinance as mpf
import pandas as pd
import io
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from datetime import date

from src.models import BacktestResult


# Disable GUI interactive mode and force the 'Agg' backend for headless server-side image generation
matplotlib.use('Agg')

_MARKET_COLORS = mpf.make_marketcolors(
    up='red',
    down='green',
    edge='inherit',
    wick='inherit',
    volume='#87ceeb',
)
_MPF_STYLE = mpf.make_mpf_style(marketcolors=_MARKET_COLORS, gridstyle='--')

# Beyond this many bars a candle is about a pixel wide, so a long backtest is drawn as a line instead.
_MAX_CANDLES = 600

# Indicators overlay on the daily chart: column -> (color, label).
_HISTORY_CHART_IND = {
    'SMA_5': ("#FFA41C", '5MA'),
    'SMA_10': ("#05B3F3", '10MA'),
    'SMA_20': ("#A137E4", '20MA'),
}


def generate_history_chart(ticker: str, data: pd.DataFrame, days: int = 61) -> io.BytesIO:
    """Generate a daily candlestick chart with indicators and volume and return an in-memory PNG."""
    from src.quant.indicator import compute_indicators

    # Averaged over the full history, then sliced
    frame = data.copy()
    compute_indicators(ticker, frame, list(_HISTORY_CHART_IND))
    plot_data = frame.iloc[-days:]

    addplot = [
        mpf.make_addplot(plot_data[col], color=color, width=1, label=label)
        for col, (color, label) in _HISTORY_CHART_IND.items()
    ]

    # Use an in-memory stream to avoid writing the image to disk
    buffer = io.BytesIO()

    mpf.plot(
        plot_data,
        type='candle',
        addplot=addplot,
        style=_MPF_STYLE,
        title=f"\n{ticker}",
        show_nontrading=False,
        datetime_format='%m/%d',
        tight_layout=True,
        xrotation=0,
        volume=True,
        volume_alpha=0.3,
        panel_ratios=(4, 1),
        savefig=buffer
    )
    buffer.seek(0)  # Rewind so the caller can read from the start
    plt.close('all')
    return buffer


def generate_intraday_chart(ticker: str, data: pd.DataFrame, baseline: float) -> io.BytesIO:
    """Generate an intraday line chart, colored red-up / green-down relative to `baseline`.

    `baseline` is the previous close.
    """
    above = data['Close'].where(data['Close'] >= baseline)
    below = data['Close'].where(data['Close'] < baseline)

    # Dotted line at the baseline as the up/down reference
    ref_line = pd.Series(baseline, index=data.index)
    addplot = [mpf.make_addplot(ref_line, color='#a0a0a0', linestyle='dotted', width=2)]

    if above.notna().any():
        addplot.append(mpf.make_addplot(above, color='#e74c3c', width=1))
    if below.notna().any():
        addplot.append(mpf.make_addplot(below, color='#2ecc71', width=1))

    fills = [
        dict(y1=data['Close'].values, y2=baseline, where=(data['Close'] >= baseline).values, color='#e74c3c', alpha=0.1),
        dict(y1=data['Close'].values, y2=baseline, where=(data['Close'] < baseline).values, color='#2ecc71', alpha=0.1)
    ]

    buffer = io.BytesIO()

    mpf.plot(
        data,
        type='line',
        linecolor='#555555',
        addplot=addplot,
        fill_between=fills,
        style=_MPF_STYLE,
        title=f"\n{ticker}",
        datetime_format='%H:%M',
        tight_layout=True,
        xrotation=0,
        volume=True,
        volume_alpha=0.3,
        panel_ratios=(4, 1),
        savefig=buffer
    )

    buffer.seek(0)
    plt.close('all')
    return buffer


def generate_backtest_chart(ticker: str, result: BacktestResult) -> io.BytesIO:
    """Generate a backtest chart: candlesticks + entry/exit markers + equity curve."""
    data = result.data

    long_entries = [(t.entry_date, t.entry_price) for t in result.trades if t.side == "LONG"]
    long_exits = [(t.exit_date, t.exit_price) for t in result.trades if t.side == "LONG"]
    short_entries = [(t.entry_date, t.entry_price) for t in result.trades if t.side == "SHORT"]
    short_exits = [(t.exit_date, t.exit_price) for t in result.trades if t.side == "SHORT"]

    marker_long_entries = _build_marker_series(data, long_entries)
    marker_long_exits = _build_marker_series(data, long_exits)
    marker_short_entries = _build_marker_series(data, short_entries)
    marker_short_exits = _build_marker_series(data, short_exits)

    addplot = [
        mpf.make_addplot(result.equity_curve, panel=1, color='#3498db', ylabel='Equity', width=1.2),
    ]
    if marker_long_entries.notna().any():
        addplot.append(mpf.make_addplot(marker_long_entries, type='scatter', markersize=40, marker='^', color="#e73ce7"))
    if marker_long_exits.notna().any():
        addplot.append(mpf.make_addplot(marker_long_exits, type='scatter', markersize=40, marker='v', color='#e73ce7'))
    if marker_short_entries.notna().any():
        addplot.append(mpf.make_addplot(marker_short_entries, type='scatter', markersize=40, marker='^', color="#2eccbf"))
    if marker_short_exits.notna().any():
        addplot.append(mpf.make_addplot(marker_short_exits, type='scatter', markersize=40, marker='v', color='#2eccbf'))

    buffer = io.BytesIO()

    mpf.plot(
        data,
        type='candle' if len(data) <= _MAX_CANDLES else 'line',
        addplot=addplot,
        style=_MPF_STYLE,
        title=f"\n{ticker}",
        show_nontrading=False,
        datetime_format='%m/%d',
        tight_layout=True,
        xrotation=0,
        panel_ratios=(4, 2),
        savefig=buffer
    )

    buffer.seek(0)
    plt.close('all')
    return buffer


def _build_marker_series(data: pd.DataFrame, points: list[tuple[date, float]]) -> pd.Series:
    """Convert a list of (date, price) into a marker series aligned to data.index, with NaN on non-trading days."""
    marker = pd.Series(data=np.nan, index=data.index)
    for d, price in points:
        marker.loc[pd.Timestamp(d)] = price # pyright: ignore[reportCallIssue, reportArgumentType]
    return marker
