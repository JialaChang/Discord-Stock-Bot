"""Rule opinions in isolation.

`bias()` is called directly with hand-built indicator frames, so a case is one exact set
of values rather than a price series reverse-engineered to produce them. Rules that carry
state are replayed bar by bar through the engine's own `history_window`, which keeps these
tests from exercising a wider view than a rule is ever handed.
"""
import pandas as pd
import pytest

from src.quant.backtest import history_window
from src.quant.indicator import compute_indicators
from src.quant.rule import (BollingerBand, EMACross, MACDSignalCross, MACDZeroLine,
                            RSIReversal, SMATrend, StochCross)
from tests.conftest import make_ohlcv


def frame(**columns):
    """Frame of indicator columns, oldest first, with the last row standing for today."""
    length = len(next(iter(columns.values())))
    return pd.DataFrame(columns, index=pd.bdate_range('2026-01-01', periods=length))


@pytest.fixture(scope="module")
def computed():
    """Synthetic OHLCV with every indicator computed, long enough for the deepest warm-up."""
    data = make_ohlcv([100 + (i % 17) - (i % 7) for i in range(300)])
    compute_indicators('T', data, None)
    return data


def replay(rule, history):
    """Feed the bars to a rule one at a time and return the bias from each.

    Needed wherever a case only exists as a sequence: a rule that carries state cannot be
    judged from a single call.
    """
    return [rule.bias(history_window(history, i, rule.lookback)) for i in range(len(history))]


class TestRSIReversal:
    def test_overbought_favours_a_pullback(self):
        assert RSIReversal().bias(frame(RSI=[80.0])) == -1.0

    def test_the_thresholds_are_exclusive(self):
        # Exactly at the level is not yet past it, on either side.
        assert RSIReversal().bias(frame(RSI=[35.0])) == 0.0
        assert RSIReversal().bias(frame(RSI=[70.0])) == 0.0

    def test_a_reading_is_rounded_to_the_cent_before_comparing(self):
        # 34.999 rounds to 35.00, which is not below the level.
        assert RSIReversal().bias(frame(RSI=[34.999])) == 0.0
        assert RSIReversal().bias(frame(RSI=[34.99])) == 1.0

    def test_the_thresholds_are_configurable(self):
        assert RSIReversal(oversold=25, overbought=75).bias(frame(RSI=[30.0])) == 0.0

    def test_only_todays_value_is_read(self):
        assert RSIReversal().bias(frame(RSI=[80.0, 20.0])) == 1.0


class TestSMATrend:
    def test_a_full_band_above_is_a_full_opinion(self):
        # band=0.05, so 5% above the average earns exactly +1.
        assert SMATrend().bias(frame(Close=[105.0], SMA_60=[100.0])) == pytest.approx(1.0)

    def test_below_the_average_is_negative(self):
        # Graded rather than binary, so a reading inside the band scales with the distance.
        assert SMATrend().bias(frame(Close=[97.0], SMA_60=[100.0])) == pytest.approx(-0.6)

    def test_a_reading_beyond_the_band_is_clipped(self):
        # The -1..+1 contract is what lets the composite average rules together; a rule
        # able to answer +2 would outvote two opposing rules on its own.
        assert SMATrend().bias(frame(Close=[200.0], SMA_60=[100.0])) == 1.0
        assert SMATrend().bias(frame(Close=[10.0], SMA_60=[100.0])) == -1.0

    def test_a_narrower_band_reaches_a_full_opinion_sooner(self):
        assert SMATrend(band=0.01).bias(frame(Close=[101.0], SMA_60=[100.0])) == pytest.approx(1.0)

    def test_the_length_selects_the_column_it_reads(self):
        rule = SMATrend(length=20)
        assert rule.required_columns == ['Close', 'SMA_20']
        assert rule.bias(frame(Close=[105.0], SMA_20=[100.0])) == pytest.approx(1.0)

    def test_an_unusable_average_has_no_opinion(self):
        # Rows with a missing or non-positive close keep their raw values, so an average
        # of zero is reachable; it is a divide by zero rather than a trend reading.
        assert SMATrend().bias(frame(Close=[100.0], SMA_60=[0.0])) == 0.0


