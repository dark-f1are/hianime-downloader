import customtkinter as ctk
import asyncio
import threading
from main import HLSDownloader
from pathlib import Path
from tkinter import filedialog
from os.path import expanduser

class HLSDownloaderGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Configure window
        self.title("HLS Video Downloader")
        self.geometry("800x600")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Initialize variables
        self.url_var = ctk.StringVar()
        self.output_dir_var = ctk.StringVar(value=str(Path.home() / "Videos"))  # Default to user's Videos directory
        self.status_var = ctk.StringVar(value="Ready")
        self.available_tracks = None
        self.downloader = None

        self.create_widgets()

    def create_widgets(self):
        # Create main frame
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(padx=20, pady=20, fill="both", expand=True)

        # URL Input
        url_frame = ctk.CTkFrame(main_frame)
        url_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(url_frame, text="Master Playlist URL:").pack(side="left", padx=5)
        url_entry = ctk.CTkEntry(url_frame, textvariable=self.url_var, width=400)
        url_entry.pack(side="left", padx=5, fill="x", expand=True)

        # Output Directory
        dir_frame = ctk.CTkFrame(main_frame)
        dir_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(dir_frame, text="Output Directory:").pack(side="left", padx=5)
        ctk.CTkEntry(dir_frame, textvariable=self.output_dir_var, width=300).pack(side="left", padx=5)
        ctk.CTkButton(dir_frame, text="Browse", command=self.browse_output_dir).pack(side="left", padx=5)

        # Tracks Frame
        self.tracks_frame = ctk.CTkFrame(main_frame)
        self.tracks_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Video Resolution
        self.video_var = ctk.StringVar()
        self.video_menu = ctk.CTkOptionMenu(self.tracks_frame, variable=self.video_var)
        self.video_menu.pack(pady=5)
        self.video_menu.configure(state="disabled")

        # Audio Track
        self.audio_var = ctk.StringVar()
        self.audio_menu = ctk.CTkOptionMenu(self.tracks_frame, variable=self.audio_var)
        self.audio_menu.pack(pady=5)
        self.audio_menu.configure(state="disabled")

        # Time Range Frame
        time_frame = ctk.CTkFrame(main_frame)
        time_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(time_frame, text="Start Time (s):").pack(side="left", padx=5)
        self.start_time = ctk.CTkEntry(time_frame, width=100)
        self.start_time.pack(side="left", padx=5)
        self.start_time.insert(0, "0")
        
        ctk.CTkLabel(time_frame, text="End Time (s):").pack(side="left", padx=5)
        self.end_time = ctk.CTkEntry(time_frame, width=100)
        self.end_time.pack(side="left", padx=5)
        self.end_time.insert(0, "60")

        # Subtitle URL
        sub_frame = ctk.CTkFrame(main_frame)
        sub_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(sub_frame, text="Subtitle URL (optional):").pack(side="left", padx=5)
        self.subtitle_url = ctk.CTkEntry(sub_frame, width=400)
        self.subtitle_url.pack(side="left", padx=5, fill="x", expand=True)

        # Progress Bar
        self.progress_bar = ctk.CTkProgressBar(main_frame)
        self.progress_bar.pack(fill="x", padx=10, pady=5)
        self.progress_bar.set(0)

        # Status Label
        self.status_label = ctk.CTkLabel(main_frame, textvariable=self.status_var)
        self.status_label.pack(pady=5)

        # Buttons
        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkButton(button_frame, text="Load Tracks", command=self.load_tracks).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="Download", command=self.start_download).pack(side="left", padx=5)

    def browse_output_dir(self):
        dir_path = filedialog.askdirectory(initialdir=expanduser(self.output_dir_var.get()))
        if dir_path:
            self.output_dir_var.set(str(Path(dir_path).resolve()))

    def load_tracks(self):
        self.status_var.set("Loading tracks...")
        threading.Thread(target=self._async_load_tracks, daemon=True).start()

    def _async_load_tracks(self):
        async def load():
            try:
                self.downloader = HLSDownloader(
                    self.url_var.get(), 
                    str(Path(expanduser(self.output_dir_var.get())).resolve())
                )
                await self.downloader.initialize()
                self.available_tracks = self.downloader.get_available_tracks()
                
                # Update UI in main thread
                self.after(0, self._update_track_menus)
                self.status_var.set("Tracks loaded successfully")
            except Exception as e:
                # Fix: Create a lambda with explicit parameter
                self.after(0, lambda err=e: self.status_var.set(f"Error: {str(err)}"))

        asyncio.run(load())

    def _update_track_menus(self):
        # Update video tracks
        video_resolutions = list(self.available_tracks["video_tracks"].keys())
        self.video_menu.configure(state="normal", values=video_resolutions)
        if video_resolutions:
            self.video_var.set(video_resolutions[0])

        # Update audio tracks
        audio_languages = ["None"] + list(self.available_tracks["audio_tracks"].keys())
        self.audio_menu.configure(state="normal", values=audio_languages)
        self.audio_var.set(audio_languages[0])

    def start_download(self):
        if not self.downloader or not self.available_tracks:
            self.status_var.set("Please load tracks first")
            return

        threading.Thread(target=self._async_download, daemon=True).start()

    def _async_download(self):
        async def download():
            try:
                output_dir = str(Path(expanduser(self.output_dir_var.get())).resolve())
                self.downloader.output_dir = Path(output_dir)
                
                self.status_var.set("Downloading video...")
                self.progress_bar.set(0)

                video_path, video_initial_time = await self.downloader.download_partial_stream(
                    self.downloader.video_tracks[self.video_var.get()].url,
                    float(self.start_time.get()),
                    float(self.end_time.get()),
                    "partial_video.ts"
                )

                audio_path = None
                if self.audio_var.get() != "None":
                    self.status_var.set("Downloading audio...")
                    audio_path, _ = await self.downloader.download_partial_stream(
                        self.downloader.audio_tracks[self.audio_var.get()].url,
                        float(self.start_time.get()),
                        float(self.end_time.get()),
                        "partial_audio.ts"
                    )

                subtitle_path = None
                if self.subtitle_url.get():
                    self.status_var.set("Processing subtitles...")
                    subtitle_path = await self.downloader.process_subtitles(
                        self.subtitle_url.get(),
                        video_initial_time,
                        float(self.start_time.get()),
                        float(self.end_time.get())
                    )

                self.status_var.set("Merging streams...")
                output_path = str(Path(output_dir) / "output_partial.mkv")
                await self.downloader.merge_streams(video_path, audio_path, output_path)

                # Cleanup
                temp_files = [f for f in [video_path, audio_path] if f]
                self.downloader.cleanup(temp_files)

                self.status_var.set("Download complete!")
                self.progress_bar.set(1)

            except Exception as e:
                # Fix: Create a lambda with explicit parameter
                self.after(0, lambda err=e: self.status_var.set(f"Error: {str(err)}"))

        asyncio.run(download())

def main():
    app = HLSDownloaderGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
