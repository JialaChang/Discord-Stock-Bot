import pytest
from pandas import DataFrame

from src.models import Position, Signal
from src.quant import BacktestEngine, EMAStrategy, InsufficientDataError, RSIStrategy, Strategy
from src.quant.backtest import INITIAL_CAPITAL, STOP_LOSS, history_window
from tests.conftest import make_ohlcv


class ScriptedStrategy(Strategy):
    """Emits a predetermined action per bar, so tests drive the engine directly.

    Declares `Close` so the engine's dropna has a real column to work on without
    any indicator being computed. The bar counter lives in `reset()` so replaying
    the same instance restarts the script rather than resuming part-way through it.
    """
    required_columns = ["Close"]
    warmup = 0

    def __init__(self, actions):
        self.actions = list(actions)
        super().__init__()

    def reset(self) -> None:
        self.bar = -1

    def signal(self, history: DataFrame, position: Position | None) -> Signal:
        self.bar += 1
        action = self.actions[self.bar] if self.bar < len(self.actions) else "HOLD"
        return Signal(action, {}, {})


class RecordingStrategy(Strategy):
    """Holds throughout and keeps every history frame the engine handed it."""
    required_columns = ["Close"]
    warmup = 0

    def __init__(self, lookback: int | None = 1):
        self.lookback = lookback
        super().__init__()

    def reset(self) -> None:
        self.seen: list[DataFrame] = []

    def signal(self, history: DataFrame, position: Position | None) -> Signal:
        self.seen.append(history)
        return Signal("HOLD", {}, {})


class TestOrderExecution:
    def test_signal_fills_at_the_next_open(self):
        # A signal is raised on today's close and filled on tomorrow's open.
        data = make_ohlcv([100, 100, 100], opens=[100, 105, 110])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG", "EXIT_LONG"])).run("X", data)

        trade = result.trades[0]
        assert (trade.entry_price, trade.exit_price) == (105, 110)

    def test_open_position_is_closed_at_the_final_close(self):
        data = make_ohlcv([100, 100, 130], opens=[100, 100, 120])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG"])).run("X", data)

        trade = result.trades[-1]
        assert trade.exit_price == 130
        assert trade.exit_signal.conditions == {"end_of_backtest": True}

    def test_entry_at_an_unusable_price_is_skipped(self):
        # A zero fill price would make every later ratio meaningless.
        data = make_ohlcv([100, 100, 100], opens=[100, 0, 100])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG"])).run("X", data)

        assert result.trades == []


class TestPositionSizing:
    def test_position_is_sized_in_whole_shares(self):
        # 100,000 buys 162 shares at 617; the 46 left over stays in cash.
        data = make_ohlcv([617, 617, 617])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG"])).run("X", data)

        assert result.trades[0].shares == 162

    def test_leftover_cash_does_not_participate_in_the_move(self):
        # 333 shares at 300 leaves 100 idle, so a +10% move earns slightly less than 10%.
        data = make_ohlcv([300, 300, 330], opens=[300, 300, 330])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG"])).run("X", data)

        assert result.total_return == pytest.approx(9.99)

    def test_a_share_costing_more_than_the_account_is_not_bought(self):
        # Whole shares only, so an unaffordable share means no trade at all.
        data = make_ohlcv([INITIAL_CAPITAL * 2] * 3)
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG"])).run("X", data)

        assert result.trades == []


