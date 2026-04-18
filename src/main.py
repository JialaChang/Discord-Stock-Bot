import yfinance as yf
import pandas as pd
import pandas_ta as pta  # type: ignore
import mplfinance as mpf
import twstock
import io


# Stock Object
class Stock:

    def __init__(self, ticker):
        """Initialize"""

        self.ticker = ticker
        self.name = ""
        self.data = pd.DataFrame()
        self.prices = pd.Series()
        self.change = 0.0
        self.rsi = pd.Series()
        self.ma5 = pd.Series()
        self.ma20 = pd.Series()

        # get stock's name
        try:
            if self.ticker.split('.')[0] in twstock.codes:
                self.name = twstock.codes[self.ticker.split('.')[0]].name
            else:
                self.name = self.ticker
        except:
            self.name = ticker

    def get_data(self, data):
        """Get stock data"""

        # all data (open, high, low, close)
        # determine the dimension of data
        if isinstance(data.columns, pd.MultiIndex):
            self.data = data.xs(self.ticker, level=1, axis=1)
        else:
            self.data = data
        # price
        self.prices = self.data['Close']
        # price change
        now_p = self.prices.iloc[-1]
        prev_p = self.prices.iloc[-2]
        self.change = (now_p - prev_p) / prev_p * 100
        # RSI
        self.rsi = pta.rsi(self.prices, length=14)  # type: ignore
        # MA
        self.ma5 = pta.sma(self.prices, length=5)   # type: ignore
        self.ma20 = pta.sma(self.prices, length=20) # type: ignore

    def output_data(self):
        """Output stock data"""

        print("-" * 40)
        print(f"╎ [{self.name:<12}({self.ticker:<10})]")
        print(f"╎ {'∇' if self.change < 0 else '∆'} {abs(self.change):<8.2f}%")
        print(f"╎ Price: {self.prices.iloc[-1]:<8.2f} RSI: {self.rsi.iloc[-1]:<6.2f}")
        print("-" * 40)

    def return_data(self):
        """Return data to discord bot"""

        return {
            "name": self.name,
            "price": f"{self.prices.iloc[-1]:.2f}",
            "change": self.change,
            "change_str": f"{'∇' if self.change < 0 else '∆'} {abs(self.change):.2f}%",
            "rsi": f"{self.rsi.iloc[-1]:.2f}"
        }

    def output_plot(self):
        """Output stock plot"""

        # use n days data to draw
        n = 28
        plot_data = self.data.iloc[-n:]
        plot_ma5 = self.ma5.iloc[-n:]
        plot_ma20 = self.ma20.iloc[-n:]

        addplot = [mpf.make_addplot(plot_ma5, color='orange', width=1, label='5MA'),
              mpf.make_addplot(plot_ma20, color='#9A1EE8', width=1, label='20MA')]
        color = mpf.make_marketcolors(up='red', down='green', inherit=True)
        style = mpf.make_mpf_style(marketcolors=color, gridstyle='--')

        mpf.plot(plot_data,
                 type='candle',
                 addplot=addplot,
                 style=style,
                 title=f"\n{self.ticker}",
                 show_nontrading=False)
        
    def return_plot(self):
        """Return stock plot to discord bot"""

        # use n days data to draw
        n = 28
        plot_data = self.data.iloc[-n:]
        plot_ma5 = self.ma5.iloc[-n:]
        plot_ma20 = self.ma20.iloc[-n:]

        addplot = [mpf.make_addplot(plot_ma5, color='orange', width=1, label='5MA'),
              mpf.make_addplot(plot_ma20, color='#9A1EE8', width=1, label='20MA')]
        color = mpf.make_marketcolors(up='red', down='green', inherit=True)
        style = mpf.make_mpf_style(marketcolors=color, gridstyle='--')

        # setup memory buffer
        buffer = io.BytesIO()

        mpf.plot(plot_data,
                 type='candle',
                 addplot=addplot,
                 style=style,
                 title=f"\n{self.ticker}",
                 show_nontrading=False,
                 savefig=buffer)
        
        # point to the begining
        buffer.seek(0)
        return buffer

        
    def fetch_analyze(self) -> bool:
        """Download data and use other functions"""

        try:

            raw_data = yf.download(self.ticker, period="3mo", threads=True, group_by="column")
            if raw_data is None or raw_data.empty:
                return False
            
            self.get_data(raw_data)
            return True
        
        except:
            return False
        

if __name__ == "__main__":

    # Stock List
    ticker_list = ["2330.TW", "0050.TW"]
    # Setup object list
    stocks = [Stock(t) for t in ticker_list]
    all_raw_data = yf.download(ticker_list, period="3mo", group_by="column")

    for s in stocks:
        s.get_data(all_raw_data)
        s.output_data()
        s.output_plot()