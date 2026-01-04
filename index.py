import os
import asyncio
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "extractaudio": True,
    "audioformat": "mp3",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "referer": "https://www.google.com/",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, requester, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("url")
        self.web_url = data.get("webpage_url")
        self.duration = data.get("duration")
        self.thumbnail = data.get("thumbnail")
        self.requester = requester

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, requester=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if "entries" in data:
            data = data["entries"][0]
        filename = data["url"] if stream else ytdl.prepare_filename(data)
        return {"source": cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data, requester=requester), "data": data}

class MusicPlayer:
    def __init__(self, bot, interaction):
        self.bot = bot
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.playlist = []
        self.index = -1
        self.current = None
        self.volume = 0.5

    async def play_current(self):
        track_data = self.playlist[self.index]
        try:
            source_dict = await YTDLSource.from_url(track_data["url"], loop=self.bot.loop, stream=True, requester=track_data["requester"])
            self.current = source_dict["source"]
            self.current.volume = self.volume
            
            if self.guild.voice_client.is_playing() or self.guild.voice_client.is_paused():
                self.guild.voice_client.stop()
                
            self.guild.voice_client.play(self.current, after=self.play_next_event)
            await self.channel.send(embed=self.build_embed(), view=ControlView(self))
        except Exception as e:
            self.play_next_event(None)

    def play_next_event(self, error):
        if self.index + 1 < len(self.playlist):
            self.index += 1
            coro = self.play_current()
            fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            try:
                fut.result()
            except:
                pass

    def build_embed(self):
        dur = f"{self.current.duration // 60}:{self.current.duration % 60:02d}"
        e = discord.Embed(title="Music Player", color=0x5B2C83)
        e.description = f"**[{self.current.title}]({self.current.web_url})**"
        e.add_field(name="Thời lượng", value=dur, inline=True)
        e.add_field(name="Âm lượng", value=f"{int(self.volume * 100)}%", inline=True)
        e.add_field(name="Người gọi bài", value=self.current.requester.display_name, inline=True)
        e.add_field(name="Vị trí", value=f"{self.index + 1}/{len(self.playlist)}", inline=True)
        if self.current.thumbnail: e.set_image(url=self.current.thumbnail)
        return e

class ControlView(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="⏮ Back", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.player.index > 0:
            self.player.index -= 2
            interaction.guild.voice_client.stop()

    @discord.ui.button(label="⏯ Pause", style=discord.ButtonStyle.success)
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc.is_paused(): vc.resume()
        else: vc.pause()
        await interaction.response.defer()

    @discord.ui.button(label="⏭ Next", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        interaction.guild.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(label="🔊 +", style=discord.ButtonStyle.primary)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.volume = min(self.player.volume + 0.1, 2.0)
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = self.player.volume
        await interaction.response.edit_message(embed=self.player.build_embed())

    @discord.ui.button(label="🔉 -", style=discord.ButtonStyle.primary)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.volume = max(self.player.volume - 0.1, 0.0)
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = self.player.volume
        await interaction.response.edit_message(embed=self.player.build_embed())

players = {}

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = MusicBot()

@bot.tree.command(name="play", description="Phát nhạc trên Railway")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("Vào Voice Channel trước nhé")

    await interaction.response.defer()
    
    if not interaction.guild.voice_client:
        await interaction.user.voice.channel.connect()

    if interaction.guild.id not in players:
        players[interaction.guild.id] = MusicPlayer(bot, interaction)

    player = players[interaction.guild.id]
    data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False, process=False))
    if "entries" in data: data = data["entries"][0]
    
    player.playlist.append({"url": data.get("url") or data.get("webpage_url"), "title": data.get("title"), "requester": interaction.user})
    
    if not interaction.guild.voice_client.is_playing() and not interaction.guild.voice_client.is_paused():
        player.index += 1
        await player.play_current()
token = os.getenv("DISCORD_TOKEN")
bot.run(token)