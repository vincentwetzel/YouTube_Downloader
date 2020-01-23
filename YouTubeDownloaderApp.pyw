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


# SAMPLE URLS:
# https://www.youtube.com/watch?v=KEB16y1zBgA&list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO
# https://youtu.be/KEB16y1zBgA?list=PLdZ9Lagj8np1dOb8DrHcNkDid9uII9etO&t=1
# https://www.youtube.com/watch?time_continue=1661&v=EYDwHSGgkm8
# https://www.youtube.com/watch?v=CqqvzVblbsA&feature=youtu.be

class YouTubeDownloaderApp:
    def __init__(self):
        # Constants
        self.COMPLETED_DOWNLOADS_DIR = None
        """The directory that completed downloads will be moved to"""
        self.DOWNLOAD_TEMP_LOC = None
        """The temporary location that active downloads are put in"""
        self.UPDATE_INTERVAL = 100
        self.PROGRESS_BAR_LENGTH = 200

        # GUI variables
        self.root_tk: tkinter.Tk = tkinter.Tk()
        self.root_tk.title("YouTube Downloader by Vincent Wetzel")
        self.root_tk.geometry("700x500")
        self.root_tk.resizable(False, False)

        self.notebook: tkinter.ttk.Notebook = None
        self.frames: List[tkinter.Frame] = list()
        self.progress_bars_list: List[tkinter.ttk.Progressbar] = list()
        self.download_names_labels_list: List[str] = list()

        # Threading Variables
        self.threads: List[threading.Thread] = list()
        self.thread_monitoring_active = False
        self.maximum_simultaneous_downloads: tkinter.IntVar = tkinter.IntVar(master=self.root_tk, value=2)

        # Download management variables
        self.downloads_queue: deque = deque()
        self.download_objs_list: List[YouTubeDownloader] = list()

        self.download_type: tkinter.StringVar = tkinter.StringVar(value="Video")
        """False is MP4, True is mp3"""

        self.init_gui()
        self.init_settings()

        self.root_tk.mainloop()

    def add_dls_to_queue(self, url: str) -> None:
        """
        Adds a URL to the download queue and attempts to start it.
        :type url: A URL to be added to the download queue.
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
            playlist_yes = tkinter.messagebox.askyesno(title="Download Playlist",
                                                       message="Do you want to download this entire playlist?")
            if playlist_yes:
                matches = []
                for line in YouTubeDownloader.run_win_cmd("youtube-dl --flat-playlist --dump-json \"" + url + "\""):
                    if line[0] == "{":
                        line = line.strip('\n')
                        search_result = re.search(r'\"url\": \"(.*?)\"', line)
                        if search_result:
                            matches.append(search_result.group(1))
                for match in matches:
                    self.add_dls_to_queue("https://www.youtube.com/watch?v=" + match)
                return

        self.downloads_queue.append(url)
        # Append the new download to the downloads queue
        logging.info(url + " has been added to the download queue")
        logging.info("current downloads queue: " + str(self.downloads_queue))

        self.notebook.select(self.frames[1])

        # If there is room for another thread, start that download.
        if len(self.threads) < self.maximum_simultaneous_downloads.get():
            self.start_download_thread()

    def start_download_thread(self) -> None:
        """
        Spins up a new thread to start a download.
        :return: None
        """

        # Spin up a thread and launch the download

        url = self.downloads_queue.pop()
        download_obj = YouTubeDownloader(url, self.DOWNLOAD_TEMP_LOC,
                                         self.COMPLETED_DOWNLOADS_DIR,
                                         False if self.download_type.get() == "Video" else True)
        self.download_objs_list.append(download_obj)
        self.threads.append(threading.Thread(target=download_obj.run_yt_download))
        self.threads[-1].daemon = True  # Closing the program will kill this thread
        self.threads[-1].start()

        self.progress_bars_list.append(
            tkinter.ttk.Progressbar(master=self.frames[1], orient="horizontal",
                                    variable=self.download_objs_list[-1].download_progress_string_var,
                                    length=self.PROGRESS_BAR_LENGTH))

        self.download_names_labels_list.append(tkinter.ttk.Label(self.frames[1], textvariable=self.download_objs_list[
            -1].video_title).grid(column=0, row=len(self.progress_bars_list) - 1,
                                  sticky=(tkinter.N, tkinter.W)))

        self.progress_bars_list[-1].grid(column=1, row=len(self.progress_bars_list) - 1, sticky=(tkinter.N, tkinter.E))

        # Initiate monitoring
        if not self.thread_monitoring_active:
            self.thread_monitoring_active = True
            self.root_tk.after(self.UPDATE_INTERVAL, lambda: self.update_threads())

    def update_threads(self) -> None:
        """
        Monitors all running threads and updates important program values
        :return: None
        """
        # Clear finished threads
        for idx, thread in enumerate(list(self.threads)):
            if thread.is_alive() is False:
                del self.threads[idx]
                logging.debug("threads[" + str(idx) + "] has been deleted.")

        # If we do not have all available threads running and there are queued downloads then launch a new thread.
        if len(self.threads) < self.maximum_simultaneous_downloads.get() and self.downloads_queue:
            self.start_download_thread()

        if self.threads:
            # If threads are active, recursively call check_threads()
            self.root_tk.after(self.UPDATE_INTERVAL, lambda: self.update_threads())
        else:
            logging.debug("check_threads() monitoring ended.")
            self.thread_monitoring_active = False

    def download_text_var_selected(self, text_var: tkinter.Text) -> None:
        """
        If the download Text field is selected when a valid YouTube URL is on the clipboard,
        copy that value into the entry's StringVar.
        :param text_var: The Entry associated with this entry.
        :return: None
        """
        clipboard_url = get_clipboard_string()
        if self.verify_is_youtube_url(clipboard_url):
            text_var.delete(1.0, "end")
            text_var.insert(1.0, clipboard_url)

    def init_gui(self):
        self.notebook = tkinter.ttk.Notebook(self.root_tk)

        for _ in range(3):
            self.frames.append(tkinter.ttk.Frame(self.notebook, padding="3 3 12 12"))

        self.notebook.add(self.frames[0], text="Download")
        self.notebook.add(self.frames[1], text="Queue")
        self.notebook.add(self.frames[2], text="Settings")

        self.notebook.grid()
        self.notebook.enable_traversal()

        # Text box
        download_urls_text_var = tkinter.Text(self.frames[0], width=50, height=5)
        download_urls_text_var.grid(column=0, row=0, sticky=(tkinter.W, tkinter.N, tkinter.S), columnspan=11)
        download_urls_text_var.bind('<FocusIn>',
                                    lambda _: self.download_text_var_selected(download_urls_text_var))

        # Download button
        tkinter.ttk.Button(self.frames[0], text="Download",
                           command=lambda: self.add_dls_to_queue(
                               download_urls_text_var.get(1.0, "end").strip())).grid(
            column=11, row=0, sticky=(tkinter.E, tkinter.N, tkinter.S))

        tkinter.ttk.Label(self.frames[0], text="Simultaneous Downloads: ").grid(column=0, row=10,
                                                                                sticky=(tkinter.E,))
        combobox = tkinter.ttk.Combobox(self.frames[0], justify="left",
                                        textvariable=self.maximum_simultaneous_downloads, state="readonly",
                                        values=[1, 2, 3, 4])
        combobox.grid(column=1, row=10, sticky=('W',))

        tkinter.ttk.Label(self.frames[0], text="Download type: ").grid(column=10, row=10, sticky=(tkinter.E,))
        tkinter.Radiobutton(self.frames[0], text="Video", variable=self.download_type,
                            value="Video").grid(column=11, row=10, sticky=(tkinter.W,))
        tkinter.Radiobutton(self.frames[0], text="Audio", variable=self.download_type,
                            value="Audio").grid(column=11, row=11, sticky=(tkinter.W,))

    def init_settings(self):
        settings_lines = []
        need_to_rewrite_settings_ini = False

        if os.path.isfile("settings.ini"):
            with open("settings.ini", 'r') as f:
                settings_lines = f.readlines()

        for line in settings_lines:
            line = line.strip()
            if "completed_downloads_directory=" in line:
                self.COMPLETED_DOWNLOADS_DIR = os.path.realpath(line.split("=")[1])
                if not os.path.exists(self.COMPLETED_DOWNLOADS_DIR):
                    need_to_rewrite_settings_ini = True
                    tkinter.messagebox.showinfo(title="settings.ini error",
                                                message="settings.ini contains an invalid value "
                                                        "for completed_downloads_directory. "
                                                        "Please select an output folder for completed downloads.")
                    self.COMPLETED_DOWNLOADS_DIR = tkinter.filedialog.askdirectory(
                        title="Choose a directory for completed downloads")
            elif "temporary_downloads_directory=" in line:
                self.DOWNLOAD_TEMP_LOC = os.path.realpath(line.split("=")[1])
                if not os.path.exists(self.DOWNLOAD_TEMP_LOC):
                    need_to_rewrite_settings_ini = True
                    tkinter.messagebox.showinfo(title="settings.ini error",
                                                message="settings.ini contains an invalid value "
                                                        "for temporary_downloads_directory. "
                                                        "Please select a temporary folder for downloads.")
                    self.DOWNLOAD_TEMP_LOC = tkinter.filedialog.askdirectory(
                        title="Choose a temporary directory for ongoing downloads")
            elif line == "":
                pass
            else:
                need_to_rewrite_settings_ini = True
        if self.COMPLETED_DOWNLOADS_DIR is None:
            need_to_rewrite_settings_ini = True
            tkinter.messagebox.showinfo(title="Choose a download directory",
                                        message="Please select a download directory to use for completed downloads.")
            self.COMPLETED_DOWNLOADS_DIR = tkinter.filedialog.askdirectory(
                title="Choose a directory for completed downloads")
        if self.DOWNLOAD_TEMP_LOC is None:
            need_to_rewrite_settings_ini = True
            tkinter.messagebox.showinfo(title="Choose a temporary download directory",
                                        message="Please choose a temporary directory to be used for ongoing downloads")
            self.DOWNLOAD_TEMP_LOC = tkinter.filedialog.askdirectory(
                title="Choose a temporary directory for ongoing downloads")

        if need_to_rewrite_settings_ini:
            logging.debug("Rewriting settings.ini...")
            with open("settings.ini", 'w') as newfile:
                newfile.write("completed_downloads_directory=" + self.COMPLETED_DOWNLOADS_DIR + "\n")
                newfile.write("temporary_downloads_directory=" + self.DOWNLOAD_TEMP_LOC)

    @staticmethod
    def verify_is_youtube_url(url: str) -> bool:
        """
        Verifies that a given URL is a YouTube URL.
        :param url: The URL to check
        :return: True if it is a YouTube URL, False if it is not
        """
        if (url.startswith("https://www.youtube.com/watch?") or url.startswith("https://youtu.be/")) and " " not in url:
            return True
        return False


def get_clipboard_string() -> str:
    """
    Helper method to quickly get the string on the clipboard
    :return:
    """
    win32clipboard.OpenClipboard()
    clipboard_str = str((win32clipboard.GetClipboardData(win32con.CF_TEXT)).decode(
        "utf-8"))  # must decode from bytes to string
    win32clipboard.CloseClipboard()
    return clipboard_str


if __name__ == "__main__":
    YouTubeDownloaderApp()
