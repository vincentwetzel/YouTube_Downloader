# MediaDownloaderApp.pyw
# PyQt6 application - full rewrite with logging in every step and safe worker pattern.
# Requires YT_DLP_Download.py in same folder.

import sys
import os
import logging
import configparser
import shutil
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QTextEdit, QPushButton, QCheckBox,
    QProgressBar, QHBoxLayout, QVBoxLayout, QGridLayout, QFileDialog,
    QMessageBox, QTabWidget, QComboBox, QGroupBox, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QTimer

from YT_DLP_Download import YT_DLP_Download

# Configure logging
logger = logging.getLogger("MediaDownloaderApp")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(ch)

SETTINGS_FILE = "settings.ini"

def ensure_config():
    cfg = configparser.ConfigParser()
    if os.path.exists(SETTINGS_FILE):
        cfg.read(SETTINGS_FILE)
    changed = False
    if "Paths" not in cfg:
        cfg["Paths"] = {
            "completed_downloads_directory": str(Path.cwd()),
            "temporary_downloads_directory": str(Path.cwd() / "dl_temp"),
        }
        changed = True
    if "General" not in cfg:
        cfg["General"] = {
            "max_threads": "2",
            "video_quality": "best",
            "video_ext": "",
            "audio_ext": "",
            "video_codec": "",
            "audio_codec": "",
            "audio_quality": "best",
            "restrict_filenames": "False",
            "sponsorblock": "False",
            "yt_dlp_channel": "stable",
            "yt_dlp_version": "",
            "cookies_mode": "None",
            "cookies_file": "",
            "use_part_files": "True",
            "rate_limit": "0",
            "theme": "system",
        }
        changed = True
    if changed:
        with open(SETTINGS_FILE, "w") as fh:
            cfg.write(fh)
    return cfg

class UrlTextEdit(QTextEdit):
    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        try:
            from PyQt6.QtGui import QGuiApplication
            cb = QGuiApplication.clipboard()
            txt = cb.text().strip()
            if txt.startswith("http://") or txt.startswith("https://"):
                logger.debug("Pasted URL from clipboard into URL field: %s", txt)
                self.setPlainText(txt)
        except Exception:
            pass

# ---------- Background workers ----------
class PlaylistExpanderWorker(QThread):
    # emits original_url, list_of_item_urls
    expanded = pyqtSignal(str, list)
    failed = pyqtSignal(str, str)
    def __init__(self, url: str):
        super().__init__()
        self.url = url
    def run(self):
        logger.debug("PlaylistExpanderWorker starting for URL: %s", self.url)
        try:
            import yt_dlp, json
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
            logger.debug("PlaylistExpanderWorker expanded %s -> %d items", self.url, len(items))
            self.expanded.emit(self.url, items)
        except Exception as e:
            logger.exception("PlaylistExpanderWorker failed for %s: %s", self.url, e)
            self.failed.emit(self.url, str(e))

class DuplicateCheckerWorker(QThread):
    # emits url, exists(bool), prepared_path (str)
    result = pyqtSignal(str, bool, str)
    def __init__(self, url: str, outdir: str):
        super().__init__()
        self.url = url
        self.outdir = outdir
    def run(self):
        logger.debug("DuplicateCheckerWorker starting for URL: %s", self.url)
        try:
            import yt_dlp, os
            opts = {"quiet": True, "no_warnings": True, "paths":{"home": self.outdir}}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                try:
                    prepared = ydl.prepare_filename(info)
                except Exception:
                    prepared = ""
            exists = bool(prepared and os.path.exists(prepared))
            logger.debug("DuplicateCheckerWorker result for %s exists=%s path=%s", self.url, exists, prepared)
            self.result.emit(self.url, exists, prepared or "")
        except Exception as e:
            logger.exception("DuplicateCheckerWorker error for %s: %s", self.url, e)
            self.result.emit(self.url, False, "")

