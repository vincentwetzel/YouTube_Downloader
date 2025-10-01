# MediaDownloaderApp.pyw
# PyQt6 frontend (Media Downloader) updated with:
#  - Immediate-cancel behavior (progress-hook abort)
#  - Pre-download overwrite prompting (blocking prompt)
#  - Rate-limit friendly labels (e.g. "10 MB")
#  - Startup yt-dlp update check (stable vs pre-release) with window-title status
#  - Dropdown to choose stable vs pre-release yt-dlp in Settings

import sys
import os
import configparser
import threading
import subprocess
import shutil
import time
from pathlib import Path
from typing import List, Tuple

from PyQt6.QtCore import Qt, QThread, QSize, pyqtSignal, QObject
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QTextEdit,
    QPushButton,
    QCheckBox,
    QProgressBar,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QComboBox,
    QFrame,
    QScrollArea,
    QSizePolicy,
)

from YT_DLP_Download import YT_DLP_Download

SETTINGS_FILE = "settings.ini"


# small subclass for clipboard-paste-on-focus behavior
class UrlTextEdit(QTextEdit):
    def focusInEvent(self, event):
        super().focusInEvent(event)
        clipboard = QGuiApplication.clipboard()
        text = clipboard.text().strip()
        if text.startswith("http://") or text.startswith("https://"):
            # replace existing contents with clipboard URL
            self.setPlainText(text)


# Worker thread to run backend download
class DownloadThread(QThread):
    def __init__(self, backend: YT_DLP_Download):
        super().__init__()
        self.backend = backend

    def run(self):
        try:
            self.backend.get_video_title()
        except Exception:
            pass
        try:
            self.backend.start_yt_download()
        except Exception:
            pass


# helper thread to check/install yt-dlp update
class YTDLPUpdateWorker(QThread):
    status = pyqtSignal(str)  # emits "checking", "installing", "done", "failed"

    def __init__(self, pre_release: bool):
        super().__init__()
        self.pre_release = pre_release

    def run(self):
        try:
            self.status.emit("checking")
            # Build pip args
            pkg = "yt-dlp[default]"
            cmd = [sys.executable, "-m", "pip", "install", "-U"]
            if self.pre_release:
                cmd.append("--pre")
            cmd.append(pkg)
            self.status.emit("installing")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out, err = proc.communicate()
            if proc.returncode == 0:
                self.status.emit("done")
            else:
                self.status.emit("failed")
        except Exception:
            self.status.emit("failed")


class MediaDownloaderApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Downloader")
        self.resize(1000, 700)

        # Settings & persistence
        self.config = configparser.ConfigParser()
        self._ensure_settings()
        self.session_downloaded_urls = set()

        # State: active downloads list entries (thread, backend, widgets)
        self.active_downloads: List[Tuple[QThread, YT_DLP_Download, dict]] = []

        # UI: tabs
        self.tabs = QTabWidget()
        self.tab_new = QWidget()
        self.tab_active = QWidget()
        self.tab_settings = QWidget()

        self.tabs.addTab(self.tab_new, "New Download")
        self.tabs.addTab(self.tab_active, "Active Downloads")
        self.tabs.addTab(self.tab_settings, "Settings")

        # Build UI contents
        self._build_tab_new()
        self._build_tab_active()
        self._build_tab_settings()

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tabs)
        main_layout.setContentsMargins(8, 8, 8, 8)
        self.setLayout(main_layout)

        # concurrency limit
        self.max_threads = int(self.config["General"].get("max_threads", "2"))

        # start background check for yt-dlp update (non-blocking)
        self._start_update_check_if_configured()

    # ------------------------
    # Settings helpers
    # ------------------------
    def _ensure_settings(self):
        defaults = {
            "Paths": {
                "completed_downloads_directory": str(Path.cwd()),
                "temporary_downloads_directory": str(Path.cwd() / "temp"),
            },
            "General": {
                "max_threads": "2",
                "rate_limit": "0",
                "video_quality": "best",
                "video_ext": "mp4",
                "audio_ext": "mp3",
                "video_codec": "h264",
                "audio_codec": "aac",
                "yt_dlp_channel": "stable",  # 'stable' or 'pre'
            },
        }
        if os.path.exists(SETTINGS_FILE):
            self.config.read(SETTINGS_FILE)
        for sec, kv in defaults.items():
            if sec not in self.config:
                self.config[sec] = {}
            for k, v in kv.items():
                if k not in self.config[sec]:
                    self.config[sec][k] = v
        # write back defaults if needed
        with open(SETTINGS_FILE, "w") as f:
            self.config.write(f)

    def _save_settings(self):
        with open(SETTINGS_FILE, "w") as f:
            self.config.write(f)

    # ------------------------
    # Build UI
    # ------------------------
    def _build_tab_new(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # Title
        self.url_label = QLabel("Video/Playlist URL(s):")
        # Big multi-line URL input with clipboard auto-paste-on-focus
        self.url_input = UrlTextEdit()
        self.url_input.setPlaceholderText("Paste one or more media URLs (one per line)...")
        self.url_input.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.url_input.setFixedHeight(120)

        # Download button (match height)
        self.download_btn = QPushButton("Download")
        self.download_btn.setFixedHeight(120)
        self.download_btn.setMinimumWidth(160)
        self.download_btn.clicked.connect(self.on_download_clicked)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        url_row.addWidget(self.url_input, stretch=3)
        url_row.addWidget(self.download_btn, stretch=1)

        # Options row
        self.audio_checkbox = QCheckBox("Download audio (mp3)")
        self.playlist_label = QLabel("Playlist handling:")
        self.playlist_combo = QComboBox()
        self.playlist_combo.addItems(["Ask", "Download All (no prompt)", "Download Single (ignore playlist)"])
        self.playlist_combo.setCurrentIndex(0)

        self.exit_after_checkbox = QCheckBox("Exit after all downloads complete")

        self.max_threads_label = QLabel("Max simultaneous downloads:")
        self.max_threads_combo = QComboBox()
        self.max_threads_combo.addItems(["1", "2", "3", "4"])
        self.max_threads_combo.setCurrentText(self.config["General"].get("max_threads", "2"))
        self.max_threads_combo.currentTextChanged.connect(self.on_max_threads_changed)
        self.max_threads_combo.setMaximumWidth(80)

        options_row = QHBoxLayout()
        options_row.addWidget(self.audio_checkbox)
        options_row.addSpacing(12)
        options_row.addWidget(self.playlist_label)
        options_row.addWidget(self.playlist_combo)
        options_row.addSpacing(12)
        options_row.addWidget(self.max_threads_label)
        options_row.addWidget(self.max_threads_combo)
        options_row.addStretch()
        options_row.addWidget(self.exit_after_checkbox)

        # ADVANCED options row: rate limit, quality, ext/codec choices
        # Rate limit combo shows friendly labels but stores internal value in config via helper
        self.rate_label = QLabel("Rate limit:")
        self.rate_combo = QComboBox()
        # store mapping display -> internal
        self._rate_map = [
            ("No limit", "0"),
            ("500 KB", "500K"),
            ("1 MB", "1M"),
            ("2 MB", "2M"),
            ("5 MB", "5M"),
            ("10 MB", "10M"),
        ]
        for disp, _ in self._rate_map:
            self.rate_combo.addItem(disp)
        # set current by matching stored config value
        cur_rl = self.config["General"].get("rate_limit", "0")
        cur_index = 0
        for i, (disp, val) in enumerate(self._rate_map):
            if val == cur_rl:
                cur_index = i
                break
        self.rate_combo.setCurrentIndex(cur_index)
        self.rate_combo.currentIndexChanged.connect(self.on_rate_changed)

        # Video quality
        self.quality_label = QLabel("Video quality:")
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["best", "2160p", "1440p", "1080p", "720p", "480p"])
        self.quality_combo.setCurrentText(self.config["General"].get("video_quality", "best"))
        self.quality_combo.currentTextChanged.connect(lambda v: self._save_general("video_quality", v))

        # video ext
        self.videoext_label = QLabel("Video ext:")
        self.videoext_combo = QComboBox()
        self.videoext_combo.addItems(["mp4", "mkv", "webm"])
        self.videoext_combo.setCurrentText(self.config["General"].get("video_ext", "mp4"))
        self.videoext_combo.currentTextChanged.connect(lambda v: self._save_general("video_ext", v))

        # audio ext
        self.audioext_label = QLabel("Audio ext:")
        self.audioext_combo = QComboBox()
        self.audioext_combo.addItems(["mp3", "m4a", "opus", "aac", "flac"])
        self.audioext_combo.setCurrentText(self.config["General"].get("audio_ext", "mp3"))
        self.audioext_combo.currentTextChanged.connect(lambda v: self._save_general("audio_ext", v))

        # video codec (friendly names)
        self.vcodec_label = QLabel("Video codec:")
        self.vcodec_combo = QComboBox()
        vcodec_items = [("h264 / AVC", "h264"), ("h265 / HEVC", "h265"), ("vp9", "vp9"), ("av1", "av1")]
        for disp, key in vcodec_items:
            self.vcodec_combo.addItem(disp, key)
        vc_key = self.config["General"].get("video_codec", "h264")
        for i in range(self.vcodec_combo.count()):
            if self.vcodec_combo.itemData(i) == vc_key:
                self.vcodec_combo.setCurrentIndex(i)
                break
        self.vcodec_combo.currentIndexChanged.connect(lambda i: self._save_general("video_codec", self.vcodec_combo.itemData(i)))

        # audio codec
        self.acodec_label = QLabel("Audio codec:")
        self.acodec_combo = QComboBox()
        self.acodec_combo.addItems(["aac", "opus", "mp3", "vorbis", "flac"])
        self.acodec_combo.setCurrentText(self.config["General"].get("audio_codec", "aac"))
        self.acodec_combo.currentTextChanged.connect(lambda v: self._save_general("audio_codec", v))

        opts2_row = QHBoxLayout()
        opts2_row.setSpacing(10)
        opts2_row.addWidget(self.rate_label)
        opts2_row.addWidget(self.rate_combo)
        opts2_row.addSpacing(6)
        opts2_row.addWidget(self.quality_label)
        opts2_row.addWidget(self.quality_combo)
        opts2_row.addSpacing(6)
        opts2_row.addWidget(self.videoext_label)
        opts2_row.addWidget(self.videoext_combo)
        opts2_row.addSpacing(6)
        opts2_row.addWidget(self.audioext_label)
        opts2_row.addWidget(self.audioext_combo)
        opts2_row.addSpacing(6)
        opts2_row.addWidget(self.vcodec_label)
        opts2_row.addWidget(self.vcodec_combo)
        opts2_row.addSpacing(6)
        opts2_row.addWidget(self.acodec_label)
        opts2_row.addWidget(self.acodec_combo)
        opts2_row.addStretch()

        # Open downloads folder button (bigger)
        self.open_downloads_btn = QPushButton("Open Downloads Folder")
        self.open_downloads_btn.setMinimumHeight(40)
        self.open_downloads_btn.setStyleSheet("font-weight: bold;")
        self.open_downloads_btn.clicked.connect(self.on_open_downloads_folder)

        open_row = QHBoxLayout()
        open_row.addStretch()
        open_row.addWidget(self.open_downloads_btn)

        # Put it all together
        layout.addWidget(self.url_label)
        layout.addLayout(url_row)
        layout.addLayout(options_row)
        layout.addLayout(opts2_row)
        layout.addLayout(open_row)
        layout.addStretch()  # push all content upward
        self.tab_new.setLayout(layout)

    def _build_tab_active(self):
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(6, 6, 6, 6)

        # scroll area for download rows (prevents window growing)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.scroll_area_widget = QWidget()
        self.scroll_layout = QVBoxLayout()
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)
        self.scroll_layout.addStretch()
        self.scroll_area_widget.setLayout(self.scroll_layout)

        self.scroll_area.setWidget(self.scroll_area_widget)
        outer_layout.addWidget(self.scroll_area)
        self.tab_active.setLayout(outer_layout)

    def _build_tab_settings(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(12)

        # Output folder label + button
        self.outdir_title = QLabel("Output folder:")
        self.outdir_display = QLabel(self.config["Paths"]["completed_downloads_directory"])
        self.outdir_display.setStyleSheet("border: 1px solid #bbb; padding: 6px; background:#fafafa;")
        self.outdir_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.outdir_btn = QPushButton("📁")
        self.outdir_btn.setFixedSize(QSize(36, 28))
        self.outdir_btn.clicked.connect(self.on_browse_outdir)

        # Temp folder label + button
        self.temp_title = QLabel("Temporary folder:")
        self.temp_display = QLabel(self.config["Paths"]["temporary_downloads_directory"])
        self.temp_display.setStyleSheet("border: 1px solid #bbb; padding: 6px; background:#fafafa;")
        self.temp_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.temp_btn = QPushButton("📁")
        self.temp_btn.setFixedSize(QSize(36, 28))
        self.temp_btn.clicked.connect(self.on_browse_temp)

        # yt-dlp channel dropdown (stable / pre-release)
        self.ytdlp_label = QLabel("yt-dlp channel:")
        self.ytdlp_combo = QComboBox()
        self.ytdlp_combo.addItems(["stable", "pre-release (use --pre)"])
        stored = self.config["General"].get("yt_dlp_channel", "stable")
        if stored == "pre":
            self.ytdlp_combo.setCurrentIndex(1)
        else:
            self.ytdlp_combo.setCurrentIndex(0)
        self.ytdlp_combo.currentIndexChanged.connect(self.on_ytdlp_channel_changed)

        grid.addWidget(self.outdir_title, 0, 0)
        grid.addWidget(self.outdir_display, 0, 1)
        grid.addWidget(self.outdir_btn, 0, 2)

        grid.addWidget(self.temp_title, 1, 0)
        grid.addWidget(self.temp_display, 1, 1)
        grid.addWidget(self.temp_btn, 1, 2)

        grid.addWidget(self.ytdlp_label, 2, 0)
        grid.addWidget(self.ytdlp_combo, 2, 1)

        layout.addLayout(grid)
        layout.addStretch()
        self.tab_settings.setLayout(layout)

    # ------------------------
    # UI actions & helpers
    # ------------------------
    def on_browse_outdir(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output directory", self.outdir_display.text())
        if folder:
            self.outdir_display.setText(folder)
            self.config["Paths"]["completed_downloads_directory"] = folder
            self._save_settings()

    def on_browse_temp(self):
        folder = QFileDialog.getExistingDirectory(self, "Select temporary directory", self.temp_display.text())
        if folder:
            self.temp_display.setText(folder)
            self.config["Paths"]["temporary_downloads_directory"] = folder
            self._save_settings()

    def on_open_downloads_folder(self):
        path = self.outdir_display.text().strip()
        if not path:
            QMessageBox.information(self, "Open Downloads Folder", "No output folder set.")
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "Open Downloads Folder", f"Folder does not exist: {path}")
            return
        if sys.platform == "win32":
            os.startfile(os.path.normpath(path))
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def on_max_threads_changed(self, text: str):
        try:
            self.max_threads = int(text)
            self.config["General"]["max_threads"] = text
            self._save_settings()
        except Exception:
            pass

    def on_rate_changed(self, idx: int):
        # store the internal mapping value
        _, val = self._rate_map[idx]
        self._save_general("rate_limit", val)

    def _save_general(self, key, value):
        self.config["General"][key] = str(value)
        self._save_settings()

    def on_ytdlp_channel_changed(self, idx: int):
        # 0 = stable, 1 = pre
        v = "stable" if idx == 0 else "pre"
        self._save_general("yt_dlp_channel", v)

    # ------------------------
    # Playlist detection & expansion
    # ------------------------
    def _detect_playlist(self, url: str):
        """
        Use yt_dlp to inspect URL; return (is_playlist, info)
        """
        try:
            import yt_dlp
            ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist" or (info.get("entries") and len(info.get("entries")) > 1):
                return True, info
            return False, info
        except Exception:
            return False, None

    def _extract_playlist_items(self, url: str):
        """
        Return a list of webpage_url strings for every entry in the playlist.
        """
        try:
            import yt_dlp
            ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            items = []
            if info.get("_type") == "playlist" and info.get("entries"):
                for entry in info["entries"]:
                    entry_url = entry.get("webpage_url")
                    if not entry_url and entry.get("id"):
                        # best-effort construct for YouTube
                        entry_url = f"https://www.youtube.com/watch?v={entry['id']}"
                    if entry_url:
                        items.append(entry_url)
            return items
        except Exception:
            return []

    # ------------------------
    # Queue and start downloads
    # ------------------------
    def on_download_clicked(self):
        raw_text = self.url_input.toPlainText()
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        if not lines:
            QMessageBox.warning(self, "Missing URL", "Please enter at least one URL.")
            return

        for raw_url in lines:
            choice = self.playlist_combo.currentIndex()  # 0=Ask,1=All,2=Single
            if choice == 1:
                # expand playlist into items and queue each
                items = self._extract_playlist_items(raw_url)
                if items:
                    for item in items:
                        self._queue_worker(item, allow_playlist=False)
                else:
                    # failed to expand, queue as-is with allow_playlist True
                    self._queue_worker(raw_url, allow_playlist=True)
            elif choice == 2:
                # download single (attempt to extract first entry)
                items = self._extract_playlist_items(raw_url)
                if items:
                    self._queue_worker(items[0], allow_playlist=False)
                else:
                    self._queue_worker(raw_url, allow_playlist=False)
            else:
                # Ask mode
                is_playlist, info = self._detect_playlist(raw_url)
                if is_playlist and info:
                    box = QMessageBox(self)
                    box.setWindowTitle("Playlist Detected")
                    box.setText(f"The following URL is a playlist:\n\n{raw_url}\n\nWhat would you like to do?")
                    btn_full = box.addButton("Download Full Playlist", QMessageBox.ButtonRole.YesRole)
                    btn_single = box.addButton("Download Single Item Only", QMessageBox.ButtonRole.NoRole)
                    btn_cancel = box.addButton("Cancel Download", QMessageBox.ButtonRole.RejectRole)
                    box.exec()

                    if box.clickedButton() == btn_full:
                        items = self._extract_playlist_items(raw_url)
                        if items:
                            for item in items:
                                self._queue_worker(item, allow_playlist=False)
                        else:
                            self._queue_worker(raw_url, allow_playlist=True)
                    elif box.clickedButton() == btn_single:
                        items = self._extract_playlist_items(raw_url)
                        if items:
                            self._queue_worker(items[0], allow_playlist=False)
                        else:
                            self._queue_worker(raw_url, allow_playlist=False)
                    else:
                        return
                else:
                    self._queue_worker(raw_url, allow_playlist=False)

    def _queue_worker(self, url: str, allow_playlist: bool = False):
        """
        Create backend and thread, create GUI entry in Active Downloads tab,
        hook signals, and either start immediately (respecting concurrency)
        or leave waiting (status shows waiting).
        """

        temp_dir = self.temp_display.text()
        out_dir = self.outdir_display.text()
        download_mp3 = bool(self.audio_checkbox.isChecked())

        # Duplicate check in current session
        if url in self.session_downloaded_urls:
            res = QMessageBox.question(
                self,
                "Duplicate Download",
                f"This URL was already downloaded in this session:\n\n{url}\n\nRedownload anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if res == QMessageBox.StandardButton.No:
                return
        # Also check on disk
        if self._file_already_downloaded(url):
            res = QMessageBox.question(
                self,
                "File Already Exists",
                f"A matching file already exists in the output folder for:\n\n{url}\n\nDo you want to redownload it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if res == QMessageBox.StandardButton.No:
                return

        # Record in session set
        self.session_downloaded_urls.add(url)

        # Pull advanced settings from config
        rate_limit = self.config["General"].get("rate_limit", "0")
        video_quality = self.config["General"].get("video_quality", "best")
        video_ext = self.config["General"].get("video_ext", "mp4")
        audio_ext = self.config["General"].get("audio_ext", "mp3")
        video_codec = self.config["General"].get("video_codec", "h264")
        audio_codec = self.config["General"].get("audio_codec", "aac")

        backend = YT_DLP_Download(
            raw_url=url,
            temp_dl_loc=temp_dir,
            final_destination_dir=out_dir,
            download_mp3=download_mp3,
            allow_playlist=allow_playlist,
            rate_limit=rate_limit,
            video_quality=video_quality,
            video_ext=video_ext,
            audio_ext=audio_ext,
            video_codec=video_codec,
            audio_codec=audio_codec,
        )
        thread = DownloadThread(backend)

        # GUI row for this download
        row_frame = QFrame()
        row_frame.setFrameShape(QFrame.Shape.StyledPanel)
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(12)

        title_label = QLabel("Fetching title...")
        title_label.setFixedWidth(360)
        title_label.setWordWrap(True)

        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setFixedHeight(36)
        progress_bar.setFormat("%p%")
        progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #bbb;
                border-radius: 6px;
                background: #eee;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #0aa7a7;
                border-radius: 6px;
            }
            """
        )
        progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setFixedWidth(100)

        row_layout.addWidget(title_label)
        row_layout.addWidget(progress_bar, stretch=1)
        row_layout.addWidget(cancel_btn)
        row_frame.setLayout(row_layout)

        # Insert row near top (below stretch)
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, row_frame)

        # Connect backend signals to widgets
        backend.title_updated.connect(lambda t, lbl=title_label: lbl.setText(t))
        backend.progress_updated.connect(lambda v, pb=progress_bar: pb.setValue(int(v)))
        backend.status_updated.connect(lambda s: None)
        backend.error_occurred.connect(lambda msg, pb=progress_bar, cb=cancel_btn: self._on_error(pb, msg, cb))
        backend.finished.connect(lambda success, pb=progress_bar, btn=cancel_btn, fr=row_frame, th=thread, be=backend: self._on_finished(success, pb, btn, fr, th, be))

        # connect prompt overwrite signal => GUI should show dialog and call backend.provide_overwrite_response
        backend.prompt_overwrite.connect(self._on_prompt_overwrite)

        # Cancel action: immediately request cancel and update UI
        def _on_cancel(b=backend, pb=progress_bar, btn=cancel_btn):
            b.cancel()
            btn.hide()
            pb.setFormat("Cancelled")
            pb.setStyleSheet("QProgressBar::chunk { background-color: #9e9e9e; }")
        cancel_btn.clicked.connect(_on_cancel)

        # Store and manage starting
        self.active_downloads.append((thread, backend, {"frame": row_frame, "progress": progress_bar, "cancel": cancel_btn}))
        self._start_queued_if_possible()

    def _file_already_downloaded(self, url: str) -> bool:
        try:
            import yt_dlp
            out_dir = self.outdir_display.text().strip()
            if not out_dir:
                return False

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "paths": {"home": out_dir},
                "outtmpl": "%(title).80s [%(uploader|UnknownUploader)s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                filename = ydl.prepare_filename(info)
            return os.path.exists(filename)
        except Exception:
            return False

    # ------------------------
    # Prompt overwrite UI handler (called from backend thread via signal)
    # Backend waits on its event; this function will set the response
    # ------------------------
    def _on_prompt_overwrite(self, filepath: str):
        # This runs on GUI thread (because signal emitted from backend in worker)
        # Ask user and set response on the backend object that emitted it.
        res = QMessageBox.question(
            self,
            "File exists",
            f"The file already exists:\n{filepath}\n\nDo you want to overwrite it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        allow = res == QMessageBox.StandardButton.Yes

        # We need to find the backend instance that emitted the signal.
        # The backend stored the event internally and the instance sent the signal;
        # we cannot easily access which object emitted it, so instead we scan active_downloads
        # for any backend with an outstanding prompt event.
        for (_, backend, _) in self.active_downloads:
            if hasattr(backend, "_prompt_event") and backend._prompt_event:
                backend.provide_overwrite_response(allow)
                return

    # ------------------------
    # Error / finish handlers
    # ------------------------
    def _on_error(self, progress_bar: QProgressBar, msg: str, cancel_btn: QPushButton):
        progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #c62828; }")
        progress_bar.setFormat("Failed")
        QMessageBox.critical(self, "Download error", msg)
        cancel_btn.hide()

    def _on_finished(self, success: bool, progress_bar: QProgressBar, cancel_btn: QPushButton, frame: QFrame, thread: QThread, backend: YT_DLP_Download):
        cancel_btn.hide()
        if success:
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #2e7d32; }")
            progress_bar.setValue(100)
        else:
            # if already marked Cancelled by user, keep that look; otherwise mark failed
            if progress_bar.format() != "Cancelled":
                progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #c62828; }")

        # remove from active list so future queued threads can start
        self._remove_thread_from_active(thread)
        self._start_queued_if_possible()

        # Exit if requested and nothing is running
        if self.exit_after_checkbox.isChecked():
            running = [t for (t, _, _) in self.active_downloads if t.isRunning()]
            if not running:
                QApplication.quit()

    def _remove_thread_from_active(self, thread: QThread):
        self.active_downloads = [(t, b, w) for (t, b, w) in self.active_downloads if t != thread]

    def _start_queued_if_possible(self):
        running_count = sum(1 for (t, _, _) in self.active_downloads if t.isRunning())
        for (t, backend, widgets) in self.active_downloads:
            if running_count >= self.max_threads:
                break
            if not t.isRunning():
                t.start()
                running_count += 1

    # ------------------------
    # Startup yt-dlp update handling
    # ------------------------
    def _start_update_check_if_configured(self):
        channel = self.config["General"].get("yt_dlp_channel", "stable")
        pre = channel == "pre"
        # run the update worker; we don't force update, just install latest
        self._updater = YTDLPUpdateWorker(pre_release=pre)
        self._updater.status.connect(self._on_update_status)
        self._updater.start()

    def _on_update_status(self, status: str):
        # update window title to reflect progress
        base = "Media Downloader"
        if status == "checking":
            self.setWindowTitle(f"{base} -- checking for yt-dlp updates")
        elif status == "installing":
            self.setWindowTitle(f"{base} -- updating yt-dlp (installing)...")
        elif status == "done":
            self.setWindowTitle(f"{base} -- yt-dlp up to date")
            # revert back after a short delay
            threading.Timer(2.0, lambda: self.setWindowTitle("Media Downloader")).start()
        else:
            self.setWindowTitle(f"{base} -- yt-dlp update failed")
            threading.Timer(4.0, lambda: self.setWindowTitle("Media Downloader")).start()

    # ------------------------
    # Application entrypoint
    # ------------------------
def main():
    app = QApplication(sys.argv)
    wnd = MediaDownloaderApp()
    wnd.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
