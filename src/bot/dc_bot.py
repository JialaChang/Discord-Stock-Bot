import discord
from discord.ext import commands
import sys, os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import asyncio

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.data import StockDataFetcher
from src.quant import compute_indicators_for_discord, BacktestEngine, RSIStrategy, EMAStrategy, PERIOD_DAYS, InsufficientDataError
from src.utils import generate_history_chart, generate_intraday_chart, generate_backtest_chart
from src.bot import send_stock_response, send_backtest_response


logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
# Specifying a test guild speeds up command sync; without it commands go through a global sync (takes about 1 hour to take effect).
GUILD_ID = os.getenv('GUILD')
GUILD = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

# Validate environment variables
if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in environment variables")
if not GUILD:
    logger.info("GUILD_ID not found in environment variables")

# Intents decide which gateway events the bot subscribes to; undeclared events are not pushed.
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='$', intents=intents)


@bot.event
async def on_ready():
    if GUILD:
        bot.tree.copy_global_to(guild=GUILD)
    await bot.tree.sync()
    logger.info(f"Discord Bot Login Identity --> {bot.user}")


@bot.event
async def on_disconnect():
    logger.debug("Discord Bot Disconnected...")


@bot.event
async def on_resumed():
    logger.info("Discord Bot Reconnected.")


@bot.tree.command(name="stock", description="Enter a ticker to query info and charts (TW/US stocks and some indices only)")
async def analyze_stock(interaction: discord.Interaction, ticker: str):
    # defer() prevents Discord from timing out the interaction if processing takes over 3 seconds
    await interaction.response.defer()

    try:
        # asyncio.to_thread offloads the blocking SQLite/yfinance calls to a thread pool so the event loop is not blocked.
        # The constructor itself hits SQLite to normalize the ticker, so it is offloaded too.
        fetcher = await asyncio.to_thread(StockDataFetcher, ticker)
        stock_name = await asyncio.to_thread(fetcher.fetch_stock_name)
        stock_ticker = fetcher.ticker

        # Run requests concurrently to improve responsiveness
        history_data, intraday_data = await asyncio.gather(
            asyncio.to_thread(fetcher.fetch_historical_data),
            asyncio.to_thread(fetcher.fetch_intraday_data)
        )

        if history_data.empty:
            await interaction.followup.send(f"Could not retrieve data for `{stock_ticker}`...\n> Please check that the ticker is correct, or the stock may not be in the database yet")
            logger.warning(f"Failed to retrieve data for '{stock_ticker}'...")
            return
        # Intraday is optional
        if intraday_data.empty:
            logger.info(f"No intraday data for '{stock_ticker}', showing the daily chart only.")

        latest_time = await asyncio.to_thread(fetcher.fetch_latest_time)

        snapshot = await asyncio.to_thread(
            compute_indicators_for_discord, stock_ticker, stock_name, history_data, intraday_data, latest_time
        )

        chart_jobs = [asyncio.to_thread(generate_history_chart, stock_ticker, history_data)]
        if not intraday_data.empty:
            chart_jobs.append(asyncio.to_thread(generate_intraday_chart, stock_ticker, intraday_data))
        buffers = await asyncio.gather(*chart_jobs)

        chart_bytes = [b.getvalue() for b in buffers]
        for b in buffers:
            b.close()
        history_bytes = chart_bytes[0]
        intraday_bytes = chart_bytes[1] if len(chart_bytes) > 1 else None

        await send_stock_response(interaction, snapshot, history_bytes, intraday_bytes)
        logger.info(f"Response for '{stock_ticker}' sent successfully!")

    except InsufficientDataError as e:
        logger.warning(f"Insufficient data for '{ticker}': {e}")
        await interaction.followup.send(f"Not enough historical data for `{ticker}`...\n> {e}")
    except Exception as e:
        logger.error(f"Error sending response for '{ticker}': {e}")
        await interaction.followup.send("An error occurred, please try again later or check that the ticker is correct...")


STRATEGIES = {"RSI": RSIStrategy, "EMA": EMAStrategy}
# A subset of the canonical periods in src.quant
BOT_PERIODS = ["1mo", "3mo", "6mo", "1y", "2y", "3y", "5y", "10y"]

@bot.tree.command(name="backtest", description="Enter a ticker to run a strategy backtest and show a chart (TW/US stocks and some indices only)")
@discord.app_commands.choices(
    strategy=[discord.app_commands.Choice(name=name, value=name) for name in STRATEGIES],
    period=[discord.app_commands.Choice(name=p, value=p) for p in BOT_PERIODS]
)
async def backtest_stock(interaction: discord.Interaction, ticker: str, strategy: discord.app_commands.Choice[str], period: discord.app_commands.Choice[str]):
    await interaction.response.defer()

    try:
        fetcher = await asyncio.to_thread(StockDataFetcher, ticker)
        stock_name = await asyncio.to_thread(fetcher.fetch_stock_name)
        stock_ticker = fetcher.ticker

        engine = BacktestEngine(STRATEGIES[strategy.value]())

        # Fetch extra history so the indicator warm-up is not taken out of the period the user asked for, then backtest only from `start` onwards.
        period_days = PERIOD_DAYS[period.value]
        start = datetime.now() - timedelta(days=period_days)
        data = await asyncio.to_thread(
            fetcher.fetch_historical_data, days=engine.required_history_days(period_days)
        )
        if data.empty:
            await interaction.followup.send(f"Could not retrieve historical data for `{stock_ticker}`...\n> Please check that the ticker is correct, or the stock may not be in the database yet")
            logger.warning(f"Failed to retrieve data for '{stock_ticker}'...")
            return

        result = await asyncio.to_thread(engine.run, stock_ticker, data, start)

        chart_buffer = await asyncio.to_thread(generate_backtest_chart, stock_ticker, result)
        chart_bytes = chart_buffer.getvalue()
        chart_buffer.close()

        await send_backtest_response(interaction, result, strategy.name, chart_bytes)
        logger.info(f"Backtest result for '{stock_ticker}' sent successfully!")

    except InsufficientDataError as e:
        logger.warning(f"Insufficient data to backtest '{ticker}': {e}")
        await interaction.followup.send(f"Cannot backtest `{ticker}` over {period.value}...\n> {e}")
    except Exception as e:
        logger.error(f"Error sending backtest for '{ticker}': {e}")
        await interaction.followup.send("An error occurred, please try again later or check that the ticker is correct...")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        logger.info("Starting Discord bot...")
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Discord bot Error : {e}")
