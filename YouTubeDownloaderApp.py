#! /usr/bin/env python3

# TODO: Handle error: "ERROR: This video is unavailable." which occurs for scheduled livestreams

# NOTE TO USER: youtube-dl must be in the system PATH for this to run on Windows.
# I recommend installing youtube-dl with PIP.
from collections import deque
from typing import List
import win32clipboard
import win32con
import os
import sys
import tkinter
import tkinter.ttk
import tkinter.messagebox
import tkinter.filedialog
import logging
import threading
from YouTubeDownload import YouTubeDownloader
import re

# NOTE TO USER: use logging.DEBUG for testing, logging.CRITICAL for runtime
logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

COMPLETED_DOWNLOADS_DIR = None
"""The directory that completed downloads will be moved to"""
DOWNLOAD_TEMP_LOC = None
"""The temporary location that active downloads are put in"""

PROGRESS_BAR_LENGTH = 200

maximum_simultaneous_downloads: tkinter.IntVar = None

UPDATE_INTERVAL = 100
thread_monitoring_active = False

root_tk: tkinter.Tk = None
notebook: tkinter.ttk.Notebook = None
frames: List[tkinter.Frame] = list()

output_filepaths = list()
"""This is a list of lists of full filepaths for output files
Each download is one index of the list.
Playlists will spawn sublists."""

threads: List[threading.Thread] = list()
download_names: List[tkinter.StringVar] = list()

downloads_queue: deque = deque()
download_objs_list: List[YouTubeDownloader] = list()

progress_bars_list: List[tkinter.ttk.Progressbar] = list()
download_names_labels_list: List[str] = list()


# SAMPLE URLS:
# https://www.youtube.com/watch?v=KEB16y1zBgA&list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO
# https://youtu.be/KEB16y1zBgA?list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO&t=1
# https://www.youtube.com/watch?time_continue=1661&v=EYDwHSGgkm8
# https://www.youtube.com/watch?v=CqqvzVblbsA&feature=youtu.be

def main():
    global root_tk
    root_tk = tkinter.Tk()
    root_tk.title("YouTube Downloader by Vincent Wetzel")
    root_tk.geometry("700x500")
    root_tk.resizable(False, False)

    init_gui()
    init_settings()

    root_tk.mainloop()

    # Done!


def add_dls_to_queue(url: str) -> None:
    """
    Adds a URL to the download queue and attempts to start it.
    :return:
    """
    # Check to make sure the URL is valid
    if (url.startswith("https://www.youtube.com/watch?") is False and url.startswith(
            "https://youtu.be/") is False) or " " in url:
        logging.debug("Invalid URL: " + url)
        tkinter.messagebox.showerror(title="Error: Invalid URL",
                                     message="The value on the clipboard is not a valid YouTube URL.")
        return
    if YouTubeDownloader.check_to_see_if_playlist(url):
        choice = tkinter.messagebox.askyesno(title="Download Playlist",
                                             message="Do you want to download this entire playlist?")
        if choice:
            matches = []
            for line in YouTubeDownloader.run_win_cmd("youtube-dl --flat-playlist --dump-json \"" + url + "\""):
                if line[0] == "{":
                    line = line.strip('\n')
                    search_result = re.search(r'\"url\": \"(.*?)\"', line)
                    if search_result:
                        matches.append(search_result.group(1))
            for match in matches:
                add_dls_to_queue("https://www.youtube.com/watch?v=" + match)
            return
        else:
            # Continue on with the download.
            pass

    global downloads_queue
    global threads
    downloads_queue.append(url)
    # Append the new download to the downloads queue
    logging.info(url + " has been added to the download queue")
    logging.info("current downloads queue: " + str(downloads_queue))

    global frames
    global notebook
    notebook.select(frames[1])

    # If there is room for another thread, start that download.
    global maximum_simultaneous_downloads
    if len(threads) < maximum_simultaneous_downloads.get():
        start_download_thread()


