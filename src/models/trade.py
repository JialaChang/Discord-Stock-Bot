from dataclasses import dataclass
from datetime import date
import pandas as pd
from typing import Literal


Side = Literal["LONG", "SHORT"]


def pnl_ratio(side: Side, entry_price: float, exit_price: float) -> float:
    """Capital multiplier of a position: 1.05 means the capital grew 5%.

    A short is modelled as unleveraged and fully funded, so the multiplier isfloored at 0. 
    Without the floor a price that more than doubles produces anegative multiplier, 
    which flips the sign of every later compounding step and corrupts the whole equity curve.
    """
    if entry_price <= 0:
        return 1.0
    if side == "LONG":
        return exit_price / entry_price
    elif side == "SHORT":
        # = 1 + (entry_price - exit_price) / entry_price
        pnlr = 2 - exit_price / entry_price
        return max(0.0, pnlr)


@dataclass
class Signal:
    """A buy/sell signal along with the strategy conditions that produced it."""
    action: Literal["ENTER_LONG", "EXIT_LONG", "ENTER_SHORT", "EXIT_SHORT", "HOLD"]
    conditions: dict[str, bool]  # Whether each sub-condition holds
    values: dict[str, float]     # Indicator values at trigger time


@dataclass
class Position:
    """An open position and the signal that opened it."""
    entry_date: date
    entry_price: float
    entry_signal: Signal
    side: Side

    def unrealized_pnl_ratio(self, price_now: float) -> float:
        return pnl_ratio(self.side, self.entry_price, price_now)


@dataclass
class Trade:
    """Record of a single completed round-trip trade."""
    ticker: str
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    entry_signal: Signal
    exit_signal: Signal
    side: Side
    shares: int = 1  # Number of shares traded

    @property
    def profit_and_loss(self) -> float:
        if self.side == "LONG":
            return (self.exit_price - self.entry_price) * self.shares
        else:
            return (self.entry_price - self.exit_price) * self.shares

    @property
    def return_on_investment(self) -> float:
        return (pnl_ratio(self.side, self.entry_price, self.exit_price) - 1) * 100

    @property
    def is_profit(self) -> bool:
        return self.profit_and_loss > 0


@dataclass
class BacktestResult:
    """Aggregated result of a single complete backtest run."""
    ticker: str
    trades: list[Trade]
    equity_curve: pd.Series  # index=date, value=netWorth
    data: pd.DataFrame  # OHLCV data

    @property
    def total_return(self) -> float:
        if self.equity_curve.empty: return 0.0
        first = self.equity_curve.iloc[0]
        last = self.equity_curve.iloc[-1]
        if first <= 0: return 0.0
        return (last - first) / first * 100

    @property
    def win_rate(self) -> float:
        if not self.trades: return 0.0
        return sum(t.is_profit for t in self.trades) / len(self.trades) * 100

    @property
    def max_drawdown(self) -> float:
        if self.equity_curve.empty: return 0.0
        peak = self.equity_curve.cummax()
        drawdown = (self.equity_curve - peak) / peak.where(peak > 0) * 100
        return float(drawdown.min()) if drawdown.notna().any() else 0.0

    @property
    def trade_count(self) -> int:
        return len(self.trades)
