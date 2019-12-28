# NOTE: youtube-dl must be in the system PATH for this to run.
# I recommend installing youtube-dl with PIP, including on Windows.

import win32clipboard
import subprocess
import win32con
import os
import platform
import shutil
import re
import sys
import collections
import tkinter
import tkinter.ttk
import tkinter.messagebox
import logging
import threading

# NOTE TO USER: use logging.DEBUG for testing, logging.CRITICAL for runtime
logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

FINAL_DESTINATION_DIR = os.path.realpath("E:/Google Drive (vincentwetzel3@gmail.com)")
DOWNLOAD_LOCATION_ARGUMENT = "-o \"E:\%(title)s.%(ext)s\""  # TODO: Initialize this from settings.ini
DOWNLOAD_LOCATION = os.path.realpath("E:/")

MAXIMUM_SIMULTANEOUS_DOWNLOADS = 3  # TODO: Make a dropdown box to control this number

download_playlist_yes = False

youtube_dl_mp4_formats = [137, 136, 398, 22]
"""
137          mp4        1920x1080  1080p 4752k , avc1.640028, 30fps, video only, 481.99MiB
398          mp4        1280x720   720p 1794k , av01.0.05M.08, 30fps, video only, 209.23MiB
136          mp4        1280x720   720p 2697k , avc1.4d401f, 30fps, video only, 260.78MiB
22           mp4        1280x720   hd720 1357k , avc1.64001F, mp4a.40.2@192k (44100Hz) (best)
"""

output_filepaths = collections.deque([])
"""These are full filepaths for the downloaded file(s).
This will be used to move the downloaded file(s) to Google Drive"""


# SAMPLE URLS:
# https://www.youtube.com/watch?v=KEB16y1zBgA&list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO
# https://youtu.be/KEB16y1zBgA?list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO&t=1
# https://www.youtube.com/watch?time_continue=1661&v=EYDwHSGgkm8
# https://www.youtube.com/watch?v=CqqvzVblbsA&feature=youtu.be

def main():
    root_tk = tkinter.Tk()
    root_tk.title("YouTube Downloader by Vincent Wetzel")
    frame_1 = tkinter.ttk.Frame(root_tk, padding="3 3 12 12")

    # Download button
    tkinter.ttk.Button(frame_1, text="Button!", command=lambda: init_download()).grid(column=0, row=0,
                                                                                      sticky='W')
    frame_1.grid()
    root_tk.mainloop()

    # Done!