def start_download_thread() -> None:
    """
    Spins up a new thread to start a download.
    :return: None
    """

    # Spin up a thread and launch the download
    global threads
    global root_tk

    global downloads_queue
    url = downloads_queue.pop()

    global COMPLETED_DOWNLOADS_DIR
    global DOWNLOAD_TEMP_LOC
    download_obj = YouTubeDownloader(url, DOWNLOAD_TEMP_LOC,
                                     COMPLETED_DOWNLOADS_DIR)  # TODO: Add mp3 handling here via ComboBox
    global download_objs_list
    download_objs_list.append(download_obj)
    threads.append(threading.Thread(target=download_obj.run_yt_download))
    threads[-1].daemon = True  # Closing the program will kill this thread
    threads[-1].start()

    global progress_bars_list
    global frames
    global PROGRESS_BAR_LENGTH

    # Create custom style just for this progresbar
    progress_bars_list.append(
        tkinter.ttk.Progressbar(master=frames[1], orient="horizontal",
                                variable=download_objs_list[-1].download_progress_string_var,
                                length=PROGRESS_BAR_LENGTH))

    global download_names_labels_list
    download_names_labels_list.append(tkinter.ttk.Label(frames[1], textvariable=download_objs_list[
        -1].video_title).grid(column=0, row=len(progress_bars_list) - 1,
                              sticky=(tkinter.N, tkinter.W)))

    progress_bars_list[-1].grid(column=1, row=len(progress_bars_list) - 1, sticky=(tkinter.N, tkinter.E))

    # Initiate monitoring
    global thread_monitoring_active
    if not thread_monitoring_active:
        global UPDATE_INTERVAL
        thread_monitoring_active = True
        root_tk.after(UPDATE_INTERVAL, lambda: update_threads())


def update_threads() -> None:
    """
    Monitors all running threads and updates important program values
    :return: None
    """
    global threads

    # Clear finished threads
    for idx, thread in enumerate(list(threads)):
        if thread.is_alive() is False:
            del threads[idx]

    # If we do not have all available threads running and there are queued downloads then launch a new thread.
    global maximum_simultaneous_downloads
    if len(threads) < maximum_simultaneous_downloads.get() and downloads_queue:
        start_download_thread()

    if threads:
        # If threads are active, recursively call check_threads()
        global root_tk
        global UPDATE_INTERVAL
        root_tk.after(UPDATE_INTERVAL, lambda: update_threads())
    else:
        logging.debug("check_threads() monitoring ended.")
        global thread_monitoring_active
        thread_monitoring_active = False


def download_text_var_selected(textVar: tkinter.Text) -> None:
    """
    If the download Text field is selected when a valid YouTube URL is on the clipboard,
    copy that value into the entry's StringVar.
    :param textVar: The Entry associated with this entry.
    :return: None
    """
    clipboard_url = get_clipboard_string()
    if veryify_is_youtube_url(clipboard_url):
        textVar.delete(1.0, "end")
        textVar.insert(1.0, clipboard_url)


def get_clipboard_string() -> None:
    """
    Helper method to quickly get the string on the clipboard
    :return: None
    """
    win32clipboard.OpenClipboard()
    clipboard_str = str((win32clipboard.GetClipboardData(win32con.CF_TEXT)).decode(
        "utf-8"))  # must decode from bytes to string
    win32clipboard.CloseClipboard()
    return clipboard_str


def veryify_is_youtube_url(url: str) -> bool:
    """
    Verifies that a given URL is a YouTube URL.
    :param url: The URL to check
    :return: True if it is a YouTube URL, False if it is not
    """
    if (url.startswith("https://www.youtube.com/watch?") or url.startswith("https://youtu.be/")) and " " not in url:
        return True
    return False


