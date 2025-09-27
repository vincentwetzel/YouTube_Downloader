import os
import logging
import shutil
import re
import subprocess
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
        self.FINAL_DESTINATION_DIR = final_destination_dir
        self.TEMP_DOWNLOAD_LOC = temp_dl_loc
        self.output_dir_files = os.listdir(
            self.FINAL_DESTINATION_DIR)
        self.video_title: tkinter.StringVar = tkinter.StringVar(value=self.raw_url)
        self.download_audio = download_mp3
        self.failed_download_attempts = 0
        self.output_file_path: str = ""
        self.output_file_name: str = ""
        """The path to the finished download file. This is calculated during the download."""

        # Get a list of the files in the final output directory
        for idx, file in enumerate(self.output_dir_files):
            self.output_dir_files[idx] = os.path.splitext(os.path.basename(file))[0].strip()
        self.download_progress_str_var = tkinter.StringVar(value="0")
        self.redownload_video = None
        self.video_doesnt_exist = False

        self.need_to_clear_download_cache = False

    def start_yt_download(self) -> bool:
        """
        This is the main download method.
        :return: True if successful, false otherwise.
        """
        self.get_video_title()

        if self.video_doesnt_exist:
            return False

        if not self.redownload_video and self.redownload_video is not None:
            return False
        while True:
            download_opts = self.determine_download_options()
            logging.info("DOWNLOAD OPTIONS:")
            logging.info(download_opts)

            # Run command to download the file
            downloads_was_successful = self.run_youtube_dl_download(download_opts)
            if downloads_was_successful:
                break

            # Sometimes downloads will fail because of the download cache.
            # If that happens, clear the cache and attempt to download again.
            elif not downloads_was_successful and self.need_to_clear_download_cache:
                for cache_clear_line in self.run_win_cmd("yt-dlp --rm-cache-dir"):
                    logging.info(cache_clear_line)
                logging.debug("Download cache clearing successful. Attempting to redo download...")
            else:
                logging.info("Download attempt #" + str(self.failed_download_attempts + 1) + " failed.")
                self.failed_download_attempts += 1

                # Clear any .part files that might be associated with the failed download.
                download_dir_list = os.listdir(self.TEMP_DOWNLOAD_LOC)
                for dir_item in download_dir_list:
                    if self.video_title.get() in dir_item:
                        logging.info("Deleting failed download file: " + str(dir_item))
                        os.remove(os.path.join(self.TEMP_DOWNLOAD_LOC, dir_item))

            if self.failed_download_attempts > 10:
                # Catastrophic failure, kill the download
                logging.info("After many tries, this download has failed.")
                return False

        # If the file is a mp3 then we need to modify the name of the output file once it is downloaded
        # because our variable is tracking the video file, not the audio file
        if self.download_audio:
            self.output_file_path = os.path.splitext(self.output_file_path)[0] + ".mp3"

        if os.path.dirname(self.output_file_path) == self.FINAL_DESTINATION_DIR:
            logging.info(
                "The final destination directory for this file is the same as the location "
                "that it was downloaded to so we don't have to move it.")
        else:
            # Move and replace the file if it already exists.
            shutil.move(self.output_file_path,
                        os.path.join(self.FINAL_DESTINATION_DIR, os.path.basename(self.output_file_path)))
            logging.info("\n" + str(self.output_file_path) + " moved to directory " + str(self.FINAL_DESTINATION_DIR))

        return True

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
                self.output_file_name = ydl.prepare_filename(info)

            # Extract raw title from metadata
            self.video_title.set(info.get('title', ''))
            logging.debug(f"TITLE: {self.video_title.get()}")

            # If no title is found, raise an exception
            if not self.video_title:
                raise Exception("No title found in metadata")

            # Check if the file already exists in the output directory
            if self.output_file_name in self.output_dir_files and self.redownload_video is None:
                # Prompt user via GUI to confirm redownload
                self.redownload_video = tkinter.messagebox.askyesno(
                    title="File already exists",
                    message=f"This file already exists in {self.FINAL_DESTINATION_DIR}. Download again?"
                )
                if self.redownload_video:
                    logging.debug("Redownloading video...")

            # Log and return the final sanitized title
            return self.video_title
        except Exception as e:
            error_msg = str(e)
            logging.error(error_msg)

            # Handle known error: video removed or unavailable
            if "ExtractorError" in error_msg or "This video has been removed" in error_msg:
                self.video_doesnt_exist = True
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
        self.output_file_path = None

        # ------------------------------
        # Define a progress hook callback
        # ------------------------------
        # yt-dlp calls this function repeatedly with a dict describing the current state.
        # This replaces your old stdout parsing logic.
        def progress_hook(d):
            status = d.get('status')

            # Case 1: Actively downloading
            if status == 'downloading':
                # yt-dlp provides a percent string like " 42.3%"
                percent_str: str = d.get('_percent_str', '').strip()
                if percent_str.endswith('%'):
                    try:
                        val = float(percent_str.replace('%', ''))
                        if (val > float(self.download_progress_str_var.get()) or
                                float(self.download_progress_str_var.get()) == 100.0):
                            self.download_progress_str_var.set(val)
                    except ValueError:
                        pass

            # Case 2: Download finished (file written to disk)
            if status == 'finished':
                filename = d.get('filename')
                if filename:
                    # Store the absolute path for later use
                    self.output_file_path = os.path.realpath(filename)
                    self.download_successful = True

            # Case 3: Post-processing (merging formats, converting audio, etc.)
            if status == 'postprocessing':
                message = d.get('message', '')

                # Detect when ffmpeg writes a new destination file
                if 'Destination' in message:
                    dest = message.split('Destination: ')[-1].strip()
                    self.output_file_path = os.path.realpath(dest)

            # Case 4: File already exists (yt-dlp skips download)
            if status == 'already_downloaded':
                filename = d.get('filename')
                if filename:
                    self.output_file_path = os.path.realpath(filename)
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

    @staticmethod
    def run_win_cmd(command: str) -> Generator[List[str], None, None]:
        """
        Runs a command in a new cmd window.
        This function ONLY works on Windows OS.
        The values output in the CMD window are now piped through a generator.

        :param command: The command to run.
        :return: None
        """
        logging.debug("WIN_CMD: " + command)

        # Note: errors="ignore" ignores special characters and returns the string without them.
        process = subprocess.Popen(command, shell=True, encoding='utf-8', stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, errors="replace")

        # Yield stderr lines first so there aren't blank stdout lines fumbling around in the generator
        # and stderr issues can be handled immediately.
        for stdout_line in iter(process.stdout.readline, ""):
            yield stdout_line

        # Once the process has completed, get the return code to see if the process was successful.
        return_code = process.wait()
        if return_code != 0:
            raise Exception('command %s failed, see above for details', command)

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
