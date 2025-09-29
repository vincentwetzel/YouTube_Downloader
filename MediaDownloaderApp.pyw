"""
MediaDownloaderApp.pyw
PyQt6 GUI frontend for YT_DLP_Download backend with:
- Multi-tab interface (New Download / Active Downloads / Settings)
- Thread pool concurrency (1–4 simultaneous downloads)
- Configurable temp + output folders
"""

import sys
import os
import configparser
from pathlib import Path

from PyQt6.QtCore import Qt, QThread
from PyQt6 import QtCore
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QCheckBox,
    QProgressBar, QTextEdit, QHBoxLayout, QVBoxLayout, QGridLayout,
    QFileDialog, QMessageBox, QTabWidget, QComboBox, QListWidget, QListWidgetItem,
    QFrame
)

from YT_DLP_Download import YT_DLP_Download


def qt_message_handler(mode, context, message):
    mode_map = {
        QtCore.QtMsgType.QtDebugMsg: "DEBUG",
        QtCore.QtMsgType.QtInfoMsg: "INFO",
        QtCore.QtMsgType.QtWarningMsg: "WARNING",
        QtCore.QtMsgType.QtCriticalMsg: "CRITICAL",
        QtCore.QtMsgType.QtFatalMsg: "FATAL",
    }
    print(f"[Qt-{mode_map.get(mode, 'UNKNOWN')}] {message}")


QtCore.qInstallMessageHandler(qt_message_handler)

SETTINGS_FILE = "settings.ini"