class TestMACDZeroLine:
    def test_a_full_scale_reading_is_a_full_opinion(self):
        # full=2.0 standard deviations from zero, and half of it earns half an opinion.
        assert MACDZeroLine().bias(frame(MACD_z=[2.0])) == pytest.approx(1.0)
        assert MACDZeroLine().bias(frame(MACD_z=[-2.0])) == pytest.approx(-1.0)
        assert MACDZeroLine().bias(frame(MACD_z=[1.0])) == pytest.approx(0.5)

    def test_an_extreme_reading_is_clipped(self):
        # The scale is a rolling standard deviation, so z is unbounded in a quiet stretch.
        assert MACDZeroLine().bias(frame(MACD_z=[8.0])) == 1.0
        assert MACDZeroLine().bias(frame(MACD_z=[-8.0])) == -1.0


class TestBollingerBand:
    def test_reaching_the_upper_band_leans_short(self):
        assert BollingerBand().bias(frame(Close=[110.0], BB_U=[110.0], BB_L=[90.0])) == -1.0

    def test_reaching_the_lower_band_leans_long(self):
        assert BollingerBand().bias(frame(Close=[90.0], BB_U=[110.0], BB_L=[90.0])) == 1.0

    def test_inside_the_bands_is_no_opinion(self):
        assert BollingerBand().bias(frame(Close=[100.0], BB_U=[110.0], BB_L=[90.0])) == 0.0


def macd_frame(*bars):
    return frame(MACD_dif=[dif for dif, _ in bars], MACD_dem=[dem for _, dem in bars])


class TestMACDSignalCross:
    def test_crossing_above_the_signal_line_leans_long(self):
        assert MACDSignalCross().bias(macd_frame((-0.2, 0.0), (0.3, 0.0))) == 1.0

    def test_crossing_below_the_signal_line_leans_short(self):
        assert MACDSignalCross().bias(macd_frame((0.3, 0.0), (-0.2, 0.0))) == -1.0

    def test_staying_above_the_signal_line_is_no_opinion(self):
        # The histogram's sign holds every bar; only its turn is the event.
        assert MACDSignalCross().bias(macd_frame((0.3, 0.0), (0.5, 0.0))) == 0.0

    def test_a_cross_far_from_the_zero_line_still_counts(self):
        # The two lines swapping places is the signal, wherever the pair happens to sit.
        assert MACDSignalCross().bias(macd_frame((5.0, 5.4), (5.6, 5.4))) == 1.0

    def test_a_sub_cent_separation_is_a_real_cross_here(self):
        # Unlike price lines, both scale with the ticker's price level: on a stock trading
        # near 8 a gap of 0.003 is ordinary, and rounding it away would lose the crossing.
        assert MACDSignalCross().bias(macd_frame((0.010, 0.013), (0.016, 0.013))) == 1.0
        # The same pair of values under the price-tick default rounds to equal and is lost.
        assert EMACross().bias(ema_frame((0.010, 0.013), (0.016, 0.013))) == 0.0


def ema_frame(*bars):
    return frame(EMA_5=[fast for fast, _ in bars], EMA_20=[slow for _, slow in bars])


