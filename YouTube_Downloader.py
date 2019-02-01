import win32clipboard
import subprocess
import win32con
import os
import shutil
import re
import sys

# SAMPLE URLS:
# https://www.youtube.com/watch?v=KEB16y1zBgA&list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO
# https://youtu.be/KEB16y1zBgA?list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO&t=1
# https://www.youtube.com/watch?time_continue=1661&v=EYDwHSGgkm8
# https://www.youtube.com/watch?v=CqqvzVblbsA&feature=youtu.be

youtube_dl_loc = os.path.realpath(os.path.join(str(os.path.expanduser("~")), "youtube-dl.exe"))
final_destination_dir = os.path.realpath("E:/Google Drive (vincentwetzel3@gmail.com)")

output_filepaths = []


def main():
    # Dump clipboard data into a variable
    win32clipboard.OpenClipboard()
    clipboard_youtube_url = str((win32clipboard.GetClipboardData(win32con.CF_TEXT)).decode(
        "utf-8"))  # must decode from bytes to string
    win32clipboard.CloseClipboard()
    if (clipboard_youtube_url.startswith(
            "https://www.youtube.com/watch?") is False and clipboard_youtube_url.startswith(
        "https://youtu.be/") is False) or " " in clipboard_youtube_url:
        raise Exception("The value on the clipboard is not a YouTube URL.")

    # Strip out extra stuff in URL
    download_playlist_yes = False
    simplified_youtube_url = check_url_for_extra_parameters(clipboard_youtube_url)

    # Run a command to see if the file already exists and we should skip the download.
    # NOTE: This will only produce 1 line of output
    if download_playlist_yes:
        command = youtube_dl_loc + " --skip-download --get-title -i --yes-playlist \"" + simplified_youtube_url + "\""
    else:
        if "&list" in simplified_youtube_url:
            command = youtube_dl_loc + " --skip-download --get-title " + strip_argument_from_youtube_url(
                simplified_youtube_url, "&list")
        else:
            command = youtube_dl_loc + " --skip-download --get-title " + simplified_youtube_url

    # Output formatting
    print()

    # Get a list of the files in the final output directory
    google_drive_files = os.listdir(final_destination_dir)
    for i, output_file in enumerate(google_drive_files):
        google_drive_files[i] = os.path.splitext(os.path.basename(output_file))[0].strip()

    video_titles = get_video_titles(command, google_drive_files)

    # Output formatting
    print()

    # Figure out the formatting of the DOWNLOAD command to run in cmd
    command = determine_download_command(simplified_youtube_url, download_playlist_yes)

    # Run command to download the file
    try:
        run_youtube_dl_download(command)
    except DataBlocksError:
        # TODO: Handle this error
        pass

    # Put the downloaded file in its proper location
    # For playlists, leave them in the default download directory.
    move_file = True
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
            if output_file_size < 209715200:  # 200 MB
                move_file = True
            else:
                user_input = input(
                    "This file is " + sizeof_fmt(output_file_size) + ". Do you still want to move it to " + str(
                        final_destination_dir) + "?(y/n)").lower()
                if user_input == "y" or user_input == "yes":
                    move_file = True
                else:
                    move_file = False
            if move_file:
                # Use shutil to make sure the file is replaced if it already exists.
                shutil.move(output_file, os.path.join(final_destination_dir, os.path.basename(output_file)))
                print("\n" + str(output_file) + " moved to directory " + str(final_destination_dir))

    # Done!


def check_url_for_extra_parameters(youtube_url):
    """
    Strips a YouTube URL down to its most basic form.
    If the URL is for a playlist then this method has an option to retain that.

    :param youtube_url: A YouTube URL
    :return:    A simplified version of the input URL
    """
    # TODO: Make this better using regex
    simplified_youtube_url = youtube_url
    if "&list=" in simplified_youtube_url:
        user_input = input("Do you want to download this whole playlist? (y/n): ")
        if user_input.lower() == "y" or user_input.lower() == "yes":
            download_playlist_yes = True

    if "&feature=" in simplified_youtube_url:
        simplified_youtube_url = strip_argument_from_youtube_url(simplified_youtube_url, "&feature")
    if "?t=" in simplified_youtube_url:
        simplified_youtube_url = strip_argument_from_youtube_url(simplified_youtube_url, "?t=")
    if "&t=" in simplified_youtube_url:
        simplified_youtube_url = strip_argument_from_youtube_url(simplified_youtube_url, "&t")
    if "time_continue" in simplified_youtube_url:
        simplified_youtube_url = strip_argument_from_youtube_url(simplified_youtube_url, "time_continue")
    if "&index" in simplified_youtube_url:
        simplified_youtube_url = strip_argument_from_youtube_url(simplified_youtube_url, "&index")

    return simplified_youtube_url


