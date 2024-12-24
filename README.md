# HiAnime Downloader

A Python application specifically designed for downloading video segments from hianime.to streaming site. This tool allows downloading anime episodes with multiple resolution options, audio tracks, and subtitles.

## ⚠️ Disclaimer

This tool is for educational purposes only. Please support content creators by using official streaming services when available. Make sure you have the right to download and store the content in your jurisdiction.

## Features

- Download anime episodes from hianime.to
- Select video quality/resolution
- Select audio tracks (multiple languages when available)
- Download specific time segments
- Support for subtitles
- Both CLI and GUI interfaces
- Progress tracking with rich console output
- Cross-platform support

## Requirements

- Python 3.8+
- FFmpeg installed and available in system PATH

## Installation

1. Clone this repository:
```bash
git clone https://github.com/abrar-wadud/hianime-downloader.git
cd hianime-downloader
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

## Usage

### GUI Version

Run the GUI version with:
```bash
python gui_main.py
```

### CLI Version

Run the CLI version with:
```bash
python main.py
```

### How to Use

1. Get the master playlist URL from hianime.to:
   - Open the anime episode in your browser
   - Open developer tools (F12)
   - Go to Network tab
   - Look for `.m3u8` file in the network requests
   - Copy the master playlist URL

2. In the application:
   - Paste the master playlist URL
   - Select output directory
   - Choose video resolution
   - Choose audio track (if multiple available)
   - Enter start and end times (optional)
   - Add subtitle URL if needed

## Legal Notice

This application is intended for personal use only. Users are responsible for ensuring they comply with local laws and regulations regarding content downloading and storage.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.