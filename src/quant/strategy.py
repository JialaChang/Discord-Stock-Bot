from abc import ABC, abstractmethod
from collections.abc import Sequence
from pandas import DataFrame

from src.models import Signal, Position, Side
from src.quant.rule import (BollingerBand, Decay, EMACross, MACDSignalCross, MACDZeroLine,
                            RSIReversal, Rule, SMATrend, StochCross)


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
            return Signal("ENTER_LONG", {"RSI oversold": True}, {"RSI": rsi})
        if position is not None and position.side == "LONG" and rsi > 70:
            return Signal("EXIT_LONG", {"RSI overbought": True}, {"RSI": rsi})
        # Short
        if position is None and rsi > 75:
            return Signal("ENTER_SHORT", {"RSI overbought": True}, {"RSI": rsi})
        if position is not None and position.side == "SHORT" and rsi < 35:
            return Signal("EXIT_SHORT", {"RSI oversold": True}, {"RSI": rsi})

        return Signal("HOLD", {}, {"RSI": rsi})


class EMAStrategy(Strategy):
    """EMA crossover: go long when the fast line crosses above the slow one, short below.

    Detecting the crossing is `EMACross`'s job; a cross names the side to be on,
    and so closes an opposite position in the same fill rather than merely opening a new one.
    """

    def __init__(self, fast: int = 5, slow: int = 20) -> None:
        self._cross = EMACross(fast, slow)
        # Declared by the rule rather than repeated here, so the two cannot drift apart
        self.required_columns = self._cross.required_columns
        self.warmup = self._cross.warmup
        self.lookback = self._cross.lookback
        super().__init__()

    def reset(self) -> None:
        self._cross.reset()

    def signal(self, history: DataFrame, position: Position | None) -> Signal:
        curr = history.iloc[-1]
        indicators = {column: round(float(curr[column]), 2)
                      for column in self._cross.required_columns}

        direction = self._cross.bias(history)
        if direction == 0:
            return Signal("HOLD", {}, indicators)

        if direction > 0:
            reverse = position is not None and position.side == "SHORT"
            return Signal("REVERSE_LONG" if reverse else "ENTER_LONG",
                          {"EMA golden cross": True}, indicators)

        reverse = position is not None and position.side == "LONG"
        return Signal("REVERSE_SHORT" if reverse else "ENTER_SHORT",
                      {"EMA death cross": True}, indicators)


RuleSpec = Rule | tuple[Rule, float]  # A rule, or one carrying a weight other than 1.0