class TestEMACross:
    def test_a_golden_cross_leans_long(self):
        assert EMACross().bias(ema_frame((99, 100), (101, 100))) == 1.0

    def test_a_death_cross_leans_short(self):
        assert EMACross().bias(ema_frame((101, 100), (99, 100))) == -1.0

    def test_holding_a_side_without_crossing_is_no_opinion(self):
        # The whole point of reading the event: a state check would answer +1 every bar.
        assert EMACross().bias(ema_frame((101, 100), (102, 100))) == 0.0

    def test_a_single_row_cannot_show_a_cross(self):
        assert EMACross().bias(ema_frame((101, 100))) == 0.0

    def test_a_touch_on_the_way_through_fires_exactly_once(self):
        # Below -> equal -> above is a real crossing, and must produce one opinion.
        assert replay(EMACross(), ema_frame((99, 100), (100, 100), (101, 100))) == [0.0, 0.0, 1.0]

    def test_a_touch_returning_to_the_side_it_came_from_is_not_a_cross(self):
        assert replay(EMACross(), ema_frame((99, 100), (100, 100), (99, 100))) == [0.0, 0.0, 0.0]
        assert replay(EMACross(), ema_frame((101, 100), (100, 100), (101, 100))) == [0.0, 0.0, 0.0]

    def test_a_run_of_touching_bars_is_crossed_by_where_it_ends(self):
        history = ema_frame((99, 100), (100, 100), (100, 100), (100, 100), (101, 100))
        assert replay(EMACross(), history) == [0.0, 0.0, 0.0, 0.0, 1.0]

    def test_a_gap_under_a_cent_does_not_count_as_separation(self):
        # Both lines round to 100.00, so this is a touch and not a cross.
        assert EMACross().bias(ema_frame((99, 100), (100.004, 100))) == 0.0

    def test_two_bars_alone_cannot_resolve_a_touch(self):
        # Opening on an equal bar leaves no order to have reversed from.
        assert EMACross().bias(ema_frame((100, 100), (101, 100))) == 0.0

    def test_reset_forgets_the_order_carried_by_the_previous_run(self):
        rule = EMACross()
        assert replay(rule, ema_frame((99, 100), (101, 100))) == [0.0, 1.0]

        rule.reset()
        assert replay(rule, ema_frame((99, 100), (101, 100))) == [0.0, 1.0]

    def test_the_lengths_select_the_columns_it_reads(self):
        rule = EMACross(fast=10, slow=60)
        assert rule.required_columns == ['EMA_10', 'EMA_60']
        assert rule.warmup == 60


def stoch_frame(*bars):
    return frame(STOCH_K=[k for k, _ in bars], STOCH_D=[d for _, d in bars])


class TestStochCross:
    def test_a_golden_cross_at_a_low_leans_long(self):
        assert StochCross().bias(stoch_frame((20, 25), (28, 25))) == 1.0

    def test_a_death_cross_at_a_high_leans_short(self):
        assert StochCross().bias(stoch_frame((85, 80), (78, 80))) == -1.0

    def test_a_cross_through_the_middle_says_nothing(self):
        # K and D cross constantly mid-range, which is why the extremes gate the reading.
        assert StochCross().bias(stoch_frame((45, 50), (55, 50))) == 0.0
        assert StochCross().bias(stoch_frame((55, 50), (45, 50))) == 0.0

    def test_a_golden_cross_above_the_low_is_not_counted(self):
        assert StochCross().bias(stoch_frame((25, 32), (35, 32))) == 0.0

    def test_sitting_at_a_low_without_crossing_is_no_opinion(self):
        assert StochCross().bias(stoch_frame((15, 20), (18, 20))) == 0.0


class TestDeclaredColumnsAndWarmup:
    """Every rule must be able to run on what `compute_indicators` produces.

    A rule declaring a column nobody computes fails only at backtest time, and one
    declaring too little warm-up reads values that have not converged.
    """

    RULES = [SMATrend(), MACDZeroLine(), RSIReversal(), StochCross(),
             EMACross(), BollingerBand(), MACDSignalCross()]

    @pytest.mark.parametrize("rule", RULES, ids=lambda r: r.name)
    def test_every_declared_column_is_computed(self, rule, computed):
        assert set(rule.required_columns) <= set(computed.columns)

    @pytest.mark.parametrize("rule", RULES, ids=lambda r: r.name)
    def test_the_declared_warmup_is_never_optimistic(self, rule, computed):
        # Declaring more warm-up than needed only costs history; declaring less means the
        # engine's dropna leaves rows the rule cannot actually read.
        for column in rule.required_columns:
            first_valid = computed.index.get_loc(computed[column].first_valid_index()) + 1
            assert rule.warmup >= first_valid, f"{rule.name} under-declares warmup for {column}"

    @pytest.mark.parametrize("rule", RULES, ids=lambda r: r.name)
    def test_a_bias_stays_inside_the_contract(self, rule, computed):
        data = computed.dropna(subset=rule.required_columns)
        assert all(-1.0 <= b <= 1.0 for b in replay(rule, data))