def strip_argument_from_youtube_url(url, argument):
    """
    This strips an argument out of a YouTube URL.

    :param url:     A full youtube URL.
    :param argument:    The parameter to strip out.
                        If you need to strip out an '&' symbol then that MUST be included when passing it to this method.
    :return:    The URL without the argument.
    """

    first_search = r".+?(?=" + argument + r")"
    first_half = re.search(first_search, url).group(0)

    # NOTE: Sometimes the 2nd half will be empty (None)
    # EXAMPLE: https://www.youtube.com/watch?v=CqqvzVblbsA&feature=youtu.be
    second_search = r"(?<=" + argument + r"=).*&(.*)"
    second_half = None
    if re.search(second_search, url) is not None:
        second_half = re.search(second_search, url).group(0)

    return "".join([first_half, second_half]) if second_half else first_half


def determine_download_command(simplified_youtube_url, download_playlist_yes):
    """
    Figures out the correct youtube-dl command to run.

    :param simplified_youtube_url:  A YouTube URL with all the extra stuff stripped out of it.
    :param download_playlist_yes:   A Boolean to determine if the full playlist should be downloaded (if applicable).
    :return:    A string with the correct download command.
    """
    if len(sys.argv) > 1 and sys.argv[1] == "mp3":
        if download_playlist_yes:
            command = youtube_dl_loc + " -f best --extract-audio --audio-format mp3 --yes-playlist \"" + simplified_youtube_url + "\" && exit"
        else:
            command = youtube_dl_loc + " -f best --extract-audio --audio-format mp3 " + simplified_youtube_url + " && exit"
    else:
        if download_playlist_yes:
            command = youtube_dl_loc + " -i -f best[ext=mp4]/best --yes-playlist \"" + simplified_youtube_url + "\" && exit"
        else:
            if "&list" in simplified_youtube_url:
                command = youtube_dl_loc + " -f best[ext=mp4]/best " + strip_argument_from_youtube_url(
                    simplified_youtube_url, "&list") + " && exit"
            else:
                command = youtube_dl_loc + " -f best[ext=mp4]/best " + simplified_youtube_url + " && exit"
    return command


def get_video_titles(command, google_drive_files):
    """
    Gets the title of the file(s) to be downloaded.
    If the file(s) already exists in Google Drive then this method will ask for user input to handle the situation.

    :param command: A youtube-dl command formatted to only get the video title instead of doing a download.
    :param google_drive_files:  A list of the files in the main directory of my Google Drive.
    :return:    The title(s) of the video(s) to be downloaded as a list.
    """

    # Set a flag that can be toggled if we need to kill the script.
    redownload_videos = None
    video_titles = []

    # Get the video title(s) for the file(s) we are downloading.
    for video_title in run_win_cmd(command):
        video_title = video_title.strip()

        # Colons are not valid in file names in Windows so youtube-dl changes them and we must do the same.
        video_titles.append(video_title.replace(":", " -"))

        # Print the video title(s)
        print("VIDEO TITLE: " if len(video_titles) == 1 else "VIDEO TITLES: ")
        print(video_titles[(len(video_titles) - 1)])

        # If our download already exists, handle the situation.
        if video_title in google_drive_files and redownload_videos is None:
            while True:
                choice = input("We have detected that this file has already been downloaded to " + str(
                    final_destination_dir) + ". Do you want to download it again? (y/n)").lower()
                if choice == "y" or choice == "yes":
                    redownload_videos = True
                    break  # Continue running the script in the normal way as if none of this happened.
                elif choice == "n" or choice == "no":
                    print("\nThis script will now terminate.")
                    exit(0)
                else:
                    print("That didn't work. Please try again.\n")
    return video_titles


def run_youtube_dl_download(command):
    """
    This pipes a youtube-dl commmand into run_win_cmd().
    The purpose of running download commands this way is to be able to catch and handle errors.

    :param command: A youtube-dl command to download a video from a YouTube URL.
    :return:    None
    """

    merge_required = False
    global output_filepaths
    for line in run_win_cmd(command):
        line = line.strip()  # Strip off \n from each line
        print(line)
        if "WARNING: Requested formats are incompatible for merge and will be merged into" in line:
            merge_required = True
        if "ERROR: Did not get any data blocks" in line:
            # EXAMPLE URL: https://www.youtube.com/watch?v=9YXVvr44Hwc
            raise DataBlocksError("youtube-dl failed to get data blocks. Try downloading another format.")

        if "[download] Destination: " in line and merge_required is False:
            if re.search(r".f[0-9]{3}", line) is not None:
                merge_required = True
            else:
                output_filepaths.append(os.path.realpath(line.split("[download] Destination: ")[1]))
        if "[ffmpeg] Merging formats into" in line:
            output_filepaths.append(
                os.path.realpath(line.split("\"")[1]))  # Index 1 in this will give us the filename.
        if "has already been downloaded" in line:
            output_filepaths.append(
                os.path.realpath(line.split("[download]")[1].strip().split(" has already")[0].strip()))
    if not output_filepaths:
        raise Exception("ERROR: command ran but no output file was detected.")


def run_win_cmd(command):
    """
    Runs a command in a new cmd window.
    This function ONLY works on Windows OS.
    The values output in the CMD window are now piped through a generator.

    :param command: The command to run.
    """

    print(str(command) + "\n")
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


class DataBlocksError(Exception):
    """
    This is a custom Exception class to handle situations where youtube-dl fails to get data blocks.
    """
    pass


if __name__ == "__main__":
    main()
