# MediaDownloaderApp.pyw
# Full regeneration to stop QThread GC-crashes and improve thread lifecycle handling.

import sys
import os
import configparser
import traceback
import subprocess
import shutil
import time
from pathlib import Path
from typing import List, Tuple, Dict

from functools import partial

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
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

# Backend - your uploaded file (unchanged)
from YT_DLP_Download import YT_DLP_Download

SETTINGS_FILE = "settings.ini"


# ----------------- Utilities -----------------
def safe_exec(func):
    """Decorator that prints exceptions raised in thread.run to stderr (prevents silent crashes)."""
    def wrapper(*a, **kw):
        try:
            return func(*a, **kw)
        except Exception:
            print("Exception in background thread:", file=sys.stderr)
            traceback.print_exc()
    return wrapper


class UrlTextEdit(QTextEdit):
    """When focused, paste clipboard URL (replacing existing contents) if clipboard holds a URL."""
    def focusInEvent(self, event):
        super().focusInEvent(event)
        try:
            cb = QGuiApplication.clipboard()
            text = cb.text().strip()
            if text.startswith("http://") or text.startswith("https://"):
                self.setPlainText(text)
        except Exception:
            pass


# ----------------- Background helpers -----------------
class PlaylistExpander(QThread):
    done = pyqtSignal(str, list)   # url, items
    failed = pyqtSignal(str, str)  # url, error

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    @safe_exec
    def run(self):
        import yt_dlp
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        print("before YoutubeDL", flush=True)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
        print("after YoutubeDL", flush=True)
        items = []
        if info.get("_type") == "playlist" and info.get("entries"):
            for entry in info["entries"]:
                entry_url = entry.get("webpage_url")
                if not entry_url and entry.get("id"):
                    entry_url = f"https://www.youtube.com/watch?v={entry['id']}"
                if entry_url:
                    items.append(entry_url)
        if not items:
            items = [self.url]
        self.done.emit(self.url, items)


class DuplicateChecker(QThread):
    result = pyqtSignal(str, bool, str)  # url, exists, prepared_or_err

    def __init__(self, url: str, outdir: str):
        super().__init__()
        self.url = url
        self.outdir = outdir

    @safe_exec
    def run(self):
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "paths": {"home": self.outdir},
            "outtmpl": "%(title).80s [%(uploader|UnknownUploader)s][%(upload_date>%m-%d-%Y)s][%(id)s].%(ext)s",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
            prepared = ydl.prepare_filename(info)
        exists = os.path.exists(prepared)
        self.result.emit(self.url, exists, prepared)


class YTDLPUpdateWorker(QThread):
    status = pyqtSignal(str)

    def __init__(self, pre_release: bool):
        super().__init__()
        self.pre_release = pre_release

    @safe_exec
    def run(self):
        self.status.emit("checking")
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
            print("yt-dlp update failed:", out, err, file=sys.stderr)
            self.status.emit("failed")


# Browser detection helper
def is_browser_installed(browser_key: str) -> bool:
    import shutil, sys, os
    browser_key = browser_key.lower()

    names = {
        "chrome": ["chrome", "google-chrome", "chrome.exe"],
        "chromium": ["chromium", "chromium-browser", "chromium.exe"],
        "brave": ["brave", "brave-browser", "brave.exe"],
        "edge": ["msedge", "edge", "msedge.exe"],
        "firefox": ["firefox", "firefox.exe"],
        "opera": ["opera", "opera.exe"],
        "safari": ["safari"],  # safari handled separately below too
        "vivaldi": ["vivaldi", "vivaldi.exe"],
        "whale": ["whale", "whale.exe"],
    }

    if sys.platform == "win32":
        win_paths = {
            "chrome": [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ],
            "chromium": [
                r"C:\Program Files\Chromium\Application\chrome.exe",
                r"C:\Program Files (x86)\Chromium\Application\chrome.exe",
            ],
            "brave": [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            ],
            "edge": [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ],
            "firefox": [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            ],
            "opera": [
                r"C:\Program Files\Opera\launcher.exe",
                r"C:\Program Files (x86)\Opera\launcher.exe",
            ],
            "vivaldi": [
                r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
                r"C:\Program Files (x86)\Vivaldi\Application\vivaldi.exe",
            ],
            "whale": [
                r"C:\Program Files\Naver\Naver Whale\Application\whale.exe",
                r"C:\Program Files (x86)\Naver\Naver Whale\Application\whale.exe",
            ],
            "safari": [
                r"C:\Program Files\Safari\Safari.exe",
                r"C:\Program Files (x86)\Safari\Safari.exe",
            ],
        }
        for p in win_paths.get(browser_key, []):
            if os.path.exists(p):
                return True

    # PATH-based lookup (Linux/macOS/Windows if added to PATH)
    for candidate in names.get(browser_key, []):
        if shutil.which(candidate):
            return True

    if sys.platform == "darwin":
        mac_map = {
            "safari": "/Applications/Safari.app/Contents/MacOS/Safari",
            "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "firefox": "/Applications/Firefox.app/Contents/MacOS/firefox",
            "opera": "/Applications/Opera.app/Contents/MacOS/Opera",
            "brave": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "vivaldi": "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
            "whale": "/Applications/Naver Whale.app/Contents/MacOS/Whale",
        }
        path = mac_map.get(browser_key)
        if path and os.path.exists(path):
            return True

    return False


