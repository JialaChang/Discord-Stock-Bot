import yfinance as yf
import pandas as pd
import pandas_ta as pta  # type: ignore
import mplfinance as mpf
import twstock
import io

TW_CODES = twstock.codes

# Stock Object
class Stock:

    def __init__(self, ticker):
        """Constructor"""

        self.ticker: str = ticker
        self.name: str = self.fetch_stock_name()
        self.data = pd.DataFrame()
        self.prices = pd.Series()
        self.change = 0.0
        self.rsi = pd.Series()
        self.ma5 = pd.Series()
        self.ma10 = pd.Series()
        self.ma20 = pd.Series()

    def fetch_stock_name(self):
        """Get stock's name"""

        # here are only work on taiwan stock
        try:
            stock_code = self.ticker.split(".TW")[0]
                
            if stock_code in TW_CODES:
                if not (self.ticker.endswith(".TW")):
                    self.ticker += ".TW"
                return TW_CODES[stock_code].name
            
            return self.ticker
            
        except:
            return self.ticker
        
        # complete us stock part
        pass

    def download_data(self) -> bool:
        """Download data and call other functions"""

        try:

            raw_data = yf.download(self.ticker, period="3mo", interval="1d", threads=True, group_by="column", auto_adjust=True)
            if raw_data is None or raw_data.empty:
                return False
            
            self.get_data(raw_data)
            return True
        
        except:
            return False

    def get_data(self, data):
        """Get stock data"""

        # determine the dimension of data
        if isinstance(data.columns, pd.MultiIndex):
            tmp_data = data.xs(self.ticker, level=1, axis=1)
        else:
            tmp_data = data
        # handle data
        self.data = tmp_data.dropna(subset=['Close'])
        if self.data.empty: return

        # price
        self.prices = self.data['Close']
        # avoid data too short
        if (len(self.prices) < 20) : return 
        # price change
        now_p = self.prices.iloc[-1]
        prev_p = self.prices.iloc[-2]
        self.change = (now_p - prev_p) / prev_p * 100
        # RSI
        self.rsi = pta.rsi(self.prices, length=14)  # type: ignore
        # MA
        self.ma5 = pta.sma(self.prices, length=5)   # type: ignore
        self.ma10 = pta.sma(self.prices, length=10) # type: ignore
        self.ma20 = pta.sma(self.prices, length=20) # type: ignore

    def output_data(self):
        """Output stock data"""

        print("-" * 40)
        print(f"╎ [{self.name}({self.ticker})]")
        print(f"╎ {'∇' if self.change < 0 else '∆'} {abs(self.change):<8.2f}%")
        print(f"╎ Price: {self.prices.iloc[-1]:<8.2f} RSI: {self.rsi.iloc[-1]:<6.2f}")
        print("-" * 40)

    def return_data(self):
        """Return stock data"""

        return {
            "name": self.name,
            "price": f"{self.prices.iloc[-1]:.2f}",
            "change": self.change,
            "change_str": f"{'∇' if self.change < 0 else '∆'} {abs(self.change):.2f}%",
            "rsi": f"{self.rsi.iloc[-1]:.2f}"
        }

    def _setup_plot(self, n=28):
        """Prepare plot data for later using"""

        # use n days data to draw
        plot_data = self.data.iloc[-n:]
        plot_ma5 = self.ma5.iloc[-n:]
        plot_ma10 = self.ma10.iloc[-n:]
        plot_ma20 = self.ma20.iloc[-n:]

        addplot = [
            mpf.make_addplot(plot_ma5, color="#FF9538", width=1, label='5MA'),
            mpf.make_addplot(plot_ma10, color="#00BBFF", width=1, label='10MA'),
            mpf.make_addplot(plot_ma20, color='#9A1EE8', width=1, label='20MA')
        ]
        color = mpf.make_marketcolors(
            # price
            up='red',
            down='green',
            edge='inherit',
            wick='inherit',
            # volume
            volume='#87ceeb',
        )
        style = mpf.make_mpf_style(marketcolors=color, gridstyle='--')

        return plot_data, addplot, style

    def output_plot(self):
        """Output stock plot"""

        plot_data, addplot, style = self._setup_plot()
        mpf.plot(
            plot_data,
            type='candle',
            addplot=addplot,
            style=style,
            title=f"\n{self.ticker}",
            show_nontrading=False,

            volume=True,
            volume_alpha=0.3,
            panel_ratios=(3, 1),
        )
        
    def return_plot(self):
        """Return stock plot to discord bot"""

        # setup memory buffer
        buffer = io.BytesIO()

        plot_data, addplot, style = self._setup_plot()
        mpf.plot(
            plot_data,
            type='candle',
            addplot=addplot,
            style=style,
            title=f"\n{self.ticker}",
            show_nontrading=False,

            volume=True,
            volume_alpha=0.3,
            panel_ratios=(3, 1),

            savefig=buffer
        )
        
        # point to the begining
        buffer.seek(0)
        return buffer


if __name__ == "__main__":

    # Stock List
    ticker_list = ["AAPL"]
    # Setup object list
    stocks = [Stock(t) for t in ticker_list]
    all_raw_data = yf.download(ticker_list, period="3mo", group_by="column")

    for s in stocks:
        s.get_data(all_raw_data)
        s.output_data()
        s.output_plot()