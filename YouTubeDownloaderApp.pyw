#! /usr/bin/env python3

# TODO: Create a checkbox to allow the app to terminate when downloads complete.

# NOTE TO USER: youtube-dl must be in the system PATH for this to run on Windows.
# I recommend installing youtube-dl with PIP.
from collections import deque
from typing import List
import os
import sys
import tkinter
import tkinter.ttk
import tkinter.messagebox
import tkinter.filedialog
import logging
import threading
from YouTubeDownload import YouTubeDownload
import re

# NOTE TO USER: use logging.DEBUG for testing, logging.CRITICAL for runtime
logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)


class YouTubeDownloaderApp:

    def __init__(self):
        """
        Sets up the app.
        """

        # Constants
        self.COMPLETED_DOWNLOADS_DIR = None
        """The directory that completed downloads will be moved to"""
        self.DOWNLOAD_TEMP_LOC = None
        """The temporary location that active downloads are put in"""
        self.UPDATE_INTERVAL = 100
        """Specifies how often (in milliseconds) the progress bars will update"""
        self.PROGRESS_BAR_LENGTH = 150
        """The size of download progress bars"""
        self.WINDOW_WIDTH = 700
        """App window width"""
        self.WINDOW_HEIGHT = 500
        """App window height"""

        # GUI variables
        self.root_tk: tkinter.Tk = tkinter.Tk()
        self.root_tk.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root_tk.grid_rowconfigure(0, weight=1)
        self.root_tk.grid_columnconfigure(0, weight=1)
        self.root_tk.title("YouTube Downloader by Vincent Wetzel")
        self.root_tk.geometry(str(self.WINDOW_WIDTH) + "x" + str(self.WINDOW_HEIGHT))
        self.root_tk.resizable(False, False)

        self.notebook: tkinter.ttk.Notebook = None
        self.notebook_frames: List[tkinter.Frame] = list()
        self.downloads_queue_canvas: tkinter.Canvas = None
        self.downloads_queue_frame: tkinter.Frame = None
        self.downloads_queue_scrollbar: tkinter.Scrollbar = None
        self.downloads_queue_progress_bars_list: List[tkinter.ttk.Progressbar] = list()
        self.downloads_queue_labels_list: List[tkinter.Label] = list()

        # Threading Variables
        self.threads: List[threading.Thread] = list()
        self.thread_monitoring_active = False
        self.maximum_simultaneous_downloads: tkinter.IntVar = tkinter.IntVar(master=self.root_tk, value=2)

        # Download management variables
        """A deque of objects waiting to be downloaded"""
        self.downloads_queue: deque[YouTubeDownload] = deque()
        """A list of active downloads"""
        self.active_dl_objs_list: List[YouTubeDownload] = list()

        # Other globals
        self.download_type: tkinter.StringVar = tkinter.StringVar(value="Video")
        """False is MP4, True is mp3"""
        self.exit_after_downloads_bool_var: tkinter.BooleanVar = tkinter.BooleanVar()
        self.completed_downloads: int = 0

        # Start the script!
        self.init_gui()
        self.init_settings()
        self.root_tk.mainloop()

    def add_dls_to_queue(self, url: str) -> None:
        """
        Adds a URL to the download queue and attempts to start it.
        :type url: A URL to be added to the download queue.
        :return: None
        """
        if url == "":
            tkinter.messagebox.showerror("No URL entered.", "Please enter a URL to download.")
            return

        # If this URL is already in the downloads queue, ignore it and tell the user
        for dl_obj in list(self.downloads_queue):
            if url == dl_obj.raw_url:
                tkinter.messagebox.showerror(title="ERROR: Download already exists",
                                             message="This download is already in the queue!")
                return

        # If this URL is currently being downloaded, ignore it and tell the user
        for dl_obj in self.active_dl_objs_list:
            if url == dl_obj.raw_url:
                tkinter.messagebox.showerror(title="ERROR: Download already exists",
                                             message="This item is currently being downloaded!")
                return

        # handle playlist URLs
        if YouTubeDownload.check_to_see_if_playlist(url):
            playlist_yes = tkinter.messagebox.askyesno(title="Download Playlist",
                                                       message="Do you want to download this entire playlist?")
            if playlist_yes:
                matches = []
                for line in YouTubeDownload.run_win_cmd("youtube-dl --flat-playlist --dump-json \"" + url + "\""):
                    if line[0] == "{":
                        line = str(line).strip('\n')
                        search_result = re.search(r'\"url\": \"(.*?)\"', line)
                        if search_result:
                            matches.append(search_result.group(1))
                for match in matches:
                    self.add_dls_to_queue("https://www.youtube.com/watch?v=" + match)
                return

        # create YouTubeDownload object to store info about the download
        download_obj = YouTubeDownload(self.root_tk, url, self.DOWNLOAD_TEMP_LOC,
                                       self.COMPLETED_DOWNLOADS_DIR,
                                       False if self.download_type.get() == "Video" else True)

        # Create a GUI Label for the download's name
        self.downloads_queue_labels_list.append(
            tkinter.Label(self.downloads_queue_frame, textvariable=download_obj.video_title, anchor=tkinter.W,
                          justify=tkinter.LEFT, width=70, wraplength=self.WINDOW_WIDTH - self.PROGRESS_BAR_LENGTH - 50))
        self.downloads_queue_labels_list[-1].grid(column=0, row=len(
            self.downloads_queue_progress_bars_list), sticky=(tkinter.W, tkinter.E))
        print(str(self.downloads_queue_labels_list))
        # Create new progress bar for this download
        self.downloads_queue_progress_bars_list.append(
            tkinter.ttk.Progressbar(master=self.downloads_queue_frame, orient="horizontal",
                                    variable=download_obj.download_progress_string_var,
                                    length=self.PROGRESS_BAR_LENGTH))
        self.downloads_queue_progress_bars_list[-1].grid(column=1, row=len(self.downloads_queue_progress_bars_list) - 1,
                                                         sticky=tkinter.E)

        # Append the new download to the downloads queue
        self.downloads_queue.append(download_obj)
        logging.info(url + " has been added to the download queue")

        # Move the GUI to the downloads tab
        self.notebook.select(self.notebook_frames[1])

        # If there are available threads, start the download
        if len(self.threads) < self.maximum_simultaneous_downloads.get():
            self.start_download_thread()

    def start_download_thread(self) -> None:
        """
        Spins up a new thread to start a download.
        :return: None
        """

        # Spin up a thread and launch the download
        download_obj: YouTubeDownload = self.downloads_queue.popleft()
        self.active_dl_objs_list.append(download_obj)
        self.threads.append(threading.Thread(target=download_obj.start_yt_download))
        self.threads[-1].daemon = True  # Closing the program will kill this thread
        self.threads[-1].start()

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
        for idx, thread in enumerate(self.threads):
            if thread.is_alive() is False:
                # Make the Label containing the name of the video being downloaded independent from the download object
                # so it isn't destroyed when the download object is deleted
                self.downloads_queue_labels_list[self.completed_downloads + idx]["text"] = self.active_dl_objs_list[
                    idx].video_title.get()

                # Delete the download thread
                del self.threads[idx]
                logging.debug("threads[" + str(idx) + "] has been deleted.")

                # Delete the download object
                del self.active_dl_objs_list[idx]
                logging.debug("active_dl_objs_list[" + str(idx) + "] has been deleted.")

                # Update the completed downloads counter
                self.completed_downloads += 1

        # If we do not have all available threads running and there are queued downloads then launch a new thread.
        if len(self.threads) < self.maximum_simultaneous_downloads.get() and self.downloads_queue:
            self.start_download_thread()

        # If threads are active, recursively call check_threads()
        if self.threads:
            self.root_tk.after(self.UPDATE_INTERVAL, lambda: self.update_threads())
        # Else, all downloads have completed
        else:
            logging.debug("check_threads() monitoring ended.")
            logging.info("All downloads have completed!")
            self.thread_monitoring_active = False
            if self.exit_after_downloads_bool_var.get():
                self.root_tk.destroy()

    def download_text_field_selected(self, text_entry: tkinter.Text) -> None:
        """
        If the download Text field is selected when a valid download URL is on the clipboard,
        copy that value into the entry's StringVar.
        :param text_entry: The Entry associated with this entry.
        :return: None
        """
        clipboard_val = self.root_tk.clipboard_get()
        if isinstance(clipboard_val, str):
            text_entry.delete(1.0, "end")
            text_entry.insert(1.0, clipboard_val)

    def init_gui(self) -> None:
        """
        Initializes the app's GUI.
        :return: None
        """
        self.notebook = tkinter.ttk.Notebook(self.root_tk)

        # Init Frames
        for _ in range(3):
            self.notebook_frames.append(tkinter.ttk.Frame(self.notebook, padding="3 3 12 12"))

        self.notebook.add(self.notebook_frames[0], text="Download")
        self.notebook.add(self.notebook_frames[1], text="Queue")
        self.notebook.add(self.notebook_frames[2], text="Settings")

        self.notebook.grid(sticky=(tkinter.N, tkinter.S, tkinter.W, tkinter.E))
        # self.notebook.grid_rowconfigure(0, weight=0)
        # self.notebook.grid_columnconfigure(0, weight=0)
        self.notebook.enable_traversal()

        # Queue Frame: other GUI elements
        self.downloads_queue_canvas = tkinter.Canvas(self.notebook_frames[1], borderwidth=0)
        self.downloads_queue_canvas.bind_all("<MouseWheel>", self.on_mousewheel)
        self.downloads_queue_frame = tkinter.Frame(self.downloads_queue_canvas)
        self.downloads_queue_frame.grid(sticky="nswe")
        self.downloads_queue_frame.grid_columnconfigure(0, weight=1)
        self.downloads_queue_scrollbar = tkinter.Scrollbar(self.notebook_frames[1], orient="vertical",
                                                           command=self.downloads_queue_canvas.yview)
        self.downloads_queue_canvas.configure(yscrollcommand=self.downloads_queue_scrollbar.set)
        self.downloads_queue_scrollbar.pack(side="right", fill=tkinter.Y)
        self.downloads_queue_canvas.pack(fill=tkinter.BOTH, expand=True)
        self.downloads_queue_canvas.create_window((4, 4), window=self.downloads_queue_frame, anchor="nw")
        self.downloads_queue_frame.bind("<Configure>",
                                        lambda event, canvas=self.downloads_queue_canvas: self.on_frame_configure())

        # Download Frame: Text box to enter download URLs
        download_urls_text_var = tkinter.Text(self.notebook_frames[0], width=50, height=5)
        download_urls_text_var.grid(column=0, row=0, sticky=(tkinter.W, tkinter.N, tkinter.S), columnspan=10)
        download_urls_text_var.bind('<FocusIn>',
                                    lambda _: self.download_text_field_selected(download_urls_text_var))

        # Download Frame: Download button
        tkinter.ttk.Button(self.notebook_frames[0], text="Download",
                           command=lambda: self.add_dls_to_queue(
                               download_urls_text_var.get(1.0, "end").strip())).grid(
            column=10, row=0, sticky=(tkinter.E, tkinter.N, tkinter.S))

        # Download Frame: Simultaneous Downloads
        tkinter.ttk.Label(self.notebook_frames[0], text="Simultaneous Downloads: ").grid(column=1, row=10)
        combobox = tkinter.ttk.Combobox(self.notebook_frames[0], justify="left",
                                        textvariable=self.maximum_simultaneous_downloads, state="readonly",
                                        values=[1, 2, 3, 4]).grid(column=1, row=11)

        # Download Frame: Audio or Video download?
        tkinter.ttk.Label(self.notebook_frames[0], text="Download type: ").grid(column=10, row=10, sticky=(tkinter.E,))
        tkinter.Radiobutton(self.notebook_frames[0], text="Video", variable=self.download_type,
                            value="Video").grid(column=10, row=11, sticky=(tkinter.W,))
        tkinter.Radiobutton(self.notebook_frames[0], text="Audio", variable=self.download_type,
                            value="Audio").grid(column=10, row=12, sticky=(tkinter.W,))

        # Add a Checkbutton so the user can automatically close the app when downloads complete
        tkinter.Label(self.notebook_frames[0], text="Exit after Downloads Complete:").grid(column=0, row=10)
        tkinter.Checkbutton(self.notebook_frames[0], variable=self.exit_after_downloads_bool_var).grid(column=0, row=11)

    def init_settings(self):
        """
        Initializes the app by loading certain values from storage files.
        :return: None
        """
        settings_lines = []
        need_to_rewrite_settings_ini = False

        print("FROM PYTHON:" + str(os.getcwd()))
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
            logging.info("Rewriting settings.ini...")
            with open("settings.ini", 'w') as new_file:
                new_file.write("completed_downloads_directory=" + self.COMPLETED_DOWNLOADS_DIR + "\n")
                new_file.write("temporary_downloads_directory=" + self.DOWNLOAD_TEMP_LOC)

    def on_mousewheel(self, event: tkinter.Event):
        """
        Mouse event handler to scroll Canvas up/down
        :param event: Tkinter Event
        :return: None
        """
        if len(self.downloads_queue_labels_list) > 15:
            self.downloads_queue_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def on_frame_configure(self) -> None:
        """
        Resets the scrollbar region to encompass the inner frame
        :param canvas:
        """
        # Note: bbox is a bounding box
        self.downloads_queue_canvas.configure(scrollregion=self.downloads_queue_canvas.bbox("all"))

    def on_closing(self):
        """
        Confirm application exits
        :return: None
        """
        if self.threads:
            if tkinter.messagebox.askokcancel("Quit", "Do you want to quit?"):
                self.root_tk.destroy()
        else:
            self.root_tk.destroy()


if __name__ == "__main__":
    YouTubeDownloaderApp()