# ----------------- Main App -----------------
class MediaDownloaderApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Downloader")
        self.resize(1000, 700)

        # persistent settings
        self.config = configparser.ConfigParser()
        self._ensure_settings()

        # session downloads (urls)
        self.session_downloaded_urls = set()

        # background thread references (to avoid GC while running)
        self._background_threads: List[QThread] = []

        # active downloads: list of dicts with keys: thread, backend, widgets
        self.active_downloads: List[Dict] = []

        # UI tabs
        self.tabs = QTabWidget()
        self.tab_new = QWidget()
        self.tab_active = QWidget()
        self.tab_adv = QWidget()
        self.tabs.addTab(self.tab_new, "New Download")
        self.tabs.addTab(self.tab_active, "Active Downloads")
        self.tabs.addTab(self.tab_adv, "Advanced Settings")

        self.shown_no_downloads_message = False

        self._build_tab_new()
        self._build_tab_active()
        self._build_tab_advanced()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        layout.setContentsMargins(8, 8, 8, 8)
        self.setLayout(layout)

        self.max_threads = int(self.config["General"].get("max_threads", "2"))

        # start updater
        self._start_update_check_if_configured()

    # ---------- settings ----------
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
                "yt_dlp_channel": "stable",
                "cookies_mode": "None",
                "cookies_file": "",
                "restrict_filenames": "False",
                "theme": "system",
                "audio_quality": "best",
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
        with open(SETTINGS_FILE, "w") as f:
            self.config.write(f)

    def _save_settings(self):
        with open(SETTINGS_FILE, "w") as f:
            self.config.write(f)

    def _save_general(self, key, value):
        self.config["General"][key] = str(value)
        self._save_settings()

    # ---------- UI build: New tab ----------
    def _build_tab_new(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        self.url_label = QLabel("Video/Playlist URL(s):")
        self.url_input = UrlTextEdit()
        self.url_input.setPlaceholderText("Paste one or more media URLs (one per line)...")
        self.url_input.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.url_input.setFixedHeight(120)

        self.download_btn = QPushButton("Download")
        self.download_btn.setFixedHeight(120)
        self.download_btn.setMinimumWidth(160)
        self.download_btn.clicked.connect(self.on_download_clicked)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        url_row.addWidget(self.url_input, stretch=3)
        url_row.addWidget(self.download_btn, stretch=1)

        # top options row
        self.audio_checkbox = QCheckBox("Download audio (mp3)")
        self.playlist_combo = QComboBox()
        self.playlist_combo.addItems(["Ask", "Download All (no prompt)", "Download Single (ignore playlist)"])
        self.playlist_combo.setCurrentIndex(0)
        self.exit_after_checkbox = QCheckBox("Exit after all downloads complete")

        self.max_threads_combo = QComboBox()
        self.max_threads_combo.addItems(["1", "2", "3", "4"])
        self.max_threads_combo.setCurrentText(self.config["General"].get("max_threads", "2"))
        self.max_threads_combo.currentTextChanged.connect(self.on_max_threads_changed)
        self.max_threads_combo.setMaximumWidth(80)

        top_opts = QHBoxLayout()
        top_opts.addWidget(self.audio_checkbox)
        top_opts.addSpacing(12)
        top_opts.addWidget(QLabel("Playlist handling:"))
        top_opts.addWidget(self.playlist_combo)
        top_opts.addSpacing(12)
        top_opts.addWidget(QLabel("Max simultaneous downloads:"))
        top_opts.addWidget(self.max_threads_combo)
        top_opts.addStretch()
        top_opts.addWidget(self.exit_after_checkbox)

        # second options
        self.rate_combo = QComboBox()
        self._rate_map = [("No limit", "0"), ("500 KB", "500K"), ("1 MB", "1M"), ("2 MB", "2M"), ("5 MB", "5M"), ("10 MB", "10M")]
        for disp, _ in self._rate_map:
            self.rate_combo.addItem(disp)
        cur_rl = self.config["General"].get("rate_limit", "0")
        for i, (_, val) in enumerate(self._rate_map):
            if val == cur_rl:
                self.rate_combo.setCurrentIndex(i)
                break
        self.rate_combo.currentIndexChanged.connect(self._on_rate_combo_changed)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["best", "2160p", "1440p", "1080p", "720p", "480p"])
        self.quality_combo.setCurrentText(self.config["General"].get("video_quality", "best"))
        self.quality_combo.currentTextChanged.connect(lambda v: self._save_general("video_quality", v))

        # --- inside _build_tab_new(), when building videoext_combo ---
        self.videoext_combo = QComboBox()
        self.videoext_combo.addItem("default", "")
        self.videoext_combo.addItems(["mp4", "mkv", "webm"])
        stored_ext = self.config["General"].get("video_ext", "")
        self.videoext_combo.setCurrentText(stored_ext if stored_ext else "default (yt-dlp decides)")
        self.videoext_combo.currentIndexChanged.connect(
            lambda i: self._save_general("video_ext", self.videoext_combo.itemData(i))
        )

        # --- same for audioext_combo ---
        self.audioext_combo = QComboBox()
        self.audioext_combo.addItem("default", "")
        self.audioext_combo.addItems(["mp3", "m4a", "opus", "aac", "flac"])
        stored_aext = self.config["General"].get("audio_ext", "")
        self.audioext_combo.setCurrentText(stored_aext if stored_aext else "default (yt-dlp decides)")
        self.audioext_combo.currentIndexChanged.connect(
            lambda i: self._save_general("audio_ext", self.audioext_combo.itemData(i))
        )

        self.vcodec_combo = QComboBox()
        self.vcodec_combo.addItem("default (yt-dlp decides)", "")
        self.vcodec_combo.addItems(["h264", "av1", "vp9"])
        stored_vcodec = self.config["General"].get("video_codec", "")
        self.vcodec_combo.setCurrentText(stored_vcodec if stored_vcodec else "default (yt-dlp decides)")
        self.vcodec_combo.currentIndexChanged.connect(
            lambda i: self._save_general("video_codec", self.vcodec_combo.itemData(i) or "")
        )

        self.acodec_combo = QComboBox()
        self.acodec_combo.addItem("default (yt-dlp decides)", "")
        self.acodec_combo.addItems(["aac", "mp3", "opus", "flac"])
        stored_acodec = self.config["General"].get("audio_codec", "")
        self.acodec_combo.setCurrentText(stored_acodec if stored_acodec else "default (yt-dlp decides)")
        self.acodec_combo.currentIndexChanged.connect(
            lambda i: self._save_general("audio_codec", self.acodec_combo.itemData(i) or "")
        )

        self.audio_quality_combo = QComboBox()
        quality_map = [
            ("64 KB", "64k"),
            ("128 KB", "128k"),
            ("192 KB", "192k"),
            ("256 KB", "256k"),
            ("320 KB", "320k"),
        ]
        for label, val in quality_map:
            self.audio_quality_combo.addItem(label, val)

        stored_quality = self.config["General"].get("audio_quality", "192k")
        # find label for stored value
        for i in range(self.audio_quality_combo.count()):
            if self.audio_quality_combo.itemData(i) == stored_quality:
                self.audio_quality_combo.setCurrentIndex(i)
                break

        self.audio_quality_combo.currentIndexChanged.connect(
            lambda i: self._save_general("audio_quality", self.audio_quality_combo.itemData(i))
        )

        opts2 = QGridLayout()
        opts2.addWidget(QLabel("Rate limit:"), 0, 0)
        opts2.addWidget(self.rate_combo, 0, 1)
        opts2.addWidget(QLabel("Video quality:"), 0, 2)
        opts2.addWidget(self.quality_combo, 0, 3)
        opts2.addWidget(QLabel("Video Extension:"), 1, 0)
        opts2.addWidget(self.videoext_combo, 1, 1)
        opts2.addWidget(QLabel("Audio Extension:"), 1, 2)
        opts2.addWidget(self.audioext_combo, 1, 3)
        opts2.addWidget(QLabel("Video codec:"), 2, 0)
        opts2.addWidget(self.vcodec_combo, 2, 1)
        opts2.addWidget(QLabel("Audio codec:"), 2, 2)
        opts2.addWidget(self.acodec_combo, 2, 3)
        opts2.addWidget(QLabel("Audio quality:"), 3, 0)
        opts2.addWidget(self.audio_quality_combo, 3, 1)

        self.open_downloads_btn = QPushButton("Open Downloads Folder")
        self.open_downloads_btn.setMinimumHeight(40)
        self.open_downloads_btn.clicked.connect(self.on_open_downloads_folder)
        open_row = QHBoxLayout()
        open_row.addStretch()
        open_row.addWidget(self.open_downloads_btn)

        layout.addWidget(self.url_label)
        layout.addLayout(url_row)
        layout.addLayout(top_opts)
        layout.addLayout(opts2)
        layout.addLayout(open_row)
        layout.addStretch()
        self.tab_new.setLayout(layout)

    # ---------- UI build: Active tab ----------
    def _build_tab_active(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)

        self.no_downloads_label = QLabel()
        self._update_no_downloads_message()
        layout.addWidget(self.no_downloads_label)

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

        layout.addWidget(self.scroll_area)
        self.tab_active.setLayout(layout)

    def _update_no_downloads_message(self):
        if not self.active_downloads and not self.shown_no_downloads_message:
            text = '<div style="font-size:14px;color:#666">No current downloads. <a href="#">Click here to start a new download.</a></div>'
            self.no_downloads_label.setText(text)
            self.no_downloads_label.linkActivated.connect(lambda _: self.tabs.setCurrentIndex(0))
            self.shown_no_downloads_message = True
        else:
            self.no_downloads_label.setText("")

    # ---------- UI build: Advanced tab ----------
    def _build_tab_advanced(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        restore_btn = QPushButton("Reset All Settings to Default")
        restore_btn.clicked.connect(self._restore_defaults)

        outlbl = QLabel("Output folder:")
        self.out_display = QLabel(self.config["Paths"]["completed_downloads_directory"])
        self.out_display.setStyleSheet("border:1px solid #bbb;padding:6px;background:#fafafa;")
        out_btn = QPushButton("📁")
        out_btn.setFixedSize(36, 28)
        out_btn.clicked.connect(self.on_browse_outdir)

        templbl = QLabel("Temporary folder:")
        self.temp_display = QLabel(self.config["Paths"]["temporary_downloads_directory"])
        self.temp_display.setStyleSheet("border:1px solid #bbb;padding:6px;background:#fafafa;")
        temp_btn = QPushButton("📁")
        temp_btn.setFixedSize(36, 28)
        temp_btn.clicked.connect(self.on_browse_temp)

        cookies_lbl = QLabel("Browser Cookies:")
        self.cookies_combo = QComboBox()
        cookie_options = ["None", "brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale", "Choose file..."]
        self.cookies_combo.addItems(cookie_options)
        stored = self.config["General"].get("cookies_mode", "None")
        if stored == "FILE":
            idx = cookie_options.index("Choose file...")
        else:
            try:
                idx = cookie_options.index(stored)
            except Exception:
                idx = 0
        self.cookies_combo.setCurrentIndex(idx)
        self.cookies_combo.currentTextChanged.connect(self._on_cookies_choice_changed)
        self.cookies_path_label = QLabel(self.config["General"].get("cookies_file", ""))
        self.cookies_path_label.setStyleSheet("border:1px solid #eee;padding:4px;")

        self.restrict_cb = QCheckBox("Restrict filenames (yt-dlp --restrict-filenames)")
        self.restrict_cb.setChecked(self.config["General"].get("restrict_filenames", "False") == "True")
        self.restrict_cb.stateChanged.connect(lambda s: self._save_general("restrict_filenames", str(bool(s))))

        clear_temp_btn = QPushButton("Clear temporary files")
        clear_temp_btn.clicked.connect(self._clear_temp_files)

        theme_lbl = QLabel("Theme:")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["system", "light", "dark"])
        self.theme_combo.setCurrentText(self.config["General"].get("theme", "system"))
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)

        ytdlp_lbl = QLabel("yt-dlp channel:")
        self.ytdlp_combo = QComboBox()
        self.ytdlp_combo.addItems(["stable", "pre-release (use --pre)"])
        ch = self.config["General"].get("yt_dlp_channel", "stable")
        self.ytdlp_combo.setCurrentIndex(1 if ch == "pre" else 0)
        self.ytdlp_combo.currentIndexChanged.connect(self._on_ytdlp_channel_changed)

        self.ytdlp_version_label = QLabel("yt-dlp version: (checking...)")
        try:
            import yt_dlp
            self.ytdlp_version_label.setText(f"yt-dlp version: {getattr(yt_dlp, '__version__', 'unknown')}")
        except Exception:
            self.ytdlp_version_label.setText("yt-dlp version: (not installed)")

        grid = QGridLayout()
        grid.addWidget(restore_btn, 0, 0, 1, 2)
        grid.addWidget(outlbl, 1, 0)
        grid.addWidget(self.out_display, 1, 1)
        grid.addWidget(out_btn, 1, 2)
        grid.addWidget(templbl, 2, 0)
        grid.addWidget(self.temp_display, 2, 1)
        grid.addWidget(temp_btn, 2, 2)
        grid.addWidget(cookies_lbl, 3, 0)
        grid.addWidget(self.cookies_combo, 3, 1)
        grid.addWidget(self.cookies_path_label, 3, 2)
        grid.addWidget(self.restrict_cb, 4, 0, 1, 3)
        grid.addWidget(clear_temp_btn, 5, 0)
        grid.addWidget(theme_lbl, 6, 0)
        grid.addWidget(self.theme_combo, 6, 1)
        grid.addWidget(ytdlp_lbl, 7, 0)
        grid.addWidget(self.ytdlp_combo, 7, 1)
        grid.addWidget(self.ytdlp_version_label, 8, 0, 1, 3)

        layout.addLayout(grid)
        layout.addStretch()
        self.tab_adv.setLayout(layout)

    # ---------- simple handlers ----------
    def _on_rate_combo_changed(self, idx: int):
        _, val = self._rate_map[idx]
        self._save_general("rate_limit", val)

    def on_max_threads_changed(self, text: str):
        try:
            self.max_threads = int(text)
        except Exception:
            self.max_threads = 2
        self._save_general("max_threads", str(self.max_threads))

    def on_browse_outdir(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output directory", self.out_display.text())
        if folder:
            self.out_display.setText(folder)
            self.config["Paths"]["completed_downloads_directory"] = folder
            self._save_settings()

    def on_browse_temp(self):
        folder = QFileDialog.getExistingDirectory(self, "Select temporary directory", self.temp_display.text())
        if folder:
            self.temp_display.setText(folder)
            self.config["Paths"]["temporary_downloads_directory"] = folder
            self._save_settings()

    def _on_cookies_choice_changed(self, txt: str):
        if txt == "None":
            self._save_general("cookies_mode", "None")
            self._save_general("cookies_file", "")
            self.cookies_path_label.setText("")
            return
        if txt == "Choose file...":
            fname, _ = QFileDialog.getOpenFileName(self, "Select cookies file", os.getcwd(), "Cookies files (*.txt *.cookies);;All files (*)")
            if not fname:
                stored_mode = self.config["General"].get("cookies_mode", "None")
                if stored_mode == "FILE":
                    self.cookies_combo.setCurrentText("Choose file...")
                else:
                    self.cookies_combo.setCurrentText(stored_mode if stored_mode in [self.cookies_combo.itemText(i) for i in range(self.cookies_combo.count())] else "None")
                return
            if not os.path.exists(fname):
                QMessageBox.critical(self, "Cookies", "Selected file does not exist.")
                self.cookies_combo.setCurrentText("None")
                self._save_general("cookies_mode", "None")
                self._save_general("cookies_file", "")
                self.cookies_path_label.setText("")
                return
            self._save_general("cookies_mode", "FILE")
            self._save_general("cookies_file", fname)
            self.cookies_path_label.setText(fname)
            return
        key = txt.lower()
        if not is_browser_installed(key):
            QMessageBox.critical(self, "Cookies", f"Selected browser '{txt}' not found; reverting to None.")
            self.cookies_combo.setCurrentText("None")
            self._save_general("cookies_mode", "None")
            self._save_general("cookies_file", "")
            self.cookies_path_label.setText("")
            return
        self._save_general("cookies_mode", key)
        self._save_general("cookies_file", "")
        self.cookies_path_label.setText("")

    def _restore_defaults(self):
        ok = QMessageBox.question(self, "Restore Defaults", "Restore application settings to defaults? This will overwrite settings.ini.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            if os.path.exists(SETTINGS_FILE):
                os.remove(SETTINGS_FILE)
            self._ensure_settings()
            QMessageBox.information(self, "Defaults", "Settings restored. Please restart the app.")
            QApplication.exit(0)
        except Exception as e:
            QMessageBox.critical(self, "Restore Defaults", f"Failed: {e}")

    def _clear_temp_files(self):
        tmp = self.temp_display.text()
        if not tmp or not os.path.exists(tmp):
            QMessageBox.information(self, "Clear temporary files", "Temporary folder does not exist.")
            return
        confirm = QMessageBox.question(self, "Clear temporary files", f"Remove temporary files in:\n\n{tmp}\n\nFiles modified within the last 60 seconds will be kept. Continue?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        removed = 0
        now = time.time()
        for fn in os.listdir(tmp):
            fp = os.path.join(tmp, fn)
            try:
                if os.path.isfile(fp):
                    m = os.path.getmtime(fp)
                    if now - m > 60:
                        os.remove(fp)
                        removed += 1
                else:
                    try:
                        if now - os.path.getmtime(fp) > 60:
                            shutil.rmtree(fp, ignore_errors=True)
                            removed += 1
                    except Exception:
                        pass
            except Exception:
                pass
        QMessageBox.information(self, "Clear temporary files", f"Removed {removed} files/folders from temporary folder.")

    def _on_theme_changed(self, t: str):
        self._save_general("theme", t)
        if t == "dark":
            self.setStyleSheet("QWidget { background: #222; color: #ddd; }")
        elif t == "light":
            self.setStyleSheet("")
        else:
            self.setStyleSheet("")

    def _start_update_check_if_configured(self):
        ch = self.config["General"].get("yt_dlp_channel", "stable")
        pre = ch == "pre"
        w = YTDLPUpdateWorker(pre_release=pre)
        w.status.connect(self._on_update_status)
        w.finished.connect(partial(self._background_thread_finished, w))
        self._background_threads.append(w)
        w.start()

    def _on_update_status(self, st: str):
        base = "Media Downloader"
        if st == "checking":
            self.setWindowTitle(f"{base} -- checking for yt-dlp updates")
        elif st == "installing":
            self.setWindowTitle(f"{base} -- updating yt-dlp (installing)...")
        elif st == "done":
            try:
                import yt_dlp
                ver = getattr(yt_dlp, "__version__", "unknown")
                # only update label if the widget exists
                if hasattr(self, "ytdlp_version_label"):
                    self.ytdlp_version_label.setText(f"yt-dlp version: {ver}")
            except Exception:
                if hasattr(self, "ytdlp_version_label"):
                    self.ytdlp_version_label.setText("yt-dlp version: (installed)")
            self.setWindowTitle(base)
        else:
            self.setWindowTitle(f"{base} -- yt-dlp update failed")
            # schedule revert
            QThread.msleep(300)
            self.setWindowTitle(base)

    # ---------- download flow ----------
    def on_download_clicked(self):
        raw = self.url_input.toPlainText()
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        if not lines:
            QMessageBox.warning(self, "Missing URL", "Please enter at least one URL.")
            return
        for url in lines:
            exp = PlaylistExpander(url)
            exp.done.connect(self._on_playlist_expanded)
            exp.failed.connect(lambda u, e: QMessageBox.critical(self, "Playlist expand failed", f"{u}\n{e}"))
            # store expander so it isn't GC'd while running
            self._background_threads.append(exp)
            exp.finished.connect(partial(self._background_thread_finished, exp))
            exp.start()
        self.tabs.setCurrentIndex(1)  # switch to Active Downloads tab

    def _on_playlist_expanded(self, original_url: str, items: list):
        for item_url in items:
            # session-level duplicate quick check
            if item_url in self.session_downloaded_urls:
                res = QMessageBox.question(self, "Already downloaded in this session", f"The URL was already downloaded in this session:\n\n{item_url}\n\nRedownload?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if res != QMessageBox.StandardButton.Yes:
                    continue
            # disk duplicate check in background
            dc = DuplicateChecker(item_url, self.out_display.text())
            dc.result.connect(self._on_duplicate_check_result)
            self._background_threads.append(dc)
            dc.finished.connect(partial(self._background_thread_finished, dc))
            dc.start()

    def _on_duplicate_check_result(self, url: str, exists: bool, prepared: str):
        if exists:
            res = QMessageBox.question(self, "File already exists", f"A file matching this URL already exists:\n\n{prepared}\n\nRedownload?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if res != QMessageBox.StandardButton.Yes:
                return
        self.session_downloaded_urls.add(url)
        self._queue_worker(url, allow_playlist=False)

    def _queue_worker(self, url: str, allow_playlist: bool = False):
        # gather options
        temp_dir = self.temp_display.text()
        out_dir = self.out_display.text()
        download_mp3 = bool(self.audio_checkbox.isChecked())
        rate_limit = self.config["General"].get("rate_limit", "0")
        video_quality = self.config["General"].get("video_quality", "best")
        video_ext = self.config["General"].get("video_ext", "")
        audio_ext = self.config["General"].get("audio_ext", "")
        video_codec = self.config["General"].get("video_codec", "h264")
        audio_codec = self.config["General"].get("audio_codec", "aac")
        restrict_filenames = self.config["General"].get("restrict_filenames", "False") == "True"

        cookies_mode = self.config["General"].get("cookies_mode", "None")
        cookies_file = self.config["General"].get("cookies_file", "")
        cookies_arg = None
        if cookies_mode == "FILE" and cookies_file:
            cookies_arg = cookies_file

        backend = YT_DLP_Download(
            raw_url=url,
            temp_dl_loc=temp_dir,
            final_destination_dir=out_dir,
            download_mp3=download_mp3,
            allow_playlist=allow_playlist,
            rate_limit=rate_limit,
            video_quality=video_quality,
            video_ext=video_ext or None,
            audio_ext=audio_ext or None,
            video_codec=video_codec,
            audio_codec=audio_codec,
            restrict_filenames=restrict_filenames,
            cookies=cookies_arg,
        )

        # Create a worker thread and move backend into it
        thread = QThread()
        backend.moveToThread(thread)
        # When the thread starts, run the backend's start function (runs in worker thread)
        thread.started.connect(backend.start_yt_download)

        # Setup UI row
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
        progress_bar.setStyleSheet("QProgressBar{border:1px solid #bbb;border-radius:6px;background:#eee} QProgressBar::chunk{background-color:#0aa7a7;border-radius:6px;}")
        progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setFixedWidth(100)

        row_layout.addWidget(title_label)
        row_layout.addWidget(progress_bar, stretch=1)
        row_layout.addWidget(cancel_btn)
        row_frame.setLayout(row_layout)

        # insert into scroll area
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, row_frame)
        self._update_no_downloads_message()

        # connect backend signals to UI
        backend.title_updated.connect(lambda t, lbl=title_label: lbl.setText(t))
        backend.progress_updated.connect(lambda v, pb=progress_bar: pb.setValue(int(v)))
        backend.error_occurred.connect(lambda msg, pb=progress_bar, cb=cancel_btn: self._on_error(pb, msg, cb))
        # finished will call our cleanup handler below
        backend.finished.connect(partial(self._on_backend_finished, thread, backend))
        backend.prompt_overwrite.connect(self._on_prompt_overwrite)

        def _on_cancel():
            backend.cancel()
            cancel_btn.hide()
            progress_bar.setFormat("Cancelled")
            progress_bar.setStyleSheet("QProgressBar::chunk{background-color:#9e9e9e;}")

        cancel_btn.clicked.connect(_on_cancel)

        # ensure thread and backend are kept alive by storing them
        entry = {"thread": thread, "backend": backend, "widgets": {"frame": row_frame, "progress": progress_bar, "cancel": cancel_btn}}
        self.active_downloads.append(entry)

        # also keep thread in background list to prevent GC before start
        self._background_threads.append(thread)

        # ensure thread resources are cleaned after termination
        thread.finished.connect(partial(self._background_thread_finished, thread))
        backend.finished.connect(thread.quit)

        # start queued threads up to max
        self._start_queued_if_possible()

    def _on_backend_finished(self, thread: QThread, backend: YT_DLP_Download, success: bool):
        # Find the entry
        matching = [e for e in self.active_downloads if e["thread"] is thread]
        if not matching:
            return
        entry = matching[0]
        progress_bar = entry["widgets"]["progress"]
        cancel_btn = entry["widgets"]["cancel"]
        # UI update based on success/cancelled/failure
        cancel_btn.hide()
        if success:
            progress_bar.setValue(100)
            progress_bar.setStyleSheet("QProgressBar::chunk{background-color:#2e7d32;}")
        else:
            if progress_bar.format() != "Cancelled":
                progress_bar.setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")

        # stop and wait for thread to quit
        try:
            # thread.quit() called via backend.finished -> thread.quit above
            if thread.isRunning():
                thread.quit()
            # wait a short time for clean exit
            thread.wait(3000)
        except Exception:
            pass

        # remove from active_downloads and background threads lists
        try:
            self.active_downloads.remove(entry)
        except ValueError:
            pass
        try:
            self._background_threads.remove(thread)
        except ValueError:
            pass

        # remove UI row reference - let Qt delete it when appropriate
        self._update_no_downloads_message()
        # if exit after downloads requested and none left running -> quit app
        if self.exit_after_checkbox.isChecked():
            running = [e for e in self.active_downloads if e["thread"].isRunning()]
            if not running:
                QApplication.quit()
        # allow starting more queued threads
        self._start_queued_if_possible()

    def _background_thread_finished(self, thread_obj: QThread):
        """Remove a background thread (playlist/duplicate/checker/updater) when finished."""
        try:
            if thread_obj in self._background_threads:
                # wait briefly to ensure it's stopped
                if isinstance(thread_obj, QThread) and thread_obj.isRunning():
                    thread_obj.quit()
                    thread_obj.wait(2000)
                self._background_threads.remove(thread_obj)
        except Exception:
            pass

    def _on_prompt_overwrite(self, filepath: str):
        # Ask user and provide response via provide_overwrite_response
        dlg = QMessageBox(self)
        dlg.setWindowTitle("File exists")
        dlg.setText(f"The file already exists:\n\n{filepath}\n\nWhat would you like to do?")
        btn_over = dlg.addButton("Overwrite", QMessageBox.ButtonRole.YesRole)
        btn_skip = dlg.addButton("Skip", QMessageBox.ButtonRole.NoRole)
        dlg.exec()
        allow = dlg.clickedButton() == btn_over
        # find backend waiting
        for e in self.active_downloads:
            be = e["backend"]
            if hasattr(be, "_prompt_event") and be._prompt_event:
                be.provide_overwrite_response(allow)
                return

    def _on_error(self, progress_bar: QProgressBar, msg: str, cancel_btn: QPushButton):
        try:
            progress_bar.setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")
            progress_bar.setFormat("Failed")
            QMessageBox.critical(self, "Download error", msg)
            cancel_btn.hide()
        except Exception:
            pass

    def _start_queued_if_possible(self):
        # start threads up to max_threads
        running_count = sum(1 for e in self.active_downloads if e["thread"].isRunning())
        for e in self.active_downloads:
            if running_count >= self.max_threads:
                break
            th = e["thread"]
            if not th.isRunning():
                th.start()
                running_count += 1

    # ---------- duplicates quick helper ----------
    def _file_already_downloaded_quick(self, url: str) -> bool:
        return url in self.session_downloaded_urls

    # ---------- small utilities ----------
    def on_open_downloads_folder(self):
        path = self.out_display.text().strip()
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

    def _on_ytdlp_channel_changed(self, idx: int):
        v = "stable" if idx == 0 else "pre"
        self._save_general("yt_dlp_channel", v)

# ---------- entry point ----------
def main():
    # Fix DPI warning early
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass

    app = QApplication(sys.argv)
    wnd = MediaDownloaderApp()
    wnd.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
