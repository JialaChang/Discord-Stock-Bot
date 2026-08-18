from abc import ABC, abstractmethod
from pandas import DataFrame


class Rule(ABC):
    name: str = ""  # Reported in `Signal.conditions`, so it must read well in the HTML report
    required_columns: list[str] = []
    warmup: int = 0  # Rows consumed before the first valid value of those columns
    lookback: int | None = 1  # Rows of history wanted, today included; None means everything

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Clear per-run state. The composite calls this before each backtest."""

    @abstractmethod
    def bias(self, history: DataFrame) -> float:
        """Which side this rule favours at today's close, from -1 (short) to +1 (long).

        Zero means no opinion, which is the honest answer on most bars for a rule that
        reads events rather than states. `history` ends at today and never contains
        future rows, exactly as it does for a strategy.
        """
        ...


class _CrossTracker:
    """Remembers which of two lines was on top the last time they were strictly apart.

    Adjacent rows alone cannot tell a real crossing from a touch that returns to the side
    it came from, because `below -> equal -> above` and `above -> equal -> above` are the
    same pair of inputs. Carrying the last ordered bar separates them.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._fast_above: bool | None = None

    def seed(self, fast: float, slow: float) -> None:
        """Adopt an earlier bar's ordering, so the first bar of a run can see a crossing."""
        if self._fast_above is None and fast != slow:
            self._fast_above = fast > slow

    def update(self, fast: float, slow: float) -> int:
        """+1 when the fast line crossed above on this bar, -1 below, 0 for no crossing."""
        if fast == slow:
            return 0  # The order has not reversed; it may still fall back

        fast_above = fast > slow
        previous, self._fast_above = self._fast_above, fast_above
        if previous is None or previous == fast_above:
            return 0
        return 1 if fast_above else -1


class CrossRule(Rule):
    """Base for rules that fire on two lines swapping places."""

    lookback = 2  # Only to seed the remembered order on the run's first bar
    fast_column: str = ""
    slow_column: str = ""
    # Decimals the two lines are compared at, None for exact. Only price-quoted lines have
    # a tick making a small gap noise; a line scaling with the price loses real crossings.
    precision: int | None = 2

    def reset(self) -> None:
        self._tracker = _CrossTracker()

    def _read(self, row) -> tuple[float, float]:
        fast = float(row[self.fast_column])
        slow = float(row[self.slow_column])
        if self.precision is None:
            return fast, slow
        return round(fast, self.precision), round(slow, self.precision)

    def _cross(self, history: DataFrame) -> int:
        """The crossing direction on today's bar.

        Equality is settled before anything else: an equal bar is neither a crossing nor
        evidence of an ordering, so it must not seed the tracker or disturb what it holds.
        """
        fast, slow = self._read(history.iloc[-1])

        if fast == slow:
            return 0

        if len(history) >= 2:
            self._tracker.seed(*self._read(history.iloc[-2]))

        return self._tracker.update(fast, slow)


# ── Combinators ───────────────────────────────────────────────
# Wrap another rule rather than reading a column of their own.

class Decay(Rule):
    """Hold an event rule's opinion for a few bars, fading it geometrically.

    An event rule speaks on the bar it fires and is silent after, so averaging several
    clears a threshold only when two fire on the *same* bar — coincidence, not agreement.
    Fading asks whether they agreed within a window, and `half_life` is that window.

    Wrap once and share the wrapper — a composite registers rules by identity and cannot
    see through it, so two wrappers around one rule march its tracker past real crossings.
    """

    def __init__(self, rule: Rule, half_life: float = 3.0, floor: float = 0.05) -> None:
        if half_life <= 0:
            raise ValueError("A Decay needs a positive half_life; an opinion cannot fade in no time.")
        self._rule = rule  # Before super(), which calls reset()
        self._factor = 0.5 ** (1.0 / half_life)
        self._floor = floor
        # Declared by the wrapped rule, so the composite computes the columns it needs
        # and hands it the history it asked for.
        self.name = rule.name
        self.required_columns = rule.required_columns
        self.warmup = rule.warmup
        self.lookback = rule.lookback
        super().__init__()

    def reset(self) -> None:
        self._held = 0.0
        self._rule.reset()

    def bias(self, history: DataFrame) -> float:
        # Consulted every bar, expired or not: a cross tracker that misses one ends up
        # comparing rows either side of a crossing it never saw.
        fresh = self._rule.bias(history)
        if fresh != 0.0:
            self._held = fresh  # Replaces what was held, sign included
        else:
            self._held *= self._factor
            if abs(self._held) < self._floor:
                self._held = 0.0
        return self._held


# ── Trend rules ───────────────────────────────────────────────
# Read as a gate by the composite: they say which side may be traded at all.