class TestStopLoss:
    def test_long_stop_fills_at_the_stop_price(self):
        # Enter at 100, then an intraday low of 80 breaks the 15% stop at 85.
        data = make_ohlcv([100, 100, 90], opens=[100, 100, 95],
                          highs=[101, 101, 96], lows=[99, 99, 80])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG"])).run("X", data)

        trade = result.trades[0]
        assert trade.exit_price == pytest.approx(100 * (1 - STOP_LOSS))
        assert trade.exit_signal.conditions == {"stop_loss": True}

    def test_long_stop_fills_at_the_open_when_the_price_gaps_below_it(self):
        # Gapping to 70 means the 85 stop was never available; fill where it opened.
        data = make_ohlcv([100, 100, 70], opens=[100, 100, 70],
                          highs=[101, 101, 72], lows=[99, 99, 68])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG"])).run("X", data)

        assert result.trades[0].exit_price == pytest.approx(70.0)

    def test_short_stop_fills_at_the_stop_price(self):
        data = make_ohlcv([100, 100, 120], opens=[100, 100, 110],
                          highs=[101, 101, 130], lows=[99, 99, 105])
        result = BacktestEngine(ScriptedStrategy(["ENTER_SHORT"])).run("X", data)

        trade = result.trades[0]
        assert trade.exit_price == pytest.approx(100 * (1 + STOP_LOSS))
        assert trade.exit_signal.conditions == {"stop_loss": True}

    def test_short_stop_fills_at_the_open_when_the_price_gaps_above_it(self):
        data = make_ohlcv([100, 100, 140], opens=[100, 100, 140],
                          highs=[101, 101, 145], lows=[99, 99, 138])
        result = BacktestEngine(ScriptedStrategy(["ENTER_SHORT"])).run("X", data)

        assert result.trades[0].exit_price == pytest.approx(140.0)


class TestEquityCurve:
    def test_flat_market_leaves_capital_untouched(self):
        result = BacktestEngine(ScriptedStrategy([])).run("X", make_ohlcv([100, 100, 100]))

        assert result.equity_curve.iloc[0] == pytest.approx(INITIAL_CAPITAL)
        assert result.total_return == pytest.approx(0.0)

    def test_pnl_of_every_trade_sums_to_the_final_equity(self):
        # Two round trips of +10%: 1,000 shares at 100, then 1,000 at 110.
        data = make_ohlcv([100, 100, 110, 110, 121, 121],
                          opens=[100, 100, 110, 110, 121, 121])
        actions = ["ENTER_LONG", "EXIT_LONG", "ENTER_LONG", "EXIT_LONG"]
        result = BacktestEngine(ScriptedStrategy(actions)).run("X", data)

        assert [t.profit_and_loss for t in result.trades] == [pytest.approx(10_000), pytest.approx(11_000)]
        assert result.equity_curve.iloc[-1] == pytest.approx(
            INITIAL_CAPITAL + sum(t.profit_and_loss for t in result.trades))

    def test_a_gapped_short_loses_more_than_the_account(self):
        # The collateral is the whole account, so the stop at +15% would cap the loss --
        # but a quadrupling overnight means it fills at the open, far past the collateral.
        data = make_ohlcv([100, 100, 400, 100], opens=[100, 100, 400, 100],
                          highs=[101, 101, 410, 101], lows=[99, 99, 390, 99])
        result = BacktestEngine(ScriptedStrategy(["ENTER_SHORT", "HOLD", "ENTER_LONG"])).run("X", data)

        assert result.equity_curve.iloc[-1] == pytest.approx(-200_000)
        assert result.max_drawdown < -100.0
        assert result.trade_count == 1  # Nothing left to trade with

    def test_equity_has_one_point_per_backtested_bar(self):
        data = make_ohlcv([100] * 6)
        result = BacktestEngine(ScriptedStrategy([])).run("X", data)

        assert len(result.equity_curve) == len(data)


