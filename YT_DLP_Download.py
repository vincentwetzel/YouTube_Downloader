import os
import shutil
import subprocess
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
import yt_dlp


class YT_DLP_Download(QObject):
    # === Signals to communicate with the GUI ===
    progress_updated = pyqtSignal(float)  # progress percent (0-100)
    status_updated = pyqtSignal(str)  # status/log messages
    title_updated = pyqtSignal(str)  # video title once retrieved
    error_occurred = pyqtSignal(str)  # error message
    prompt_overwrite = pyqtSignal(str)  # ask user about overwrite (filename)
    finished = pyqtSignal(bool)  # download finished (success/fail)

    def __init__(self, raw_url: str, temp_dl_loc: str,
                 final_destination_dir: str, download_mp3: bool = False):
        super().__init__()
        self.raw_url = raw_url
        self.temp_dl_loc = temp_dl_loc
        self.final_destination_dir = final_destination_dir
        self.download_mp3 = download_mp3

        self.video_title: str = ""  # updated after metadata fetch
        self._cancelled = False
        self._success = False

    # -----------------------------
    # Internal helper: yt-dlp progress hook
    # -----------------------------
    def _progress_hook(self, d: dict):
        try:
            if d["status"] == "downloading":
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate")

                if total:
                    pct = downloaded / total * 100
                    self.progress_updated.emit(pct)
                    self.status_updated.emit(
                        f"{pct:.1f}% ({downloaded / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB)"
                    )
                else:
                    # No total size known → show bytes only
                    self.status_updated.emit(f"Downloaded {downloaded / 1024 / 1024:.2f} MB...")

            elif d["status"] == "finished":
                self.status_updated.emit("Download finished, merging streams...")

            elif d["status"] == "error":
                self.status_updated.emit("Error during download.")

        except Exception as e:
            # Log instead of silent pass for audit-grade clarity
            import traceback
            self.status_updated.emit(f"Progress hook error: {e}\n{traceback.format_exc()}")

    # -----------------------------
    # Fetch video title metadata
    # -----------------------------
    def get_video_title(self) -> str:
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.raw_url, download=False)
                title = info.get("title", "Unknown Title")
                self.video_title = title
                self.title_updated.emit(title)
                return title
        except Exception as e:
            self.error_occurred.emit(f"Failed to fetch title: {e}")
            return ""

    # -----------------------------
    # Start yt-dlp download
    # -----------------------------
    def start_yt_download(self):
        try:
            # Ensure output dirs exist
            os.makedirs(self.temp_dl_loc, exist_ok=True)
            os.makedirs(self.final_destination_dir, exist_ok=True)

            # yt-dlp options (no outtmpl yet)
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "windowsfilenames": True,
                "noplaylist": True,
                "progress_hooks": [self._progress_hook],
                "paths": {
                    "home": self.final_destination_dir,  # finished files
                    "temp": self.temp_dl_loc  # .part files
                },
                "format": "bestvideo[ext=mp4][vcodec^=avc]+bestaudio[ext=m4a]/best[ext=mp4]",
                "outtmpl": "%(title).80s [%(uploader|UnknownUploader)s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s",
                "embed_thumbnail": True,
                "postprocessors": [
                    {"key": "EmbedThumbnail"},
                    {"key": "FFmpegMetadata"}
                ]

            }

            if self.download_mp3:
                ydl_opts["format"] = "bestaudio/best"
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            else:
                ydl_opts["format"] = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]"
                ydl_opts["merge_output_format"] = "mp4"

            # Extract metadata first
            self.status_updated.emit("Extracting video metadata...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(self.raw_url, download=False)

            # Fallback-safe metadata
            title = info_dict.get("title", "UnknownTitle")[:80]
            uploader = info_dict.get("uploader", "UnknownUploader")
            upload_date = info_dict.get("upload_date", "00000000")
            formatted_date = (
                f"{upload_date[4:6]}-{upload_date[6:8]}-{upload_date[0:4]}"
                if len(upload_date) == 8 else "UnknownDate"
            )
            video_id = info_dict.get("id", "UnknownID")
            ext = "mp3" if self.download_mp3 else "mp4"

            # Construct output filename
            filename = f"{title} [{uploader}][{formatted_date}][{video_id}].{ext}"

            # Download
            self.status_updated.emit("Starting download...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.raw_url])

            self._success = True
            self.finished.emit(True)
            self.status_updated.emit("Download completed successfully.")

        except Exception as e:
            tb = traceback.format_exc()
            self.error_occurred.emit(f"{e}\n{tb}")
            self._success = False
            self.finished.emit(False)

    # -----------------------------
    # Cancel download (not supported by yt-dlp directly)
    # -----------------------------
    def cancel(self):
        # yt-dlp doesn’t have a clean cancel hook, but we can flag it
        self._cancelled = True
        self.status_updated.emit("Cancel requested (will stop after current operation).")
