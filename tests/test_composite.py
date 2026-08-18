"""Composite decisions in isolation.

`StubRule` returns scripted biases, so a case states exactly what each rule said on each
bar and no indicator is involved. What is under test is the mapping from opinions to an
action, plus the aggregation of the declarations the engine reads.
"""
import math
from datetime import date

import pandas as pd
import pytest

from src.models import Position, Signal
from src.quant import (BacktestEngine, CompositeStrategy, Decay, Rule, compute_indicators,
                       gated_strategy, voting_strategy)
from src.quant.backtest import history_window
from tests.conftest import make_ohlcv


class StubRule(Rule):
    """A rule answering from a script, one entry per call, holding the last one after.

    The cursor into the script deliberately survives `reset()`, unlike the call counter:
    a test that resets part-way needs the next bar to carry the next scripted reading,
    or the strategy would be handed the opening bar again and the reset prove nothing.
    """

    def __init__(self, *biases: float, name: str = "stub", columns=None,
                 warmup: int = 0, lookback: int | None = 1) -> None:
        self.name = name
        self.required_columns = list(columns or [])
        self.warmup = warmup
        self.lookback = lookback
        self._script = list(biases) or [0.0]
        self._cursor = 0
        super().__init__()

    def reset(self) -> None:
        self.calls = 0
        self.views: list[int] = []

    def bias(self, history: pd.DataFrame) -> float:
        self.views.append(len(history))
        value = self._script[min(self._cursor, len(self._script) - 1)]
        self._cursor += 1
        self.calls += 1
        return value


def bars(count: int = 1) -> pd.DataFrame:
    """A frame of the right shape; the stub rules ignore its contents."""
    return pd.DataFrame({'Close': [100.0] * count},
                        index=pd.bdate_range('2026-01-01', periods=count))


def holding(side) -> Position:
    return Position(date(2026, 1, 1), 100.0, Signal("HOLD", {}, {}), side, 10)


def act(strategy, position=None, count=1) -> str:
    return strategy.signal(bars(count), position).action


class TestEntryNeedsGateAndTrigger:
    def test_a_trigger_inside_an_allowing_gate_opens(self):
        strategy = CompositeStrategy(trend=[StubRule(1.0)], entry=[StubRule(1.0)])

        assert act(strategy) == "ENTER_LONG"

    def test_a_trigger_the_gate_forbids_is_vetoed(self):
        # The gate is a veto, not a vote: no trigger strength overrides it.
        strategy = CompositeStrategy(trend=[StubRule(-1.0)], entry=[StubRule(1.0)])

        assert act(strategy) == "HOLD"

    def test_an_allowing_gate_without_a_trigger_does_nothing(self):
        strategy = CompositeStrategy(trend=[StubRule(1.0)], entry=[StubRule(0.0)])

        assert act(strategy) == "HOLD"

    def test_an_undecided_gate_blocks_both_sides(self):
        strategy = CompositeStrategy(trend=[StubRule(0.0)], entry=[StubRule(1.0)])

        assert act(strategy) == "HOLD"

    def test_a_trigger_below_the_threshold_is_not_enough(self):
        # Stated against an explicit threshold rather than the shipped default: what is
        # under test is that the bucket's mean has to reach the bar, not where the bar sits.
        strategy = CompositeStrategy(
            trend=[StubRule(1.0)],
            entry=[StubRule(1.0), StubRule(0.0), StubRule(0.0)],
            entry_threshold=0.5,
        )

        assert act(strategy) == "HOLD"

    def test_reaching_the_threshold_exactly_is_enough(self):
        # Chosen so the mean is exact in binary: a bucket landing a hair under a round
        # threshold is a floating-point accident, not a decision the composite made.
        strategy = CompositeStrategy(
            trend=[StubRule(1.0)],
            entry=[StubRule(1.0), StubRule(0.5), StubRule(0.0)],
            entry_threshold=0.5,
        )

        assert act(strategy) == "ENTER_LONG"

    def test_falling_short_of_the_threshold_is_not(self):
        strategy = CompositeStrategy(
            trend=[StubRule(1.0)],
            entry=[StubRule(1.0), StubRule(0.49), StubRule(0.0)],
            entry_threshold=0.5,
        )

        assert act(strategy) == "HOLD"

    def test_the_default_threshold_asks_for_more_than_one_rule(self):
        # No rule answers above 1.0, so any default above 1/n is what makes the bucket ask
        # for agreement at all instead of passing whichever rule spoke first. This is the
        # property worth pinning; the exact level is a tuning decision that may move.
        strategy = CompositeStrategy(
            trend=[StubRule(1.0)],
            entry=[StubRule(1.0), StubRule(0.0), StubRule(0.0)],
        )

        assert act(strategy) == "HOLD"

    def test_the_threshold_decides_how_stale_an_agreeing_event_may_be(self):
        # Paired with `Decay`, the threshold stops being a count of rules and becomes a
        # window: beside one full opinion, a faded event still carries the bucket while it
        # is worth `n * threshold - 1`, and stops the bar after it drops below.
        strategy = CompositeStrategy(
            trend=[StubRule(1.0)],
            entry=[StubRule(1.0), Decay(StubRule(1.0, 0.0), half_life=3), StubRule(0.0)],
            entry_threshold=0.4,
        )

        actions = [act(strategy) for _ in range(9)]

        assert actions[:7] == ["ENTER_LONG"] * 7   # the firing bar, then six more
        assert actions[7:] == ["HOLD"] * 2

    def test_two_of_three_agreeing_clears_the_threshold(self):
        strategy = CompositeStrategy(
            trend=[StubRule(1.0)],
            entry=[StubRule(1.0), StubRule(1.0), StubRule(0.0)],
        )

        assert act(strategy) == "ENTER_LONG"

    def test_weights_shift_what_counts_as_agreement(self):
        strategy = CompositeStrategy(
            trend=[StubRule(1.0)],
            entry=[(StubRule(1.0), 3.0), (StubRule(0.0), 1.0)],
        )

        assert act(strategy) == "ENTER_LONG"


