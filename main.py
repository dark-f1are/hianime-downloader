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

@dataclass
class DownloadConfig:
    master_url: str
    output_dir: str
    selected_resolution: Optional[str] = None
    selected_language: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    subtitle_url: Optional[str] = None

class DownloadProgress:
    def __init__(self):
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
        )
        self.download_task = None

    def __enter__(self):
        return self.progress.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.progress.__exit__(exc_type, exc_val, exc_tb)

class HLSDownloader:
    def __init__(self, master_playlist_url: str, output_dir: str = "downloads"):
        """Initialize the HLS downloader with the master playlist URL."""
        self.master_playlist_url = master_playlist_url
        self.output_dir = Path(output_dir)
        self.base_url = master_playlist_url.rsplit('/', 1)[0] + '/'
        self.video_tracks: Dict[str, VideoTrack] = {}
        self.audio_tracks: Dict[str, AudioTrack] = {}
        self.session = requests.Session()
        self.progress = DownloadProgress()

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
        """Download a partial stream asynchronously with progress bar."""
        output_path = self.output_dir / output_name
        playlist = m3u8.load(playlist_url)
        total_time = 0
        initial_total_time = 0
        segments_to_download = []

        # Identify segments to download
        for segment in playlist.segments:
            if total_time + segment.duration < start_time:
                total_time += segment.duration
                continue
            if initial_total_time == 0:
                initial_total_time = total_time
            if total_time > end_time:
                break
            segment_url = urljoin(playlist_url.rsplit('/', 1)[0] + '/', segment.uri)
            segments_to_download.append(segment_url)
            total_time += segment.duration

        # Download segments with progress bar
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

        # Write segments to file
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

            if start <= start_time <= end or start <= end_time <= end:
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

# Add new UI helper functions
async def display_intro():
    """Display an attractive intro banner."""
    intro_text = Text()
    intro_text.append("🎬 ", style="bold yellow")
    intro_text.append("HLS Video Downloader", style="bold blue")
    intro_text.append(" 🎬", style="bold yellow")
    
    panel = Panel(
        intro_text,
        subtitle="Made with ❤️  using Python",
        border_style="cyan"
    )
    console.print(panel)
    console.print()

def create_tracks_table(tracks: dict) -> Table:
    """Create a table to display available tracks."""
    table = Table(title="Available Tracks", show_header=True, header_style="bold magenta")
    table.add_column("Type", style="dim", width=12)
    table.add_column("Resolution/Language", style="dim", width=20)
    table.add_column("Bandwidth/Name", style="dim", width=20)

    for res, track in tracks["video_tracks"].items():
        table.add_row("Video", res, str(track["bandwidth"]))

    for lang, track in tracks["audio_tracks"].items():
        table.add_row("Audio", lang, track["name"])

    return table

def get_complete_config() -> DownloadConfig:
    """Get all user configuration upfront before any async operations."""
    config = DownloadConfig(
        master_url=questionary.text(
            "Enter master playlist URL:",
            default="https://example.com/master.m3u8"
        ).ask(),
        output_dir=questionary.text(
            "Enter output directory:",
            default="downloads"
        ).ask()
    )
    return config

async def get_user_selections(tracks: dict) -> Tuple[str, Optional[str], float, float, Optional[str]]:
    """Get user selections for tracks and timing in an async-safe way."""
    resolution = list(tracks["video_tracks"].keys())[0]  # Default to first resolution
    language = None
    start_time = 0.0
    end_time = 60.0
    subtitle_url = None

    loop = asyncio.get_event_loop()
    # Run questionary prompts in executor to avoid event loop issues
    resolution = await loop.run_in_executor(None, lambda: questionary.select(
        "Select video resolution:",
        choices=list(tracks["video_tracks"].keys())
    ).ask())

    if tracks["audio_tracks"]:
        lang = await loop.run_in_executor(None, lambda: questionary.select(
            "Select audio language:",
            choices=["None"] + list(tracks["audio_tracks"].keys())
        ).ask())
        language = None if lang == "None" else lang

    start_time = await loop.run_in_executor(None, lambda: float(
        questionary.text("Enter start time in seconds:").ask()
    ))
    
    end_time = await loop.run_in_executor(None, lambda: float(
        questionary.text("Enter end time in seconds:").ask()
    ))
    
    subtitle_url = await loop.run_in_executor(None, lambda: 
        questionary.text("Enter subtitle URL (press Enter to skip):",
        default="https://s.megastatics.com/subtitle/73fd2e74257659a8ef9b9cdd004623a5/eng-2.vtt"
        ).ask() or None
    )

    proceed = await loop.run_in_executor(None, lambda:
        questionary.confirm("Proceed with download?").ask()
    )

    if not proceed:
        raise RuntimeError("Download cancelled by user")

    return resolution, language, start_time, end_time, subtitle_url

