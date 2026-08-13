from datetime import date, datetime
import pandas as pd
import pytest

from src.models import BacktestResult, Signal, StockSnapshot, Trade


HOLD = Signal("HOLD", {}, {})


def make_trade(side, entry_price, exit_price, shares=1):
    return Trade("X", date(2026, 1, 1), entry_price, date(2026, 1, 2), exit_price,
                 HOLD, HOLD, side, shares)


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
        assert make_snapshot(110, 100, rsi_value=48.246).rsi_str == "48.25"


class TestTrade:
    @pytest.mark.parametrize("side,exit_price,expected_roi",
                             [("LONG", 125, 25.0), ("LONG", 90, -10.0),
                              ("SHORT", 80, 20.0), ("SHORT", 110, -10.0)])
    def test_roi_follows_the_side(self, side, exit_price, expected_roi):
        assert make_trade(side, 100, exit_price).return_on_investment == pytest.approx(expected_roi)

    def test_squeezed_short_reports_more_than_a_total_loss(self):
        # Nothing caps the loss at -100%: the buy-back price has no ceiling.
        assert make_trade("SHORT", 100, 350).return_on_investment == pytest.approx(-250.0)

    @pytest.mark.parametrize("entry_price", [0, -5])
    def test_unusable_entry_price_does_not_divide_by_zero(self, entry_price):
        # Unreachable from the engine, which refuses to fill at a non-positive price.
        assert make_trade("LONG", entry_price, 50).return_on_investment == 0.0
        assert make_trade("SHORT", entry_price, 50).return_on_investment == 0.0

    def test_pnl_and_roi_describe_the_same_trade(self):
        # The two sit side by side in the HTML report; 7 shares sold at 100, bought back at 137.
        trade = make_trade("SHORT", 100, 137, shares=7)
        assert trade.profit_and_loss == pytest.approx(-259.0)
        assert trade.return_on_investment == pytest.approx(-37.0)

    def test_pnl_scales_with_position_size_not_with_price_level(self):
        cheap = make_trade("LONG", 50, 55, shares=200)     # 10,000 committed, +10%
        pricey = make_trade("LONG", 500, 550, shares=20)   # 10,000 committed, +10%
        assert cheap.profit_and_loss == pytest.approx(pricey.profit_and_loss)

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