class TestReversal:
    def test_reverse_short_closes_the_long_and_opens_at_the_same_price(self):
        # Bar 2 both exits the long and enters the short, on that bar's open.
        data = make_ohlcv([100, 100, 110, 110], opens=[100, 100, 110, 110])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG", "REVERSE_SHORT"])).run("X", data)

        closed, opened = result.trades
        assert (closed.side, opened.side) == ("LONG", "SHORT")
        assert closed.exit_date == opened.entry_date == data.index[2].date()
        assert closed.exit_price == opened.entry_price == 110

    def test_reverse_long_closes_the_short_and_opens_long(self):
        data = make_ohlcv([100, 100, 90, 90], opens=[100, 100, 90, 90])
        result = BacktestEngine(ScriptedStrategy(["ENTER_SHORT", "REVERSE_LONG"])).run("X", data)

        closed, opened = result.trades
        assert (closed.side, opened.side) == ("SHORT", "LONG")
        assert closed.exit_price == opened.entry_price == 90

    def test_the_closing_trade_is_attributed_to_the_flipping_signal(self):
        # Otherwise the report would show a bare exit with no reason attached.
        data = make_ohlcv([100, 100, 110, 110], opens=[100, 100, 110, 110])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG", "REVERSE_SHORT"])).run("X", data)

        assert result.trades[0].exit_signal.action == "REVERSE_SHORT"

    def test_reversing_while_flat_does_nothing(self):
        # A reverse names a side to flip to; with nothing held there is nothing to flip.
        data = make_ohlcv([100, 100, 100])
        result = BacktestEngine(ScriptedStrategy(["REVERSE_LONG"])).run("X", data)

        assert result.trades == []

    def test_reversing_onto_the_side_already_held_does_nothing(self):
        data = make_ohlcv([100, 100, 100, 100], opens=[100, 100, 100, 100])
        result = BacktestEngine(ScriptedStrategy(["ENTER_LONG", "REVERSE_LONG"])).run("X", data)

        assert result.trade_count == 1
        assert result.trades[0].exit_signal.conditions == {"end_of_backtest": True}

    def test_a_crossover_strategy_alternates_sides_without_going_flat(self):
        # Falling, rising, falling. The opening leg leaves EMA_5 under EMA_20 by the time the
        # warm-up ends, so both a golden and a death cross land inside the backtest itself.
        closes = ([100 - 0.4 * i for i in range(25)]
                  + [90 + 1.0 * i for i in range(25)]
                  + [115 - 0.8 * i for i in range(25)])
        result = BacktestEngine(EMAStrategy()).run("X", make_ohlcv(closes))

        assert [t.side for t in result.trades] == ["LONG", "SHORT"]
        assert result.trades[0].entry_signal.conditions == {"ema_golden_cross": True}
        assert result.trades[0].exit_signal.conditions == {"ema_death_cross": True}
        assert result.trades[0].exit_date == result.trades[1].entry_date


class TestHistoryWindow:
    """The slicing rule itself, which the strategy tests also build their inputs from."""

    def test_the_window_ends_at_the_requested_bar(self):
        data = make_ohlcv([100] * 10)

        assert list(history_window(data, 4, 3).index) == list(data.index[2:5])

    def test_an_early_bar_gets_a_short_window_rather_than_a_wrapped_one(self):
        # An unclamped start would go negative, count from the end, and return nothing.
        data = make_ohlcv([100] * 10)

        assert list(history_window(data, 0, 3).index) == [data.index[0]]

    def test_a_lookback_longer_than_the_frame_yields_the_whole_frame(self):
        data = make_ohlcv([100] * 5)

        assert len(history_window(data, 4, 99)) == 5

    def test_a_lookback_of_none_yields_everything_up_to_the_bar(self):
        data = make_ohlcv([100] * 10)

        assert list(history_window(data, 6, None).index) == list(data.index[:7])


