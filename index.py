import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} đã sẵn sàng!")
    try:
        synced = await bot.tree.sync()
        print(f"Đã đồng bộ {len(synced)} lệnh slash")
    except Exception as e:
        print(f"Lỗi đồng bộ: {e}")

async def load_cogs():
    await bot.load_extension("cogs.music")

async def main():
    async with bot:
        await load_cogs()
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
