import m3u8
import requests
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
from urllib.parse import urljoin
from webvtt import WebVTT, Caption
from pathlib import Path
import asyncio
import aiohttp
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich.table import Table
import questionary
from rich.text import Text
from functools import partial
from os.path import expanduser

console = Console()

@dataclass
class VideoTrack:
    resolution: str
    bandwidth: int
    url: str

@dataclass
class AudioTrack:
    language: str
    name: str
    url: str

class HLSDownloader:
    def __init__(self, master_playlist_url: str, output_dir: str = "downloads"):
        self.master_playlist_url = master_playlist_url
        self.output_dir = Path(expanduser(output_dir)).resolve()
        self.base_url = master_playlist_url.rsplit('/', 1)[0] + '/'
        self.video_tracks: Dict[str, VideoTrack] = {}
        self.audio_tracks: Dict[str, AudioTrack] = {}
        self.session = requests.Session()
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
        )

    async def initialize(self):
        """Initialize by parsing the master playlist."""
        try:
            self.output_dir.mkdir(exist_ok=True)
            master_playlist = m3u8.load(self.master_playlist_url)
            self._parse_master_playlist(master_playlist)
        except Exception as e:
            raise

    def _parse_master_playlist(self, master_playlist: m3u8.M3U8):
        """Parse the master playlist to extract video and audio tracks."""
        # Parse audio tracks
        if master_playlist.media:
            for media in master_playlist.media:
                if media.type == "AUDIO":
                    self.audio_tracks[media.language] = AudioTrack(
                        language=media.language,
                        name=media.name,
                        url=urljoin(self.base_url, media.uri)
                    )

        # Parse video tracks
        for playlist in master_playlist.playlists:
            resolution = playlist.stream_info.resolution
            res_str = f"{resolution[0]}x{resolution[1]}"
            self.video_tracks[res_str] = VideoTrack(
                resolution=res_str,
                bandwidth=playlist.stream_info.bandwidth,
                url=urljoin(self.base_url, playlist.uri)
            )

    def get_available_tracks(self) -> dict:
        """Return information about available tracks."""
        return {
            "video_tracks": {k: v.__dict__ for k, v in self.video_tracks.items()},
            "audio_tracks": {k: v.__dict__ for k, v in self.audio_tracks.items()}
        }

    async def _download_segment(self, session: aiohttp.ClientSession, segment_url: str) -> bytes:
        """Download a single segment using aiohttp."""
        async with session.get(segment_url) as response:
            response.raise_for_status()
            return await response.read()

    async def download_partial_stream(
        self,
        playlist_url: str,
        start_time: float,
        end_time: float,
        output_name: str
    ) -> Tuple[str, float]:
        output_path = self.output_dir / output_name
        playlist = m3u8.load(playlist_url)
        total_time = initial_total_time = 0
        isFirstSegment = True
        segments_to_download = []

        # Calculate segments to download
        for segment in playlist.segments:
            if total_time + segment.duration < start_time:
                total_time += segment.duration
                isFirstSegment = False
                continue
            if initial_total_time == 0 and isFirstSegment:
                initial_total_time = 0
            elif initial_total_time == 0 and not isFirstSegment:
                initial_total_time = total_time
            if total_time > end_time:
                break
            segments_to_download.append(
                urljoin(playlist_url.rsplit('/', 1)[0] + '/', segment.uri)
            )
            total_time += segment.duration

        # Download segments
        async with aiohttp.ClientSession() as session:
            with self.progress as progress:
                task = progress.add_task(
                    f"[cyan]Downloading {output_name}...",
                    total=len(segments_to_download)
                )
                
                segment_contents = []
                for url in segments_to_download:
                    content = await self._download_segment(session, url)
                    segment_contents.append(content)
                    progress.update(task, advance=1)

        # Write to file
        with open(output_path, 'wb') as f:
            for content in segment_contents:
                f.write(content)

        return str(output_path), initial_total_time

    async def process_subtitles(
        self,
        subtitle_url: str,
        initial_time: float,
        start_time: float,
        end_time: float
    ) -> str:
        """Download and process subtitles."""
        subtitle_path = self.output_dir / "subtitle.vtt"
        adjusted_subtitle_path = self.output_dir / "adjusted_subtitle.vtt"

        async with aiohttp.ClientSession() as session:
            async with session.get(subtitle_url) as response:
                subtitle_content = await response.text()
                with open(subtitle_path, 'w', encoding='utf-8') as f:
                    f.write(subtitle_content)

        self._adjust_subtitle_timing(
            subtitle_path,
            adjusted_subtitle_path,
            initial_time,
            start_time,
            end_time
        )

        return str(adjusted_subtitle_path)

    def _adjust_subtitle_timing(
        self,
        input_file: Path,
        output_file: Path,
        initial_time: float,
        start: float,
        end: float
    ):
        """Adjust subtitle timing based on clip start and end times."""
        vtt = WebVTT().read(str(input_file))
        adjusted_captions = []

        for caption in vtt:
            start_time = self._parse_timestamp(caption.start)
            end_time = self._parse_timestamp(caption.end)

            adjusted_start = start_time - initial_time
            adjusted_end = end_time - initial_time

            if initial_time <= start_time <= end or initial_time <= end_time <= end:
                adjusted_start = max(0, adjusted_start)
                adjusted_captions.append(Caption(
                    start=self._format_timestamp(adjusted_start),
                    end=self._format_timestamp(adjusted_end),
                    text=caption.text
                ))

        new_vtt = WebVTT()
        new_vtt.captions = adjusted_captions
        new_vtt.save(str(output_file))

    @staticmethod
    def _parse_timestamp(timestamp: str) -> float:
        """Convert VTT timestamp to seconds."""
        h, m, s = timestamp.split(':')
        s, ms = s.split('.')
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Convert seconds to VTT timestamp format."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{int(seconds):02d}.{int((seconds % 1) * 1000):03d}"

    async def merge_streams(
        self,
        video_path: str,
        audio_path: Optional[str],
        output_path: str
    ):
        """Merge video and audio streams using FFmpeg."""
        try:
            cmd = ['ffmpeg', '-i', video_path]
            if audio_path:
                cmd.extend(['-i', audio_path])
            cmd.extend(['-c', 'copy', output_path, '-y'])
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg error: {stderr.decode()}")
            
        except Exception as e:
            raise

    def cleanup(self, files: List[str]):
        """Clean up temporary files."""
        for file in files:
            if file and os.path.exists(file):
                os.remove(file)