def init_download():
    # Dump clipboard data into a variable
    win32clipboard.OpenClipboard()
    raw_url = str((win32clipboard.GetClipboardData(win32con.CF_TEXT)).decode(
        "utf-8"))  # must decode from bytes to string
    win32clipboard.CloseClipboard()
    if (raw_url.startswith("https://www.youtube.com/watch?") is False and raw_url.startswith(
            "https://youtu.be/") is False) or " " in raw_url:
        raise Exception("The value on the clipboard is not a YouTube URL.")

    # Get the video title
    if check_to_see_if_playlist(raw_url):
        global download_playlist_yes
        download_playlist_yes = True  # TODO: Make this more eloquent
        dl_command = "youtube-dl --get-title -i --yes-playlist \"" + raw_url + "\""
    else:
        dl_command = "youtube-dl --get-title \"" + raw_url + "\""

    # Get a list of the files in the final output directory
    google_drive_files = os.listdir(FINAL_DESTINATION_DIR)
    for idx, output_file in enumerate(google_drive_files):
        google_drive_files[idx] = os.path.splitext(os.path.basename(output_file))[0].strip()

    video_titles_list = get_video_titles(dl_command, google_drive_files)

    # Track failed download attempts so we can modify the youtube-dl command as needed.
    failed_download_attempts = 0

    while True:
        dl_command = determine_download_command(raw_url, failed_download_attempts)

        # Run command to download the file
        result_successful = run_youtube_dl_download(dl_command)
        if not result_successful:
            logging.debug("Download attempt #" + str(failed_download_attempts + 1) + " failed.")
            failed_download_attempts += 1

            # Clear any .part files that might be associated with the failed download.
            download_dir_list = os.listdir(DOWNLOAD_LOCATION)
            for title in video_titles_list:
                for dir_item in download_dir_list:
                    if title in dir_item:
                        logging.debug("Deleting failed download file: " + str(dir_item))
                        os.remove(os.path.join(DOWNLOAD_LOCATION, dir_item))

        else:
            break

    # Put the downloaded file in its proper location
    # For playlists, leave them in the default download directory.
    move_file_after_download = True
    if not download_playlist_yes:
        for output_file in output_filepaths:
            try:
                output_file_size = os.path.getsize(output_file)
            except FileNotFoundError:
                # This usually happens when there was an issue during the download
                # with decoding output from youtube-dl in order to grab the file name.
                start_of_file_name = re.search(r".+?(?=�)", os.path.basename(output_file)).group(0)
                for f in os.listdir(os.path.dirname(output_file)):
                    if os.path.basename(f).startswith(start_of_file_name):
                        output_file = os.path.realpath(
                            os.path.join(os.path.dirname(output_file), os.path.basename(f)))
                output_file_size = os.path.getsize(output_file)

            # TODO: Modify this with a dropdown box.
            if output_file_size < 209715200:  # 200 MB
                move_file_after_download = True
            elif is_video_not_to_move(video_titles_list[0]):
                move_file_after_download = False
            else:
                move_file_after_download = tkinter.messagebox(title="Move File?",
                                                              message=str(output_file) + " is " + sizeof_fmt(
                                                                  output_file_size) + ". Do you still want to move it to " + str(
                                                                  FINAL_DESTINATION_DIR) + "?(y/n)")
            if move_file_after_download:
                # Use shutil to make sure the file is replaced if it already exists.
                shutil.move(output_file, os.path.join(FINAL_DESTINATION_DIR, os.path.basename(output_file)))
                logging.debug("\n" + str(output_file) + " moved to directory " + str(FINAL_DESTINATION_DIR))


def check_to_see_if_playlist(youtube_url):
    """
    Strips a YouTube URL down to its most basic form.
    If the URL is for a playlist then this method has an option to retain that.

    :param youtube_url: A YouTube URL
    :return:    A simplified version of the input URL
    """
    if "list=" in youtube_url:
        return tkinter.messagebox.askyesno(title="Download Playlist",
                                           message="Do you want to download this entire playlist?")
    else:
        return False


def determine_download_command(youtube_url, failed_download_attempts, download_mp3=False):
    """
    Figures out the correct youtube-dl command to run.

    :param youtube_url:  A YouTube URL.
    :return:    A string with the correct download command.
    """
    # TODO: Verify this
    if failed_download_attempts == 0:
        dl_format = "-f best[ext=mp4]/best"
    elif failed_download_attempts == 1:
        dl_format = "-f best"
    elif failed_download_attempts < (2 + len(youtube_dl_mp4_formats)):
        dl_format = "-f " + str(youtube_dl_mp4_formats[failed_download_attempts - 2])
    else:
        dl_format = ""

    command = "youtube-dl " + str(dl_format) + " " + DOWNLOAD_LOCATION_ARGUMENT + " "
    if download_mp3:
        # Audio downloads
        command += "--extract-audio --audio-format mp3 "
        if download_playlist_yes:
            command += "--yes-playlist \"" + youtube_url + "\""
        else:
            command += "\"" + youtube_url + "\""
    else:
        # Video downloads
        if download_playlist_yes:
            # Video playlists
            command += "--yes-playlist \"" + youtube_url + "\""
        else:
            command += "\"" + youtube_url + "\""

    command += " && exit"
    return command


