import os
import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, List

import discord
import wavelink  # Thay thế yt-dlp bằng wavelink
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Cấu hình Embed giống bản cũ
def format_duration(milliseconds: int) -> str:
    seconds = int(milliseconds / 1000)
    if seconds <= 0: return "Unknown"
    s = int(seconds % 60)
    m = int((seconds // 60) % 60)
    h = int(seconds // 3600)
    if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def build_embed(track: Optional[wavelink.Playable], volume: int, paused: bool) -> discord.Embed:
    color = 0x5B2C83
    e = discord.Embed(color=color)
    if track is None:
        e.title = "Now Playing"
        e.description = "Chưa có bài nào đang phát."
        e.add_field(name="Volume", value=f"{volume}%", inline=True)
        return e
    e.title = "Now Playing"
    e.description = f"**[{track.title}]({track.uri})**"
    e.add_field(name="Tác giả", value=track.author, inline=True)
    e.add_field(name="Thời lượng", value=format_duration(track.length), inline=True)
    e.add_field(name="Trạng thái", value="Paused" if paused else "Playing", inline=True)
    if track.artwork:
        e.set_image(url=track.artwork)
    e.set_footer(text="Lavalink Player")
    return e

class PlayerView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="◀️/⏸️", style=discord.ButtonStyle.success)
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc: return
        await vc.pause(not vc.paused)
        await interaction.response.edit_message(embed=build_embed(vc.current, vc.volume, vc.paused), view=self)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc: return
        await vc.skip()
        await interaction.response.defer()

    @discord.ui.button(label="👍 Stop", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc: wavelink.Player = interaction.guild.voice_client
        if not vc: return
        await vc.disconnect()
        await interaction.response.send_message("Đã dừng và rời kênh.", ephemeral=True)

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Kết nối tới Lavalink Server cục bộ trên VPS Azure
        nodes = [wavelink.Node(uri="http://127.0.0.1:2333", password="youshallnotpass")]
        await wavelink.Pool.connect(nodes=nodes, client=self, cache_capacity=100)

    async def on_ready(self):
        print(f"Logged in as {self.user} | Lavalink Ready")
        await self.tree.sync()

    # Tự động cập nhật tin nhắn Now Playing khi chuyển bài
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        # Bạn có thể thêm code gửi thông báo Now Playing ở đây nếu muốn

bot = MusicBot()

@bot.tree.command(name="play", description="Phát nhạc qua Lavalink")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("Bạn cần vào Voice Channel!")

    await interaction.response.defer()
    
    # Kết nối vào Voice Channel dùng Wavelink Player
    if not interaction.guild.voice_client:
        vc: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
    else:
        vc: wavelink.Player = interaction.guild.voice_client

    # Tìm kiếm bài hát (Tự động bypass qua Lavalink)
    tracks = await wavelink.Playable.search(query)
    if not tracks:
        return await interaction.followup.send("Không tìm thấy kết quả.")

    track = tracks[0]
    await vc.queue.put_wait(track)

    if not vc.playing:
        await vc.play(vc.queue.get())
        msg = "Đang phát"
    else:
        msg = "Đã thêm vào hàng đợi"

    view = PlayerView(bot, interaction.guild.id)
    embed = build_embed(track, vc.volume, vc.paused)
    await interaction.followup.send(f"{msg}: **{track.title}**", embed=embed, view=view)

@bot.tree.command(name="stop", description="Rời khỏi kênh")
async def stop(interaction: discord.Interaction):
    vc: wavelink.Player = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("Đã dừng nhạc.")
    else:
        await interaction.response.send_message("Bot không ở trong kênh voice.")

token = os.getenv("DISCORD_TOKEN")
bot.run(token)