# YT_DLP_Download.py
# Backend download worker using yt-dlp inside a QThread context.
# Emits only primitive data via Qt signals and uses logging.debug extensively.

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
    progress_updated = pyqtSignal(float)      # percentage 0-100
    status_updated = pyqtSignal(str)          # status text
    title_updated = pyqtSignal(str)           # video/playlist title
    error_occurred = pyqtSignal(str)          # error message / traceback
    prompt_overwrite = pyqtSignal(str)        # filepath: request GUI to allow overwrite (worker will wait)
    finished = pyqtSignal(bool)               # success boolean

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
        sponsorblock: bool = False,
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

        logger.debug("Initialized YT_DLP_Download for URL: %s", raw_url)

    # progress hook called inside yt_dlp operation
    def _progress_hook(self, d: dict):
        try:
            if self._cancelled:
                logger.debug("Progress hook: cancel requested, raising RuntimeError")
                raise RuntimeError("Download cancelled by user")

            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes", 0) or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total and total > 0:
                    pct = downloaded / total * 100
                    self.progress_updated.emit(float(pct))
                    self.status_updated.emit(f"{pct:.1f}% ({downloaded/1024/1024:.2f} MB / {total/1024/1024:.2f} MB)")
                else:
                    self.status_updated.emit(f"Downloaded {downloaded/1024/1024:.2f} MB")
            elif status == "finished":
                self.status_updated.emit("Download finished (processing)")
        except Exception as e:
            logger.exception("Exception in progress hook: %s", e)
            raise

    # GUI will call this on main thread in response to prompt_overwrite
    def provide_overwrite_response(self, allow: bool):
        with self._prompt_lock:
            if self._prompt_event:
                logger.debug("Provide overwrite response: %s", allow)
                self._prompt_response = bool(allow)
                self._prompt_event.set()

    def _ask_overwrite_blocking(self, filepath: str) -> bool:
        ev = threading.Event()
        with self._prompt_lock:
            self._prompt_event = ev
            self._prompt_response = None
        logger.debug("Emitting prompt_overwrite for path: %s", filepath)
        self.prompt_overwrite.emit(filepath)
        # wait until GUI calls provide_overwrite_response
        ev.wait()
        with self._prompt_lock:
            resp = bool(self._prompt_response)
            self._prompt_event = None
            self._prompt_response = None
        logger.debug("Overwrite response received: %s", resp)
        return resp

    def _extract_info(self, ydl_opts):
        logger.debug("Extracting metadata for URL: %s", self.raw_url)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.raw_url, download=False)
        return info

    def start_yt_download(self):
        """
        Main entrypoint to run yt-dlp in this worker thread. Emits status/title/progress/finished.
        """
        logger.debug("Worker thread started for URL: %s", self.raw_url)
        try:
            os.makedirs(self.temp_dl_loc, exist_ok=True)
            os.makedirs(self.final_destination_dir, exist_ok=True)
            logger.debug("Ensured output dirs: temp=%s final=%s", self.temp_dl_loc, self.final_destination_dir)

            # Build ydl_opts
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [self._progress_hook],
                "paths": {"home": self.final_destination_dir, "temp": self.temp_dl_loc},
                "outtmpl": "%(title).80s [%(uploader|UnknownUploader)s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s",
                "windowsfilenames": True,
            }

            if not self.use_part_files:
                logger.debug("Disabling part files (nopart)")
                ydl_opts["nopart"] = True

            # restrict filenames
            if self.restrict_filenames:
                ydl_opts["restrictfilenames"] = True

            # cookies
            if self.cookies:
                ydl_opts["cookiefile"] = self.cookies
                logger.debug("Using cookies file: %s", self.cookies)

            # sponsorblock
            if self.sponsorblock:
                # example: remove sponsor segments
                ydl_opts["sponsorblock_remove"] = ["sponsor", "intro", "outro", "selfpromo", "interaction", "preview", "music_offtopic"]
                logger.debug("SponsorBlock enabled: sponsorblock_remove set")

            # rate limit
            if self.rate_limit and self.rate_limit not in ("0", "", "no limit", "No limit"):
                # yt-dlp expects bytes/sec; here we accept strings such as "10M" or "10Mib"
                rl = self.rate_limit
                # try to normalize "10 MB" etc.
                try:
                    rl_parsed = rl.replace(" ", "").lower()
                    # example "10mb" -> "10M"
                    ydl_opts["ratelimit"] = rl_parsed
                    logger.debug("Setting ratelimit to: %s", rl_parsed)
                except Exception:
                    logger.debug("Failed to parse rate_limit '%s'", rl)

            # format selection (basic)
            if self.download_mp3:
                ydl_opts["format"] = f"bestaudio[ext={self.audio_ext}]/bestaudio/best"
                ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": self.audio_ext or "mp3", "preferredquality": self.audio_quality.replace("k","") if isinstance(self.audio_quality,str) else "192"}]
                logger.debug("Configured audio-only download: %s", ydl_opts["format"])
            else:
                quality = self.video_quality if self.video_quality != "best" else "bestvideo"
                vext_clause = f"[ext={self.video_ext}]" if self.video_ext else ""
                vcodec_clause = f"[vcodec~={self.video_codec}]" if self.video_codec else ""
                a_codec_clause = f"[acodec~={self.audio_codec}]" if self.audio_codec else ""
                ydl_opts["format"] = f"{quality}{vext_clause}{vcodec_clause}+bestaudio{a_codec_clause}/best"
                logger.debug("Configured video download format: %s", ydl_opts["format"])

            # postprocessors (basic)
            ydl_opts.setdefault("postprocessors", [])
            ydl_opts.setdefault("merge_output_format", None)  # leave to yt-dlp if user didn't specify

            # Extract metadata first
            try:
                self.status_updated.emit("Extracting metadata...")
                logger.debug("Calling extract_info for metadata with opts: %s", ydl_opts)
                with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
                    info = ydl.extract_info(self.raw_url, download=False)
                logger.debug("Metadata extracted: type=%s title=%s", info.get("_type"), info.get("title"))
            except Exception as ex:
                # If metadata fails, try a minimal extraction
                logger.exception("Metadata extraction failed: %s", ex)
                self.error_occurred.emit(f"Metadata extraction failed: {ex}")
                self.finished.emit(False)
                return

            # update title
            if info.get("_type") == "playlist":
                title = info.get("title", "Playlist")
            else:
                title = info.get("title", info.get("id", "Item"))
            self.video_title = title
            self.title_updated.emit(title)
            logger.debug("Emitted title: %s", title)

            # compute expected filename to check for collisions
            try:
                with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
                    prepared_name = ydl.prepare_filename(info)
                expected_path = os.path.join(self.final_destination_dir, os.path.basename(prepared_name))
                logger.debug("Prepared expected filename: %s", expected_path)
                if os.path.exists(expected_path):
                    logger.debug("File already exists at: %s", expected_path)
                    allow = self._ask_overwrite_blocking(expected_path)
                    if not allow:
                        self.status_updated.emit("Skipped (file exists)")
                        self.finished.emit(False)
                        return
                    else:
                        try:
                            os.remove(expected_path)
                        except Exception:
                            logger.exception("Unable to remove existing file: %s", expected_path)
                            self.error_occurred.emit(f"Unable to remove existing file: {expected_path}")
                            self.finished.emit(False)
                            return
            except Exception:
                logger.debug("Could not compute prepared filename (continuing)")

            # Start actual download
            try:
                self.status_updated.emit("Starting download...")
                logger.debug("Starting yt-dlp download with opts: %s", ydl_opts)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(self.raw_url, download=True)
                logger.debug("yt-dlp extract_info finished for %s", self.raw_url)
                self._success = True
                # update title if possible
                self.video_title = info_dict.get("title", self.video_title)
                self.title_updated.emit(self.video_title)
                self.finished.emit(True)
                self.status_updated.emit("Completed")
                logger.debug("Download completed: %s", self.raw_url)
                return
            except Exception as e:
                logger.exception("Download failed: %s", e)
                # Check if cancelled
                if isinstance(e, RuntimeError) and "cancel" in str(e).lower():
                    self.status_updated.emit("Cancelled")
                    self.finished.emit(False)
                    return
                tb = traceback.format_exc()
                self.error_occurred.emit(f"{e}\n{tb}")
                self.finished.emit(False)
                return

        except Exception as e:
            logger.exception("Unexpected error in start_yt_download: %s", e)
            self.error_occurred.emit(str(e))
            self.finished.emit(False)

    def cancel(self):
        logger.debug("Cancel requested for %s", self.raw_url)
        self._cancelled = True
        self.status_updated.emit("Cancel requested")
