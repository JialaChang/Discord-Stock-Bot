from datetime import date, datetime
import pandas as pd
import pytest

from src.models import BacktestResult, Signal, StockSnapshot, Trade, pnl_ratio


HOLD = Signal("HOLD", {}, {})


def make_trade(side, entry_price, exit_price):
    return Trade("X", date(2026, 1, 1), entry_price, date(2026, 1, 2), exit_price,
                 HOLD, HOLD, side)


def make_result(equity, trades=()):
    index = pd.bdate_range('2026-01-01', periods=len(equity))
    curve = pd.Series(equity, index=index, dtype=float)
    return BacktestResult("X", list(trades), curve, pd.DataFrame())


def make_snapshot(current_price, previous_close, rsi_value: float | None =50.0):
    return StockSnapshot("X", "XName", current_price, previous_close, rsi_value,
                         datetime(2026, 1, 2, 13, 30))


class TestStockSnapshot:
    def test_change_is_derived_from_the_previous_close(self):
        assert make_snapshot(110, 100).change_percent == pytest.approx(10.0)
        assert make_snapshot(90, 100).change_percent == pytest.approx(-10.0)

    @pytest.mark.parametrize("previous_close", [0, -5])
    def test_unusable_baseline_does_not_divide_by_zero(self, previous_close):
        # A stored close of 0 survives into the frame unadjusted, so this is reachable.
        assert make_snapshot(110, previous_close).change_percent == 0.0

    def test_change_str_direction(self):
        assert make_snapshot(110, 100).change_str.startswith('∆')
        assert make_snapshot(90, 100).change_str.startswith('∇')

    def test_missing_rsi_reads_as_na(self):
        # 0.00 on the Embed would read as extreme oversold.
        assert make_snapshot(110, 100, rsi_value=None).rsi_str == "N/A"

    def test_present_rsi_is_formatted(self):
        assert make_snapshot(110, 100, rsi_value=48.246).rsi_str == "48.25"


class TestPnlRatio:
    def test_long_gain(self):
        assert pnl_ratio("LONG", 100, 110) == pytest.approx(1.10)

    def test_long_loss(self):
        assert pnl_ratio("LONG", 100, 90) == pytest.approx(0.90)

    def test_short_gains_when_price_falls(self):
        assert pnl_ratio("SHORT", 100, 90) == pytest.approx(1.10)

    def test_short_loses_when_price_rises(self):
        assert pnl_ratio("SHORT", 100, 110) == pytest.approx(0.90)

    def test_short_is_wiped_out_at_double(self):
        assert pnl_ratio("SHORT", 100, 200) == 0.0

    def test_short_is_floored_at_zero(self):
        # Unclamped this is 2 - 3.5 = -1.5, which would invert the sign of every
        # later compounding step and corrupt the rest of the equity curve.
        assert pnl_ratio("SHORT", 100, 350) == 0.0

    @pytest.mark.parametrize("entry_price", [0, -5])
    def test_unusable_entry_price_is_inert(self, entry_price):
        assert pnl_ratio("LONG", entry_price, 50) == 1.0
        assert pnl_ratio("SHORT", entry_price, 50) == 1.0


class TestTrade:
    def test_long_roi(self):
        assert make_trade("LONG", 100, 125).return_on_investment == pytest.approx(25.0)

    def test_short_roi(self):
        assert make_trade("SHORT", 100, 80).return_on_investment == pytest.approx(20.0)

    def test_wiped_short_reports_minus_100_percent(self):
        # The equity curve floors at zero, so the report must not claim -250%.
        assert make_trade("SHORT", 100, 350).return_on_investment == pytest.approx(-100.0)

    @pytest.mark.parametrize("side,entry_price,exit_price",
                             [("LONG", 100, 137), ("SHORT", 100, 137), ("SHORT", 100, 260)])
    def test_roi_always_agrees_with_the_compounding_ratio(self, side, entry_price, exit_price):
        trade = make_trade(side, entry_price, exit_price)
        expected = (pnl_ratio(side, entry_price, exit_price) - 1) * 100
        assert trade.return_on_investment == pytest.approx(expected)

    def test_is_profit_follows_direction(self):
        assert make_trade("SHORT", 100, 90).is_profit
        assert not make_trade("SHORT", 100, 110).is_profit
        assert make_trade("LONG", 100, 110).is_profit


class TestBacktestResult:
    def test_total_return(self):
        assert make_result([100.0, 150.0]).total_return == pytest.approx(50.0)

    def test_max_drawdown_measures_peak_to_trough(self):
        assert make_result([100.0, 120.0, 60.0, 90.0]).max_drawdown == pytest.approx(-50.0)

    def test_win_rate(self):
        trades = [make_trade("LONG", 100, 110), make_trade("LONG", 100, 90)]
        assert make_result([100.0], trades).win_rate == pytest.approx(50.0)

    def test_win_rate_without_trades(self):
        assert make_result([100.0]).win_rate == 0.0

    def test_empty_equity_curve_does_not_raise(self):
        empty = make_result([])
        assert empty.total_return == 0.0
        assert empty.max_drawdown == 0.0

    def test_wiped_out_equity_does_not_divide_by_zero(self):
        # A short squeeze can take the curve to zero; the metrics must still resolve.
        assert make_result([100.0, 50.0, 0.0]).max_drawdown == pytest.approx(-100.0)
