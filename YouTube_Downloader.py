import win32clipboard
import subprocess
import win32con
import os
import shutil

youtube_dl_loc = os.path.realpath("C:/Users/vince/youtube-dl.exe")
final_destination_dir = os.path.realpath("E:/Google Drive (vincentwetzel3@gmail.com)")


def main():
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
    if "&feature=" in clipboard_youtube_url:
        clipboard_youtube_url = clipboard_youtube_url.split("&feature=")[0]
    if "&list=" in clipboard_youtube_url:
        user_input = input("Do you want to download this whole playlist? (y/n): ")
        if user_input.lower() == "y" or user_input.lower() == "yes":
            is_playlist = True
    if "?t=" in clipboard_youtube_url:
        clipboard_youtube_url = clipboard_youtube_url.split("?t=")[0]
    if "&t=" in clipboard_youtube_url:
        clipboard_youtube_url = clipboard_youtube_url.split("&t=")[0]

    # Run a command to see if the file already exists and we should skip the download.
    # NOTE: This will only produce 1 line of output
    command = youtube_dl_loc + " --skip-download --get-title " + clipboard_youtube_url
    print()  # Formatting for prettier output
    for video_title in run_win_cmd(command):
        video_title = video_title.strip()

        # Colons are not valid in file names in Windows so youtube-dl changes them and we must do the same.
        if ":" in video_title:
            video_title = video_title.replace(":", " -")

        print("VIDEO TITLE: " + video_title + "\n")

        # Get a list of the files in the final output directory
        google_drive_files = os.listdir(final_destination_dir)
        for i, file in enumerate(google_drive_files):
            google_drive_files[i] = os.path.splitext(os.path.basename(file))[0].strip()

        # If our download already exists, handle the situation.
        if video_title in google_drive_files:
            while True:
                choice = input("We have detected that this file has already been downloaded to " + str(
                    final_destination_dir) + ". Do you want to download it again? (y/n)").lower()
                if choice == "y" or choice == "yes":
                    break  # Continue running the script in the normal way as if none of this happened.
                elif choice == "n" or choice == "no":
                    print("\nThis script will now terminate.")
                    exit(0)
                else:
                    print("That didn't work. Please try again.\n")

    # Figure out the formatting of the DOWNLOAD command to run in cmd
    if is_playlist:
        command = youtube_dl_loc + " -i --yes-playlist \"" + clipboard_youtube_url + "\" && exit"
    else:
        command = youtube_dl_loc + " " + clipboard_youtube_url + " && exit"

    # Run command to download the file
    # The stdout values will be returned via a generator.
    output_filepaths = []
    for line in run_win_cmd(command):
        line = line.strip()  # Strip off \n from each line
        print(line)
        if "[ffmpeg] Merging formats into" in line:
            output_filepaths.append(
                os.path.realpath(line.split("\"")[1]))  # Index 1 in this will give us the filename.
        if "has already been downloaded and merged" in line:
            output_filepaths.append(
                os.path.realpath(line.split("[download]")[1].strip().split(" has already")[0].strip()))
    if not output_filepaths:
        raise Exception("ERROR: command ran but no output file was detected.")

    # Put the downloaded file in its proper location
    # For playlists, leave them in the default download directory.
    if not is_playlist:
        for file in output_filepaths:
            output_file_size = os.path.getsize(file)
            if output_file_size < 104857600:  # 100 MB
                # Use shutil to make sure the file is replaced if it already exists.
                shutil.move(file, os.path.join(final_destination_dir, os.path.basename(file)))
                print("\n" + str(file) + " moved to directory " + str(final_destination_dir))
            if output_file_size > 104857600:  # 100 MB
                print("\nThis file is quite large so we are not moving it to Google Drive.")

    # Done!


def run_win_cmd(command):
    """
    Runs a command in a new cmd window.
    This function ONLY works on Windows OS.
    The values output in the CMD window are now piped through a generator.

    :param command: The command to run.
    """

    print(str(command) + "\n")
    process = subprocess.Popen(command, shell=True, encoding='utf-8', stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for stdout_line in iter(process.stdout.readline, ""):
        yield stdout_line

    # Once the process has completed, get the return code to see if the process was successful.
    return_code = process.wait()
    if return_code != 0:
        raise Exception('command %s failed, see above for details', command)


if __name__ == "__main__":
    main()
