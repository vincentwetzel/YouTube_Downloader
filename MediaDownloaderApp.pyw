# MediaDownloaderApp.pyw
# PyQt6 GUI for yt-dlp based downloader
# Uses YT_DLP_Download.py as backend worker
import sys
import os
import logging
import configparser
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QTextEdit, QPushButton, QCheckBox,
    QProgressBar, QHBoxLayout, QVBoxLayout, QGridLayout, QFileDialog,
    QMessageBox, QTabWidget, QComboBox, QGroupBox, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSlot, pyqtSignal, QTimer

from YT_DLP_Download import YT_DLP_Download

# logging
logger = logging.getLogger("MediaDownloaderApp")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)

SETTINGS_FILE = "settings.ini"


def load_or_create_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if os.path.exists(SETTINGS_FILE):
        cfg.read(SETTINGS_FILE)
    # only set defaults for structure; DO NOT auto-default paths
    if "Paths" not in cfg:
        cfg["Paths"] = {}
    if "General" not in cfg:
        cfg["General"] = {}
    # Ensure keys exist with defaults (but not paths)
    g = cfg["General"]
    defaults = {
        "max_threads": "2",
        "video_quality": "best",
        "video_ext": "",
        "audio_ext": "",
        "video_codec": "",
        "audio_codec": "",
        "audio_quality": "best",
        "restrict_filenames": "False",
        "sponsorblock": "True",
        "yt_dlp_channel": "stable",
        "yt_dlp_version": "",
        "cookies_mode": "None",
        "cookies_file": "",
        "use_part_files": "True",
        "rate_limit": "0",
        "theme": "system",
    }
    for k, v in defaults.items():
        if k not in g:
            g[k] = v
    # Do not overwrite existing Paths entries; they may be missing
    return cfg


class UrlTextEdit(QTextEdit):
    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        try:
            from PyQt6.QtGui import QGuiApplication
            cb = QGuiApplication.clipboard()
            txt = cb.text().strip()
            if txt.startswith("http://") or txt.startswith("https://"):
                logger.debug("Auto-paste from clipboard: %s", txt)
                self.setPlainText(txt)
        except Exception:
            pass


# ---------- lightweight background workers ----------
class PlaylistExpanderWorker(QThread):
    expanded = pyqtSignal(str, list)  # original_url, items list
    failed = pyqtSignal(str, str)     # original_url, error text

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        logger.debug("PlaylistExpanderWorker: expanding %s", self.url)
        try:
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True}
            items = []
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
            if info.get("_type") == "playlist":
                for entry in info.get("entries", []):
                    if not entry:
                        continue
                    u = entry.get("webpage_url") or entry.get("url")
                    if u:
                        items.append(u)
            else:
                items = [self.url]
            logger.debug("PlaylistExpanderWorker: %s expanded to %d items", self.url, len(items))
            self.expanded.emit(self.url, items)
        except Exception as e:
            logger.exception("PlaylistExpanderWorker failed for %s: %s", self.url, e)
            self.failed.emit(self.url, str(e))


class DuplicateCheckerWorker(QThread):
    result = pyqtSignal(str, bool, str)  # url, exists, prepared_path

    def __init__(self, url: str, outdir: str):
        super().__init__()
        self.url = url
        self.outdir = outdir

    def run(self):
        logger.debug("DuplicateCheckerWorker: checking %s in %s", self.url, self.outdir)
        try:
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True, "paths": {"home": self.outdir}}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                try:
                    prepared = ydl.prepare_filename(info)
                except Exception:
                    prepared = ""
            exists = bool(prepared and os.path.exists(prepared))
            logger.debug("DuplicateCheckerWorker: %s exists=%s path=%s", self.url, exists, prepared)
            self.result.emit(self.url, exists, prepared or "")
        except Exception as e:
            logger.exception("DuplicateCheckerWorker error for %s: %s", self.url, e)
            self.result.emit(self.url, False, "")