class TestNoTrendRules:
    def test_an_unconfigured_gate_permits_both_sides(self):
        # None from an empty bucket means "nothing to say", not "not now" — otherwise a
        # pure vote could never open a position at all.
        assert act(CompositeStrategy(entry=[StubRule(1.0)])) == "ENTER_LONG"
        assert act(CompositeStrategy(entry=[StubRule(-1.0)])) == "ENTER_SHORT"

    def test_an_unconfigured_gate_never_forces_an_exit(self):
        strategy = CompositeStrategy(entry=[StubRule(0.0)], exit=[StubRule(0.0)])

        assert act(strategy, holding("LONG")) == "HOLD"

    def test_a_strategy_that_could_never_open_is_refused(self):
        with pytest.raises(ValueError, match="entry rule"):
            CompositeStrategy(trend=[StubRule(1.0)], exit=[StubRule(1.0)])


class TestExit:
    def test_a_bucket_favouring_the_other_side_closes_a_long(self):
        strategy = CompositeStrategy(entry=[StubRule(0.0)], exit=[StubRule(-1.0)])

        assert act(strategy, holding("LONG")) == "EXIT_LONG"

    def test_the_same_reading_leaves_a_short_alone(self):
        # An exit is read against the position, so one bucket serves both sides.
        strategy = CompositeStrategy(entry=[StubRule(0.0)], exit=[StubRule(-1.0)])

        assert act(strategy, holding("SHORT")) == "HOLD"

    def test_a_bucket_favouring_the_other_side_closes_a_short(self):
        strategy = CompositeStrategy(entry=[StubRule(0.0)], exit=[StubRule(1.0)])

        assert act(strategy, holding("SHORT")) == "EXIT_SHORT"

    def test_one_of_two_rules_is_enough_to_leave(self):
        # Threshold 0.3 against a 0.5 score: harder to enter than to leave, on purpose.
        strategy = CompositeStrategy(entry=[StubRule(0.0)],
                                     exit=[StubRule(-1.0), StubRule(0.0)])

        assert act(strategy, holding("LONG")) == "EXIT_LONG"

    def test_the_gate_turning_against_a_position_closes_it(self):
        strategy = CompositeStrategy(trend=[StubRule(-1.0)], entry=[StubRule(0.0)],
                                     exit=[StubRule(0.0)])

        assert act(strategy, holding("LONG")) == "EXIT_LONG"

    def test_an_exit_is_never_turned_into_a_reversal(self):
        # Leaving and opening the other side are decided by different buckets against
        # different thresholds, so the composite only ever emits the exit.
        strategy = CompositeStrategy(trend=[StubRule(-1.0)], entry=[StubRule(-1.0)],
                                     exit=[StubRule(-1.0)])

        assert act(strategy, holding("LONG")) == "EXIT_LONG"


