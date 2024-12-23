import m3u8
import requests
import os
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
from urllib.parse import urljoin
from webvtt import WebVTT, Caption
from datetime import timedelta
from pathlib import Path
import subprocess
import json
from concurrent.futures import ThreadPoolExecutor
import asyncio
import aiohttp

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
        """Initialize the HLS downloader with the master playlist URL."""
        self.master_playlist_url = master_playlist_url
        self.output_dir = Path(output_dir)
        self.base_url = master_playlist_url.rsplit('/', 1)[0] + '/'
        self.video_tracks: Dict[str, VideoTrack] = {}
        self.audio_tracks: Dict[str, AudioTrack] = {}
        self.setup_logging()
        self.session = requests.Session()

    def setup_logging(self):
        """Configure logging for the application."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.output_dir / 'download.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    async def initialize(self):
        """Initialize by parsing the master playlist."""
        try:
            self.output_dir.mkdir(exist_ok=True)
            master_playlist = m3u8.load(self.master_playlist_url)
            self._parse_master_playlist(master_playlist)
        except Exception as e:
            self.logger.error(f"Failed to initialize: {str(e)}")
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
        """Download a partial stream asynchronously."""
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

        # Download segments concurrently
        async with aiohttp.ClientSession() as session:
            tasks = [self._download_segment(session, url) for url in segments_to_download]
            segment_contents = await asyncio.gather(*tasks)

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
            self.logger.error(f"Failed to merge streams: {str(e)}")
            raise

    def cleanup(self, files: List[str]):
        """Clean up temporary files."""
        for file in files:
            try:
                if file and os.path.exists(file):
                    os.remove(file)
                    self.logger.info(f"Deleted temporary file: {file}")
            except Exception as e:
                self.logger.warning(f"Failed to delete {file}: {str(e)}")

async def main():
    try:
        # Get the master playlist URL from user
        master_playlist_url = input("Enter the master playlist URL: ").strip()
        if not master_playlist_url:
            master_playlist_url = "https://ec.netmagcdn.com:2228/hls-playback/23048f2535193fbbdac71b7ce21af40517ec675d4954fffc98e10363880825cba393aae242af810face55230d42119059ea55a2e098c4e57b5998db26b12188f1f9552ccc5ab77064bacf38d7479a50e3abebfbbb08d2817abdfc09c85096a92cd309b064dd24a19fbee8fd044bf47a7017740f4926f31800d3a008ef67d0341257d105a575d8037ed3c6fb3a0bbae33/master.m3u8"
            print(f"Using default URL: {master_playlist_url}")

        # Initialize downloader
        downloader = HLSDownloader(master_playlist_url, "downloads")
        await downloader.initialize()

        # Display available tracks
        tracks = downloader.get_available_tracks()
        
        print("\nAvailable Video Resolutions:")
        for res, track in tracks["video_tracks"].items():
            print(f"- {res} (Bandwidth: {track['bandwidth']})")

        print("\nAvailable Audio Tracks:")
        if tracks["audio_tracks"]:
            for lang, track in tracks["audio_tracks"].items():
                print(f"- {lang}: {track['name']}")
        else:
            print("No separate audio tracks available")

        # Get user selections
        while True:
            selected_resolution = input("\nSelect video resolution (e.g., '1920x1080'): ").strip()
            if selected_resolution in tracks["video_tracks"]:
                break
            print("Invalid resolution. Please choose from the available options.")

        selected_language = None
        if tracks["audio_tracks"]:
            while True:
                selected_language = input("Select audio language (press Enter to skip): ").strip()
                if not selected_language or selected_language in tracks["audio_tracks"]:
                    break
                print("Invalid language. Please choose from the available options.")

        # Get time range
        while True:
            try:
                start_time = float(input("\nEnter start time in seconds: "))
                end_time = float(input("Enter end time in seconds: "))
                if start_time >= 0 and end_time > start_time:
                    break
                print("Invalid time range. End time must be greater than start time.")
            except ValueError:
                print("Please enter valid numbers for start and end times.")

        # Get subtitle URL
        subtitle_url = input("\nEnter subtitle URL (press Enter to skip): ").strip()

        # Create configuration from user input
        config = {
            "master_playlist_url": master_playlist_url,
            "subtitle_url": subtitle_url if subtitle_url else None,
            "output_dir": "downloads",
            "start_time": start_time,
            "end_time": end_time,
            "selected_resolution": selected_resolution,
            "selected_language": selected_language
        }

        # Display confirmed configuration
        print("\nSelected Configuration:")
        print(json.dumps(config, indent=2))

        # Confirm proceed
        if input("\nProceed with download? (y/n): ").lower() != 'y':
            print("Download cancelled.")
            return

        # Download video stream
        print("\nDownloading video stream...")
        video_path, video_initial_time = await downloader.download_partial_stream(
            downloader.video_tracks[config["selected_resolution"]].url,
            config["start_time"],
            config["end_time"],
            "partial_video.ts"
        )

        # Download audio stream if selected
        audio_path = None
        if config["selected_language"]:
            print("Downloading audio stream...")
            audio_path, _ = await downloader.download_partial_stream(
                downloader.audio_tracks[config["selected_language"]].url,
                config["start_time"],
                config["end_time"],
                "partial_audio.ts"
            )

        # Process subtitles if URL provided
        subtitle_path = None
        if config["subtitle_url"]:
            print("Processing subtitles...")
            subtitle_path = await downloader.process_subtitles(
                config["subtitle_url"],
                video_initial_time,
                config["start_time"],
                config["end_time"]
            )

        # Merge streams
        print("Merging streams...")
        output_path = str(Path(config["output_dir"]) / "output_partial.mp4")
        await downloader.merge_streams(video_path, audio_path, output_path)

        # Cleanup
        temp_files = [f for f in [video_path, audio_path] if f]
        downloader.cleanup(temp_files)

        print(f"\nDownload complete! Final file saved as: {output_path}")
        if subtitle_path:
            print(f"Subtitles saved as: {subtitle_path}")

    except Exception as e:
        logging.error(f"Download failed: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(main())