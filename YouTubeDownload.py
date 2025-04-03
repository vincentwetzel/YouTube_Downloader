import os
import logging
import shutil
import re
import subprocess
import tkinter
import tkinter.messagebox
from typing import Generator, List
from urllib.error import HTTPError


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
        self.download_mp3 = download_mp3
        self.failed_download_attempts = 0
        self.output_file_path: str = None
        """The path to the finished download file. This is calculated during the download."""

        # Get a list of the files in the final output directory
        for idx, file in enumerate(self.output_dir_files):
            self.output_dir_files[idx] = os.path.splitext(os.path.basename(file))[0].strip()
        self.download_progress_string_var = tkinter.StringVar(value="0")
        self.redownload_video = None
        self.video_doesnt_exist = False

        self.need_to_clear_download_cache = False

    def start_yt_download(self) -> bool:
        """
        This is the main download method.
        :return: True if successful, false otherwise.
        """
        self.video_title.set(self.get_video_title())

        if self.video_doesnt_exist:
            return False

        if not self.redownload_video and self.redownload_video is not None:
            return False
        while True:
            download_command = self.determine_download_command()
            logging.info("DOWNLOAD COMMAND: " + download_command)

            # Run command to download the file
            downloads_was_successful = self.run_youtube_dl_download(download_command)
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

        # If the file is an mp3 then we need to modify the name of the output file once it is downloaded
        # because our variable is tracking the video file, not the audio file
        if self.download_mp3:
            self.output_file_path = self.output_file_path.split(".")[0] + ".mp3"

        # Put the downloaded file in its proper location
        output_file_size = os.path.getsize(self.output_file_path)

        move_after_download = True

        if os.path.dirname(self.output_file_path) == self.FINAL_DESTINATION_DIR:
            logging.info("The final destination directory for this file is the same as the location that it was downloaded to so we don't have to move it.")
            move_after_download = False
        elif output_file_size < 209715200:  # 200 MB  # TODO: Modify this with a combobox box??
            move_after_download = True
        else:
            # Bring the tkinter window to the front of other windows.
            self.root_tk.lift()

            # Ask the user if they want to move the file regardless of it being larger than the limit.
            move_after_download = tkinter.messagebox.askyesno(title="Move File?", message=str(
                self.output_file_path) + " is " + YouTubeDownload.sizeof_fmt(
                output_file_size) + ". Do you still want to move it to " + str(self.FINAL_DESTINATION_DIR))
        if move_after_download:
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

    def determine_download_command(self) -> str:
        """
        Figures out the correct yt-dlp command to run.

        :return:    A string with the correct download command.
        """

        dl_format = r'-S codec:h264:aac -f "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best" --merge-output-format mp4'
        command = "yt-dlp --verbose --no-playlist " + str(dl_format) + " -o \"" + "".join([self.TEMP_DOWNLOAD_LOC,
                                                                                           self.video_title.get().replace(
                                                                                               '"', "'"),
                                                                                           ".%(ext)s"]) + "\" "

        if self.download_mp3:
            # Audio downloads
            command += "--extract-audio --audio-format mp3 \"" + self.raw_url + "\""
        else:
            # Video downloads
            command += "\"" + self.raw_url + "\""

        # TODO: Handle the cookies-from-browser option better
        command += " --windows-filenames --trim-filenames 150 --cookies-from-browser firefox --sponsorblock-remove sponsor"
        command += " && exit"
        return command

    def get_video_title(self) -> str:
        """
        Gets the title of the file(s) to be downloaded.
        If the file(s) already exists in the output directory then ask for user input to handle the situation.

        :return:    The title(s) of the video(s) to be downloaded as a list.
        """

        # TODO: Handle the cookies-from-browser option better
        # Get the video title
        get_video_title_command = "yt-dlp --verbose --get-title --cookies-from-browser firefox --no-playlist \"" + self.raw_url + "\""

        vid_title = None

        # Get the video title(s) for the file(s) we are downloading.
        for line in self.run_win_cmd(get_video_title_command):
            if "youtube_dl.utils.ExtractorError: This video has been removed by the user" in line:
                self.video_doesnt_exist = True
                return "ERROR: Video removed"

            line = str(line).strip()

            logging.debug(line)

            vid_title = line
            for c in ['/', '\\', ':', '*', '?', '\"', '<', '>', '|']:
                if c in vid_title:
                    vid_title = vid_title.replace(c, '_')

            # If our download already exists, handle the situation.
            if line in self.output_dir_files and self.redownload_video is None:
                self.redownload_video = tkinter.messagebox.askyesno(title="File is already downloaded",
                                                                    message="This file already exists in "
                                                                            + str(self.FINAL_DESTINATION_DIR)
                                                                            + ". Do you want to download it again?")
                if self.redownload_video:
                    logging.debug("Redownloading video...")
        # Print the video title(s)
        vid_title = vid_title.encode("ascii", errors="ignore").decode().replace("%", " percent").strip()
        logging.info("VIDEO TITLE IS: " + vid_title)
        return vid_title

    def run_youtube_dl_download(self, download_command) -> bool:
        """
        This pipes a yt-dlp command into run_win_cmd().
        The purpose of running download commands this way is to be able to catch and handle errors.

        :return:    True if successful, false otherwise
        """

        merge_required = False
        download_successful = False

        for line in YouTubeDownload.run_win_cmd(download_command):
            line = str(line).strip()  # Strip off \n from each line
            logging.info(line)
            if "WARNING: Requested formats are incompatible for merge and will be merged into" in line:
                merge_required = True
            if "[download] Destination: " in line and merge_required is False:
                if re.search(r".f[0-9]{3}", line) is not None:
                    merge_required = True
                else:
                    self.output_file_path = os.path.realpath(line.split("[download] Destination: ")[1])
            if "Merging formats into" in line:
                # Now add the new converted file
                self.output_file_path = os.path.realpath(
                    line.split("\"")[1])  # Index 1 in this will give us the filename.
            if "has already been downloaded" in line:
                logging.debug("LINE:" + line)
                logging.debug(
                    "LINE AFTER SPLIT:" + line.split("[download]")[1].strip().split(" has already")[0].strip())
                self.output_file_path = os.path.realpath(
                    line.split("[download]")[1].strip().split(" has already")[0].strip())
                logging.debug("VAL:" + str(self.output_file_path))
            if "[download] 100% of " in line:
                # NOTE: yt-dlp refers to downloads as 100.0% until the file is completely downloaded.
                download_successful = True
            if "[ffmpeg] Destination:" in line:
                # When files are converted from video to audio
                # then the original file has to be removed from output_filepaths.
                self.output_file_path = os.path.realpath(line.split("[ffmpeg] Destination: ")[1].strip())
            if re.search(r'^\[download\][\s]+[0-9]+\.[0-9]+%', line):
                self.download_progress_string_var.set(re.search(r'[0-9]+\.[0-9]+', line).group(0))
            if "ERROR: unable to download video data: HTTP Error 403: Forbidden" in line:
                self.need_to_clear_download_cache = True
                download_successful = False
                break

        return download_successful

    @staticmethod
    def run_win_cmd(command: str) -> Generator[List[str], None, None]:
        """
        Runs a command in a new cmd window.
        This function ONLY works on Windows OS.
        The values output in the CMD window are now piped through a generator.

        :param command: The command to run.
        :return: None
        """
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
