from abc import ABC, abstractmethod
from pandas import DataFrame

from src.models import Signal, Position


class Strategy(ABC):
    required_columns: list[str] = []  # Indicator columns this strategy needs; engine computes only these
    warmup: int = 0  # Rows consumed before the first valid indicator value; engine fetches this many extra
    lookback: int | None = 1  # Rows of history handed to signal(), today included; None means everything so far

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Clear per-run state. The engine calls this once before each backtest."""

    @abstractmethod
    def signal(self, history: DataFrame, position: Position | None) -> Signal:
        """Decide what to do at today's close.

        `history` ends at today (`history.iloc[-1]`) and never contains future rows,
        so a strategy cannot look ahead by construction. It holds up to `lookback`
        rows and is drawn from the full computed frame, meaning the warm-up rows
        before the backtest window are available to the first day of the window.
        """
        ...


class RSIStrategy(Strategy):
    required_columns = ["RSI"]
    warmup = 14
    lookback = 1  # Threshold levels are read off today alone

    def signal(self, history: DataFrame, position: Position | None) -> Signal:
        rsi = round(float(history['RSI'].iloc[-1]), 2)

        # Long
        if position is None and rsi < 35:
            return Signal("ENTER_LONG", {"rsi_oversold": True}, {"RSI": rsi})
        if position is not None and position.side == "LONG" and rsi > 70:
            return Signal("EXIT_LONG", {"rsi_overbought": True}, {"RSI": rsi})
        # Short
        if position is None and rsi > 75:
            return Signal("ENTER_SHORT", {"rsi_overbought": True}, {"RSI": rsi})
        if position is not None and position.side == "SHORT" and rsi < 35:
            return Signal("EXIT_SHORT", {"rsi_oversold": True}, {"RSI": rsi})

        return Signal("HOLD", {}, {"RSI": rsi})


class EMAStrategy(Strategy):
    """EMA crossover: go long when the fast line crosses above the slow one, short below."""

    required_columns = ["EMA_5", "EMA_20"]
    warmup = 20
    lookback = 2  # Only to seed the remembered order on the run's first bar

    def reset(self) -> None:
        # Which line was on top the last time the two were strictly apart, carried across
        # bars because adjacent rows alone cannot tell a crossing from a touch that returns.
        self._fast_above: bool | None = None

    def signal(self, history: DataFrame, position: Position | None) -> Signal:
        curr = history.iloc[-1]
        ema5 = round(float(curr['EMA_5']), 2)
        ema20 = round(float(curr['EMA_20']), 2)
        indicators = {"EMA_5": ema5, "EMA_20": ema20}

        if ema5 == ema20:
            return Signal("HOLD", {}, indicators)

        fast_above = ema5 > ema20

        # The engine keeps that row reachable even at the window's opening bar.
        if self._fast_above is None and len(history) >= 2:
            prev = history.iloc[-2]
            prev5 = round(float(prev['EMA_5']), 2)
            prev20 = round(float(prev['EMA_20']), 2)
            if prev5 != prev20:
                self._fast_above = prev5 > prev20

        previous, self._fast_above = self._fast_above, fast_above

        # A cross is a change in that order, measured against the last ordered bar.
        # Over two bars `below -> equal -> above` (a real crossing)
        # and `above -> equal -> above` (a touch that returns) are the same input;
        # A cross names the side to be on, so it reverses an opposite position.
        if previous is None or previous == fast_above:
            return Signal("HOLD", {}, indicators)

        if fast_above:
            reverse = position is not None and position.side == "SHORT"
            return Signal("REVERSE_LONG" if reverse else "ENTER_LONG",
                          {"ema_golden_cross": True}, indicators)

        reverse = position is not None and position.side == "LONG"
        return Signal("REVERSE_SHORT" if reverse else "ENTER_SHORT",
                      {"ema_death_cross": True}, indicators)