class CompositeStrategy(Strategy):
    """Several rules combined by the role each is given rather than by a single vote.

    A rule only says which side it favours; what that opinion *means* is decided here,
    by the bucket it was placed in:

    - `trend` is a gate. Its score picks the one side that may be opened at all, and
      vetoes the other however strong the trigger.
    - `entry` is a trigger. It says when to act, once the gate has said what is allowed.
    - `exit` is read against the open position: a score favouring the other side is a
      reason to leave.

    Rules sharing a bucket should answer in the same shape — all graded or all events —
    because the average is what mixes them. A rule answering ±1 on every bar decides a
    bucket by itself next to one that speaks rarely, whatever weight either was given.
    """

    def __init__(self, *,
                 trend: Sequence[RuleSpec] = (),
                 entry: Sequence[RuleSpec] = (),
                 exit: Sequence[RuleSpec] = (),
                 trend_enter: float = 0.5,
                 trend_exit: float = 0.2,
                 entry_threshold: float = 0.4,
                 exit_threshold: float = 0.3) -> None:
        self._rules: list[Rule] = []
        self._trend = self._bucket(trend)
        self._entry = self._bucket(entry)
        self._exit = self._bucket(exit)
        if not self._entry:
            raise ValueError("A composite strategy needs at least one entry rule; "
                             "without one no position could ever be opened.")

        self.trend_enter = trend_enter  # Score opening the gate onto a side
        self.trend_exit = trend_exit    # Score below which the gate closes again
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold

        # Declared by the rules rather than restated here. The union is what the engine
        # computes and drops NaNs on, so the deepest warm-up among them governs.
        self.required_columns = sorted({c for r in self._rules for c in r.required_columns})
        self.warmup = max((r.warmup for r in self._rules), default=0)
        lookbacks = [r.lookback for r in self._rules]
        self.lookback = None if None in lookbacks else max((l for l in lookbacks if l), default=1)

        super().__init__()  # Last: it calls reset(), which needs the rules to exist

    def _bucket(self, specs: Sequence[RuleSpec]) -> list[tuple[int, float]]:
        """Resolve a bucket to (rule index, weight) pairs, registering rules by identity.

        Indices rather than the rules themselves so that a rule placed in two buckets is
        still evaluated once per bar: a second `bias()` call would advance a cross tracker
        past the very crossing it is meant to report.
        """
        bucket = []
        for spec in specs:
            rule, weight = spec if isinstance(spec, tuple) else (spec, 1.0)
            bucket.append((self._register(rule), float(weight)))
        return bucket

    def _register(self, rule: Rule) -> int:
        for i, existing in enumerate(self._rules):
            if existing is rule:
                return i
        self._rules.append(rule)
        return len(self._rules) - 1

    def reset(self) -> None:
        self._gate: Side | None = None
        for rule in self._rules:
            rule.reset()

    def signal(self, history: DataFrame, position: Position | None) -> Signal:
        # Every rule is evaluated once, before any decision is taken. A rule carrying state
        # has to see every bar, so which buckets a decision happens to consult must not
        # change what any rule is shown.
        biases = [rule.bias(self._view(rule, history)) for rule in self._rules]

        trend = self._score(self._trend, biases)
        entry = self._score(self._entry, biases)
        exit = self._score(self._exit, biases)
        gate = self._update_gate(trend)

        values = {rule.name: round(bias, 3) for rule, bias in zip(self._rules, biases)}
        values |= {"trend": round(trend, 3), "entry": round(entry, 3), "exit": round(exit, 3)}

        if position is None:
            allowed = self._allowed(gate)
            if "LONG" in allowed and entry >= self.entry_threshold:
                return Signal("ENTER_LONG", self._agreed(self._entry, biases, 1), values)
            if "SHORT" in allowed and entry <= -self.entry_threshold:
                return Signal("ENTER_SHORT", self._agreed(self._entry, biases, -1), values)
            return Signal("HOLD", {}, values)

        # An exit is never turned into a reversal: the reason to leave and open
        # the other side come from different buckets held to different thresholds,
        # and the gate can refuse both sides at once.
        leaving = "EXIT_LONG" if position.side == "LONG" else "EXIT_SHORT"
        against = -1 if position.side == "LONG" else 1
        if exit * against >= self.exit_threshold:
            return Signal(leaving, self._agreed(self._exit, biases, against), values)
        if gate is not None and gate != position.side:
            return Signal(leaving, {f"Trend gate turned {gate}": True}, values)
        return Signal("HOLD", {}, values)

    def _view(self, rule: Rule, history: DataFrame) -> DataFrame:
        """The slice a rule asked for, cut from what the engine handed the strategy."""
        return history if rule.lookback is None else history.iloc[-rule.lookback:]

    @staticmethod
    def _score(bucket: list[tuple[int, float]], biases: list[float]) -> float:
        """Weighted mean of a bucket's biases, and 0.0 for a bucket with nothing in it."""
        total = sum(weight for _, weight in bucket)
        if total <= 0:
            return 0.0
        return sum(biases[i] * weight for i, weight in bucket) / total

    def _agreed(self, bucket: list[tuple[int, float]], biases: list[float],
                direction: int) -> dict[str, bool]:
        """The rules in a bucket that voted the way the decision went, named for the report."""
        return {self._rules[i].name: True for i, weight in bucket
                if weight > 0 and biases[i] * direction > 0}

    def _update_gate(self, trend: float) -> Side | None:
        """Which side the trend allows, held through a band around zero.

        Between `trend_exit` and `trend_enter` the gate keeps whatever it last decided.
        Without that band it flips on every bar that brushes the threshold, and each flip
        forces a position closed — enough to bury the result under its own churn.
        """
        if trend >= self.trend_enter:
            self._gate = "LONG"
        elif trend <= -self.trend_enter:
            self._gate = "SHORT"
        elif abs(trend) < self.trend_exit:
            self._gate = None
        return self._gate

    def _allowed(self, gate: Side | None) -> tuple[Side, ...]:
        """Which sides may be opened right now.

        `gate` is None both when no trend rule was given and when the ones given are
        undecided, and the two must not behave alike: the first has nothing to say and
        gates nothing, while the second has said "not now" and blocks both sides.
        """
        if not self._trend:
            return ("LONG", "SHORT")
        return (gate,) if gate is not None else ()


def gated_strategy() -> CompositeStrategy:
    """Role-based composition: trade a reversal only in the direction the trend allows.

    The gate is one rule on purpose. A second one would have to answer on the same scale
    to share the bucket fairly.
    """
    return CompositeStrategy(
        trend=[SMATrend(200)],
        entry=[RSIReversal(), Decay(StochCross()), Decay(EMACross())],
        exit=[BollingerBand(), Decay(MACDSignalCross())],
    )


def voting_strategy() -> CompositeStrategy:
    """Every rule on one equally weighted ballot, with no gate — the control to beat.

    Measured rather than used. Graded rules answering on every bar outweigh events firing
    on a few, and trend rules cancel against mean-reverting ones instead of qualifying them.
    """
    rules: list[RuleSpec] = [SMATrend(60), MACDZeroLine(), RSIReversal(), StochCross(), EMACross()]
    return CompositeStrategy(entry=rules, exit=rules)
