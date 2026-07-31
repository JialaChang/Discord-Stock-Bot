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


def generate_history_chart(ticker: str, data: pd.DataFrame, days: int = 61) -> io.BytesIO:
    """Generate a daily candlestick chart (with moving averages and volume) and return an in-memory PNG."""
    plot_data = data.iloc[-days:]

    addplot = [
        mpf.make_addplot(plot_data['SMA_5'], color="#FFA41C", width=1, label='5MA'),
        mpf.make_addplot(plot_data['SMA_10'], color="#05B3F3", width=1, label='10MA'),
        mpf.make_addplot(plot_data['SMA_20'], color="#A137E4", width=1, label='20MA')
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


def generate_intraday_chart(ticker: str, data: pd.DataFrame) -> io.BytesIO:
    """Generate an intraday line chart, colored red-up / green-down relative to the open price."""
    open_price = data['Open'].iloc[0]

    above_open = data['Close'].where(data['Close'] >= open_price)
    below_open = data['Close'].where(data['Close'] < open_price)

    # Dotted line at the open price as the up/down reference
    ref_line = pd.Series(open_price, index=data.index)
    addplot = [mpf.make_addplot(ref_line, color='#a0a0a0', linestyle='dotted', width=2)]

    if above_open.notna().any():
        addplot.append(mpf.make_addplot(above_open, color='#e74c3c', width=1))
    if below_open.notna().any():
        addplot.append(mpf.make_addplot(below_open, color='#2ecc71', width=1))

    fills = [
        dict(y1=data['Close'].values, y2=open_price, where=(data['Close'] >= open_price).values, color='#e74c3c', alpha=0.1),
        dict(y1=data['Close'].values, y2=open_price, where=(data['Close'] < open_price).values, color='#2ecc71', alpha=0.1)
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
        type='candle',
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
