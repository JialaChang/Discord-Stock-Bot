import discord
from discord.ext import commands
from main import Stock
import os
from dotenv import load_dotenv
import asyncio


# load .env
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD')
GUILD = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None
# check .env
if TOKEN is None:
    raise ValueError("DISCORD_TOKEN not found in environment variables")
if GUILD is None:
    raise ValueError("GUILD_ID not found in environment variables")


# setting bot permission
intents = discord.Intents.default()
intents.message_content = True

# setup bot instance
bot = commands.Bot(command_prefix='$', intents=intents)


@bot.event
async def on_ready():
    if GUILD:
        bot.tree.copy_global_to(guild=GUILD)
    await bot.tree.sync()
    print(f"Login Identity --> {bot.user}")


@bot.tree.command(name="stock", description="輸入股票代碼查詢資訊與圖表（目前僅支援台股與美股）")
async def analyze_stock(interaction: discord.Interaction, ticker: str):

    # check postfix whether have .TW
    # if "." not in ticker:
    #         ticker = f"{ticker}.TW"
    # else:
    #         ticker = ticker

    await interaction.response.defer()

    try:

        stock_obj = Stock(ticker)
        success = await asyncio.to_thread(stock_obj.download_data)

        if not success:
            await interaction.followup.send(f"Cannot get data, please try again...")

        result = stock_obj.return_data()
        img_buf = await asyncio.to_thread(stock_obj.return_plot)
        file = discord.File(img_buf, filename="chart.png")

        # green, red
        color = 0xe74c3c if result['change'] >= 0 else 0x2ecc71
        
        # create Embed
        embed = discord.Embed(
            title=f"📈{result['name']} ({ticker})",
            color=color,
        )
        embed.add_field(name="價格", value=f"**{result['price']}**", inline=True)
        embed.add_field(name="漲跌", value=f"**{result['change_str']}**", inline=True)
        embed.add_field(name="RSI", value=f"**{result['rsi']}**", inline=True)
        embed.set_image(url="attachment://chart.png")

        await interaction.followup.send(embed=embed, file=file)

    except Exception as e:
        await interaction.followup.send(f"Errors : {e}")


bot.run(TOKEN)