class DownloadWorker(QThread):
    """Run YT_DLP_Download in a worker thread."""

    def __init__(self, raw_url, temp_dl_loc, final_dir, download_mp3):
        super().__init__()
        self.backend = YT_DLP_Download(raw_url, temp_dl_loc, final_dir, download_mp3)

    def run(self):
        self.backend.get_video_title()
        self.backend.start_yt_download()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Media Downloader")
        self.resize(900, 600)

        # Load settings
        self.config = configparser.ConfigParser()
        self._load_settings()

        # --- Tab Widget ---
        self.tabs = QTabWidget()

        # Tab 1: New Download
        self.tab_new = QWidget()
        self._setup_tab_new()

        # Tab 2: Active Downloads
        self.tab_active = QWidget()
        self._setup_tab_active()

        # Tab 3: Settings (placeholder)
        self.tab_settings = QWidget()
        self._setup_tab_settings()

        self.tabs.addTab(self.tab_new, "New Download")
        self.tabs.addTab(self.tab_active, "Active Downloads")
        self.tabs.addTab(self.tab_settings, "Settings")

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)

        # Concurrency control
        self.max_threads = int(self.max_threads_combo.currentText())
        self.active_downloads = []  # [(worker, backend, widgets_dict)]

    def _setup_tab_new(self):
        layout = QVBoxLayout()

        # URL input
        self.url_label = QLabel("Video/Playlist URL(s):")
        self.url_input = QTextEdit()
        self.url_input.setPlaceholderText("Paste one or more URLs here...")
        self.url_input.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.url_input.setFixedHeight(100)

        # Big download button
        self.download_btn = QPushButton("Download")
        self.download_btn.setFixedHeight(100)
        self.download_btn.setMinimumWidth(150)
        self.download_btn.clicked.connect(self.on_download)

        url_row = QHBoxLayout()
        url_row.addWidget(self.url_input, stretch=1)
        url_row.addWidget(self.download_btn)

        # Audio + Max threads (side by side)
        self.audio_checkbox = QCheckBox("Download audio (mp3)")
        self.max_threads_label = QLabel("Max simultaneous downloads:")
        self.max_threads_combo = QComboBox()
        self.max_threads_combo.addItems(["1", "2", "3", "4"])
        self.max_threads_combo.setCurrentText(self.config["General"].get("max_threads", "2"))
        self.max_threads_combo.currentTextChanged.connect(self.on_threads_changed)

        options_row = QHBoxLayout()
        options_row.addWidget(self.audio_checkbox)
        options_row.addWidget(self.max_threads_label)
        options_row.addWidget(self.max_threads_combo)
        options_row.addStretch()

        # Open folder button (bigger)
        self.open_folder_btn = QPushButton("Open Downloads Folder")
        self.open_folder_btn.setMinimumHeight(40)
        self.open_folder_btn.setStyleSheet("font-weight: bold;")
        self.open_folder_btn.clicked.connect(self.on_open_folder)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.open_folder_btn)

        layout.addWidget(self.url_label)
        layout.addLayout(url_row)
        layout.addLayout(options_row)
        layout.addLayout(btn_row)

        self.tab_new.setLayout(layout)

    # -----------------------------
    # Tab 1: New Download
    # -----------------------------
    def _setup_tab_settings(self):
        layout = QVBoxLayout()

        # Output folder
        self.outdir_label = QLabel("Output folder:")
        self.outdir_display = QLabel(self.config["Paths"]["completed_downloads_directory"])
        self.outdir_display.setStyleSheet("border: 1px solid gray; padding: 4px; background-color: #f0f0f0;")
        self.browse_outdir_btn = QPushButton("📁")
        self.browse_outdir_btn.setFixedWidth(40)
        self.browse_outdir_btn.clicked.connect(self.on_browse_outdir)

        # Temp folder
        self.temp_label = QLabel("Temporary folder:")
        self.temp_display = QLabel(self.config["Paths"]["temporary_downloads_directory"])
        self.temp_display.setStyleSheet("border: 1px solid gray; padding: 4px; background-color: #f0f0f0;")
        self.browse_temp_btn = QPushButton("📁")
        self.browse_temp_btn.setFixedWidth(40)
        self.browse_temp_btn.clicked.connect(self.on_browse_temp)

        grid = QGridLayout()
        grid.addWidget(self.outdir_label, 0, 0)
        grid.addWidget(self.outdir_display, 0, 1)
        grid.addWidget(self.browse_outdir_btn, 0, 2)

        grid.addWidget(self.temp_label, 1, 0)
        grid.addWidget(self.temp_display, 1, 1)
        grid.addWidget(self.browse_temp_btn, 1, 2)

        layout.addLayout(grid)
        layout.addStretch()
        self.tab_settings.setLayout(layout)

    # -----------------------------
    # Tab 2: Active Downloads
    # -----------------------------
    def _setup_tab_active(self):
        layout = QVBoxLayout()
        self.active_list = QVBoxLayout()  # holds frames for each active download
        layout.addLayout(self.active_list)
        layout.addStretch()
        self.tab_active.setLayout(layout)

    # -----------------------------
    # Tab 3: Settings (placeholder)
    # -----------------------------
    def _setup_tab_settings(self):
        layout = QVBoxLayout()

        # Output folder
        self.outdir_label = QLabel("Output folder:")
        self.outdir_display = QLabel(self.config["Paths"]["completed_downloads_directory"])
        self.outdir_display.setStyleSheet("border: 1px solid gray; padding: 4px; background-color: #f0f0f0;")
        self.browse_outdir_btn = QPushButton("📁")
        self.browse_outdir_btn.setFixedWidth(40)
        self.browse_outdir_btn.clicked.connect(self.on_browse_outdir)

        # Temp folder
        self.temp_label = QLabel("Temporary folder:")
        self.temp_display = QLabel(self.config["Paths"]["temporary_downloads_directory"])
        self.temp_display.setStyleSheet("border: 1px solid gray; padding: 4px; background-color: #f0f0f0;")
        self.browse_temp_btn = QPushButton("📁")
        self.browse_temp_btn.setFixedWidth(40)
        self.browse_temp_btn.clicked.connect(self.on_browse_temp)

        grid = QGridLayout()
        grid.addWidget(self.outdir_label, 0, 0)
        grid.addWidget(self.outdir_display, 0, 1)
        grid.addWidget(self.browse_outdir_btn, 0, 2)

        grid.addWidget(self.temp_label, 1, 0)
        grid.addWidget(self.temp_display, 1, 1)
        grid.addWidget(self.browse_temp_btn, 1, 2)

        layout.addLayout(grid)
        layout.addStretch()
        self.tab_settings.setLayout(layout)

    # -----------------------------
    # UI Actions
    # -----------------------------
    def on_browse_outdir(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output directory", os.getcwd())
        if folder:
            self.outdir_display.setText(folder)
            self.config["Paths"]["completed_downloads_directory"] = folder
            self._save_settings()

    def on_browse_temp(self):
        folder = QFileDialog.getExistingDirectory(self, "Select temporary directory", os.getcwd())
        if folder:
            self.temp_display.setText(folder)
            self.config["Paths"]["temporary_downloads_directory"] = folder
            self._save_settings()

    def on_open_folder(self):
        path = self.outdir_display.text().strip()
        if not os.path.exists(path):
            QMessageBox.warning(self, "Open folder", "Folder does not exist: " + path)
            return
        if sys.platform == "win32":
            os.startfile(os.path.normpath(path))
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def on_threads_changed(self, value: str):
        self.max_threads = int(value)
        self.config["General"]["max_threads"] = value
        self._save_settings()

    def on_download(self):
        urls = [u.strip() for u in self.url_input.toPlainText().splitlines() if u.strip()]
        if not urls:
            QMessageBox.warning(self, "Missing URL", "Please enter at least one Media URL.")
            return

        outdir = self.outdir_display.text().strip()
        tempdir = self.temp_display.text().strip()
        audio_only = self.audio_checkbox.isChecked()

        for url in urls:
            worker = DownloadWorker(url, tempdir, outdir, audio_only)
            backend = worker.backend

            # Frame for each download
            frame = QFrame()
            flayout = QHBoxLayout(frame)

            title_label = QLabel("Fetching title...")
            progress_bar = QProgressBar()
            progress_bar.setRange(0, 100)
            progress_bar.setFixedHeight(30)
            progress_bar.setFormat("%p%")
            progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            progress_bar.setStyleSheet("""
                QProgressBar {
                    text-align: center;
                }
                QProgressBar::chunk {
                    background-color: teal;
                }
            """)

            cancel_btn = QPushButton("Cancel")
            cancel_btn.setFixedHeight(30)

            flayout.addWidget(title_label, stretch=2)
            flayout.addWidget(progress_bar, stretch=3)
            flayout.addWidget(cancel_btn, stretch=1)

            self.active_list.addWidget(frame)

            # Connect signals
            backend.title_updated.connect(title_label.setText)
            backend.progress_updated.connect(lambda v, pb=progress_bar: pb.setValue(int(v)))
            backend.error_occurred.connect(lambda msg, pb=progress_bar: self._mark_failed(pb, msg))
            backend.finished.connect(
                lambda success, pb=progress_bar, btn=cancel_btn: self._mark_finished(success, pb, btn))

            cancel_btn.clicked.connect(lambda b=backend: b.cancel())

            self.active_downloads.append(
                (worker, backend, {"frame": frame, "progress": progress_bar, "cancel": cancel_btn}))
            worker.start()

    def _mark_finished(self, success: bool, progress_bar: QProgressBar, cancel_btn: QPushButton):
        cancel_btn.hide()
        if success:
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: green; }")
            progress_bar.setValue(100)
        else:
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
            progress_bar.setFormat("Failed")

    def _mark_failed(self, progress_bar: QProgressBar, msg: str):
        progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        progress_bar.setFormat("Failed")
        QMessageBox.critical(self, "Error", msg)

    # -----------------------------
    # Signal Handlers
    # -----------------------------
    def _log_status(self, widget, msg: str):
        print(msg)

    def _show_error(self, widget, msg: str):
        QMessageBox.critical(self, "Error", msg)

    def _prompt_overwrite(self, filepath: str):
        res = QMessageBox.question(
            self,
            "File exists",
            f"The file already exists:\n{filepath}\n\nDo you want to overwrite it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            os.remove(filepath)

    def _on_finished(self, worker, frame):
        frame.setStyleSheet("opacity: 0.5;")
        # Remove from active downloads
        self.active_downloads = [(w, b, wd) for (w, b, wd) in self.active_downloads if w != worker]
        # Check queue: start next waiting download if under max_threads
        running = [w for w, _, _ in self.active_downloads if w.isRunning()]
        waiting = [w for w, _, _ in self.active_downloads if not w.isRunning()]
        while len(running) < self.max_threads and waiting:
            nxt = waiting.pop(0)
            nxt.start()
            running.append(nxt)

    # -----------------------------
    # Settings persistence
    # -----------------------------
    def _load_settings(self):
        # Always ensure defaults
        defaults = {
            "Paths": {
                "completed_downloads_directory": str(Path.cwd()),
                "temporary_downloads_directory": str(Path.cwd() / "temp"),
            },
            "General": {
                "max_threads": "2",
            }
        }

        # Load file if it exists
        if os.path.exists(SETTINGS_FILE):
            self.config.read(SETTINGS_FILE)

        # Ensure all sections/keys exist
        for section, kv in defaults.items():
            if section not in self.config:
                self.config[section] = {}
            for key, val in kv.items():
                if key not in self.config[section]:
                    self.config[section][key] = val

        # Save back (this ensures missing sections/keys get written)
        self._save_settings()

    def _save_settings(self):
        with open(SETTINGS_FILE, "w") as f:
            self.config.write(f)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


pass