class TestStrategyHistory:
    def test_the_strategy_sees_lookback_rows_ending_at_today(self):
        data = make_ohlcv([100] * 10)
        strategy = RecordingStrategy(lookback=3)
        BacktestEngine(strategy).run("X", data)

        assert [len(h) for h in strategy.seen] == [1, 2, 3, 3, 3, 3, 3, 3, 3, 3]
        assert [h.index[-1] for h in strategy.seen] == list(data.index)

    def test_history_never_reaches_past_today(self):
        # The one guarantee that makes lookahead bias structurally impossible.
        data = make_ohlcv([100] * 10)
        strategy = RecordingStrategy(lookback=4)
        BacktestEngine(strategy).run("X", data)

        assert all(h.index.max() == data.index[i] for i, h in enumerate(strategy.seen))

    def test_the_windows_first_bar_looks_back_before_start(self):
        # Rows before `start` are kept precisely so the opening bar is not short-changed.
        data = make_ohlcv([100] * 10)
        strategy = RecordingStrategy(lookback=3)
        result = BacktestEngine(strategy).run("X", data, start=data.index[6])

        first = strategy.seen[0]
        assert len(first) == 3
        assert first.index[0] == data.index[4]   # two bars before the window opens
        assert first.index[-1] == data.index[6]
        assert len(result.equity_curve) == 4     # only the window is backtested

    def test_a_lookback_of_none_hands_over_everything_so_far(self):
        data = make_ohlcv([100] * 10)
        strategy = RecordingStrategy(lookback=None)
        BacktestEngine(strategy).run("X", data, start=data.index[6])

        assert [len(h) for h in strategy.seen] == [7, 8, 9, 10]
        assert all(h.index[0] == data.index[0] for h in strategy.seen)

    def test_the_result_carries_the_window_not_the_rows_before_it(self):
        data = make_ohlcv([100] * 10)
        result = BacktestEngine(RecordingStrategy()).run("X", data, start=data.index[6])

        assert result.data.index[0] == data.index[6]
        assert len(result.data) == len(result.equity_curve) == 4


class TestStrategyLifecycle:
    def test_replaying_an_engine_repeats_the_same_backtest(self):
        # `reset()` is what keeps per-run strategy state from leaking into the next run.
        data = make_ohlcv([100, 100, 110, 110], opens=[100, 100, 110, 110])
        engine = BacktestEngine(ScriptedStrategy(["ENTER_LONG", "EXIT_LONG"]))

        first = engine.run("X", data)
        second = engine.run("X", data)

        assert first.trade_count == second.trade_count == 1
        assert first.total_return == second.total_return

    def test_strategy_state_does_not_accumulate_across_runs(self):
        data = make_ohlcv([100] * 5)
        strategy = RecordingStrategy()
        engine = BacktestEngine(strategy)

        engine.run("X", data)
        engine.run("X", data)

        assert len(strategy.seen) == len(data)


class TestIndicatorWarmup:
    def test_required_history_days_asks_for_more_than_the_window(self):
        assert BacktestEngine(EMAStrategy()).required_history_days(30) > 30

    def test_a_longer_warmup_asks_for_more_history(self):
        rsi = BacktestEngine(RSIStrategy()).required_history_days(30)   # warmup 14
        ema = BacktestEngine(EMAStrategy()).required_history_days(30)   # warmup 20
        assert rsi < ema

    def test_a_longer_lookback_asks_for_more_history(self):
        # Warm-up and lookback stack: the lookback rows must already carry indicator values.
        short = BacktestEngine(RecordingStrategy(lookback=1)).required_history_days(30)
        long = BacktestEngine(RecordingStrategy(lookback=20)).required_history_days(30)
        assert short < long

    def test_a_lookback_of_none_asks_for_nothing_extra(self):
        # None guarantees no minimum, so there is no fixed amount to reserve for it.
        one = BacktestEngine(RecordingStrategy(lookback=1)).required_history_days(30)
        unbounded = BacktestEngine(RecordingStrategy(lookback=None)).required_history_days(30)
        assert one == unbounded

    def test_start_excludes_the_warmup_rows(self):
        data = make_ohlcv([100] * 10)
        result = BacktestEngine(ScriptedStrategy([])).run("X", data, start=data.index[6])

        assert len(result.equity_curve) == 4
        assert result.equity_curve.index[0] == data.index[6]

    def test_short_window_still_produces_a_backtest(self):
        # EMA_20 consumes 19 rows; the requested window must survive that.
        data = make_ohlcv([100 + i for i in range(40)])
        result = BacktestEngine(EMAStrategy()).run("X", data, start=data.index[30])

        assert len(result.equity_curve) == 10
        assert result.equity_curve.index[0] == data.index[30]

    def test_insufficient_data_raises_an_actionable_error(self):
        data = make_ohlcv([100 + i for i in range(25)])

        with pytest.raises(InsufficientDataError) as excinfo:
            BacktestEngine(EMAStrategy()).run("X", data, start=data.index[24])

        message = str(excinfo.value)
        assert "warm-up" in message and "longer" in message
