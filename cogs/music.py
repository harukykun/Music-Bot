import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import random
from collections import deque

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'android'],
        }
    },
}

FFMPEG_OPTIONS = {
    'before_options': "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -headers 'User-Agent: Mozilla/5.0'",
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

class Song:
    def __init__(self, data, requester, original_url):
        self.original_url = original_url
        self.title = data.get('title', 'Unknown')
        self.url = data.get('webpage_url', '')
        self.thumbnail = data.get('thumbnail', '')
        self.duration = data.get('duration', 0)
        self.requester = requester
        self.uploader = data.get('uploader', 'Unknown')

    @classmethod
    async def create(cls, url, requester, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        
        if 'entries' in data:
            data = data['entries'][0]
        
        return cls(data, requester, url)

    async def get_audio_source(self, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(self.original_url, download=False))
        if 'entries' in data:
            data = data['entries'][0]
        return data['url']

    def format_duration(self):
        if not self.duration:
            return "Unknown"
        minutes, seconds = divmod(int(self.duration), 60)
        return f"{minutes:02d}m {seconds:02d}s"

class MusicControlView(discord.ui.View):
    def __init__(self, music_cog, guild_id):
        super().__init__(timeout=None)
        self.music_cog = music_cog
        self.guild_id = guild_id

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild.voice_client and guild.voice_client.is_playing():
            guild.voice_client.pause()
            button.label = "Resume"
            button.emoji = "▶️"
        elif guild.voice_client and guild.voice_client.is_paused():
            guild.voice_client.resume()
            button.label = "Pause"
            button.emoji = "⏸️"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild.voice_client and (guild.voice_client.is_playing() or guild.voice_client.is_paused()):
            guild.voice_client.stop()
            await interaction.response.send_message("⏭️ Đã bỏ qua bài hát!", ephemeral=True)
        else:
            await interaction.response.send_message("Không có bài hát nào đang phát!", ephemeral=True)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary, emoji="🔀")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = self.music_cog.queues.get(self.guild_id)
        if queue and len(queue) > 1:
            songs = list(queue)
            random.shuffle(songs)
            self.music_cog.queues[self.guild_id] = deque(songs)
            await interaction.response.send_message("🔀 Đã xáo trộn hàng chờ!", ephemeral=True)
        else:
            await interaction.response.send_message("Hàng chờ trống hoặc chỉ có 1 bài!", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild.voice_client:
            self.music_cog.queues[self.guild_id] = deque()
            self.music_cog.current_songs.pop(self.guild_id, None)
            guild.voice_client.stop()
            await guild.voice_client.disconnect()
            await interaction.response.send_message("⏹️ Đã dừng và ngắt kết nối!", ephemeral=True)
        else:
            await interaction.response.send_message("Bot không ở trong voice channel!", ephemeral=True)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}
        self.current_songs = {}
        self.now_playing_messages = {}

    def get_queue(self, guild_id):
        if guild_id not in self.queues:
            self.queues[guild_id] = deque()
        return self.queues[guild_id]

    async def create_now_playing_embed(self, song):
        embed = discord.Embed(color=0x2b2d31)
        embed.set_author(name="Now Playing", icon_url="https://cdn.discordapp.com/emojis/741605543046807626.gif")
        embed.add_field(
            name="",
            value=f"• **{song.title}** - {song.uploader}\n• Duration: **{song.format_duration()}** - (@{song.requester.display_name})",
            inline=False
        )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        return embed

    async def play_next(self, guild):
        queue = self.get_queue(guild.id)
        
        if not queue:
            self.current_songs.pop(guild.id, None)
            return
        
        song = queue.popleft()
        self.current_songs[guild.id] = song

        try:
            audio_url = await song.get_audio_source(self.bot.loop)
            print(f"Playing: {song.title}")
            print(f"Audio URL: {audio_url[:100]}...")
            ffmpeg_source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(ffmpeg_source, volume=1.0)
        except Exception as e:
            print(f"Lỗi lấy audio: {e}")
            import traceback
            traceback.print_exc()
            asyncio.run_coroutine_threadsafe(self.play_next(guild), self.bot.loop)
            return
        
        def after_playing(error):
            if error:
                print(f"Lỗi phát nhạc: {error}")
            asyncio.run_coroutine_threadsafe(self.play_next(guild), self.bot.loop)

        if not guild.voice_client:
            print("ERROR: No voice client!")
            return
        
        print(f"Voice client connected: {guild.voice_client.is_connected()}")
        print(f"Starting playback...")
        guild.voice_client.play(source, after=after_playing)
        print(f"Is playing: {guild.voice_client.is_playing()}")

        channel = self.now_playing_messages.get(guild.id)
        if channel:
            embed = await self.create_now_playing_embed(song)
            view = MusicControlView(self, guild.id)
            try:
                await channel.send(embed=embed, view=view)
            except:
                pass

    @app_commands.command(name="play", description="Phát nhạc từ YouTube URL")
    @app_commands.describe(url="YouTube URL của bài hát")
    async def play(self, interaction: discord.Interaction, url: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("Bạn cần ở trong voice channel!", ephemeral=True)

        await interaction.response.defer()

        voice_channel = interaction.user.voice.channel

        if not interaction.guild.voice_client:
            await voice_channel.connect()
        elif interaction.guild.voice_client.channel != voice_channel:
            await interaction.guild.voice_client.move_to(voice_channel)

        try:
            song = await Song.create(url, interaction.user, self.bot.loop)
        except Exception as e:
            return await interaction.followup.send(f"Lỗi: Không thể lấy thông tin bài hát!\n{e}")

        queue = self.get_queue(interaction.guild.id)
        queue.append(song)

        self.now_playing_messages[interaction.guild.id] = interaction.channel

        if not interaction.guild.voice_client.is_playing() and not interaction.guild.voice_client.is_paused():
            await self.play_next(interaction.guild)
            await interaction.followup.send(f"🎵 Đang phát: **{song.title}**")
        else:
            await interaction.followup.send(f"📋 Đã thêm vào hàng chờ: **{song.title}** (Vị trí: #{len(queue)})")

    @app_commands.command(name="queue", description="Xem danh sách hàng chờ")
    async def queue_cmd(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild.id)
        current = self.current_songs.get(interaction.guild.id)

        if not current and not queue:
            return await interaction.response.send_message("Hàng chờ trống!", ephemeral=True)

        embed = discord.Embed(title="🎵 Danh sách phát", color=0x2b2d31)

        if current:
            embed.add_field(
                name="Đang phát",
                value=f"**{current.title}** - {current.format_duration()}",
                inline=False
            )

        if queue:
            queue_list = ""
            for i, song in enumerate(list(queue)[:10], 1):
                queue_list += f"`{i}.` {song.title} - {song.format_duration()}\n"
            
            if len(queue) > 10:
                queue_list += f"\n*...và {len(queue) - 10} bài khác*"
            
            embed.add_field(name="Hàng chờ", value=queue_list, inline=False)

        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Music(bot))