class TestGateHysteresis:
    def build(self, *trend_biases):
        return CompositeStrategy(trend=[StubRule(*trend_biases)], entry=[StubRule(1.0)])

    def replay(self, strategy, count):
        return [strategy.signal(bars(1), None).action for _ in range(count)]

    def test_the_gate_holds_through_the_band(self):
        # Opens at 0.5, and 0.3 is inside the band, so the side stays allowed.
        strategy = self.build(1.0, 0.3)

        assert self.replay(strategy, 2) == ["ENTER_LONG", "ENTER_LONG"]

    def test_the_gate_closes_below_the_band(self):
        strategy = self.build(1.0, 0.1)

        assert self.replay(strategy, 2) == ["ENTER_LONG", "HOLD"]

    def test_the_gate_flips_on_a_full_reading_of_the_other_side(self):
        strategy = CompositeStrategy(trend=[StubRule(1.0, -1.0)], entry=[StubRule(-1.0)])

        assert self.replay(strategy, 2) == ["HOLD", "ENTER_SHORT"]

    def test_reset_forgets_the_gate_carried_by_the_previous_run(self):
        strategy = self.build(1.0, 0.3)
        assert self.replay(strategy, 2) == ["ENTER_LONG", "ENTER_LONG"]

        strategy.reset()
        # The same in-band 0.3 reading, now with no gate left to hold it open: an
        # undecided gate blocks both sides, so the trigger below it decides nothing.
        assert strategy.signal(bars(1), None).action == "HOLD"


class TestEveryRuleSeesEveryBar:
    def test_a_rule_is_evaluated_even_when_its_bucket_cannot_decide(self):
        # Exit rules carrying state would desync if they were skipped while flat.
        exit_rule = StubRule(0.0, name="exit")
        strategy = CompositeStrategy(entry=[StubRule(0.0)], exit=[exit_rule])

        strategy.signal(bars(1), None)

        assert exit_rule.calls == 1

    def test_a_rule_shared_by_two_buckets_is_evaluated_once(self):
        # A second call would advance a cross tracker past the crossing it must report.
        shared = StubRule(1.0, name="shared")
        strategy = CompositeStrategy(entry=[shared], exit=[shared])

        strategy.signal(bars(1), None)

        assert shared.calls == 1

    def test_a_shared_rule_still_counts_in_both_buckets(self):
        shared = StubRule(-1.0, name="shared")
        strategy = CompositeStrategy(entry=[shared], exit=[shared])
        signal = strategy.signal(bars(1), holding("LONG"))

        assert signal.action == "EXIT_LONG"
        assert signal.values["exit"] == -1.0

    def test_each_rule_is_shown_the_window_it_asked_for(self):
        one, three = StubRule(0.0, lookback=1), StubRule(0.0, lookback=3)
        strategy = CompositeStrategy(entry=[one, three])

        strategy.signal(bars(5), None)

        assert (one.views, three.views) == ([1], [3])

    def test_a_rule_asking_for_everything_is_given_everything(self):
        everything = StubRule(0.0, lookback=None)
        strategy = CompositeStrategy(entry=[everything])

        strategy.signal(bars(5), None)

        assert everything.views == [5]

    def test_reset_reaches_every_rule(self):
        rule = StubRule(0.0)
        strategy = CompositeStrategy(entry=[rule])
        strategy.signal(bars(1), None)

        strategy.reset()

        assert rule.calls == 0


