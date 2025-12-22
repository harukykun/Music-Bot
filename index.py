import os
import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, List

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "retries": 10,
    "fragment_retries": 10,
    "sleep_interval": 1,
    "max_sleep_interval": 5,
    "concurrent_fragment_downloads": 1,
    "cookiefile": "cookies.txt", 
    "extractor_args": {"youtube": {"player_client": ["ios"]}}, 
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
        "Accept-Language": "en-US,en;q=0.9"
    }
}

FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"

def format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return "Unknown"
    s = int(seconds % 60)
    m = int((seconds // 60) % 60)
    h = int(seconds // 3600)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    duration: Optional[int]
    thumbnail: Optional[str]

def build_embed(track: Optional[Track], volume: float, paused: bool) -> discord.Embed:
    color = 0x5B2C83
    e = discord.Embed(color=color)
    if track is None:
        e.title = "Now Playing"
        e.description = "Chưa có bài nào đang phát."
        e.add_field(name="Volume", value=f"{int(volume * 100)}%", inline=True)
        return e
    e.title = "Now Playing"
    e.description = f"**{track.title}**"
    e.add_field(name="Thời lượng", value=format_duration(track.duration), inline=True)
    e.add_field(name="Volume", value=f"{int(volume * 100)}%", inline=True)
    e.add_field(name="Trạng thái", value="Paused" if paused else "Playing", inline=True)
    if track.thumbnail:
        e.set_image(url=track.thumbnail)
    e.set_footer(text="YouTube")
    return e

class GuildPlayer:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: List[Track] = []
        self.history: List[Track] = []
        self.current: Optional[Track] = None
        self.voice: Optional[discord.VoiceClient] = None
        self.volume: float = 0.5
        self.paused: bool = False
        self._source: Optional[discord.PCMVolumeTransformer] = None
        self.text_channel_id: Optional[int] = None
        self.nowplaying_message: Optional[discord.Message] = None
        self._lock = asyncio.Lock()

    async def ensure_voice(self, interaction: discord.Interaction) -> None:
        if not interaction.user or not isinstance(interaction.user, discord.Member):
            raise RuntimeError("User invalid")
        if not interaction.user.voice or not interaction.user.voice.channel:
            raise RuntimeError("Bạn cần vào voice channel trước.")
        vc = interaction.user.voice.channel

        if self.voice and self.voice.is_connected():
            if self.voice.channel and self.voice.channel.id != vc.id:
                await self.voice.move_to(vc)
            return

        self.voice = await vc.connect(self_deaf=True)

    async def resolve_track(self, query: str) -> Track:
        loop = asyncio.get_running_loop()

        def extract() -> dict:
            with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
                info = ydl.extract_info(query, download=False)
                if "entries" in info and info["entries"]:
                    return info["entries"][0]
                return info

        info = await loop.run_in_executor(None, extract)
        title = info.get("title") or "Unknown"
        webpage_url = info.get("webpage_url") or info.get("original_url") or query
        duration = info.get("duration")
        thumbnail = info.get("thumbnail")
        stream_url = info.get("url")
        if not stream_url:
            raise RuntimeError("Không lấy được stream URL từ YouTube.")
        return Track(
            title=title,
            webpage_url=webpage_url,
            stream_url=stream_url,
            duration=duration,
            thumbnail=thumbnail
        )

    def _make_audio_source(self, track: Track) -> discord.PCMVolumeTransformer:
        audio = discord.FFmpegPCMAudio(
            track.stream_url,
            before_options=FFMPEG_BEFORE_OPTS,
            options=FFMPEG_OPTS
        )
        src = discord.PCMVolumeTransformer(audio, volume=self.volume)
        return src

    async def _edit_nowplaying(self, bot: commands.Bot) -> None:
        if not self.text_channel_id:
            return
        channel = bot.get_channel(self.text_channel_id)
        if not channel:
            return

        view = PlayerView(bot, self.guild_id)
        embed = build_embed(self.current, self.volume, self.paused)

        if self.nowplaying_message:
            try:
                await self.nowplaying_message.edit(embed=embed, view=view)
                return
            except Exception:
                self.nowplaying_message = None

        try:
            self.nowplaying_message = await channel.send(embed=embed, view=view)
        except Exception:
            pass

    async def play_current(self, bot: commands.Bot) -> None:
        if not self.voice or not self.voice.is_connected():
            return
        if not self.current:
            return

        self.paused = False
        self._source = self._make_audio_source(self.current)

        def after_play(err: Optional[Exception]) -> None:
            asyncio.run_coroutine_threadsafe(self._after_track(bot), bot.loop)

        self.voice.play(self._source, after=after_play)
        await self._edit_nowplaying(bot)

    async def _after_track(self, bot: commands.Bot) -> None:
        async with self._lock:
            if self.current:
                self.history.append(self.current)
            self.current = None
            self.paused = False
            self._source = None
            await self.play_next(bot)

    async def play_next(self, bot: commands.Bot) -> None:
        if not self.voice or not self.voice.is_connected():
            return
        if self.voice.is_playing() or self.voice.is_paused():
            return
        if not self.queue:
            await self._edit_nowplaying(bot)
            return
        self.current = self.queue.pop(0)
        await self.play_current(bot)

    async def add_and_maybe_start(self, bot: commands.Bot, track: Track) -> None:
        async with self._lock:
            self.queue.append(track)
            if self.voice and self.voice.is_connected():
                if not self.voice.is_playing() and not self.voice.is_paused() and self.current is None:
                    await self.play_next(bot)
                else:
                    await self._edit_nowplaying(bot)

    async def skip(self, bot: commands.Bot) -> None:
        async with self._lock:
            if not self.voice or not self.voice.is_connected():
                return
            if self.voice.is_playing() or self.voice.is_paused():
                self.voice.stop()

    async def previous(self, bot: commands.Bot) -> None:
        async with self._lock:
            if not self.voice or not self.voice.is_connected():
                return
            if not self.history:
                await self._edit_nowplaying(bot)
                return
            if self.current:
                self.queue.insert(0, self.current)
            self.current = self.history.pop()
            if self.voice.is_playing() or self.voice.is_paused():
                self.voice.stop()
            else:
                await self.play_current(bot)

    async def toggle_pause(self, bot: commands.Bot) -> None:
        async with self._lock:
            if not self.voice or not self.voice.is_connected():
                return
            if self.voice.is_playing():
                self.voice.pause()
                self.paused = True
                await self._edit_nowplaying(bot)
                return
            if self.voice.is_paused():
                self.voice.resume()
                self.paused = False
                await self._edit_nowplaying(bot)

    async def set_volume(self, bot: commands.Bot, delta: float) -> None:
        async with self._lock:
            self.volume = max(0.0, min(1.0, self.volume + delta))
            if self._source:
                self._source.volume = self.volume
            await self._edit_nowplaying(bot)

    async def stop_and_disconnect(self, bot: commands.Bot) -> None:
        async with self._lock:
            self.queue.clear()
            self.history.clear()
            self.current = None
            self.paused = False
            self._source = None
            if self.voice and self.voice.is_connected():
                try:
                    if self.voice.is_playing() or self.voice.is_paused():
                        self.voice.stop()
                except Exception:
                    pass
                try:
                    await self.voice.disconnect(force=True)
                except Exception:
                    pass
            self.voice = None
            await self._edit_nowplaying(bot)

class PlayerView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    def _get_player(self) -> GuildPlayer:
        return self.bot.music_players[self.guild_id]

    async def _ensure_same_voice(self, interaction: discord.Interaction) -> bool:
        player = self._get_player()
        if not interaction.user or not isinstance(interaction.user, discord.Member):
            return False
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("Bạn cần ở trong voice channel.", ephemeral=True)
            return False
        if not player.voice or not player.voice.is_connected():
            await interaction.response.send_message("Bot chưa ở trong voice channel.", ephemeral=True)
            return False
        if player.voice.channel and interaction.user.voice.channel.id != player.voice.channel.id:
            await interaction.response.send_message("Bạn phải ở cùng voice channel với bot.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🔉-10%", style=discord.ButtonStyle.secondary, custom_id="vol_down")
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_same_voice(interaction):
            return
        await interaction.response.defer()
        await self._get_player().set_volume(self.bot, -0.1)

    @discord.ui.button(label="🔊+10%", style=discord.ButtonStyle.secondary, custom_id="vol_up")
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_same_voice(interaction):
            return
        await interaction.response.defer()
        await self._get_player().set_volume(self.bot, 0.1)

    @discord.ui.button(label="⏮️Prev", style=discord.ButtonStyle.primary, custom_id="prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_same_voice(interaction):
            return
        await interaction.response.defer()
        await self._get_player().previous(self.bot)

    @discord.ui.button(label="◀️/⏸️Play/Pause", style=discord.ButtonStyle.success, custom_id="toggle_pause")
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_same_voice(interaction):
            return
        await interaction.response.defer()
        await self._get_player().toggle_pause(self.bot)

    @discord.ui.button(label="⏭️Skip", style=discord.ButtonStyle.primary, custom_id="next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_same_voice(interaction):
            return
        await interaction.response.defer()
        await self._get_player().skip(self.bot)

    @discord.ui.button(label="👍Stop", style=discord.ButtonStyle.danger, custom_id="stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._ensure_same_voice(interaction):
            return
        await interaction.response.defer()
        await self._get_player().stop_and_disconnect(self.bot)

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.music_players: Dict[int, GuildPlayer] = {}

    def get_player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self.music_players:
            self.music_players[guild_id] = GuildPlayer(guild_id)
        return self.music_players[guild_id]

bot = MusicBot()

@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception:
        pass
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="play", description="Phát nhạc YouTube theo link hoặc từ khóa")
@app_commands.describe(query="Link YouTube hoặc từ khóa")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng trong server.", ephemeral=True)
        return

    player = bot.get_player(interaction.guild.id)
    player.text_channel_id = interaction.channel_id

    try:
        await interaction.response.defer()
        await player.ensure_voice(interaction)
        track = await player.resolve_track(query)
        await player.add_and_maybe_start(bot, track)
        await interaction.followup.send(f"Đã thêm vào hàng đợi: {track.title}", ephemeral=True)
    except Exception as e:
        try:
            await interaction.followup.send(f"Lỗi: {e}", ephemeral=True)
        except Exception:
            pass

@bot.tree.command(name="stop", description="Stop và rời kênh voice")
async def stop_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Lệnh này chỉ dùng trong server.", ephemeral=True)
        return
    player = bot.get_player(interaction.guild.id)
    player.text_channel_id = interaction.channel_id
    await interaction.response.defer(ephemeral=True)
    await player.stop_and_disconnect(bot)
    await interaction.followup.send("Đã stop và rời kênh.", ephemeral=True)

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Thiếu DISCORD_TOKEN trong .env")

bot.run(token)