# ---------- Main App ----------
class MediaDownloaderApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Downloader")
        self.resize(1000, 700)
        self.config = ensure_config()

        # Session state
        self.session_urls = set()
        self.active_placeholders: Dict[str, dict] = {}  # url -> placeholder widgets
        self.active_workers: Dict[str, dict] = {}       # url -> {thread, backend}
        self.threads_keep = []                           # keep background worker refs

        self.max_threads = int(self.config["General"].get("max_threads", "2"))

        self._build_ui()

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

    # ---------- Start Tab ----------
    def _build_tab_start(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8,8,8,8)

        top_row = QHBoxLayout()
        top_row.addStretch()
        self.open_downloads_btn_start = QPushButton("Open Downloads Folder")
        self.open_downloads_btn_start.setFixedHeight(36)
        self.open_downloads_btn_start.clicked.connect(self.open_downloads_folder)
        top_row.addWidget(self.open_downloads_btn_start)
        layout.addLayout(top_row)

        lbl = QLabel("Video/Playlist URL(s):")
        self.url_input = UrlTextEdit()
        self.url_input.setFixedHeight(120)

        self.download_btn = QPushButton("Download")
        self.download_btn.setFixedHeight(120)
        self.download_btn.setFixedWidth(180)
        self.download_btn.clicked.connect(self.on_download_clicked)

        url_row = QHBoxLayout()
        url_row.addWidget(self.url_input, stretch=1)
        url_row.addWidget(self.download_btn)

        # Groups: video / audio
        groups_row = QHBoxLayout()
        # Video
        vg = QGroupBox("Video Settings")
        vg_layout = QGridLayout()
        self.video_quality_combo = QComboBox()
        self.video_quality_combo.addItems(["best","2160p","1440p","1080p","720p","480p"])
        self.video_quality_combo.setCurrentText(self.config["General"].get("video_quality","best"))
        self.video_ext_combo = QComboBox()
        self.video_ext_combo.addItem("default (yt-dlp)", "")
        for ex in ("mp4","mkv","webm"):
            self.video_ext_combo.addItem(ex, ex)
        self.video_ext_combo.setCurrentIndex(0)
        self.vcodec_combo = QComboBox()
        self.vcodec_combo.addItem("default","")
        self.vcodec_combo.addItem("h264","h264")
        self.vcodec_combo.addItem("h265","h265")
        self.vcodec_combo.addItem("vp9","vp9")
        vg_layout.addWidget(QLabel("Quality:"),0,0)
        vg_layout.addWidget(self.video_quality_combo,0,1)
        vg_layout.addWidget(QLabel("Extension:"),1,0)
        vg_layout.addWidget(self.video_ext_combo,1,1)
        vg_layout.addWidget(QLabel("Codec:"),2,0)
        vg_layout.addWidget(self.vcodec_combo,2,1)
        vg.setLayout(vg_layout)

        # Audio
        ag = QGroupBox("Audio Settings")
        ag_layout = QGridLayout()
        self.audio_only_cb = QCheckBox("Download audio only")
        self.audio_quality_combo = QComboBox()
        audio_opts = [("best","best"),("64 KB","64k"),("128 KB","128k"),("192 KB","192k"),("256 KB","256k"),("320 KB","320k")]
        for lab,val in audio_opts:
            self.audio_quality_combo.addItem(lab, val)
        self.audio_ext_combo = QComboBox()
        self.audio_ext_combo.addItem("default (yt-dlp)","")
        for ex in ("mp3","m4a","opus","aac","flac"):
            self.audio_ext_combo.addItem(ex, ex)
        self.acodec_combo = QComboBox()
        self.acodec_combo.addItem("default","")
        for a in ("aac","opus","mp3","vorbis","flac"):
            self.acodec_combo.addItem(a,a)
        ag_layout.addWidget(QLabel("Quality:"),0,0)
        ag_layout.addWidget(self.audio_quality_combo,0,1)
        ag_layout.addWidget(QLabel("Extension:"),1,0)
        ag_layout.addWidget(self.audio_ext_combo,1,1)
        ag_layout.addWidget(QLabel("Codec:"),2,0)
        ag_layout.addWidget(self.acodec_combo,2,1)
        ag.setLayout(ag_layout)

        groups_row.addWidget(vg)
        groups_row.addWidget(ag)

        # small row
        small = QHBoxLayout()
        self.playlist_mode = QComboBox()
        self.playlist_mode.addItems(["Ask","Download All (no prompt)","Download Single (ignore playlist)"])
        self.max_threads_combo = QComboBox()
        self.max_threads_combo.addItems(["1","2","3","4"])
        self.max_threads_combo.setCurrentText(self.config["General"].get("max_threads","2"))
        self.max_threads_combo.currentTextChanged.connect(self.on_max_threads_changed)
        small.addWidget(QLabel("Playlist:"))
        small.addWidget(self.playlist_mode)
        small.addStretch()
        small.addWidget(QLabel("Max concurrent:"))
        small.addWidget(self.max_threads_combo)
        small.addWidget(self.audio_only_cb)

        layout.addWidget(lbl)
        layout.addLayout(url_row)
        layout.addLayout(groups_row)
        layout.addLayout(small)
        layout.addStretch()
        self.tab_start.setLayout(layout)

    # ---------- Active Tab ----------
    def _build_tab_active(self):
        layout = QVBoxLayout()
        top_row = QHBoxLayout()
        top_row.addStretch()
        self.open_downloads_btn_active = QPushButton("Open Downloads Folder")
        self.open_downloads_btn_active.setFixedHeight(36)
        self.open_downloads_btn_active.clicked.connect(self.open_downloads_folder)
        top_row.addWidget(self.open_downloads_btn_active)
        layout.addLayout(top_row)

        # Centered start button on first run
        self.center_start_btn = QPushButton("Start a New Download")
        self.center_start_btn.setFixedSize(220,48)
        self.center_start_btn.clicked.connect(lambda: self.tabs.setCurrentWidget(self.tab_start))
        center_row = QHBoxLayout()
        center_row.addStretch()
        center_row.addWidget(self.center_start_btn)
        center_row.addStretch()
        layout.addLayout(center_row)

        # Scroll area for downloads
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        self.scroll_layout.addStretch()
        self.scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)
        self.tab_active.setLayout(layout)

    # ---------- Advanced Tab ----------
    def _build_tab_advanced(self):
        layout = QVBoxLayout()
        # paths display
        out_lbl = QLabel("Output folder:")
        self.out_display = QLabel(self.config["Paths"]["completed_downloads_directory"])
        btn_out = QPushButton("📁")
        btn_out.clicked.connect(self.browse_out)
        temp_lbl = QLabel("Temporary folder:")
        self.temp_display = QLabel(self.config["Paths"]["temporary_downloads_directory"])
        btn_temp = QPushButton("📁")
        btn_temp.clicked.connect(self.browse_temp)
        layout.addWidget(out_lbl)
        row1 = QHBoxLayout()
        row1.addWidget(self.out_display)
        row1.addWidget(btn_out)
        layout.addLayout(row1)
        layout.addWidget(temp_lbl)
        row2 = QHBoxLayout()
        row2.addWidget(self.temp_display)
        row2.addWidget(btn_temp)
        layout.addLayout(row2)

        # sponsorblock, restrict filenames
        self.sponsorblock_cb = QCheckBox("Enable SponsorBlock")
        self.sponsorblock_cb.setChecked(self.config["General"].get("sponsorblock","False")=="True")
        self.restrict_cb = QCheckBox("Restrict filenames")
        self.restrict_cb.setChecked(self.config["General"].get("restrict_filenames","False")=="True")
        layout.addWidget(self.sponsorblock_cb)
        layout.addWidget(self.restrict_cb)
        layout.addStretch()
        self.tab_adv.setLayout(layout)

    # ---------- helpers ----------
    def browse_out(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder", self.out_display.text())
        if path:
            self.out_display.setText(path)
            self.config["Paths"]["completed_downloads_directory"] = path
            with open(SETTINGS_FILE,"w") as fh:
                self.config.write(fh)

    def browse_temp(self):
        path = QFileDialog.getExistingDirectory(self, "Select temporary folder", self.temp_display.text())
        if path:
            self.temp_display.setText(path)
            self.config["Paths"]["temporary_downloads_directory"] = path
            with open(SETTINGS_FILE,"w") as fh:
                self.config.write(fh)

    def open_downloads_folder(self):
        p = self.out_display.text()
        if not os.path.exists(p):
            QMessageBox.warning(self, "Open folder", f"Folder does not exist: {p}")
            return
        if sys.platform == "win32":
            os.startfile(os.path.normpath(p))
        elif sys.platform == "darwin":
            os.system(f'open "{p}"')
        else:
            os.system(f'xdg-open "{p}"')

    def on_max_threads_changed(self, text):
        try:
            self.max_threads = int(text)
            self.config["General"]["max_threads"] = str(self.max_threads)
            with open(SETTINGS_FILE,"w") as fh:
                self.config.write(fh)
            logger.debug("Max threads set to %d", self.max_threads)
        except Exception:
            logger.exception("Invalid max threads value: %s", text)

    # ---------- placeholder / queue logic ----------
    def _create_placeholder(self, url: str):
        logger.debug("Creating placeholder for url: %s", url)
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout()
        h.setContentsMargins(6,6,6,6)
        label = QLabel(f"Fetching title: {url}")
        label.setWordWrap(True)
        label.setFixedWidth(360)
        progress = QProgressBar()
        progress.setRange(0,100)
        progress.setValue(0)
        progress.setFixedHeight(28)
        progress.setFormat("%p%")
        cancel = QPushButton("Cancel")
        cancel.setFixedWidth(100)
        h.addWidget(label)
        h.addWidget(progress, stretch=1)
        h.addWidget(cancel)
        frame.setLayout(h)
        self.scroll_layout.insertWidget(0, frame)
        ph = {"frame": frame, "label": label, "progress": progress, "cancel": cancel, "url": url}
        self.active_placeholders[url] = ph

        def on_cancel():
            # if worker exists, cancel it
            rec = self.active_workers.get(url)
            if rec:
                try:
                    rec["backend"].cancel()
                except Exception:
                    logger.exception("Exception calling cancel on backend for %s", url)
            # update UI
            try:
                ph["cancel"].hide()
                ph["progress"].setFormat("Cancelled")
                ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#9e9e9e;}")
            except Exception:
                pass
        cancel.clicked.connect(on_cancel)
        return ph

    # ---------- main handler: click Download ----------
    def on_download_clicked(self):
        raw = self.url_input.toPlainText().strip()
        if not raw:
            QMessageBox.information(self, "No URL", "Please paste at least one URL.")
            return
        urls = [l.strip() for l in raw.splitlines() if l.strip()]
        if not urls:
            return
        logger.debug("Download clicked, urls=%s", urls)

        # switch to active tab
        self.tabs.setCurrentWidget(self.tab_active)
        self.center_start_btn.hide()

        for url in urls:
            if url in self.active_placeholders or url in self.active_workers:
                logger.debug("URL already queued: %s", url)
                continue
            ph = self._create_placeholder(url)
            # start playlist expander
            exp = PlaylistExpanderWorker(url)
            exp.expanded.connect(self._on_expanded)
            exp.failed.connect(self._on_expand_failed)
            self.threads_keep.append(exp)
            exp.start()
            logger.debug("Started PlaylistExpanderWorker for %s", url)

    # ---------- slots for playlist expansion ----------
    @pyqtSlot(str, list)
    def _on_expanded(self, original_url: str, items: list):
        logger.debug("Expanded %s -> %d item(s)", original_url, len(items))
        # Update placeholder title text for original_url (first item)
        ph = self.active_placeholders.get(original_url)
        if ph:
            ph["label"].setText(f"Queueing {len(items)} item(s) from: {original_url}")

        for item in items:
            # session duplicate check
            if item in self.session_urls:
                logger.debug("Item %s already in session", item)
                resp = QMessageBox.question(self, "Already downloaded this session", f"{item}\nRedownload?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if resp != QMessageBox.StandardButton.Yes:
                    # remove placeholder for original_url
                    self._remove_placeholder(original_url)
                    continue

            # run duplicate checker
            chk = DuplicateCheckerWorker(item, self.out_display.text())
            chk.result.connect(self._on_duplicate_checked)
            self.threads_keep.append(chk)
            chk.start()
            logger.debug("Started DuplicateCheckerWorker for %s", item)

    @pyqtSlot(str, str)
    def _on_expand_failed(self, url: str, err: str):
        logger.debug("Playlist expansion failed for %s: %s", url, err)
        ph = self.active_placeholders.get(url)
        if ph:
            ph["label"].setText(f"Expansion failed: {err}")
            ph["progress"].setFormat("Failed")
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")
            ph["cancel"].hide()

    # ---------- duplicate checked ----------
    @pyqtSlot(str, bool, str)
    def _on_duplicate_checked(self, url: str, exists: bool, path: str):
        logger.debug("Duplicate check for %s: exists=%s path=%s", url, exists, path)
        # find a placeholder to attach: prefer same url, else any placeholder
        ph = self.active_placeholders.get(url) or (next(iter(self.active_placeholders.values()), None))
        if not ph:
            logger.debug("No placeholder available for %s - creating new", url)
            ph = self._create_placeholder(url)

        if exists:
            res = QMessageBox.question(self, "File exists", f"A file matching this URL exists:\n{path}\nRedownload?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res != QMessageBox.StandardButton.Yes:
                logger.debug("User chose not to redownload %s - removing placeholder", url)
                self._remove_placeholder(ph["url"])
                return

        # update UI
        ph["label"].setText(f"Starting download: {url}")
        ph["progress"].setValue(0)

        # configure backend
        backend = YT_DLP_Download(
            raw_url=url,
            temp_dl_loc=self.temp_display.text(),
            final_destination_dir=self.out_display.text(),
            download_mp3=self.audio_only_cb.isChecked(),
            allow_playlist=(self.playlist_mode.currentText() != "Download Single (ignore playlist)"),
            rate_limit=self.config["General"].get("rate_limit","0"),
            video_quality=self.video_quality_combo.currentText(),
            video_ext=self.video_ext_combo.currentData() or "",
            audio_ext=self.audio_ext_combo.currentData() or "",
            video_codec=self.vcodec_combo.currentData() or "",
            audio_codec=self.acodec_combo.currentData() or "",
            restrict_filenames=self.restrict_cb.isChecked(),
            cookies=self.config["General"].get("cookies_file","") or None,
            audio_quality=self.audio_quality_combo.currentData() or "192k",
            sponsorblock=self.sponsorblock_cb.isChecked(),
            use_part_files=self.config["General"].get("use_part_files","True") == "True",
        )

        # create QThread and move backend
        thread = QThread()
        backend.moveToThread(thread)

        # connect signals to safe main-thread slots (pass url primitive)
        backend.progress_updated.connect(lambda pct, u=url: self._on_progress(u, float(pct)))
        backend.status_updated.connect(lambda s, u=url: self._on_status(u, s))
        backend.title_updated.connect(lambda t, u=url: self._on_title(u, t))
        backend.error_occurred.connect(lambda e, u=url: self._on_error(u, e))
        backend.finished.connect(lambda ok, u=url: self._on_finished(u, ok))
        backend.prompt_overwrite.connect(lambda filepath, u=url: self._on_prompt_overwrite(u, filepath))

        thread.started.connect(backend.start_yt_download)
        backend.finished.connect(thread.quit)

        self.active_workers[url] = {"thread": thread, "backend": backend, "placeholder": ph}
        self.threads_keep.append(thread)
        logger.debug("Starting download thread for %s", url)
        thread.start()
        self.session_urls.add(url)

    # ---------- UI update slots (main-thread) ----------
    def _on_progress(self, url: str, pct: float):
        logger.debug("GUI _on_progress for %s: %s", url, pct)
        ph = self.active_workers.get(url, {}).get("placeholder") or self.active_placeholders.get(url)
        if not ph:
            logger.debug("No placeholder found for progress update: %s", url)
            return
        ph["progress"].setValue(int(pct))

    def _on_status(self, url: str, status: str):
        logger.debug("GUI _on_status for %s: %s", url, status)
        ph = self.active_workers.get(url, {}).get("placeholder") or self.active_placeholders.get(url)
        if not ph:
            return
        ph["label"].setText(status)

    def _on_title(self, url: str, title: str):
        logger.debug("GUI _on_title for %s: %s", url, title)
        ph = self.active_workers.get(url, {}).get("placeholder") or self.active_placeholders.get(url)
        if ph:
            ph["label"].setText(title)

    def _on_error(self, url: str, errmsg: str):
        logger.error("GUI _on_error for %s: %s", url, errmsg)
        ph = self.active_workers.get(url, {}).get("placeholder") or self.active_placeholders.get(url)
        if ph:
            ph["label"].setText("Error")
            ph["progress"].setFormat("Failed")
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")
        QMessageBox.critical(self, "Download error", f"{url}\n\n{errmsg}")

    def _on_finished(self, url: str, ok: bool):
        logger.debug("GUI _on_finished for %s ok=%s", url, ok)
        rec = self.active_workers.pop(url, None)
        ph = rec["placeholder"] if rec else self.active_placeholders.get(url)
        if not ph:
            logger.debug("No placeholder to update on finished for %s", url)
            return
        try:
            ph["cancel"].hide()
        except Exception:
            pass
        if ok:
            ph["progress"].setValue(100)
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#2e7d32;}")
            ph["label"].setText("Completed")
        else:
            ph["progress"].setStyleSheet("QProgressBar::chunk{background-color:#c62828;}")
            ph["label"].setText("Failed")
        # remove UI entry after short delay
        def remove_later():
            try:
                self.scroll_layout.removeWidget(ph["frame"])
                ph["frame"].deleteLater()
            except Exception:
                pass
            # clean mapping
            self.active_placeholders.pop(ph["url"], None)
        QTimer.singleShot(2000, remove_later)

    # ---------- prompt overwrite handler ----------
    def _on_prompt_overwrite(self, url: str, filepath: str):
        logger.debug("Prompt overwrite requested for %s -> %s", url, filepath)
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
                logger.exception("Error providing overwrite response to backend for %s", url)
        if not allow:
            # remove placeholder
            ph = rec["placeholder"] if rec else self.active_placeholders.get(url)
            if ph:
                try:
                    self.scroll_layout.removeWidget(ph["frame"])
                    ph["frame"].deleteLater()
                except Exception:
                    pass
                self.active_placeholders.pop(ph["url"], None)

    # ---------- placeholder removal ----------
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