class TestSignalPayload:
    def test_the_rules_that_voted_for_an_entry_are_named(self):
        # Two for and one against averages to 0.33, so the threshold is lowered to let
        # the decision through with a dissenter still in the bucket to be left out of.
        strategy = CompositeStrategy(entry=[StubRule(1.0, name="agrees"),
                                            StubRule(1.0, name="also agrees"),
                                            StubRule(-1.0, name="dissents")],
                                     entry_threshold=0.3)
        signal = strategy.signal(bars(1), None)

        assert signal.action == "ENTER_LONG"
        assert signal.conditions == {"agrees": True, "also agrees": True}

    def test_the_rules_that_voted_for_an_exit_are_named(self):
        strategy = CompositeStrategy(entry=[StubRule(0.0)],
                                     exit=[StubRule(-1.0, name="says leave"),
                                           StubRule(0.0, name="silent")])
        signal = strategy.signal(bars(1), holding("LONG"))

        assert signal.conditions == {"says leave": True}

    def test_a_gate_exit_names_the_side_the_trend_turned_to(self):
        strategy = CompositeStrategy(trend=[StubRule(-1.0)], entry=[StubRule(0.0)])
        signal = strategy.signal(bars(1), holding("LONG"))

        assert signal.conditions == {"Trend gate turned SHORT": True}

    def test_every_rule_reports_its_reading_whatever_was_decided(self):
        strategy = CompositeStrategy(trend=[StubRule(0.25, name="trend rule")],
                                     entry=[StubRule(0.0, name="entry rule")])
        signal = strategy.signal(bars(1), None)

        assert signal.action == "HOLD"
        assert signal.values == {"trend rule": 0.25, "entry rule": 0.0,
                                 "trend": 0.25, "entry": 0.0, "exit": 0.0}


class TestDeclarationsAggregate:
    def test_columns_are_the_union_of_what_the_rules_read(self):
        strategy = CompositeStrategy(trend=[StubRule(columns=['A', 'B'])],
                                     entry=[StubRule(columns=['B', 'C'])])

        assert strategy.required_columns == ['A', 'B', 'C']

    def test_the_warmup_is_the_deepest_one(self):
        strategy = CompositeStrategy(entry=[StubRule(warmup=14), StubRule(warmup=85)])

        assert strategy.warmup == 85

    def test_the_lookback_is_the_longest_one(self):
        strategy = CompositeStrategy(entry=[StubRule(lookback=1), StubRule(lookback=2)])

        assert strategy.lookback == 2

    def test_one_rule_asking_for_everything_makes_the_union_everything(self):
        strategy = CompositeStrategy(entry=[StubRule(lookback=2), StubRule(lookback=None)])

        assert strategy.lookback is None


class TestPresets:
    """The presets `/backtest` exposes, run whole."""

    @pytest.mark.parametrize("build", [gated_strategy, voting_strategy],
                             ids=lambda b: b.__name__)
    def test_a_preset_survives_a_run_on_real_indicators(self, build):
        closes = [100 + 35 * math.sin(i / 7) + 0.1 * i for i in range(300)]

        result = BacktestEngine(build()).run("X", make_ohlcv(closes))

        assert len(result.equity_curve) > 0
        assert result.equity_curve.notna().all()


class TestGatedPresetComposition:
    """Which rules the preset fades, read off the biases it reports rather than its buckets.

    An event rule that is wrapped answers with fractions on the bars after it fires; a state
    rule that is left bare only ever answers at full strength. That difference is visible in
    `Signal.values`, so the composition can be pinned without reaching into the strategy.
    """

    def biases(self, name: str) -> list[float]:
        closes = [100 + 35 * math.sin(i / 7) + 0.1 * i for i in range(300)]
        data = make_ohlcv(closes)
        strategy = gated_strategy()
        compute_indicators("X", data, strategy.required_columns)
        data = data.dropna(subset=strategy.required_columns)
        return [strategy.signal(history_window(data, i, strategy.lookback), None).values[name]
                for i in range(len(data))]

    def test_an_event_rule_fades_between_firings(self):
        assert any(0 < abs(v) < 1 for v in self.biases("EMA 5x20 cross"))

    def test_an_exit_event_rule_fades_too(self):
        assert any(0 < abs(v) < 1 for v in self.biases("MACD signal cross"))

    def test_a_state_rule_is_left_bare(self):
        # Fading it would go on claiming an oversold reading after the reading recovered.
        assert all(v in (-1.0, 0.0, 1.0) for v in self.biases("RSI reversal"))
