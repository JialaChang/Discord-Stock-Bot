"""Strategy decisions in isolation.

`signal()` is called directly with hand-built indicator frames, so a case is one
exact pair of bars rather than a price series reverse-engineered to produce one.
"""
from datetime import date

import pandas as pd
import pytest

from src.models import Position, Signal
from src.quant import EMAStrategy, RSIStrategy
from src.quant.backtest import history_window


def ema_history(*bars):
    """Frame of (EMA_5, EMA_20) rows, oldest first, with the last row standing for today."""
    return pd.DataFrame(
        {'EMA_5': [fast for fast, _ in bars], 'EMA_20': [slow for _, slow in bars]},
        index=pd.bdate_range('2026-01-01', periods=len(bars)),
    )


def holding(side):
    return Position(date(2026, 1, 1), 100.0, Signal("HOLD", {}, {}), side, 10)


def replay(*bars, strategy=None, position=None):
    """Feed the bars to a strategy one at a time and return the action from each.

    Needed wherever a case only exists as a sequence: a strategy that carries state cannot
    be judged from a single call. The engine's own `history_window` does the slicing, so
    these tests cannot drift into exercising a wider view than the engine ever hands over.
    """
    strategy = strategy or EMAStrategy()
    history = ema_history(*bars)
    return [strategy.signal(history_window(history, i, strategy.lookback), position).action
            for i in range(len(history))]


class TestEMACrossDetection:
    def test_golden_cross_enters_long_when_flat(self):
        signal = EMAStrategy().signal(ema_history((99, 100), (101, 100)), None)

        assert signal.action == "ENTER_LONG"
        assert signal.conditions == {"ema_golden_cross": True}

    def test_death_cross_enters_short_when_flat(self):
        signal = EMAStrategy().signal(ema_history((101, 100), (99, 100)), None)

        assert signal.action == "ENTER_SHORT"
        assert signal.conditions == {"ema_death_cross": True}

    def test_holding_above_without_a_cross_does_nothing(self):
        # The whole point of trading the event: a state check would re-enter here every bar.
        signal = EMAStrategy().signal(ema_history((101, 100), (102, 100)), None)

        assert signal.action == "HOLD"

    def test_holding_below_without_a_cross_does_nothing(self):
        signal = EMAStrategy().signal(ema_history((99, 100), (98, 100)), None)

        assert signal.action == "HOLD"

    def test_a_single_row_cannot_show_a_cross(self):
        signal = EMAStrategy().signal(ema_history((101, 100)), None)

        assert signal.action == "HOLD"

    def test_indicator_values_are_reported_even_when_holding(self):
        signal = EMAStrategy().signal(ema_history((99, 100), (98, 100)), None)

        assert signal.values == {"EMA_5": 98.0, "EMA_20": 100.0}


class TestEMATouchingLines:
    def test_lines_meeting_exactly_is_not_yet_a_cross(self):
        # Equal means the order has not reversed; it may still fall back.
        signal = EMAStrategy().signal(ema_history((99, 100), (100, 100)), None)

        assert signal.action == "HOLD"

    def test_a_gap_under_a_cent_does_not_count_as_separation(self):
        # Both lines round to 100.00, so this is a touch and not a cross.
        signal = EMAStrategy().signal(ema_history((99, 100), (100.004, 100)), None)

        assert signal.action == "HOLD"

    def test_a_touch_on_the_way_through_still_fires_one_cross(self):
        # Below -> equal -> above is a real crossing, and must produce exactly one entry.
        assert replay((99, 100), (100, 100), (101, 100)) == ["HOLD", "HOLD", "ENTER_LONG"]

    def test_a_touch_returning_from_below_is_not_a_cross(self):
        # Below -> equal -> below never reversed the order, however the middle bar looks.
        assert replay((99, 100), (100, 100), (99, 100)) == ["HOLD", "HOLD", "HOLD"]

    def test_a_touch_returning_from_above_is_not_a_cross(self):
        assert replay((101, 100), (100, 100), (101, 100)) == ["HOLD", "HOLD", "HOLD"]

    def test_a_run_of_touching_bars_is_crossed_by_where_it_ends(self):
        # The remembered order survives any number of equal bars in between.
        actions = replay((99, 100), (100, 100), (100, 100), (100, 100), (101, 100))
        assert actions == ["HOLD", "HOLD", "HOLD", "HOLD", "ENTER_LONG"]

    def test_two_bars_alone_cannot_resolve_a_touch(self):
        # The pair below opens both sequences above; with no carried order the strategy
        # declines rather than guessing, which is why `reset()` and the seed row exist.
        assert EMAStrategy().signal(ema_history((100, 100), (101, 100)), None).action == "HOLD"


class TestEMARunStart:
    def test_the_bar_before_the_window_seeds_the_remembered_order(self):
        # The engine keeps that row reachable so an opening-bar cross is not missed.
        signal = EMAStrategy().signal(ema_history((99, 100), (101, 100)), None)

        assert signal.action == "ENTER_LONG"

    def test_a_run_opens_flat_when_nothing_can_be_seeded(self):
        assert EMAStrategy().signal(ema_history((101, 100)), None).action == "HOLD"

    def test_reset_forgets_the_order_carried_by_the_previous_run(self):
        strategy = EMAStrategy()
        assert replay((99, 100), (101, 100), strategy=strategy) == ["HOLD", "ENTER_LONG"]

        strategy.reset()
        assert replay((99, 100), (101, 100), strategy=strategy) == ["HOLD", "ENTER_LONG"]


class TestEMAReversal:
    def test_a_golden_cross_reverses_a_short(self):
        signal = EMAStrategy().signal(ema_history((99, 100), (101, 100)), holding("SHORT"))

        assert signal.action == "REVERSE_LONG"
        assert signal.conditions == {"ema_golden_cross": True}

    def test_a_death_cross_reverses_a_long(self):
        signal = EMAStrategy().signal(ema_history((101, 100), (99, 100)), holding("LONG"))

        assert signal.action == "REVERSE_SHORT"
        assert signal.conditions == {"ema_death_cross": True}

    def test_a_cross_onto_the_side_already_held_is_a_plain_entry(self):
        # Nothing to reverse, so the engine's "already holding" guard can drop it.
        signal = EMAStrategy().signal(ema_history((99, 100), (101, 100)), holding("LONG"))

        assert signal.action == "ENTER_LONG"


class TestRSIReadsTheLatestBar:
    def test_the_signal_comes_from_todays_value_not_the_oldest(self):
        history = pd.DataFrame({'RSI': [80.0, 20.0]}, index=pd.bdate_range('2026-01-01', periods=2))
        signal = RSIStrategy().signal(history, None)

        assert signal.action == "ENTER_LONG"
        assert signal.values == {"RSI": 20.0}

    def test_an_overbought_long_is_closed(self):
        history = pd.DataFrame({'RSI': [20.0, 80.0]}, index=pd.bdate_range('2026-01-01', periods=2))
        signal = RSIStrategy().signal(history, holding("LONG"))

        assert signal.action == "EXIT_LONG"

    def test_a_quiet_reading_holds(self):
        history = pd.DataFrame({'RSI': [50.0]}, index=pd.bdate_range('2026-01-01', periods=1))

        assert RSIStrategy().signal(history, None).action == "HOLD"