def get_video_titles(command, google_drive_files):
    """
    Gets the title of the file(s) to be downloaded.
    If the file(s) already exists in Google Drive then this method will ask for user input to handle the situation.

    :param command: A youtube-dl command formatted to only get the video title instead of doing a download.
    :param google_drive_files:  A list of the files in the main directory of my Google Drive.
    :return:    The title(s) of the video(s) to be downloaded as a list.
    """

    video_titles_list = []

    # Set a flag that can be toggled if we need to kill the script.
    redownload_videos = None

    logging.debug("VIDEO TITLE: " if len(video_titles_list) == 1 else "VIDEO TITLES: ")

    # Get the video title(s) for the file(s) we are downloading.
    for video_title in run_win_cmd(command):
        video_title = video_title.strip()

        # Colons are not valid in file names in Windows so youtube-dl changes them and we must do the same.
        video_titles_list.append(video_title.replace(":", "-"))

        # Print the video title(s)
        logging.debug(video_title)

        # If our download already exists, handle the situation.
        if video_title in google_drive_files and redownload_videos is None:
            redownload_videos = tkinter.messagebox.askyesno(title="File is already downloaded",
                                                            message="We have detected that this file has already been downloaded to " + str(
                                                                FINAL_DESTINATION_DIR) + ". Do you want to download it again?")

    logging.debug('\n')  # Formatting

    for i, s in enumerate(video_titles_list):
        video_titles_list[i] = s.replace("|", "_")

    return video_titles_list


def run_youtube_dl_download(command):
    """
    This pipes a youtube-dl commmand into run_win_cmd().
    The purpose of running download commands this way is to be able to catch and handle errors.

    :param command: A youtube-dl command to download a video from a YouTube URL.
    :return:    None
    """

    merge_required = False  # TODO: What is this variable for?
    download_successful = False

    for line in run_win_cmd(command):
        line = line.strip()  # Strip off \n from each line
        logging.debug(line)
        if "WARNING: Requested formats are incompatible for merge and will be merged into" in line:
            merge_required = True
        if "[download] Destination: " in line and merge_required is False:
            if re.search(r".f[0-9]{3}", line) is not None:
                merge_required = True
            else:
                output_filepaths.append(os.path.realpath(line.split("[download] Destination: ")[1]))
        if "[ffmpeg] Merging formats into" in line:
            # sometimes files are downloaded in MP4 and converted into MKV or things like that.
            output_filepaths.pop()

            # Now add the new converted file
            output_filepaths.append(
                os.path.realpath(line.split("\"")[1]))  # Index 1 in this will give us the filename.
        if "has already been downloaded" in line:
            output_filepaths.append(
                os.path.realpath(line.split("[download]")[1].strip().split(" has already")[0].strip()))
        if "[download] 100% of " in line:
            # NOTE: youtube-dl refers to downloads as 100.0% until the file is completely downloaded.
            download_successful = True
        if "[ffmpeg] Destination:" in line:
            # When files are converted from video to audio
            # then the original file has to be removed from output_filepaths.
            output_filepaths.pop()
            output_filepaths.append(os.path.realpath(line.split("[ffmpeg] Destination: ")[1].strip()))
        if "WARNING: Unable to extract video title" in line:
            # TODO: Handle this properly
            logging.debug("There is an issue with trying to download this video. You may need to update youtube-dl")
            input("Press any key to exit this script...")
    return download_successful


def run_win_cmd(command):
    """
    Runs a command in a new cmd window.
    This function ONLY works on Windows OS.
    The values output in the CMD window are now piped through a generator.

    :param command: The command to run.
    """

    logging.debug(str(command) + "\n")
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


def sizeof_fmt(num, suffix='B'):
    """
    Converts a data sizes to more human-readable values.

    :param num: The number to convert. This defaults to bytes.
    :param suffix:  Default is bytes. If you want to convert another type then enter it as a parameter here (e.g. MB).
    :return:    The converted value
    """
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


def is_video_not_to_move(video_title):
    # TODO: Update documentation here. Output folders and final destination folders should be held in settings.ini
    """
    Determines if the video is a special case that should NOT be moved to Google Drive after downloading.

    :param video_title: The name of a YouTube video. This is NOT a URL.
    :return: True if a video title is a Starcraft video, False otherwise
    """
    starcraft_tournament_names = ["GSL", "WCS", "ASL", "KSL", "ThePylonShow", "IEM Katowice"]

    for tournament in starcraft_tournament_names:
        if tournament in video_title:
            return True

    if "Bannon's War Room" in video_title:
        return True

    return False


if __name__ == "__main__":
    main()