# ---------- Main application ----------
class MediaDownloaderApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Downloader")
        self.resize(1000, 700)

        self.config = load_or_create_config()
        self.session_urls = set()
        self.active_placeholders: Dict[str, dict] = {}
        self.active_workers: Dict[str, dict] = {}
        self.threads_keep = []

        self._ensure_paths_or_prompt()
        self._build_ui()
        self._apply_settings_to_widgets()

    def _ensure_paths_or_prompt(self):
        """If paths are missing in INI, prompt the user to choose them before allowing downloads."""
        paths = self.config["Paths"] if "Paths" in self.config else {}
        output = paths.get("completed_downloads_directory", "")
        temp = paths.get("temporary_downloads_directory", "")
        changed = False

        if not output or not os.path.isdir(output):
            logger.debug("No valid output folder in config; prompting user")
            out = QFileDialog.getExistingDirectory(self, "Select output (completed downloads) folder", str(Path.home()))
            if out:
                if "Paths" not in self.config:
                    self.config["Paths"] = {}
                self.config["Paths"]["completed_downloads_directory"] = out
                output = out
                changed = True

        if not temp or not os.path.isdir(temp):
            logger.debug("No valid temp folder in config; prompting user")
            t = QFileDialog.getExistingDirectory(self, "Select temporary downloads folder", str(Path.home()))
            if t:
                if "Paths" not in self.config:
                    self.config["Paths"] = {}
                self.config["Paths"]["temporary_downloads_directory"] = t
                temp = t
                changed = True

        if changed:
            with open(SETTINGS_FILE, "w") as fh:
                self.config.write(fh)

    # ---------- UI building ----------
    def _build_ui(self):
        logger.debug("Building UI")
        self.tabs = QTabWidget()
        self.tab_start = QWidget()
        self.tab_active = QWidget()
        self.tab_adv = QWidget()
        self.tabs.addTab(self.tab_start, "Start a Download")
        self.tabs.addTab(self.tab_active, "Active Downloads")
        self.tabs.addTab(self.tab_adv, "Advanced Settings")

        self._build_tab_start()
        self._build_tab_active()
        self._build_tab_advanced()

        main = QVBoxLayout()
        main.addWidget(self.tabs)
        self.setLayout(main)

    def _build_tab_start(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)

        # top row: open downloads folder at top-right (identical placement as active tab)
        top_row = QHBoxLayout()
        top_row.addStretch()
        self.open_downloads_btn_start = QPushButton("Open Downloads Folder")
        self.open_downloads_btn_start.setFixedHeight(36)
        self.open_downloads_btn_start.clicked.connect(self.open_downloads_folder)
        top_row.addWidget(self.open_downloads_btn_start)
        layout.addLayout(top_row)

        lbl = QLabel("Video/Playlist URL(s):")
        lbl.setToolTip("Enter one or more URLs (one per line). Supported sites: any yt-dlp compatible site.")
        self.url_input = UrlTextEdit()
        self.url_input.setFixedHeight(120)

        self.download_btn = QPushButton("Download")
        self.download_btn.setFixedHeight(120)
        self.download_btn.setFixedWidth(180)
        self.download_btn.clicked.connect(self.on_download_clicked)

        url_row = QHBoxLayout()
        url_row.addWidget(self.url_input, stretch=1)
        url_row.addWidget(self.download_btn)

        # Groups: video and audio
        groups_row = QHBoxLayout()

        # Video group
        vg = QGroupBox("Video Settings")
        vg_layout = QGridLayout()
        self.video_quality_combo = QComboBox()
        self.video_quality_combo.addItems(["best", "2160p", "1440p", "1080p", "720p", "480p"])
        self.video_quality_combo.setCurrentText(self.config["General"].get("video_quality", "best"))
        self.video_quality_combo.currentTextChanged.connect(lambda v: self._save_general("video_quality", v))

        self.video_ext_combo = QComboBox()
        self.video_ext_combo.addItem("default (yt-dlp)", "")
        for ex in ("mp4", "mkv", "webm"):
            self.video_ext_combo.addItem(ex, ex)
        cur_ve = self.config["General"].get("video_ext", "")
        if cur_ve:
            idx = self.video_ext_combo.findData(cur_ve)
            if idx >= 0:
                self.video_ext_combo.setCurrentIndex(idx)
        self.video_ext_combo.currentIndexChanged.connect(lambda i: self._save_general("video_ext", self.video_ext_combo.itemData(i) or ""))

        self.vcodec_combo = QComboBox()
        self.vcodec_combo.addItem("default", "")
        self.vcodec_combo.addItem("h264 / AVC", "h264")
        self.vcodec_combo.addItem("h265 / HEVC", "h265")
        self.vcodec_combo.addItem("vp9", "vp9")
        cur_vc = self.config["General"].get("video_codec", "")
        if cur_vc:
            j = self.vcodec_combo.findData(cur_vc)
            if j >= 0:
                self.vcodec_combo.setCurrentIndex(j)
        self.vcodec_combo.currentIndexChanged.connect(lambda i: self._save_general("video_codec", self.vcodec_combo.itemData(i) or ""))

        vg_layout.addWidget(QLabel("Quality:"), 0, 0)
        vg_layout.addWidget(self.video_quality_combo, 0, 1)
        vg_layout.addWidget(QLabel("Extension:"), 1, 0)
        vg_layout.addWidget(self.video_ext_combo, 1, 1)
        vg_layout.addWidget(QLabel("Codec:"), 2, 0)
        vg_layout.addWidget(self.vcodec_combo, 2, 1)
        vg.setLayout(vg_layout)

        # Audio group
        ag = QGroupBox("Audio Settings")
        ag_layout = QGridLayout()
        self.audio_only_cb = QCheckBox("Download audio only")
        self.audio_only_cb.setChecked(False)
        self.audio_only_cb.stateChanged.connect(lambda s: self._save_general("download_audio_only", str(bool(s))))

        self.audio_quality_combo = QComboBox()
        audio_opts = [("best", "best"), ("64 KB", "64k"), ("128 KB", "128k"), ("192 KB", "192k"), ("256 KB", "256k"), ("320 KB", "320k")]
        for label, val in audio_opts:
            self.audio_quality_combo.addItem(label, val)
        cura = self.config["General"].get("audio_quality", "best")
        for idx in range(self.audio_quality_combo.count()):
            if self.audio_quality_combo.itemData(idx) == cura:
                self.audio_quality_combo.setCurrentIndex(idx)
                break
        self.audio_quality_combo.currentIndexChanged.connect(lambda i: self._save_general("audio_quality", self.audio_quality_combo.itemData(i) or "best"))

        self.audio_ext_combo = QComboBox()
        self.audio_ext_combo.addItem("default (yt-dlp)", "")
        for ex in ("mp3", "m4a", "opus", "aac", "flac"):
            self.audio_ext_combo.addItem(ex, ex)
        curae = self.config["General"].get("audio_ext", "")
        if curae:
            ia = self.audio_ext_combo.findData(curae)
            if ia >= 0:
                self.audio_ext_combo.setCurrentIndex(ia)
        self.audio_ext_combo.currentIndexChanged.connect(lambda i: self._save_general("audio_ext", self.audio_ext_combo.itemData(i) or ""))

        self.acodec_combo = QComboBox()
        self.acodec_combo.addItem("default", "")
        for a in ("aac", "opus", "mp3", "vorbis", "flac"):
            self.acodec_combo.addItem(a, a)
        curaud = self.config["General"].get("audio_codec", "")
        if curaud:
            ja = self.acodec_combo.findData(curaud)
            if ja >= 0:
                self.acodec_combo.setCurrentIndex(ja)
        self.acodec_combo.currentIndexChanged.connect(lambda i: self._save_general("audio_codec", self.acodec_combo.itemData(i) or ""))

        ag_layout.addWidget(QLabel("Quality:"), 0, 0)
        ag_layout.addWidget(self.audio_quality_combo, 0, 1)
        ag_layout.addWidget(QLabel("Extension:"), 1, 0)
        ag_layout.addWidget(self.audio_ext_combo, 1, 1)
        ag_layout.addWidget(QLabel("Codec:"), 2, 0)
        ag_layout.addWidget(self.acodec_combo, 2, 1)
        ag.setLayout(ag_layout)

        groups_row.addWidget(vg, stretch=1)
        groups_row.addWidget(ag, stretch=1)

        # small row (playlist, max threads, exit after)
        small = QHBoxLayout()
        self.playlist_mode = QComboBox()
        self.playlist_mode.addItems(["Ask", "Download All (no prompt)", "Download Single (ignore playlist)"])
        self.playlist_mode.setCurrentText("Ask")
        self.max_threads_combo = QComboBox()
        self.max_threads_combo.addItems(["1", "2", "3", "4"])
        self.max_threads_combo.setCurrentText(self.config["General"].get("max_threads", "2"))
        self.max_threads_combo.currentTextChanged.connect(lambda t: self._save_general("max_threads", t))
        self.exit_after_cb = QCheckBox("Exit after all downloads complete")
        self.exit_after_cb.stateChanged.connect(lambda s: self._save_general("exit_after", str(bool(s))))

        small.addWidget(QLabel("Playlist:"))
        small.addWidget(self.playlist_mode)
        small.addSpacing(12)
        small.addWidget(QLabel("Max concurrent:"))
        small.addWidget(self.max_threads_combo)
        small.addStretch()
        small.addWidget(self.audio_only_cb)

        layout.addWidget(lbl)
        layout.addLayout(url_row)
        layout.addLayout(groups_row)
        layout.addLayout(small)
        layout.addStretch()
        self.tab_start.setLayout(layout)

    def _build_tab_active(self):
        layout = QVBoxLayout()
        top_row = QHBoxLayout()
        top_row.addStretch()
        # identical placement of the Open Downloads button as in Start tab
        self.open_downloads_btn_active = QPushButton("Open Downloads Folder")
        self.open_downloads_btn_active.setFixedHeight(36)
        self.open_downloads_btn_active.clicked.connect(self.open_downloads_folder)
        top_row.addWidget(self.open_downloads_btn_active)
        layout.addLayout(top_row)

        # Centered start button on first run
        self.center_start_btn = QPushButton("Start a New Download")
        self.center_start_btn.setFixedSize(220, 48)
        self.center_start_btn.clicked.connect(lambda: self.tabs.setCurrentWidget(self.tab_start))
        center_row = QHBoxLayout()
        center_row.addStretch()
        center_row.addWidget(self.center_start_btn)
        center_row.addStretch()
        layout.addLayout(center_row)

        # Scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        self.scroll_layout.addStretch()
        self.scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)
        self.tab_active.setLayout(layout)

        # Guarantee identical geometry for two open buttons
        # (both were created; we ensure they share fixed size)
        self.open_downloads_btn_start = self.open_downloads_btn_start if hasattr(self, "open_downloads_btn_start") else None
        # Set consistent width
        for btn in (self.open_downloads_btn_start, self.open_downloads_btn_active):
            if btn:
                btn.setFixedWidth(220)

    def _build_tab_advanced(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)

        # Output and temp rows on same line
        out_row = QHBoxLayout()
        out_lbl = QLabel("Output folder:")
        paths = self.config["Paths"] if "Paths" in self.config else {}
        self.out_display = QLabel(paths.get("completed_downloads_directory", ""))
        self.out_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_out = QPushButton("📁")
        btn_out.setFixedWidth(40)
        btn_out.clicked.connect(self.browse_out)
        out_row.addWidget(out_lbl)
        out_row.addWidget(self.out_display, stretch=1)
        out_row.addWidget(btn_out)
        layout.addLayout(out_row)

        temp_row = QHBoxLayout()
        temp_lbl = QLabel("Temporary folder:")
        self.temp_display = QLabel(paths.get("temporary_downloads_directory", ""))
        self.temp_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_temp = QPushButton("📁")
        btn_temp.setFixedWidth(40)
        btn_temp.clicked.connect(self.browse_temp)
        temp_row.addWidget(temp_lbl)
        temp_row.addWidget(self.temp_display, stretch=1)
        temp_row.addWidget(btn_temp)
        layout.addLayout(temp_row)

        # SponsorBlock and Restrict filenames + Restore Defaults button
        self.sponsorblock_cb = QCheckBox("Enable SponsorBlock")
        self.sponsorblock_cb.setChecked(self.config["General"].get("sponsorblock", "True") == "True")
        self.sponsorblock_cb.stateChanged.connect(lambda s: self._save_general("sponsorblock", str(bool(s))))
        self.restrict_cb = QCheckBox("Restrict filenames")
        self.restrict_cb.setChecked(self.config["General"].get("restrict_filenames", "False") == "True")
        self.restrict_cb.stateChanged.connect(lambda s: self._save_general("restrict_filenames", str(bool(s))))

        layout.addWidget(self.sponsorblock_cb)
        layout.addWidget(self.restrict_cb)

        restore_btn = QPushButton("Restore Defaults")
        restore_btn.clicked.connect(self._restore_defaults)
        layout.addWidget(restore_btn)

        layout.addStretch()
        self.tab_adv.setLayout(layout)

    # ---------- helpers ----------
    def _save_general(self, key: str, value: str):
        logger.debug("Saving setting %s=%s", key, value)
        if "General" not in self.config:
            self.config["General"] = {}
        self.config["General"][key] = str(value)
        with open(SETTINGS_FILE, "w") as fh:
            self.config.write(fh)

    def _apply_settings_to_widgets(self):
        """Make sure widgets reflect saved config on startup."""
        logger.debug("Applying settings to widgets")
        # Video/audio combos already initialized to config values when created.
        # Ensure max threads saved in config is used
        self.max_threads = int(self.config["General"].get("max_threads", "2"))
        # Ensure open_downloads buttons consistent size
        self.open_downloads_btn_start.setFixedWidth(220)
        self.open_downloads_btn_active.setFixedWidth(220)

    def browse_out(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder", self.out_display.text() or str(Path.home()))
        if path:
            self.out_display.setText(path)
            if "Paths" not in self.config:
                self.config["Paths"] = {}
            self.config["Paths"]["completed_downloads_directory"] = path
            with open(SETTINGS_FILE, "w") as fh:
                self.config.write(fh)

    def browse_temp(self):
        path = QFileDialog.getExistingDirectory(self, "Select temporary folder", self.temp_display.text() or str(Path.home()))
        if path:
            self.temp_display.setText(path)
            if "Paths" not in self.config:
                self.config["Paths"] = {}
            self.config["Paths"]["temporary_downloads_directory"] = path
            with open(SETTINGS_FILE, "w") as fh:
                self.config.write(fh)

    def open_downloads_folder(self):
        p = self.out_display.text()
        if not p or not os.path.isdir(p):
            QMessageBox.warning(self, "Open folder", f"Folder does not exist: {p}")
            return
        if sys.platform == "win32":
            os.startfile(os.path.normpath(p))
        elif sys.platform == "darwin":
            os.system(f'open "{p}"')
        else:
            os.system(f'xdg-open "{p}"')

    def _restore_defaults(self):
        logger.debug("Restoring defaults")
        # Reset general settings (but keep paths)
        keep_paths = dict(self.config.get("Paths", {}))
        self.config = load_or_create_config()
        self.config["Paths"] = keep_paths
        # Persist
        with open(SETTINGS_FILE, "w") as fh:
            self.config.write(fh)
        # Update UI live
        self.video_quality_combo.setCurrentText(self.config["General"].get("video_quality", "best"))
        self.video_ext_combo.setCurrentIndex(0)
        self.vcodec_combo.setCurrentIndex(0)
        self.audio_quality_combo.setCurrentIndex(0)
        self.audio_ext_combo.setCurrentIndex(0)
        self.acodec_combo.setCurrentIndex(0)
        self.max_threads_combo.setCurrentText(self.config["General"].get("max_threads", "2"))
        self.sponsorblock_cb.setChecked(self.config["General"].get("sponsorblock", "True") == "True")
        self.restrict_cb.setChecked(self.config["General"].get("restrict_filenames", "False") == "True")
        QMessageBox.information(self, "Defaults restored", "Settings were restored to defaults. Paths were preserved.")

    # ---------- placeholder creation & UI update helpers ----------
    def _create_placeholder(self, url: str):
        logger.debug("Creating placeholder for URL: %s", url)
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout()
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(8)

        # Left: title / status
        title_label = QLabel(f"Fetching title: {url}")
        title_label.setWordWrap(True)
        title_label.setFixedWidth(420)

        # Center/right: progress bar + right-side text (we will use progress.format to show percentage)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setFixedHeight(28)
        progress.setFormat("%p%")
        progress.setAlignment(Qt.AlignmentFlag.AlignCenter)

        right_text = QLabel("")  # will display "69.9% (11.74 MB / 18.24 MB)" etc.
        right_text.setFixedWidth(240)
        right_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(100)

        h.addWidget(title_label)
        h.addWidget(progress, stretch=1)
        h.addWidget(right_text)
        h.addWidget(cancel_btn)
        frame.setLayout(h)

        # insert at top
        self.scroll_layout.insertWidget(0, frame)

        ph = {"frame": frame, "title_label": title_label, "progress": progress, "right_text": right_text, "cancel": cancel_btn, "url": url}
        self.active_placeholders[url] = ph

        def on_cancel():
            logger.debug("Cancel clicked for %s", url)
            rec = self.active_workers.get(url)
            if rec:
                try:
                    rec["backend"].cancel()
                except Exception:
                    logger.exception("Error calling cancel on backend for %s", url)
            try:
                ph["cancel"].hide()
                ph["progress"].setFormat("Cancelled")
                ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#9e9e9e;}")
            except Exception:
                pass

        cancel_btn.clicked.connect(on_cancel)
        return ph

    # ---------- download pipeline ----------
    def on_download_clicked(self):
        raw = self.url_input.toPlainText().strip()
        if not raw:
            QMessageBox.information(self, "No URL", "Please paste at least one URL.")
            return
        urls = [l.strip() for l in raw.splitlines() if l.strip()]
        if not urls:
            return
        logger.debug("Starting downloads for urls: %s", urls)

        # ensure output/temp exist before starting
        if not self.out_display.text() or not os.path.isdir(self.out_display.text()):
            QMessageBox.warning(self, "Missing output folder", "Please configure a valid output folder in Advanced Settings before downloading.")
            self.tabs.setCurrentWidget(self.tab_adv)
            return
        if not self.temp_display.text() or not os.path.isdir(self.temp_display.text()):
            QMessageBox.warning(self, "Missing temp folder", "Please configure a valid temporary folder in Advanced Settings before downloading.")
            self.tabs.setCurrentWidget(self.tab_adv)
            return

        # switch to active tab and hide center button
        self.tabs.setCurrentWidget(self.tab_active)
        self.center_start_btn.hide()

        for url in urls:
            if url in self.active_placeholders or url in self.active_workers:
                logger.debug("URL already queued or running: %s", url)
                continue
            ph = self._create_placeholder(url)
            exp = PlaylistExpanderWorker(url)
            exp.expanded.connect(self._on_expanded)
            exp.failed.connect(self._on_expand_failed)
            self.threads_keep.append(exp)
            exp.start()
            logger.debug("PlaylistExpanderWorker started for %s", url)

    @pyqtSlot(str, list)
    def _on_expanded(self, original_url: str, items: list):
        logger.debug("_on_expanded for %s -> %d items", original_url, len(items))
        ph = self.active_placeholders.get(original_url)
        if ph:
            # show summary: queueing N items
            if len(items) > 1:
                ph["title_label"].setText(f"Queueing {len(items)} items from: {original_url}")
            else:
                ph["title_label"].setText(f"Queueing item: {original_url}")

        for item in items:
            # session duplicate check
            if item in self.session_urls:
                resp = QMessageBox.question(self, "Already downloaded", f"{item}\nThis URL was downloaded in this session. Redownload?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if resp != QMessageBox.StandardButton.Yes:
                    logger.debug("Skipping item already in session: %s", item)
                    self._remove_placeholder(original_url)
                    continue
            # run duplicate check background worker
            chk = DuplicateCheckerWorker(item, self.out_display.text())
            chk.result.connect(self._on_duplicate_checked)
            self.threads_keep.append(chk)
            chk.start()
            logger.debug("DuplicateCheckerWorker started for %s", item)

    @pyqtSlot(str, str)
    def _on_expand_failed(self, url: str, err: str):
        logger.debug("Expansion failed for %s: %s", url, err)
        ph = self.active_placeholders.get(url)
        if ph:
            ph["title_label"].setText(f"Expansion failed: {err}")
            ph["progress"].setFormat("Failed")
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")
            ph["cancel"].hide()

    @pyqtSlot(str, bool, str)
    def _on_duplicate_checked(self, url: str, exists: bool, prepared_path: str):
        logger.debug("_on_duplicate_checked: %s exists=%s path=%s", url, exists, prepared_path)
        ph = self.active_placeholders.get(url) or (next(iter(self.active_placeholders.values()), None))
        if not ph:
            ph = self._create_placeholder(url)
        if exists:
            res = QMessageBox.question(self, "File exists", f"A file matching this URL exists:\n{prepared_path}\nRedownload?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res != QMessageBox.StandardButton.Yes:
                # remove placeholder and stop
                logger.debug("User chose not to redownload %s", url)
                self._remove_placeholder(ph["url"])
                return
        # Update UI indicating starting
        ph["title_label"].setText(f"Starting: {url}")
        ph["progress"].setValue(0)
        ph["right_text"].setText("")  # clear

        # Create backend object and run on its own QThread
        backend = YT_DLP_Download(
            raw_url=url,
            temp_dl_loc=self.temp_display.text(),
            final_destination_dir=self.out_display.text(),
            download_mp3=self.audio_only_cb.isChecked(),
            allow_playlist=(self.playlist_mode.currentText() != "Download Single (ignore playlist)"),
            rate_limit=self.config["General"].get("rate_limit", "0"),
            video_quality=self.video_quality_combo.currentText(),
            video_ext=self.video_ext_combo.currentData() or "",
            audio_ext=self.audio_ext_combo.currentData() or "",
            video_codec=self.vcodec_combo.currentData() or "",
            audio_codec=self.acodec_combo.currentData() or "",
            restrict_filenames=self.restrict_cb.isChecked(),
            cookies=self.config["General"].get("cookies_file", "") or None,
            audio_quality=self.audio_quality_combo.currentData() or "192k",
            sponsorblock=(self.sponsorblock_cb.isChecked()),
            use_part_files=(self.config["General"].get("use_part_files", "True") == "True"),
        )

        thread = QThread()
        backend.moveToThread(thread)

        # Safe connections: backend emits primitives; GUI slots use url to map to placeholder
        backend.progress_updated.connect(lambda pct, u=url: self._on_progress(u, float(pct)))
        backend.status_updated.connect(lambda s, u=url: self._on_status(u, s))
        backend.title_updated.connect(lambda t, u=url: self._on_title(u, t))
        backend.error_occurred.connect(lambda e, u=url: self._on_error(u, e))
        backend.finished.connect(lambda ok, u=url: self._on_finished(u, ok))
        backend.prompt_overwrite.connect(lambda path, u=url: self._on_prompt_overwrite(u, path))

        thread.started.connect(backend.start_yt_download)
        backend.finished.connect(thread.quit)

        # Store mapping for main-thread updates and cancellation control
        self.active_workers[url] = {"thread": thread, "backend": backend, "placeholder": ph}
        self.threads_keep.append(thread)
        logger.debug("Starting backend thread for %s", url)
        thread.start()
        # remember in session
        self.session_urls.add(url)

    # ---------- GUI update slots (main thread) ----------
    @pyqtSlot(str, float)
    def _on_progress(self, url: str, pct: float):
        logger.debug("_on_progress %s: %s", url, pct)
        rec = self.active_workers.get(url)
        ph = (rec and rec.get("placeholder")) or self.active_placeholders.get(url)
        if not ph:
            logger.debug("No placeholder found for progress update: %s", url)
            return
        # update bar
        ph["progress"].setValue(int(pct))
        # compute human-readable downloaded/total using last status string if available
        # Note: backend.status_updated emits raw byte counts for downloaded/total; we handle in _on_status
        # leave percent in right_text until we get a proper status update

    @pyqtSlot(str, str)
    def _on_status(self, url: str, status_text: str):
        # status_text may be something like "12.3% (12345 / 23456)" from backend; we parse that to compute MB friendly display
        logger.debug("_on_status %s: %s", url, status_text)
        rec = self.active_workers.get(url)
        ph = (rec and rec.get("placeholder")) or self.active_placeholders.get(url)
        if not ph:
            return
        # Attempt to parse "xxx% (downloaded / total)" when provided
        try:
            # Many backends emit something like "69.9% (1234567 / 2345678)"
            if "(" in status_text and ")" in status_text:
                inside = status_text.split("(", 1)[1].rsplit(")", 1)[0]
                # inside example: "1234567 / 2345678" or "downloaded_bytes / total_bytes"
                parts = [p.strip() for p in inside.split("/", 1)]
                if len(parts) == 2:
                    dl = int(parts[0])
                    total = int(parts[1])
                    # compute human friendly
                    def human(n):
                        for unit in ("B", "KB", "MB", "GB", "TB"):
                            if n < 1024.0:
                                return f"{n:.2f} {unit}"
                            n /= 1024.0
                        return f"{n:.2f} PB"
                    pct = (dl / total) * 100 if total and total > 0 else 0.0
                    ph["right_text"].setText(f"{pct:.1f}% ({human(dl)} / {human(total)})")
                    # keep progress bar set via progress signal
                    return
        except Exception:
            logger.exception("Failed parsing status text: %s", status_text)
        # fallback: display raw status on right_text
        ph["right_text"].setText(status_text)

    @pyqtSlot(str, str)
    def _on_title(self, url: str, title: str):
        logger.debug("_on_title %s: %s", url, title)
        rec = self.active_workers.get(url)
        ph = (rec and rec.get("placeholder")) or self.active_placeholders.get(url)
        if not ph:
            return
        # Show title, but keep a short appended status if present
        ph["title_label"].setText(title)

    @pyqtSlot(str, str)
    def _on_error(self, url: str, errmsg: str):
        logger.error("_on_error %s: %s", url, errmsg)
        rec = self.active_workers.get(url)
        ph = (rec and rec.get("placeholder")) or self.active_placeholders.get(url)
        if ph:
            ph["title_label"].setText("Error")
            ph["progress"].setFormat("Failed")
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")
            try:
                ph["cancel"].hide()
            except Exception:
                pass
        QMessageBox.critical(self, "Download error", f"{url}\n\n{errmsg}")

    @pyqtSlot(str, bool)
    def _on_finished(self, url: str, ok: bool):
        logger.debug("_on_finished %s ok=%s", url, ok)
        rec = self.active_workers.pop(url, None)
        ph = (rec and rec.get("placeholder")) or self.active_placeholders.get(url)
        if not ph:
            logger.debug("No placeholder found to update finished state for %s", url)
            return
        try:
            ph["cancel"].hide()
        except Exception:
            pass
        if ok:
            ph["progress"].setValue(100)
            ph["right_text"].setText("100%")
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#2e7d32;}")
            ph["title_label"].setText("Completed: " + (ph["title_label"].text() or ph["url"]))
        else:
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")
            ph["title_label"].setText("Failed: " + (ph["title_label"].text() or ph["url"]))
        # schedule removal after brief pause
        def remove_later():
            try:
                self.scroll_layout.removeWidget(ph["frame"])
                ph["frame"].deleteLater()
            except Exception:
                pass
            self.active_placeholders.pop(ph["url"], None)
        QTimer.singleShot(2500, remove_later)

    # ---------- overwrite prompt handler ----------
    def _on_prompt_overwrite(self, url: str, filepath: str):
        logger.debug("_on_prompt_overwrite for %s -> %s", url, filepath)
        dlg = QMessageBox(self)
        dlg.setWindowTitle("File exists")
        dlg.setText(f"The file already exists:\n\n{filepath}\n\nWhat would you like to do?")
        btn_over = dlg.addButton("Overwrite", QMessageBox.ButtonRole.YesRole)
        btn_skip = dlg.addButton("Skip (do not download)", QMessageBox.ButtonRole.NoRole)
        dlg.exec()
        allow = dlg.clickedButton() == btn_over
        rec = self.active_workers.get(url)
        if rec:
            try:
                rec["backend"].provide_overwrite_response(allow)
            except Exception:
                logger.exception("Failed to call provide_overwrite_response for %s", url)
        if not allow:
            ph = rec["placeholder"] if rec else self.active_placeholders.get(url)
            if ph:
                try:
                    self.scroll_layout.removeWidget(ph["frame"])
                    ph["frame"].deleteLater()
                except Exception:
                    pass
                self.active_placeholders.pop(ph["url"], None)

    # ---------- helpers ----------
    def _remove_placeholder(self, url: str):
        ph = self.active_placeholders.pop(url, None)
        if ph:
            try:
                self.scroll_layout.removeWidget(ph["frame"])
                ph["frame"].deleteLater()
            except Exception:
                pass

# ---------- entry point ----------
def main():
    app = QApplication(sys.argv)
    wnd = MediaDownloaderApp()
    wnd.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