async def async_prompt(question_func, *args, **kwargs):
    """Wrapper to make questionary prompts async-compatible"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: question_func(*args, **kwargs))

async def get_user_input() -> Tuple[str, str]:
    """Get all user input in one place"""
    console.print(Panel(
        Text.assemble(("🎬 ", "bold yellow"), ("HLS Video Downloader", "bold blue"), (" 🎬", "bold yellow")),
        subtitle="Made with ❤️  using Python",
        border_style="cyan"
    ))

    master_url = await async_prompt(
        questionary.text(
            "Enter master playlist URL:").ask
    )
    
    output_dir = await async_prompt(
        questionary.text(
            "Enter output directory:",
            default=str(Path.home() / "Videos")  # Use absolute path as default
        ).ask
    )

    # Expand user path and resolve to absolute path
    output_dir = str(Path(expanduser(output_dir)).resolve())
    return master_url, output_dir

async def get_user_selections(tracks: dict) -> Tuple[str, Optional[str], float, float, Optional[str]]:
    """Get user selections for video/audio tracks and timing"""
    resolution = await async_prompt(
        questionary.select(
            "Select video resolution:",
            choices=list(tracks["video_tracks"].keys())
        ).ask
    )

    language = None
    if tracks["audio_tracks"]:
        lang = await async_prompt(
            questionary.select(
                "Select audio language:",
                choices=["None"] + list(tracks["audio_tracks"].keys())
            ).ask
        )
        language = None if lang == "None" else lang

    start_time = float(await async_prompt(
        questionary.text("Enter start time in seconds:").ask
    ))
    
    end_time = float(await async_prompt(
        questionary.text("Enter end time in seconds:").ask
    ))
    
    subtitle_url = await async_prompt(
        questionary.text("Enter subtitle URL (optional):").ask
    )

    proceed = await async_prompt(
        questionary.confirm("Proceed with download?").ask
    )

    if not proceed:
        raise RuntimeError("Download cancelled by user")

    return resolution, language, start_time, end_time, subtitle_url

def create_tracks_table(tracks: dict) -> Table:
    """Create a table to display available tracks."""
    table = Table(title="Available Tracks")

    table.add_column("Type", justify="center", style="cyan", no_wrap=True)
    table.add_column("Resolution/Language", justify="center", style="magenta")
    table.add_column("Bandwidth/Name", justify="center", style="green")

    for res, track in tracks["video_tracks"].items():
        table.add_row("Video", res, str(track["bandwidth"]))

    for lang, track in tracks["audio_tracks"].items():
        table.add_row("Audio", lang, track["name"])

    return table

async def main():
    try:
        # Get user input
        master_url, output_dir = await get_user_input()
        
        # Initialize downloader
        with console.status("[bold green]Initializing downloader..."):
            downloader = HLSDownloader(master_url, output_dir)
            await downloader.initialize()

        # Display tracks and get selections
        tracks = downloader.get_available_tracks()
        console.print("\n[bold cyan]Available Tracks:[/bold cyan]")
        console.print(create_tracks_table(tracks))

        # Get user selections
        resolution, language, start_time, end_time, subtitle_url = await get_user_selections(tracks)

        # Download and process streams
        video_path, video_initial_time = await downloader.download_partial_stream(
            downloader.video_tracks[resolution].url,
            start_time, end_time, "partial_video.ts"
        )

        audio_path = None
        if language:
            audio_path, _ = await downloader.download_partial_stream(
                downloader.audio_tracks[language].url,
                start_time, end_time, "partial_audio.ts"
            )

        subtitle_path = None
        if subtitle_url:
            subtitle_path = await downloader.process_subtitles(
                subtitle_url, video_initial_time, start_time, end_time
            )

        # Merge and cleanup
        output_path = str(Path(output_dir) / "output_partial.mkv")
        await downloader.merge_streams(video_path, audio_path, output_path)
        downloader.cleanup([video_path, audio_path])

        console.print(f"\n[bold green]Download complete! File saved as: {output_path}")
        if subtitle_path:
            console.print(f"[bold green]Subtitles saved as: {subtitle_path}")

    except Exception as e:
        console.print(f"[bold red]Error: {str(e)}[/bold red]")
        raise

if __name__ == "__main__":
    asyncio.run(main())