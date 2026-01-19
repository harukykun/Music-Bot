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
    "cookiefile": None,
    "extract_flat": False,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
if not os.path.exists(FFMPEG_PATH):
    FFMPEG_PATH = "ffmpeg"

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, requester, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "Unknown")
        self.url = data.get("url")
        self.web_url = data.get("webpage_url")
        self.duration = data.get("duration", 0)
        self.thumbnail = data.get("thumbnail")
        self.requester = requester

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, requester=None):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        except Exception as e:
            raise Exception(f"Không thể tải bài hát: {str(e)}")
        
        if data is None:
            raise Exception("Không tìm thấy bài hát")
            
        if "entries" in data:
            data = data["entries"][0]
        
        if data is None:
            raise Exception("Không tìm thấy bài hát")
            
        filename = data.get("url") if stream else ytdl.prepare_filename(data)
        
        if not filename:
            raise Exception("Không thể lấy URL bài hát")
            
        source = discord.FFmpegPCMAudio(filename, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
        return {"source": cls(source, data=data, requester=requester), "data": data}

class MusicPlayer:
    def __init__(self, bot, interaction):
        self.bot = bot
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.playlist = []
        self.index = -1
        self.current = None
        self.volume = 0.5
        self.is_playing = False

    async def play_current(self):
        if self.index < 0 or self.index >= len(self.playlist):
            self.is_playing = False
            return
            
        track_data = self.playlist[self.index]
        try:
            source_dict = await YTDLSource.from_url(
                track_data["url"], 
                loop=self.bot.loop, 
                stream=True, 
                requester=track_data["requester"]
            )
            self.current = source_dict["source"]
            self.current.volume = self.volume
            
            if self.guild.voice_client is None:
                self.is_playing = False
                return
                
            if self.guild.voice_client.is_playing() or self.guild.voice_client.is_paused():
                self.guild.voice_client.stop()
            
            self.is_playing = True
            self.guild.voice_client.play(self.current, after=self.play_next_event)
            await self.channel.send(embed=self.build_embed(), view=ControlView(self))
        except Exception as e:
            await self.channel.send(f"❌ Lỗi phát nhạc: {str(e)}")
            self.play_next_event(None)

    def play_next_event(self, error):
        if error:
            print(f"Player error: {error}")
        
        if self.index + 1 < len(self.playlist):
            self.index += 1
            coro = self.play_current()
            fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"Error in play_next: {e}")
        else:
            self.is_playing = False

    def build_embed(self):
        if self.current is None:
            return discord.Embed(title="Music Player", description="Không có bài hát", color=0x5B2C83)
            
        duration = self.current.duration or 0
        dur = f"{duration // 60}:{duration % 60:02d}"
        e = discord.Embed(title="🎵 Music Player", color=0x5B2C83)
        e.description = f"**[{self.current.title}]({self.current.web_url})**"
        e.add_field(name="⏱ Thời lượng", value=dur, inline=True)
        e.add_field(name="🔊 Âm lượng", value=f"{int(self.volume * 100)}%", inline=True)
        e.add_field(name="👤 Người gọi bài", value=self.current.requester.display_name, inline=True)
        e.add_field(name="📋 Vị trí", value=f"{self.index + 1}/{len(self.playlist)}", inline=True)
        if self.current.thumbnail:
            e.set_image(url=self.current.thumbnail)
        return e

class ControlView(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="⏮ Back", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.player.index > 0:
            self.player.index -= 2
            if interaction.guild.voice_client:
                interaction.guild.voice_client.stop()

    @discord.ui.button(label="⏯ Pause", style=discord.ButtonStyle.success)
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_paused():
                vc.resume()
            else:
                vc.pause()

    @discord.ui.button(label="⏭ Next", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()

    @discord.ui.button(label="🔊 +", style=discord.ButtonStyle.primary)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.volume = min(self.player.volume + 0.1, 2.0)
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = self.player.volume
        await interaction.response.edit_message(embed=self.player.build_embed())

    @discord.ui.button(label="🔉 -", style=discord.ButtonStyle.primary)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.volume = max(self.player.volume - 0.1, 0.0)
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = self.player.volume
        await interaction.response.edit_message(embed=self.player.build_embed())

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.danger)
    async def stop_music(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if interaction.guild.voice_client:
            self.player.playlist.clear()
            self.player.index = -1
            self.player.is_playing = False
            interaction.guild.voice_client.stop()
            await interaction.guild.voice_client.disconnect()
            if interaction.guild.id in players:
                del players[interaction.guild.id]

players = {}

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("Bot đã sẵn sàng!")

bot = MusicBot()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="play", description="Phát nhạc từ YouTube")
@app_commands.describe(search="Tên bài hát hoặc URL YouTube")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ Vào Voice Channel trước nhé!", ephemeral=True)

    await interaction.response.defer()
    
    try:
        if not interaction.guild.voice_client:
            await interaction.user.voice.channel.connect()

        if interaction.guild.id not in players:
            players[interaction.guild.id] = MusicPlayer(bot, interaction)

        player = players[interaction.guild.id]
        player.channel = interaction.channel
        
        data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        
        if data is None:
            return await interaction.followup.send("❌ Không tìm thấy bài hát!")
            
        if "entries" in data:
            data = data["entries"][0]
        
        if data is None:
            return await interaction.followup.send("❌ Không tìm thấy bài hát!")
        
        url = data.get("webpage_url") or data.get("url")
        title = data.get("title", "Unknown")
        
        player.playlist.append({
            "url": url, 
            "title": title, 
            "requester": interaction.user
        })
        
        await interaction.followup.send(f"✅ Đã thêm **{title}** vào playlist! (Vị trí: {len(player.playlist)})")
        
        if not player.is_playing:
            player.index = len(player.playlist) - 1
            await player.play_current()
            
    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi: {str(e)}")

@bot.tree.command(name="skip", description="Bỏ qua bài hát hiện tại")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("⏭ Đã bỏ qua bài hát!")
    else:
        await interaction.response.send_message("❌ Không có bài hát đang phát!", ephemeral=True)

@bot.tree.command(name="stop", description="Dừng phát nhạc và rời khỏi voice channel")
async def stop(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        if interaction.guild.id in players:
            players[interaction.guild.id].playlist.clear()
            del players[interaction.guild.id]
        interaction.guild.voice_client.stop()
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("⏹ Đã dừng phát nhạc!")
    else:
        await interaction.response.send_message("❌ Bot không ở trong voice channel!", ephemeral=True)

@bot.tree.command(name="queue", description="Xem danh sách phát")
async def queue(interaction: discord.Interaction):
    if interaction.guild.id not in players or not players[interaction.guild.id].playlist:
        return await interaction.response.send_message("❌ Playlist trống!", ephemeral=True)
    
    player = players[interaction.guild.id]
    
    e = discord.Embed(title="📋 Playlist", color=0x5B2C83)
    description = ""
    for i, track in enumerate(player.playlist):
        marker = "▶️" if i == player.index else f"{i + 1}."
        description += f"{marker} **{track['title']}** - {track['requester'].display_name}\n"
    
    e.description = description if description else "Không có bài hát"
    await interaction.response.send_message(embed=e)

@bot.tree.command(name="pause", description="Tạm dừng/Tiếp tục phát nhạc")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("❌ Bot không ở trong voice channel!", ephemeral=True)
    
    if vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Tiếp tục phát nhạc!")
    elif vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸ Đã tạm dừng!")
    else:
        await interaction.response.send_message("❌ Không có bài hát đang phát!", ephemeral=True)

token = os.getenv("DISCORD_TOKEN")
bot.run(token)