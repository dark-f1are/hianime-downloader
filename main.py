import m3u8
import requests
import os
from urllib.parse import urljoin
from datetime import timedelta
from webvtt import WebVTT, Caption

# Base master playlist URL
master_playlist_url = "https://ec.netmagcdn.com:2228/hls-playback/23048f2535193fbbdac71b7ce21af40517ec675d4954fffc98e10363880825cba393aae242af810face55230d42119059ea55a2e098c4e57b5998db26b12188f1f9552ccc5ab77064bacf38d7479a50e3abebfbbb08d2817abdfc09c85096a92cd309b064dd24a19fbee8fd044bf47a7017740f4926f31800d3a008ef67d0341257d105a575d8037ed3c6fb3a0bbae33/master.m3u8"

# Load the master playlist
master_playlist = m3u8.load(master_playlist_url)

# Extract base URL from master playlist
base_url = master_playlist_url.rsplit('/', 1)[0] + '/'

# Parse audio languages (if present)
audio_tracks = {}
if master_playlist.media:
    for media in master_playlist.media:
        audio_tracks[media.language] = {
            "name": media.name,
            "url": urljoin(base_url, media.uri)
        }

# Parse video resolutions
video_tracks = {}
for playlist in master_playlist.playlists:
    resolution = playlist.stream_info.resolution
    video_tracks[f"{resolution[0]}x{resolution[1]}"] = {
        "bandwidth": playlist.stream_info.bandwidth,
        "url": urljoin(base_url, playlist.uri)
    }

# Display available options to the user
print("Available Video Resolutions:")
for resolution in video_tracks.keys():
    print(resolution)

if audio_tracks:
    print("\nAvailable Audio Languages:")
    for lang, details in audio_tracks.items():
        print(f"{lang}: {details['name']}")
else:
    print("\nNo audio tracks available.")

# Get user choices
selected_res = input("\nSelect resolution (e.g., '1920x1080'): ")
selected_lang = None
if audio_tracks:
    selected_lang = input("Select language (or press Enter to skip audio): ").strip()

start_time = float(input("\nEnter start time in seconds: "))
end_time = float(input("Enter end time in seconds: "))

# Get selected URLs
selected_video_url = video_tracks[selected_res]["url"]
selected_audio_url = None
if selected_lang and selected_lang in audio_tracks:
    selected_audio_url = audio_tracks[selected_lang]["url"]

# Create output directory
os.makedirs("downloads", exist_ok=True)

# Function to download a range of segments
def download_partial_m3u8(m3u8_url, start_time, end_time, output_dir, output_name):
    playlist = m3u8.load(m3u8_url)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_name)

    total_time = 0
    with open(output_path, "wb") as output_file:
        for segment in playlist.segments:
            segment_duration = segment.duration
            if total_time + segment_duration < start_time:
                total_time += segment_duration
                continue
            if total_time > end_time:
                break
            segment_url = urljoin(m3u8_url.rsplit('/', 1)[0] + '/', segment.uri)
            print(f"Downloading: {segment_url}")
            response = requests.get(segment_url, stream=True)
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=8192):
                output_file.write(chunk)
            total_time += segment_duration

    print(f"Partial file saved: {output_path}")
    return output_path, total_time

# Download the selected partial video stream
video_output, video_duration = download_partial_m3u8(selected_video_url, start_time, end_time, "downloads", "partial_video.ts")

# Download the selected partial audio stream (if available)
audio_output = None
if selected_audio_url:
    audio_output, audio_duration = download_partial_m3u8(selected_audio_url, start_time, end_time, "downloads", "partial_audio.ts")

# Download the subtitle file if present (you can modify the subtitle URL accordingly)
subtitle_url = "https://s.megastatics.com/subtitle/f1ce9102d7b9e18f52c4b5376121c81b/eng-3.vtt"
subtitle_file = "downloads/subtitle.vtt"
response = requests.get(subtitle_url)
with open(subtitle_file, 'wb') as f:
    f.write(response.content)

print(f"\nSubtitle file saved: {subtitle_file}")

# Modify subtitle timing to keep only the relevant range
def adjust_subtitle_timing(input_file, output_file, start, end):
    vtt = WebVTT().read(input_file)
    captions_to_keep = []
    
    for caption in vtt:
        start_time = timedelta(
            hours=int(caption.start[:2]),
            minutes=int(caption.start[3:5]),
            seconds=int(caption.start[6:8]),
            milliseconds=int(caption.start[9:12])
        )
        end_time = timedelta(
            hours=int(caption.end[:2]),
            minutes=int(caption.end[3:5]),
            seconds=int(caption.end[6:8]),
            milliseconds=int(caption.end[9:12])
        )

        if start <= start_time.total_seconds() <= end or start <= end_time.total_seconds() <= end:
            captions_to_keep.append(Caption(start=caption.start, end=caption.end, text=caption.text))
    
    new_vtt = WebVTT()
    new_vtt.captions = captions_to_keep
    new_vtt.save(output_file)

adjusted_subtitle_file = "downloads/adjusted_subtitle.vtt"
adjust_subtitle_timing(subtitle_file, adjusted_subtitle_file, start_time, end_time)
print(f"Adjusted subtitle file saved as: {adjusted_subtitle_file}")

# Merge audio and video using FFmpeg (if audio is available)
output_file = "downloads/output_partial.mp4"
if audio_output:
    print("\nMerging audio and video...")
    merge_command = f"ffmpeg -i {video_output} -i {audio_output} -c copy {output_file} -y"
else:
    print("\nSaving video without audio...")
    merge_command = f"ffmpeg -i {video_output} -c copy {output_file} -y"

os.system(merge_command)

# Cleanup temporary .ts files
def cleanup_temp_files(files):
    for file in files:
        if os.path.exists(file):
            os.remove(file)
            print(f"Deleted temporary file: {file}")

# Delete temp .ts files after merging
temp_files = [video_output, audio_output] if audio_output else [video_output]
cleanup_temp_files(temp_files)

print(f"\nDownload complete! Final file saved as: {output_file}")
