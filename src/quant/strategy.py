from abc import ABC, abstractmethod
from pandas import Series

from src.models import Signal, Position


class Strategy(ABC):
    required_columns: list[str] = []  # Indicator columns this strategy needs; engine computes only these
    warmup: int = 0  # Rows consumed before the first valid indicator value; engine fetches this many extra

    @abstractmethod
    def signal(self, row: Series, position: Position | None) -> Signal:
        ...


class RSIStrategy(Strategy):
    required_columns = ["RSI"]
    warmup = 14

    def signal(self, row: Series, position: Position | None) -> Signal:
        rsi = round(float(row['RSI']), 2)

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
    required_columns = ["EMA_5", "EMA_20"]
    warmup = 20

    def signal(self, row: Series, position: Position | None) -> Signal:
        ema5 = round(float(row['EMA_5']), 2)
        ema20 = round(float(row['EMA_20']), 2)

        if position is None and ema5 > ema20:
            return Signal("ENTER_LONG", {"ema_golden_cross": True}, {"EMA_5": ema5, "EMA_20": ema20})
        if position is not None and position.side == "LONG" and ema20 > ema5:
            return Signal("EXIT_LONG", {"ema_death_cross": True}, {"EMA_5": ema5, "EMA_20": ema20})

        if position is None and ema5 < ema20:
            return Signal("ENTER_SHORT", {"ema_death_cross": True}, {"EMA_5": ema5, "EMA_20": ema20})
        if position is not None and position.side == "SHORT" and ema20 < ema5:
            return Signal("EXIT_SHORT", {"ema_golden_cross": True}, {"EMA_5": ema5, "EMA_20": ema20})

        return Signal("HOLD", {}, {"EMA_5": ema5, "EMA_20": ema20})