def main():
    """Synchronous entry point that handles async operations."""
    try:
        # Display intro
        console.print(Panel(
            Text.assemble(("🎬 ", "bold yellow"), ("HLS Video Downloader", "bold blue"), (" 🎬", "bold yellow")),
            subtitle="Made with ❤️  using Python",
            border_style="cyan"
        ))

        # Get initial configuration synchronously
        master_url = questionary.text(
            "Enter master playlist URL:",
            default="https://ea.netmagcdn.com:2228/hls-playback/71f87b4028d27b3ba749bd2029f3248245618a740ca81a9a9863f257784436f85c939482f4d306945639b935dc612f2301545789ad4dc7de51a80e913bb3a3742a8e5af4060cdb6a0add8dfb604387dffe89376fd5be53a44f2fc02d3049492cbd74c71e489e999c86a63e638984e9e001a253396824dd052695b8d37d1f7ef7e3d8042c93999ed50b12af67471fe106/master.m3u8"
        ).ask()
        
        output_dir = questionary.text(
            "Enter output directory:",
            default="/home/tawsif/Videos"
        ).ask()

        config = DownloadConfig(master_url=master_url, output_dir=output_dir)

        async def async_operations(config: DownloadConfig):
            try:
                with console.status("[bold green]Initializing downloader..."):
                    downloader = HLSDownloader(config.master_url, config.output_dir)
                    await downloader.initialize()

                # Show available tracks
                tracks = downloader.get_available_tracks()
                console.print("\n[bold cyan]Available Tracks:[/bold cyan]")
                console.print(create_tracks_table(tracks))

                # Get user selections
                resolution, language, start_time, end_time, subtitle_url = await get_user_selections(tracks)
                
                # Update config
                config.selected_resolution = resolution
                config.selected_language = language
                config.start_time = start_time
                config.end_time = end_time
                config.subtitle_url = subtitle_url

                # Download video stream
                console.print("\n[bold green]Downloading video stream...[/bold green]")
                video_path, video_initial_time = await downloader.download_partial_stream(
                    downloader.video_tracks[config.selected_resolution].url,
                    config.start_time,
                    config.end_time,
                    "partial_video.ts"
                )

                # Download audio stream if selected
                audio_path = None
                if config.selected_language:
                    console.print("[bold green]Downloading audio stream...[/bold green]")
                    audio_path, _ = await downloader.download_partial_stream(
                        downloader.audio_tracks[config.selected_language].url,
                        config.start_time,
                        config.end_time,
                        "partial_audio.ts"
                    )

                # Process subtitles if URL provided
                subtitle_path = None
                if config.subtitle_url:
                    console.print("[bold green]Processing subtitles...[/bold green]")
                    subtitle_path = await downloader.process_subtitles(
                        config.subtitle_url,
                        video_initial_time,
                        config.start_time,
                        config.end_time
                    )

                # Merge streams
                console.print("[bold green]Merging streams...[/bold green]")
                output_path = str(Path(config.output_dir) / "output_partial.mkv")
                await downloader.merge_streams(video_path, audio_path, output_path)

                # Cleanup
                temp_files = [f for f in [video_path, audio_path] if f]
                downloader.cleanup(temp_files)

                console.print(f"\n[bold green]Download complete! Final file saved as: {output_path}[/bold green]")
                if subtitle_path:
                    console.print(f"[bold green]Subtitles saved as: {subtitle_path}[/bold green]")

            except Exception as e:
                console.print(f"[bold red]Error: {str(e)}[/bold red]")
                raise

        # Run async operations
        asyncio.run(async_operations(config))

    except Exception as e:
        console.print(f"[bold red]Fatal error: {str(e)}[/bold red]")
        raise

if __name__ == "__main__":
    main()