def init_gui():
    global root_tk
    global notebook
    notebook = tkinter.ttk.Notebook(root_tk)

    for _ in range(3):
        frames.append(tkinter.ttk.Frame(notebook, padding="3 3 12 12"))

    notebook.add(frames[0], text="Download")
    notebook.add(frames[1], text="Queue")
    notebook.add(frames[2], text="Settings")

    notebook.grid()
    notebook.enable_traversal()

    # Text box
    download_urls_textVar = tkinter.Text(frames[0], width=50, height=5)
    download_urls_textVar.grid(column=0, row=0, sticky=(tkinter.W, tkinter.N, tkinter.S), columnspan=4)
    download_urls_textVar.bind('<FocusIn>', lambda _: download_text_var_selected(download_urls_textVar))

    # Download button
    tkinter.ttk.Button(frames[0], text="Download",
                       command=lambda: add_dls_to_queue(download_urls_textVar.get(1.0, "end").strip())).grid(column=10,
                                                                                                             row=0,
                                                                                                             sticky=(
                                                                                                                 tkinter.E,
                                                                                                                 tkinter.N,
                                                                                                                 tkinter.S))

    tkinter.ttk.Label(frames[0], text="Simultaneous Downloads: ").grid(column=0, row=10,
                                                                       sticky=(tkinter.E))
    global maximum_simultaneous_downloads
    maximum_simultaneous_downloads = tkinter.IntVar()
    combobox = tkinter.ttk.Combobox(frames[0], justify="left",
                                    textvariable=maximum_simultaneous_downloads, state="readonly",
                                    values=[1, 2, 3, 4])
    combobox.set(2)
    combobox.grid(column=1, row=10, sticky=('W',))


def init_settings():
    settings_lines = []
    need_to_rewrite_settings_ini = False

    if os.path.isfile("settings.ini"):
        with open("settings.ini", 'r') as f:
            settings_lines = f.readlines()

    global COMPLETED_DOWNLOADS_DIR
    global DOWNLOAD_TEMP_LOC
    for line in settings_lines:
        line = line.strip()
        if "completed_downloads_directory=" in line:
            COMPLETED_DOWNLOADS_DIR = os.path.realpath(line.split("=")[1])
            if not os.path.exists(COMPLETED_DOWNLOADS_DIR):
                need_to_rewrite_settings_ini = True
                tkinter.messagebox.showinfo(title="settings.ini error",
                                            message="settings.ini contains an invalid value for completed_downloads_directory. "
                                                    "Please select an output folder for completed downloads.")
                COMPLETED_DOWNLOADS_DIR = tkinter.filedialog.askdirectory(
                    title="Choose a directory for completed downloads")
        elif "temporary_downloads_directory=" in line:
            DOWNLOAD_TEMP_LOC = os.path.realpath(line.split("=")[1])
            if not os.path.exists(DOWNLOAD_TEMP_LOC):
                need_to_rewrite_settings_ini = True
                tkinter.messagebox.showinfo(title="settings.ini error",
                                            message="settings.ini contains an invalid value for temporary_downloads_directory. "
                                                    "Please select a temporary folder for downloads.")
                DOWNLOAD_TEMP_LOC = tkinter.filedialog.askdirectory(
                    title="Choose a temporary directory for ongoing downloads")
        elif line == "":
            pass
        else:
            need_to_rewrite_settings_ini = True
    if COMPLETED_DOWNLOADS_DIR is None:
        need_to_rewrite_settings_ini = True
        tkinter.messagebox.showinfo(title="Choose a download directory",
                                    message="Please select a download directory to use for completed downloads.")
        COMPLETED_DOWNLOADS_DIR = tkinter.filedialog.askdirectory(
            title="Choose a directory for completed downloads")
    if DOWNLOAD_TEMP_LOC is None:
        need_to_rewrite_settings_ini = True
        tkinter.messagebox.showinfo(title="Choose a temporary download directory",
                                    message="Please choose a temporary directory to be used for ongoing downloads")
        DOWNLOAD_TEMP_LOC = tkinter.filedialog.askdirectory(
            title="Choose a temporary directory for ongoing downloads")


    if need_to_rewrite_settings_ini:
        logging.debug("Rewriting settings.ini...")
        with open("settings.ini", 'w') as newfile:
            newfile.write("completed_downloads_directory=" + COMPLETED_DOWNLOADS_DIR + "\n")
            newfile.write("temporary_downloads_directory=" + DOWNLOAD_TEMP_LOC)

if __name__ == "__main__":
    main()
