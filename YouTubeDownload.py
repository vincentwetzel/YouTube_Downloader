import os
import logging
import shutil
import re
import subprocess
import time
import tkinter
import tkinter.messagebox
from typing import Generator, List
from yt_dlp import YoutubeDL


class YouTubeDownload:

    def __init__(self, root_tk: tkinter.Tk, raw_url, temp_dl_loc, final_destination_dir: str, download_mp3=False):
        """
        :param raw_url: A YouTube URL to download
        :param final_destination_dir: The
        :param download_mp3:
        """
        self.root_tk = root_tk
        self.raw_url = raw_url
        self.FINAL_DESTINATION_DIR = os.path.realpath(final_destination_dir)
        self.TEMP_DOWNLOAD_LOC = temp_dl_loc
        self.output_dir_files = [os.path.splitext(f)[0] for f in os.listdir(self.FINAL_DESTINATION_DIR) if
                                 os.path.isfile(os.path.join(self.FINAL_DESTINATION_DIR, f))]
        self.video_title: tkinter.StringVar = tkinter.StringVar(value=self.raw_url)
        self.download_audio = download_mp3
        self.output_file_path: str = ""
        """The path to the finished download file. This is calculated during the download."""

        # Get a list of the files in the final output directory
        self.download_progress_dbl_var = tkinter.DoubleVar(value=0.0)
        self.total_download_size_in_bytes = 0
        self.download_successful = False

        self.need_to_clear_download_cache = False

    def start_yt_download(self) -> bool:
        """
        Start a single yt-dlp download attempt using the Python API.
        Clears cache on 403 errors, updates state flags, and returns success/failure.
        """

        self.download_successful = False

        # Make sure the video title fetched correctly
        counter = 0
        while self.video_title == self.raw_url and counter < 10:
            logging.debug("Video title has not been fetched yet. Sleeping for 5 seconds...")
            time.sleep(5)
            counter = counter + 1
        if counter == 10:
            logging.error("The download was unable to start because fetching the video title failed.")
            return False

        try:
            ydl_opts = self.determine_download_options()
            logging.info("DOWNLOAD OPTIONS:")
            logging.info(ydl_opts)

            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.raw_url, download=False)
                self.output_file_path = os.path.realpath(ydl.prepare_filename(info))
                self.total_download_size_in_bytes = info.get("filesize")

                # Check if the file already exists in the output directory
                if os.path.splitext(os.path.basename(self.output_file_path))[0] in self.output_dir_files:
                    # Prompt user via GUI to confirm redownload
                    if tkinter.messagebox.askyesno(
                            title="File already exists",
                            message=f"This file already exists in {self.FINAL_DESTINATION_DIR}. Download again?"
                    ):
                        logging.debug("Redownloading video...")
                    else:
                        return False

                ydl.download([self.raw_url])

            if self.output_file_path:
                final_path = os.path.join(self.FINAL_DESTINATION_DIR,
                                          os.path.basename(self.output_file_path))
                shutil.move(self.output_file_path, final_path)

            return True

        except Exception as e:
            logging.error(f"Download failed: {e}")

            # Handle specific transient case: HTTP 403 (stale cookies/cache)
            if "HTTP Error 403" in str(e):
                try:
                    with YoutubeDL({}) as ydl:
                        ydl.cache.remove()
                    logging.info("yt-dlp cache cleared after 403 error.")
                except Exception as cache_err:
                    logging.error(f"Failed to clear yt-dlp cache: {cache_err}")

            # No retries, fail fast
            return False

    @staticmethod
    def check_to_see_if_playlist(url) -> bool:
        # TODO: Verify if this is correct. Isn't it supposed to return a bool?
        """
        Strips a YouTube URL down to its most basic form.
        If the URL is for a playlist then this method has an option to retain that.
        :param url: a YouTube URL to
        :return: True if it is a playlist, False otherwise
        """
        return "list=" in url or "playlist" in url

    def determine_download_options(self) -> dict:
        """
        Constructs yt-dlp options as a Python dict instead of a CLI string.
        This allows direct use with YoutubeDL() API.
        """

        # ------------------------------
        # Format selection
        # ------------------------------
        if self.download_audio:
            # Audio-only: best audio, convert thumbnails to jpg
            dl_format = 'ba/best'
        else:
            # Video+audio: AVC video + AAC audio, fallback to mp4
            dl_format = 'bv*[vcodec^=avc]+ba*[acodec^=aac]/b[ext=mp4]'

        # ------------------------------
        # Filename template
        # ------------------------------
        filename_template = "%(title).90s [%(uploader).30s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s"
        full_output_path = os.path.normpath(
            os.path.join(self.TEMP_DOWNLOAD_LOC, filename_template)
        )

        # ------------------------------
        # Base options dictionary
        # ------------------------------
        ydl_opts = {
            'logger': LineLogger(),
            'verbose': True,
            'noplaylist': True,
            'format': dl_format,
            'outtmpl': full_output_path,
            'windowsfilenames': True,
            'trim_filenames': 150,
            'cookiesfrombrowser': ('firefox',),  # tuple form required
            'embedthumbnail': True,
            'extractor_args': {'youtube': {'sabr': ['on']}},  # maps to --extractor-args "youtube:sabr=on"
            'postprocessors': [],
        }

        # ------------------------------
        # Audio-specific flags
        # ------------------------------
        if self.download_audio:
            ydl_opts['extractaudio'] = True
            ydl_opts['audioformat'] = 'mp3'
            # Add thumbnail conversion postprocessor
            ydl_opts['postprocessors'].append({
                'key': 'FFmpegThumbnailsConvertor',
                'format': 'jpg'
            })

        # ------------------------------
        # SponsorBlock removal
        # ------------------------------
        ydl_opts['sponsorblock_remove'] = ['sponsor']

        return ydl_opts

    def get_video_title(self) -> str:
        """
        Retrieves the title of the video to be downloaded using yt-dlp's Python API.
        Handles browser cookie issues, yt-dlp version warnings, and file collisions.

        :return: Sanitized, ASCII-safe video title string.
        """
        try:
            # Define yt-dlp options to extract metadata only (no download)
            ydl_opts = {
                'quiet': True,  # Suppress console output
                'no_warnings': True,  # Suppress warnings
                'restrictfilenames': True,  # Gets output filename the way it will be when I actually download it.
                'cookiesfrombrowser': ('firefox',),  # Use Firefox cookies for authentication
                'noplaylist': True,  # Ensure single video, not playlist
                'skip_download': True  # Don't download the video
            }

            # Use yt-dlp's Python API to extract metadata
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.raw_url, download=False)
                self.output_file_path = os.path.realpath(ydl.prepare_filename(info))

            # Extract raw title from metadata
            self.video_title.set(info.get('title', ''))
            logging.debug(f"TITLE: {self.video_title.get()}")

            # If no title is found, raise an exception
            if not self.video_title:
                raise Exception("No title found in metadata")

            # Log and return the final sanitized title
            return self.video_title
        except Exception as e:
            error_msg = str(e)
            logging.error(error_msg)

            # Handle known error: video removed or unavailable
            if "ExtractorError" in error_msg or "This video has been removed" in error_msg:
                return "ERROR: Video removed"

            # Handle known error: yt-dlp version outdated
            if "Confirm you are on the latest version" in error_msg:
                tkinter.messagebox.showerror(
                    "yt-dlp possibly out of date",
                    "yt-dlp suggests it may be outdated. Please update and try again."
                )
                return "ERROR: Title fetch failed, possibly version is outdated"

            # All other cases
            return "ERROR: Title fetch failed"

    def run_youtube_dl_download(self, ydl_opts: dict) -> bool:
        """
        Executes a yt-dlp download using the Python API.
        Tracks progress, merge status, output path, and error conditions.

        :param ydl_opts: Dictionary of yt-dlp options (built by determine_download_options)
        :return: True if successful, False otherwise
        """

        # Reset state flags for each new download attempt
        self.download_successful = False

        # ------------------------------
        # Define a progress hook callback
        # ------------------------------
        # yt-dlp calls this function repeatedly with a dict describing the current state.
        # This replaces your old stdout parsing logic.
        def progress_hook(state_dict):
            status = state_dict.get('status')

            # Case 1: Actively downloading
            if status == 'downloading':
                # yt-dlp provides a percent string like " 42.3%"
                downloaded = state_dict.get('downloaded_bytes', 0)
                logging.debug("self.total_download_size_in_bytes: " + str(self.total_download_size_in_bytes))
                if self.total_download_size_in_bytes != 0:
                    percent = downloaded / self.total_download_size_in_bytes * 100
                    # schedule update on Tk main thread
                    self.root_tk.after(0, lambda: self.download_progress_dbl_var.set(percent))

            # Case 2: Download finished (file written to disk)
            if status == 'finished':
                self.download_progress_dbl_var = 100.0
                filename = state_dict.get('filename')
                if filename:
                    # Store the absolute path for later use
                    self.download_successful = True

            # Case 3: Post-processing (merging formats, converting audio, etc.)
            if status == 'postprocessing':
                message = state_dict.get('message', '')

            # Case 4: File already exists (yt-dlp skips download)
            if status == 'already_downloaded':
                filename = state_dict.get('filename')
                if filename:
                    self.download_successful = True

        # Attach our hook to yt-dlp options
        ydl_opts['progress_hooks'] = [progress_hook]

        # ------------------------------
        # Execute the download
        # ------------------------------
        try:
            with YoutubeDL(ydl_opts) as ydl:
                # This triggers the download and calls our hook repeatedly
                ydl.download([self.raw_url])

            # Return True if the hook marked the download as successful
            return self.download_successful

        except Exception as e:
            error_msg = str(e)

            # Special case: HTTP 403 errors often mean cache corruption
            if "HTTP Error 403" in error_msg:
                self.need_to_clear_download_cache = True
                self.download_successful = False

            # Log any other error for forensic traceability
            logging.error(f"Download error: {error_msg}")
            return False

    def clear_yt_dlp_cache(self):
        try:
            with YoutubeDL({}) as ydl:
                ydl.cache.remove()
            logging.info("yt-dlp cache cleared successfully.")
        except Exception as e:
            logging.error(f"Failed to clear yt-dlp cache: {e}")

    @staticmethod
    def sizeof_fmt(num: int, suffix='B') -> str:
        """
        Converts a data sizes to more human-readable values.

        :param num: The number to convert. This defaults to bytes.
        :param suffix:  Default is bytes (B). To convert another type, enter it as a parameter here (e.g. MB).
        :return:    The converted value
        """
        for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
            if abs(num) < 1024.0:
                return "%3.1f%s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f%s%s" % (num, 'Yi', suffix)


class LineLogger:
    def debug(self, msg):
        if msg.strip():
            # Replace carriage returns with real newlines
            logging.info(msg.replace('\r', '\n'))

    def warning(self, msg):
        logging.warning(msg)

    def error(self, msg):
        logging.error(msg)
