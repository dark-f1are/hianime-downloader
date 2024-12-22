import m3u8
import requests
import os
from urllib.parse import urljoin

# Base master playlist URL
master_playlist_url = "https://ea.netmagcdn.com:2228/hls-playback/23048f2535193fbbdac71b7ce21af40517ec675d4954fffc98e10363880825cba393aae242af810face55230d4211905411e71b641939b7405873999f4b679ed3f47ab3cf77d1e079742fa3170aa219bbb07c4656f4a607811cefa77e252bf951953e9ab8f96521c1bca0e6a8b6960c1e831a01ffc88fda38628cc65cf0f7ba30256ad8e83e127a0a12b90f451abbf7a/master.m3u8"

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
    return output_path

# Download the selected partial video stream
video_output = download_partial_m3u8(selected_video_url, start_time, end_time, "downloads", "partial_video.ts")

# Download the selected partial audio stream (if available)
audio_output = None
if selected_audio_url:
    audio_output = download_partial_m3u8(selected_audio_url, start_time, end_time, "downloads", "partial_audio.ts")

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
