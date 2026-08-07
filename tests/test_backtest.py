import pytest
from pandas import Series

from src.models import Position, Signal
from src.quant import BacktestEngine, EMAStrategy, InsufficientDataError, RSIStrategy, Strategy
from src.quant.backtest import INITIAL_CAPITAL, STOP_LOSS
from tests.conftest import make_ohlcv


class ScriptedStrategy(Strategy):
    """Emits a predetermined action per bar, so tests drive the engine directly.

    Declares `Close` so the engine's dropna has a real column to work on without
    any indicator being computed.
    """
    required_columns = ["Close"]
    warmup = 0

    def __init__(self, actions):
        self.actions = list(actions)
        self.bar = -1

    def signal(self, row: Series, position: Position | None) -> Signal:
        self.bar += 1
        action = self.actions[self.bar] if self.bar < len(self.actions) else "HOLD"
        return Signal(action, {}, {})


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

    def test_equity_never_goes_negative_in_a_short_squeeze(self):
        # Unclamped, a short against a price that quadruples yields a negative
        # multiplier, which then flips the sign of everything that follows.
        data = make_ohlcv([100, 100, 400], opens=[100, 100, 400],
                          highs=[101, 101, 410], lows=[99, 99, 390])
        result = BacktestEngine(ScriptedStrategy(["ENTER_SHORT"])).run("X", data)

        assert (result.equity_curve >= 0).all()
        assert result.max_drawdown >= -100.0

    def test_equity_has_one_point_per_backtested_bar(self):
        data = make_ohlcv([100] * 6)
        result = BacktestEngine(ScriptedStrategy([])).run("X", data)

        assert len(result.equity_curve) == len(data)


class TestIndicatorWarmup:
    def test_required_history_days_asks_for_more_than_the_window(self):
        assert BacktestEngine(EMAStrategy()).required_history_days(30) > 30

    def test_a_longer_warmup_asks_for_more_history(self):
        rsi = BacktestEngine(RSIStrategy()).required_history_days(30)   # warmup 14
        ema = BacktestEngine(EMAStrategy()).required_history_days(30)   # warmup 20
        assert rsi < ema

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
