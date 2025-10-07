# YT_DLP_Download.py
# Worker backend using yt-dlp. Designed to be moved to a QThread.
# Emits primitive signals only. Includes logging and overwrite prompt mechanism.

import os
import traceback
import threading
import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

import yt_dlp

logger = logging.getLogger("YT_DLP_Download")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)


class YT_DLP_Download(QObject):
    # Signals (only primitives)
    progress_updated = pyqtSignal(float)    # percent 0-100
    status_updated = pyqtSignal(str)        # status text
    title_updated = pyqtSignal(str)         # video/playlist title
    error_occurred = pyqtSignal(str)        # error text
    prompt_overwrite = pyqtSignal(str)      # filepath (worker will wait for response)
    finished = pyqtSignal(bool)             # success boolean

    def __init__(
        self,
        raw_url: str,
        temp_dl_loc: str,
        final_destination_dir: str,
        download_mp3: bool = False,
        allow_playlist: bool = False,
        rate_limit: str = "0",
        video_quality: str = "best",
        video_ext: Optional[str] = None,
        audio_ext: Optional[str] = None,
        video_codec: Optional[str] = None,
        audio_codec: Optional[str] = None,
        restrict_filenames: bool = False,
        cookies: Optional[str] = None,
        audio_quality: str = "192k",
        sponsorblock: bool = True,
        use_part_files: bool = True,
    ):
        super().__init__()
        self.raw_url = raw_url
        self.temp_dl_loc = temp_dl_loc
        self.final_destination_dir = final_destination_dir
        self.download_mp3 = bool(download_mp3)
        self.allow_playlist = bool(allow_playlist)

        self.rate_limit = rate_limit or "0"
        self.video_quality = video_quality or "best"
        self.video_ext = video_ext or ""
        self.audio_ext = audio_ext or ""
        self.video_codec = video_codec or ""
        self.audio_codec = audio_codec or ""
        self.restrict_filenames = bool(restrict_filenames)
        self.cookies = cookies
        self.audio_quality = audio_quality
        self.sponsorblock = bool(sponsorblock)
        self.use_part_files = bool(use_part_files)

        self._cancelled = False
        self._prompt_event = None
        self._prompt_response = None
        self._prompt_lock = threading.Lock()

        self.video_title = ""
        self._success = False

        logger.debug("YT_DLP_Download initialized for %s (temp=%s final=%s)", self.raw_url, self.temp_dl_loc, self.final_destination_dir)

    def _progress_hook(self, d: dict):
        """Called frequently by yt-dlp; emits progress updates."""
        if self._cancelled:
            logger.debug("Progress hook: cancellation detected")
            raise RuntimeError("Download cancelled by user")

        status = d.get("status")
        if status == "downloading":
            downloaded = d.get("downloaded_bytes", 0) or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total and total > 0:
                pct = (downloaded / total) * 100.0
                self.progress_updated.emit(float(pct))
                self.status_updated.emit(f"{pct:.1f}% ({downloaded} / {total})")
            else:
                # Unknown total
                self.status_updated.emit(f"Downloaded {downloaded} bytes")
        elif status == "finished":
            self.status_updated.emit("Download finished (processing)")

    def provide_overwrite_response(self, allow: bool):
        """Called by GUI to respond to a prompt; unblocks worker."""
        with self._prompt_lock:
            if self._prompt_event:
                self._prompt_response = bool(allow)
                logger.debug("provide_overwrite_response: %s", allow)
                self._prompt_event.set()

    def _ask_overwrite_blocking(self, filepath: str) -> bool:
        """Emit prompt_overwrite and block until GUI responds via provide_overwrite_response."""
        ev = threading.Event()
        with self._prompt_lock:
            self._prompt_event = ev
            self._prompt_response = None
        logger.debug("Asking GUI to confirm overwrite: %s", filepath)
        self.prompt_overwrite.emit(filepath)
        ev.wait()
        with self._prompt_lock:
            resp = bool(self._prompt_response)
            self._prompt_event = None
            self._prompt_response = None
        logger.debug("Overwrite decision received: %s", resp)
        return resp

    def start_yt_download(self):
        """Run yt-dlp to extract metadata and perform download. This should run in a QThread."""
        logger.debug("start_yt_download called for %s", self.raw_url)
        try:
            # ensure directories exist
            os.makedirs(self.temp_dl_loc, exist_ok=True)
            os.makedirs(self.final_destination_dir, exist_ok=True)

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [self._progress_hook],
                "paths": {"home": self.final_destination_dir, "temp": self.temp_dl_loc},
                "outtmpl": "%(title).80s [%(uploader|UnknownUploader)s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s",
                "windowsfilenames": True,
            }

            if not self.use_part_files:
                ydl_opts["nopart"] = True

            if self.restrict_filenames:
                ydl_opts["restrictfilenames"] = True

            if self.cookies:
                ydl_opts["cookiefile"] = self.cookies
                logger.debug("Using cookies file: %s", self.cookies)

            if self.sponsorblock:
                ydl_opts["sponsorblock_remove"] = ["sponsor", "intro", "outro", "selfpromo", "interaction", "preview", "music_offtopic"]
                logger.debug("SponsorBlock enabled")

            # Rate limit (pass-through; assume user supplies a correct yt-dlp acceptable string)
            if self.rate_limit and self.rate_limit not in ("0", "", "no limit", "No limit"):
                ydl_opts["ratelimit"] = self.rate_limit
                logger.debug("Setting ratelimit: %s", self.rate_limit)

            # Format selection
            if self.download_mp3:
                # prefer best audio and postprocess to audio_ext
                ydl_opts["format"] = f"bestaudio[ext={self.audio_ext}]/bestaudio/best"
                ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": (self.audio_ext or "mp3"), "preferredquality": str(self.audio_quality).replace("k","")}]
                logger.debug("Configured audio-only format: %s", ydl_opts["format"])
            else:
                quality = self.video_quality if self.video_quality != "best" else "bestvideo"
                vext_clause = f"[ext={self.video_ext}]" if self.video_ext else ""
                vcodec_clause = f"[vcodec~={self.video_codec}]" if self.video_codec else ""
                acodec_clause = f"[acodec~={self.audio_codec}]" if self.audio_codec else ""
                ydl_opts["format"] = f"{quality}{vext_clause}{vcodec_clause}+bestaudio{acodec_clause}/best"
                logger.debug("Configured video format: %s", ydl_opts["format"])

            # Extract metadata first
            try:
                self.status_updated.emit("Extracting metadata...")
                logger.debug("Extracting metadata for %s", self.raw_url)
                with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
                    info = ydl.extract_info(self.raw_url, download=False)
            except Exception as ex:
                logger.exception("Metadata extraction failed: %s", ex)
                self.error_occurred.emit(f"Metadata extraction failed: {ex}")
                self.finished.emit(False)
                return

            # Title
            if info.get("_type") == "playlist":
                title = info.get("title", "Playlist")
            else:
                title = info.get("title", info.get("id", "Item"))
            self.video_title = title
            self.title_updated.emit(title)
            logger.debug("Emitted title: %s", title)

            # Try to predict filename and prompt overwrite if needed
            try:
                with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
                    prepared = ydl.prepare_filename(info)
                expected = os.path.join(self.final_destination_dir, os.path.basename(prepared))
                logger.debug("Expected destination: %s", expected)
                if os.path.exists(expected):
                    logger.debug("File exists at destination: %s", expected)
                    allow = self._ask_overwrite_blocking(expected)
                    if not allow:
                        self.status_updated.emit("Skipped (file exists)")
                        self.finished.emit(False)
                        return
                    else:
                        try:
                            os.remove(expected)
                        except Exception:
                            logger.exception("Unable to remove existing file: %s", expected)
                            self.error_occurred.emit(f"Unable to remove existing file: {expected}")
                            self.finished.emit(False)
                            return
            except Exception:
                logger.debug("Unable to compute expected filename - continuing")

            # Start actual download (this will obey noplaylist semantics as set in ydl_opts)
            try:
                self.status_updated.emit("Starting download...")
                logger.debug("Starting yt-dlp download for %s", self.raw_url)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(self.raw_url, download=True)
                logger.debug("yt-dlp completed for %s", self.raw_url)
                self._success = True
                self.video_title = info_dict.get("title", self.video_title)
                self.title_updated.emit(self.video_title)
                self.finished.emit(True)
                self.status_updated.emit("Completed")
                return
            except Exception as e:
                logger.exception("Download error: %s", e)
                if isinstance(e, RuntimeError) and "cancel" in str(e).lower():
                    self.status_updated.emit("Cancelled")
                    self.finished.emit(False)
                    return
                tb = traceback.format_exc()
                self.error_occurred.emit(f"{e}\n{tb}")
                self.finished.emit(False)
                return

        except Exception as e:
            logger.exception("Unexpected error in worker: %s", e)
            self.error_occurred.emit(str(e))
            self.finished.emit(False)

    def cancel(self):
        logger.debug("Cancel requested for %s", self.raw_url)
        self._cancelled = True
        self.status_updated.emit("Cancel requested")