class SMATrend(Rule):
    """Distance of the close from a long moving average, as a graded trend reading.

    Graded rather than binary because the composite gates on a band around zero: a step
    function would flip the gate on every bar that brushes the average.
    """

    def __init__(self, length: int = 60, band: float = 0.05) -> None:
        self.length = length
        self.band = band  # Distance, as a fraction of the average, counting as a full opinion
        self.name = f"SMA {length} trend"
        self.required_columns = ['Close', f'SMA_{length}']
        self.warmup = length
        super().__init__()

    def bias(self, history: DataFrame) -> float:
        curr = history.iloc[-1]
        average = float(curr[f'SMA_{self.length}'])
        if average <= 0:
            return 0.0
        distance = float(curr['Close']) / average - 1
        return max(-1.0, min(1.0, distance / self.band))


class MACDZeroLine(Rule):
    """How far above or below the zero line the MACD line sits, in its own volatility.

    The zero line is a trend reading — it says the fast EMA is above the slow one — while
    a crossing of the two MACD lines is a momentum event. Only the former belongs here.
    """

    name = "MACD zero line"
    required_columns = ['MACD_z']
    warmup = 85  # 26 rows for the MACD line, then the 60-row window measuring its scale

    def __init__(self, full: float = 2.0) -> None:
        self.full = full  # Standard deviations from zero counting as a full opinion
        super().__init__()

    def bias(self, history: DataFrame) -> float:
        z = float(history['MACD_z'].iloc[-1])
        return max(-1.0, min(1.0, z / self.full))


# ── Entry rules ───────────────────────────────────────────────
# Read as triggers: they say when to act, once the gate has said what is allowed.

class RSIReversal(Rule):
    """Oversold favours a bounce, overbought a pullback."""

    name = "RSI reversal"
    required_columns = ['RSI']
    warmup = 14

    def __init__(self, oversold: float = 35, overbought: float = 70) -> None:
        self.oversold = oversold
        self.overbought = overbought
        super().__init__()

    def bias(self, history: DataFrame) -> float:
        rsi = round(float(history['RSI'].iloc[-1]), 2)
        if rsi < self.oversold:
            return 1.0
        if rsi > self.overbought:
            return -1.0
        return 0.0


class StochCross(CrossRule):
    """A K/D crossing, counted only at the extremes where it carries information.

    A golden cross is meaningful at lows and a death cross at highs; through the middle
    of the range the two lines cross constantly and say nothing.
    """

    name = "KD cross"
    required_columns = ['STOCH_K', 'STOCH_D']
    warmup = 13  # A 9-bar range, then two 3-bar smoothings
    fast_column = 'STOCH_K'
    slow_column = 'STOCH_D'

    def __init__(self, low: float = 30, high: float = 70) -> None:
        self.low = low
        self.high = high
        super().__init__()

    def bias(self, history: DataFrame) -> float:
        direction = self._cross(history)
        if direction == 0:
            return 0.0

        k = float(history['STOCH_K'].iloc[-1])
        if direction > 0 and k < self.low:
            return 1.0
        if direction < 0 and k > self.high:
            return -1.0
        return 0.0


class EMACross(CrossRule):
    """The classic fast/slow EMA crossing."""

    def __init__(self, fast: int = 5, slow: int = 20) -> None:
        self.fast_column = f'EMA_{fast}'
        self.slow_column = f'EMA_{slow}'
        self.name = f"EMA {fast}x{slow} cross"
        self.required_columns = [self.fast_column, self.slow_column]
        self.warmup = slow
        super().__init__()

    def bias(self, history: DataFrame) -> float:
        return float(self._cross(history))


# ── Exit rules ────────────────────────────────────────────────
# Read against the open position: a bias favouring the other side is a reason to leave.

class BollingerBand(Rule):
    """Reaching a band is a stretched move, and a reason to take the position off."""

    name = "Bollinger band"
    required_columns = ['Close', 'BB_U', 'BB_L']
    warmup = 20

    def bias(self, history: DataFrame) -> float:
        curr = history.iloc[-1]
        close = float(curr['Close'])
        if close >= float(curr['BB_U']):
            return -1.0
        if close <= float(curr['BB_L']):
            return 1.0
        return 0.0


class MACDSignalCross(CrossRule):
    """The MACD line crossing its signal line, which is the histogram changing sign.

    An event, not a state, and deliberately so. The histogram's bare sign holds on every
    single bar, so a rule reading it would speak while `BollingerBand` stayed silent nine
    bars in ten, and would decide this bucket alone whatever weight it was given. Reading
    the turn instead puts the two on comparable footing.
    """

    name = "MACD signal cross"
    required_columns = ['MACD_dif', 'MACD_dem']
    warmup = 34  # The slow EMA, then the 9-bar signal line measured against it
    fast_column = 'MACD_dif'
    slow_column = 'MACD_dem'
    precision = None  # Both lines scale with the price, so cents are not their unit

    def bias(self, history: DataFrame) -> float:
        return float(self._cross(history))
