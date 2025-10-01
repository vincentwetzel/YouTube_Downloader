# YT_DLP_Download.py
# Backend download class using yt-dlp and PyQt6 signals.
# Adds cancel support via flag + progress-hook exception,
# and pre-download overwrite prompting with a blocking wait.

import os
import shutil
import traceback
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
import yt_dlp


class YT_DLP_Download(QObject):
    # === Signals to communicate with the GUI ===
    progress_updated = pyqtSignal(float)  # progress percent (0-100)
    status_updated = pyqtSignal(str)  # status/log messages
    title_updated = pyqtSignal(str)  # video title once retrieved
    error_occurred = pyqtSignal(str)  # error message
    prompt_overwrite = pyqtSignal(str)  # ask GUI about overwrite; backend will wait for response
    finished = pyqtSignal(bool)  # download finished (success/fail)

    def __init__(
        self,
        raw_url: str,
        temp_dl_loc: str,
        final_destination_dir: str,
        download_mp3: bool = False,
        allow_playlist: bool = False,
        rate_limit: str = "0",
        video_quality: str = "best",
        video_ext: str = "mp4",
        audio_ext: str = "mp3",
        video_codec: str = "h264",
        audio_codec: str = "aac",
    ):
        super().__init__()
        self.raw_url = raw_url
        self.temp_dl_loc = temp_dl_loc
        self.final_destination_dir = final_destination_dir
        self.download_mp3 = download_mp3
        self.allow_playlist = allow_playlist

        # yt-dlp option passthrough
        self.rate_limit = rate_limit
        self.video_quality = video_quality
        self.video_ext = video_ext
        self.audio_ext = audio_ext
        self.video_codec = video_codec
        self.audio_codec = audio_codec

        self.video_title: str = ""  # updated after metadata fetch
        self._cancelled = False
        self._success = False

        # For prompt/overwrite: thread-safe event and response value
        self._prompt_event = None
        self._prompt_response = None
        self._prompt_lock = threading.Lock()

    # -----------------------------
    # Progress hook: also used to abort on cancel
    # -----------------------------
    def _progress_hook(self, d: dict):
        # d contains status and partial bytes info
        try:
            if self._cancelled:
                # raise an exception to abort yt-dlp operation immediately
                raise RuntimeError("Download cancelled by user")

            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes", 0) or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total and total > 0:
                    pct = (downloaded / total) * 100
                    self.progress_updated.emit(pct)
                    self.status_updated.emit(
                        f"{pct:.1f}% ({downloaded/1024/1024:.2f} MB / {total/1024/1024:.2f} MB)"
                    )
                else:
                    # unknown total
                    self.status_updated.emit(f"Downloaded {downloaded/1024/1024:.2f} MB...")
            elif d.get("status") == "finished":
                self.status_updated.emit("Download finished, finalizing...")
        except Exception as e:
            # propagate so yt-dlp aborts
            raise

    # -----------------------------
    # Blocking prompt response setter (called by GUI slot)
    # -----------------------------
    def provide_overwrite_response(self, allow_overwrite: bool):
        with self._prompt_lock:
            if self._prompt_event:
                self._prompt_response = bool(allow_overwrite)
                self._prompt_event.set()

    # -----------------------------
    # Utility: ask GUI whether to overwrite (blocking)
    # -----------------------------
    def _ask_overwrite_blocking(self, filepath: str) -> bool:
        """
        Emit signal and wait until GUI calls provide_overwrite_response.
        Returns True if GUI allowed overwrite, False otherwise.
        """
        event = threading.Event()
        with self._prompt_lock:
            self._prompt_event = event
            self._prompt_response = None

        self.prompt_overwrite.emit(filepath)
        # Wait (backend is running in worker thread; GUI will respond and set event)
        event.wait()
        with self._prompt_lock:
            resp = bool(self._prompt_response)
            self._prompt_event = None
            self._prompt_response = None
        return resp

    # -----------------------------
    # Fetch video (or playlist) metadata
    # -----------------------------
    def get_video_title(self) -> str:
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.raw_url, download=False)
                if info.get("_type") == "playlist":
                    title = info.get("title", "Unknown Playlist")
                else:
                    title = info.get("title", "Unknown Title")
                self.video_title = title
                self.title_updated.emit(title)
                return title
        except Exception as e:
            self.error_occurred.emit(f"Failed to fetch title: {e}")
            return ""

    # -----------------------------
    # Start download (single item)
    # -----------------------------
    def start_yt_download(self):
        """
        Single-download run. Caller is expected to pass a URL that is a media item
        (not a playlist)—if the caller wanted playlist expansion, it should have
        queued individual items already.
        """
        try:
            # Ensure directories exist
            os.makedirs(self.temp_dl_loc, exist_ok=True)
            os.makedirs(self.final_destination_dir, exist_ok=True)

            # Basic ydl options
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "windowsfilenames": True,
                "progress_hooks": [self._progress_hook],
                "paths": {"home": self.final_destination_dir, "temp": self.temp_dl_loc},
                "outtmpl": "%(title).80s [%(uploader|UnknownUploader)s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s",
                "embed_thumbnail": True,
            }

            # Postprocessors default
            if self.download_mp3:
                ydl_opts["format"] = f"bestaudio[ext={self.audio_ext}]/bestaudio/best"
                ydl_opts["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredcodec": self.audio_ext, "preferredquality": "192"}
                ]
            else:
                # For video, use user-specified quality/codec/ext hints
                quality = self.video_quality if self.video_quality and self.video_quality != "best" else "bestvideo"
                # build a format string that's reasonably compatible
                # note: vcodec~= and acodec~= use regex matching in yt-dlp
                ydl_opts["format"] = f"{quality}[ext={self.video_ext}][vcodec~={self.video_codec}]+bestaudio[acodec~={self.audio_codec}]/best"

            # Rate limit
            if self.rate_limit and self.rate_limit not in ("0", "", "No limit", "no limit"):
                ydl_opts["ratelimit"] = self.rate_limit

            # Before downloading, try to compute expected filename and check existence
            # Use a temporary YoutubeDL to prepare filename from metadata
            with yt_dlp.YoutubeDL({**ydl_opts, "quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(self.raw_url, download=False)
                # prepare filename using same outtmpl from ydl_opts
                try:
                    prepared_name = ydl.prepare_filename(info)
                except Exception:
                    # fallback to title + id
                    ext = info.get("ext", self.video_ext)
                    prepared_name = f"{info.get('title','video')}.{ext}"

            # determine expected final path in final_destination_dir
            expected_dst = os.path.join(self.final_destination_dir, os.path.basename(prepared_name))

            if os.path.exists(expected_dst):
                # Ask GUI if we should overwrite
                allow = self._ask_overwrite_blocking(expected_dst)
                if not allow:
                    self.status_updated.emit("Skipped (file exists).")
                    self.finished.emit(False)
                    return
                else:
                    try:
                        os.remove(expected_dst)
                    except Exception:
                        # if we can't remove, emit error and abort
                        self.error_occurred.emit(f"Could not remove existing file: {expected_dst}")
                        self.finished.emit(False)
                        return

            # Do the actual download (will trigger progress_hook periodically)
            self.status_updated.emit("Starting download...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Note: if the user cancels, our progress_hook raises and aborts the download
                info_dict = ydl.extract_info(self.raw_url, download=True)

            # success
            self._success = True
            self.video_title = info_dict.get("title", self.video_title)
            self.title_updated.emit(self.video_title)
            self.finished.emit(True)
            self.status_updated.emit("Download completed successfully.")
        except Exception as e:
            # If cancelled by user, mark canceled explicitly
            if isinstance(e, RuntimeError) and "cancel" in str(e).lower():
                self.status_updated.emit("Cancelled")
                self.finished.emit(False)
                return
            tb = traceback.format_exc()
            self.error_occurred.emit(f"{e}\n{tb}")
            self._success = False
            self.finished.emit(False)

    # -----------------------------
    # Cancel download
    # -----------------------------
    def cancel(self):
        self._cancelled = True
        self.status_updated.emit("Cancel requested.")
