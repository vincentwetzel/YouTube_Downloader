# YT_DLP_Download.py
# Backend download class using yt-dlp and PyQt6 signals.
# Supports: cancel via flag, blocking overwrite prompt via signal+event,
# format / rate options passed in from frontend.

import os
import traceback
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
import yt_dlp


class YT_DLP_Download(QObject):
    progress_updated = pyqtSignal(float)
    status_updated = pyqtSignal(str)
    title_updated = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    prompt_overwrite = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(
        self,
        raw_url: str,
        temp_dl_loc: str,
        final_destination_dir: str,
        download_mp3: bool = False,
        allow_playlist: bool = False,
        rate_limit: str = "0",
        video_quality: str = "best",
        video_ext: str | None = None,
        audio_ext: str | None = None,
        video_codec: str | None = None,
        audio_codec: str | None = None,
        restrict_filenames: bool = False,
        cookies: str | None = None,
        audio_quality: str = "192k",
    ):
        super().__init__()
        self.raw_url = raw_url
        self.temp_dl_loc = temp_dl_loc
        self.final_destination_dir = final_destination_dir
        self.download_mp3 = download_mp3
        self.allow_playlist = allow_playlist

        self.rate_limit = rate_limit
        self.video_quality = video_quality
        self.video_ext = video_ext
        self.audio_ext = audio_ext
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.audio_quality = audio_quality
        self.restrict_filenames = bool(restrict_filenames)
        self.cookies = cookies  # path to cookies file or None

        self._cancelled = False
        self._prompt_event = None
        self._prompt_response = None
        self._prompt_lock = threading.Lock()

        self.video_title = ""

    def _progress_hook(self, d: dict):
        if self._cancelled:
            raise RuntimeError("Download cancelled by user")

        status = d.get("status")
        if status == "downloading":
            downloaded = d.get("downloaded_bytes", 0) or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total and total > 0:
                pct = downloaded / total * 100
                self.progress_updated.emit(pct)
                self.status_updated.emit(
                    f"{pct:.1f}% ({downloaded/1024/1024:.2f} MB / {total/1024/1024:.2f} MB)"
                )
            else:
                self.status_updated.emit(
                    f"Downloaded {downloaded/1024/1024:.2f} MB"
                )
        elif status == "finished":
            self.status_updated.emit("Download finished (processing)")

    def provide_overwrite_response(self, allow: bool):
        with self._prompt_lock:
            if self._prompt_event:
                self._prompt_response = bool(allow)
                self._prompt_event.set()

    def _ask_overwrite_blocking(self, filepath: str) -> bool:
        ev = threading.Event()
        with self._prompt_lock:
            self._prompt_event = ev
            self._prompt_response = None
        self.prompt_overwrite.emit(filepath)
        ev.wait()
        with self._prompt_lock:
            resp = bool(self._prompt_response)
            self._prompt_event = None
            self._prompt_response = None
        return resp

    def get_video_title(self) -> str:
        try:
            with yt_dlp.YoutubeDL(
                {"quiet": True, "no_warnings": True, "skip_download": True}
            ) as ydl:
                info = ydl.extract_info(self.raw_url, download=False)
            if info.get("_type") == "playlist":
                title = info.get("title", "Playlist")
            else:
                title = info.get("title", "Item")
            self.video_title = title
            self.title_updated.emit(title)
            return title
        except Exception as e:
            self.error_occurred.emit(f"Failed to fetch title: {e}")
            return ""

    def start_yt_download(self):
        try:
            os.makedirs(self.temp_dl_loc, exist_ok=True)
            os.makedirs(self.final_destination_dir, exist_ok=True)

            # Base options
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "windowsfilenames": True,
                "progress_hooks": [self._progress_hook],
                "paths": {
                    "home": self.final_destination_dir,
                    "temp": self.temp_dl_loc,
                },
                "outtmpl": "%(title).80s [%(uploader|UnknownUploader)s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s",
                # Windows safety
                "nopart": False,
                "overwrites": True,
                "file_access_retries": 10,
                "file_access_wait": 2,
                "continuedl": True,
                "file_access_retries": 10,
                "file_access_wait": 1.0,
                "retries": 10,
                "fragment_retries": 10,
            }

            if self.restrict_filenames:
                ydl_opts["restrictfilenames"] = True

            if self.cookies:
                ydl_opts["cookiefile"] = self.cookies

            # Build format string
            if self.download_mp3:
                audio_fmt = "bestaudio"
                if self.audio_ext:
                    audio_fmt += f"[ext={self.audio_ext}]"
                if self.audio_codec:
                    audio_fmt += f"[acodec~={self.audio_codec}]"
                fmt = audio_fmt + "/bestaudio/best"
                ydl_opts["format"] = fmt
                ydl_opts["postprocessors"] = [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": self.audio_ext or "mp3",
                        "preferredquality": self.audio_quality.replace("k", ""),
                    }
                ]
            else:
                parts = []
                # video
                video_fmt = (
                    "bestvideo" if self.video_quality == "best" else self.video_quality
                )
                if self.video_ext:
                    video_fmt += f"[ext={self.video_ext}]"
                if self.video_codec:
                    video_fmt += f"[vcodec~={self.video_codec}]"
                parts.append(video_fmt)
                # audio
                audio_fmt = "bestaudio"
                if self.audio_ext:
                    audio_fmt += f"[ext={self.audio_ext}]"
                if self.audio_codec:
                    audio_fmt += f"[acodec~={self.audio_codec}]"
                parts.append(audio_fmt)
                fmt = "+".join(parts) + "/best"
                ydl_opts["format"] = fmt

            # Rate limit
            if self.rate_limit and self.rate_limit not in (
                "0",
                "",
                "No limit",
                "no limit",
            ):
                try:
                    # assume value given like "10 MB" → convert to bytes/sec
                    val = (
                        self.rate_limit.replace(" ", "")
                        .replace("MB", "000000")
                        .replace("MiB", "000000")
                    )
                    ydl_opts["ratelimit"] = int(val)
                except Exception:
                    pass

            # File exists check
            with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
                info = ydl.extract_info(self.raw_url, download=False)
                try:
                    expected = ydl.prepare_filename(info)
                except Exception:
                    expected = None

            if expected:
                dst = os.path.join(
                    self.final_destination_dir, os.path.basename(expected)
                )
                if os.path.exists(dst):
                    allowed = self._ask_overwrite_blocking(dst)
                    if not allowed:
                        self.status_updated.emit("Skipped (file exists)")
                        self.finished.emit(False)
                        return
                    else:
                        try:
                            os.remove(dst)
                        except Exception:
                            self.error_occurred.emit(
                                f"Unable to remove existing file: {dst}"
                            )
                            self.finished.emit(False)
                            return

            self.status_updated.emit("Starting download...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(self.raw_url, download=True)

            self._success = True
            self.video_title = info_dict.get("title", self.video_title)
            self.title_updated.emit(self.video_title)
            self.finished.emit(True)
            self.status_updated.emit("Completed")
        except Exception as e:
            if isinstance(e, RuntimeError) and "cancel" in str(e).lower():
                self.status_updated.emit("Cancelled")
                self.finished.emit(False)
                return
            tb = traceback.format_exc()
            self.error_occurred.emit(f"{e}\n{tb}")
            self.finished.emit(False)

    def cancel(self):
        self._cancelled = True
        self.status_updated.emit("Cancel requested")
