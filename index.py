import os
import asyncio
import aiohttp
import re
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.private.coffee",
    "https://yt.drgnz.club",
    "https://invidious.protokolla.fi",
]

PIPED_INSTANCES = [
    "https://api.piped.private.coffee",
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.r4fo.com",
    "https://pipedapi.darkness.services",
]

COBALT_API = "https://api.cobalt.tools"

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
    "extract_flat": False,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -headers 'User-Agent: Mozilla/5.0'",
    "options": "-vn",
}

FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
if not os.path.exists(FFMPEG_PATH):
    FFMPEG_PATH = "ffmpeg"

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

def extract_video_id(url_or_search):
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_search)
        if match:
            return match.group(1)
    return None

async def get_audio_from_cobalt(video_id):
    async with aiohttp.ClientSession() as session:
        try:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            payload = {
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "downloadMode": "audio",
                "audioFormat": "best"
            }
            async with session.post(COBALT_API, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "tunnel" or data.get("status") == "redirect":
                        return {
                            "url": data.get("url"),
                            "title": "YouTube Audio",
                            "duration": 0,
                            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                            "webpage_url": f"https://www.youtube.com/watch?v={video_id}"
                        }
        except Exception as e:
            print(f"Cobalt error: {e}")
    return None

async def get_audio_from_piped(video_id):
    async with aiohttp.ClientSession() as session:
        for instance in PIPED_INSTANCES:
            try:
                print(f"Trying Piped: {instance}")
                async with session.get(f"{instance}/streams/{video_id}", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    print(f"Piped {instance} status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        audio_streams = data.get("audioStreams", [])
                        if audio_streams:
                            best_audio = max(audio_streams, key=lambda x: x.get("bitrate", 0))
                            return {
                                "url": best_audio.get("url"),
                                "title": data.get("title", "Unknown"),
                                "duration": data.get("duration", 0),
                                "thumbnail": data.get("thumbnailUrl"),
                                "webpage_url": f"https://www.youtube.com/watch?v={video_id}"
                            }
            except Exception as e:
                print(f"Piped {instance} error: {e}")
                continue
    return None

async def get_audio_from_invidious(video_id):
    async with aiohttp.ClientSession() as session:
        for instance in INVIDIOUS_INSTANCES:
            try:
                print(f"Trying Invidious: {instance}")
                async with session.get(f"{instance}/api/v1/videos/{video_id}", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    print(f"Invidious {instance} status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        adaptive = data.get("adaptiveFormats", [])
                        audio_formats = [f for f in adaptive if f.get("type", "").startswith("audio/")]
                        if audio_formats:
                            best_audio = max(audio_formats, key=lambda x: x.get("bitrate", 0))
                            return {
                                "url": best_audio.get("url"),
                                "title": data.get("title", "Unknown"),
                                "duration": data.get("lengthSeconds", 0),
                                "thumbnail": data.get("videoThumbnails", [{}])[0].get("url"),
                                "webpage_url": f"https://www.youtube.com/watch?v={video_id}"
                            }
            except Exception as e:
                print(f"Invidious {instance} error: {e}")
                continue
    return None

async def search_youtube_piped(query):
    async with aiohttp.ClientSession() as session:
        for instance in PIPED_INSTANCES:
            try:
                async with session.get(f"{instance}/search", params={"q": query, "filter": "videos"}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("items", [])
                        if items:
                            video = items[0]
                            video_id = video.get("url", "").replace("/watch?v=", "")
                            return video_id
            except Exception as e:
                print(f"Search {instance} error: {e}")
                continue
    return None

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
        self.current_data = None
        self.volume = 0.5
        self.is_playing = False

    async def play_current(self):
        if self.index < 0 or self.index >= len(self.playlist):
            self.is_playing = False
            return
            
        track_data = self.playlist[self.index]
        try:
            video_id = track_data.get("video_id")
            audio_url = track_data.get("url")
            
            if video_id and not audio_url:
                data = await get_audio_from_piped(video_id)
                if not data:
                    data = await get_audio_from_invidious(video_id)
                if not data:
                    data = await get_audio_from_cobalt(video_id)
                if data:
                    audio_url = data.get("url")
                    track_data["url"] = audio_url
            
            if not audio_url:
                await self.channel.send(f"❌ Không thể lấy audio cho bài hát này!")
                self.play_next_event(None)
                return
            
            source = discord.FFmpegPCMAudio(audio_url, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
            self.current = discord.PCMVolumeTransformer(source, volume=self.volume)
            self.current_data = track_data
            
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
        if self.current_data is None:
            return discord.Embed(title="Music Player", description="Không có bài hát", color=0x5B2C83)
            
        duration = self.current_data.get("duration", 0) or 0
        dur = f"{duration // 60}:{duration % 60:02d}"
        e = discord.Embed(title="🎵 Music Player", color=0x5B2C83)
        e.description = f"**[{self.current_data.get('title', 'Unknown')}]({self.current_data.get('webpage_url', '')})**"
        e.add_field(name="⏱ Thời lượng", value=dur, inline=True)
        e.add_field(name="🔊 Âm lượng", value=f"{int(self.volume * 100)}%", inline=True)
        e.add_field(name="👤 Người gọi bài", value=self.current_data.get("requester").display_name, inline=True)
        e.add_field(name="📋 Vị trí", value=f"{self.index + 1}/{len(self.playlist)}", inline=True)
        if self.current_data.get("thumbnail"):
            e.set_image(url=self.current_data.get("thumbnail"))
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
        
        video_id = extract_video_id(search)
        
        if not video_id:
            video_id = await search_youtube_piped(search)
        
        if not video_id:
            return await interaction.followup.send("❌ Không tìm thấy bài hát!")
        
        print(f"Found video_id: {video_id}")
        
        data = await get_audio_from_piped(video_id)
        
        if not data:
            print("Piped failed, trying Invidious...")
            data = await get_audio_from_invidious(video_id)
        
        if not data:
            print("Invidious failed, trying Cobalt...")
            data = await get_audio_from_cobalt(video_id)
        
        if not data or not data.get("url"):
            return await interaction.followup.send("❌ Không thể lấy audio từ video này!")
        
        title = data.get("title", "Unknown")
        
        player.playlist.append({
            "url": data.get("url"),
            "title": title,
            "duration": data.get("duration", 0),
            "thumbnail": data.get("thumbnail"),
            "webpage_url": data.get("webpage_url"),
            "requester": interaction.user,
            "video_id": video_id
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