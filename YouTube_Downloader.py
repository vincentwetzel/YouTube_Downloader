import win32clipboard
import win32com
import subprocess
import win32con
import win32com.client
import os
import shutil

youtube_dl_loc = os.path.realpath("C:/Users/vince/youtube-dl.exe")
google_drive_dir = os.path.realpath("E:/Google Drive (vincentwetzel3@gmail.com)")


def main():
    shell = win32com.client.Dispatch("WScript.Shell")

    # Dump clipboard data into a variable
    win32clipboard.OpenClipboard()
    clipboard_youtube_url = str((win32clipboard.GetClipboardData(win32con.CF_TEXT)).decode(
        "utf-8"))  # must decode from bytes to string
    win32clipboard.CloseClipboard()
    if (clipboard_youtube_url.startswith(
            "https://www.youtube.com/watch?v=") is False and clipboard_youtube_url.startswith(
        "https://youtu.be/") is False) or " " in clipboard_youtube_url:
        raise Exception("The value on the clipboard is not a YouTube URL.")

    # Strip out extra stuff in URL
    # SAMPLE URLS:
    # https://www.youtube.com/watch?v=KEB16y1zBgA&list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO
    # https://youtu.be/KEB16y1zBgA?list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO&t=1
    is_playlist = False
    if "&list=" in clipboard_youtube_url:
        user_input = input("Do you want to download this whole playlist? (y/n): ")
        if user_input.lower() == "y" or user_input.lower() == "yes":
            is_playlist = True
    if "?t=" in clipboard_youtube_url:
        clipboard_youtube_url = clipboard_youtube_url.split("?t=")[0]
    if "&t=" in clipboard_youtube_url:
        clipboard_youtube_url = clipboard_youtube_url.split("&t=")[0]

    # Run command to download the file
    global youtube_dl_loc
    if is_playlist:
        command = youtube_dl_loc + " -i --yes-playlist \"" + clipboard_youtube_url + "\" && exit"
    else:
        command = youtube_dl_loc + " " + clipboard_youtube_url + " && exit"
    output_file = run_youtube_dl_cmd(command)

    # Put the downloaded file in its proper location
    output_file_size = os.path.getsize(output_file)
    if output_file_size < 104857600:  # 100 MB
        # Use shutil to make sure the file is replaced if it already exists.
        shutil.move(output_file, os.path.join(google_drive_dir, os.path.basename(output_file)))
        print(str(output_file) + " moved to directory " + str(google_drive_dir))
    else:
        print("This file is quite large so we are not moving it to Google Drive.")


def run_youtube_dl_cmd(cmd):
    """
    Runs a command in a new cmd window. This ONLY works on Windows OS.

    :param cmd: The command to run.
    :return:    The path of the file downloaded from YouTube.
    """
    print("INPUT COMMAND: " + str(cmd) + "\n")
    result = []
    process = subprocess.Popen(cmd,
                               shell=True,
                               # stdout=subprocess.PIPE, # Prevents output from showing in CMD window.
                               # stderr=subprocess.PIPE  # Prevents output from showing in CMD window.
                               )

    output, error = process.communicate()  # Prevents cout and cerr from locking up cmd.
    if output:
        for line in output:
            result.append(str(line))  # In case we need to print this
    errcode = process.returncode  # 0 if completed

    if errcode != 0:  # NOTE: This was originally None but that is incorrect
        raise Exception('cmd %s failed, see above for details', cmd)

    # Figure out the output file and move
    output_filepath = None
    lines = output.split(b'\n')
    for line in lines:  # Decode converts from bytestring to string.
        line = line.decode()
        print(line)
        if "Merging formats into" in line:
            output_filepath = os.path.realpath(line.split("\"")[1])  # Index 1 in this will give us the filename.
        if "has already been downloaded and merged" in line:
            output_filepath = os.path.realpath(line.split("[download]")[1].strip().split(" has already")[0].strip())
    if output_filepath is None:
        raise Exception("ERROR: cmd ran but no output file was detected.")

    return output_filepath


if __name__ == "__main__":
    main()
