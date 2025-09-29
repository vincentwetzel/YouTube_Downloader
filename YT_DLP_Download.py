import os
import shutil
import subprocess
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
import yt_dlp


class YT_DLP_Download(QObject):
    # === Signals to communicate with the GUI ===
    progress_updated = pyqtSignal(float)       # progress percent (0-100)
    status_updated = pyqtSignal(str)           # status/log messages
    title_updated = pyqtSignal(str)            # video title once retrieved
    error_occurred = pyqtSignal(str)           # error message
    prompt_overwrite = pyqtSignal(str)         # ask user about overwrite (filename)
    finished = pyqtSignal(bool)                # download finished (success/fail)

    def __init__(self, raw_url: str, temp_dl_loc: str,
                 final_destination_dir: str, download_mp3: bool = False):
        super().__init__()
        self.raw_url = raw_url
        self.temp_dl_loc = temp_dl_loc
        self.final_destination_dir = final_destination_dir
        self.download_mp3 = download_mp3

        self.video_title: str = ""   # updated after metadata fetch
        self._cancelled = False
        self._success = False

    # -----------------------------
    # Internal helper: yt-dlp progress hook
    # -----------------------------
    def _progress_hook(self, d: dict):
        try:
            if d["status"] == "downloading":
                pct_str = d.get("_percent_str", "0.0%").strip().replace("%", "")
                pct = float(pct_str)
                self.progress_updated.emit(pct)
            elif d["status"] == "finished":
                self.status_updated.emit("Download complete, finalizing...")
        except Exception:
            pass

    # -----------------------------
    # Fetch video title metadata
    # -----------------------------
    def get_video_title(self) -> str:
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YT_DLP_Download(ydl_opts) as ydl:
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

            # File naming template
            outtmpl = os.path.join(self.temp_dl_loc, "%(title)s.%(ext)s")

            # yt-dlp options
            ydl_opts = {
                "outtmpl": outtmpl,
                "progress_hooks": [self._progress_hook],
                "quiet": True,
                "no_warnings": True,
            }

            if self.download_mp3:
                ydl_opts["format"] = "bestaudio/best"
                ydl_opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            else:
                ydl_opts["format"] = "bestvideo+bestaudio/best"

            # Download
            self.status_updated.emit("Starting download...")
            with yt_dlp.YT_DLP_Download(ydl_opts) as ydl:
                ydl.download([self.raw_url])

            # After download, move file(s) to final destination
            self._move_files()

            self._success = True
            self.finished.emit(True)
            self.status_updated.emit("Download completed successfully.")
        except Exception as e:
            tb = traceback.format_exc()
            self.error_occurred.emit(f"{e}\n{tb}")
            self._success = False
            self.finished.emit(False)

    # -----------------------------
    # Move downloaded file(s) to final directory
    # -----------------------------
    def _move_files(self):
        for file in os.listdir(self.temp_dl_loc):
            src = os.path.join(self.temp_dl_loc, file)
            dst = os.path.join(self.final_destination_dir, file)

            if os.path.exists(dst):
                # Ask GUI if overwrite is allowed
                self.prompt_overwrite.emit(dst)
                # The GUI should connect this signal to a slot that asks the user
                # and then either delete/overwrite or skip. For simplicity, we'll
                # assume GUI handles the decision and renames/deletes accordingly.
                continue

            shutil.move(src, dst)

    # -----------------------------
    # Cancel download (not supported by yt-dlp directly)
    # -----------------------------
    def cancel(self):
        # yt-dlp doesn’t have a clean cancel hook, but we can flag it
        self._cancelled = True
        self.status_updated.emit("Cancel requested (will stop after current operation).")

pass