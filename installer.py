from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QFontDatabase, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

try:
    from RinUI import ThemeManager as RinUIThemeManager
except ImportError:
    RinUIThemeManager = None

try:
    from .utils import DownloadCancelled, download_and_extract_classisland, get_installed_executable
except ImportError:
    from utils import DownloadCancelled, download_and_extract_classisland, get_installed_executable


class DownloadThread(QThread):
    progress = Signal(int)
    failed = Signal(str)
    completed = Signal(str)

    def run(self):
        try:
            executable = download_and_extract_classisland(
                self.progress.emit, self.isInterruptionRequested
            )
        except DownloadCancelled:
            return
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
        else:
            if not self.isInterruptionRequested():
                self.completed.emit(str(executable))


class Installer(QWidget):
    """RinUI-styled launcher window for ClassIsland."""

    WIDTH = 467
    HEIGHT = 206

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("installer")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        font_id = QFontDatabase.addApplicationFont(
            str(Path(__file__).with_name("HarmonyOS_Sans_SC_Regular.ttf"))
        )
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                self.setFont(QFont(families[0]))

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")

        self.title = QLabel("正在安装 ClassIsland", self.panel)
        self.title.setObjectName("title")
        self.message = QLabel("这可能需要 3~5 分钟。请勿关闭 ClassWidgets。", self.panel)
        self.message.setObjectName("message")
        self.message.setWordWrap(True)

        self.progress_bar = QProgressBar(self.panel)
        self.progress_bar.setObjectName("progress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)

        self.icon = QLabel(self.panel)
        self.icon.setObjectName("icon")
        self.icon.setFixedSize(88, 88)
        self.icon.setPixmap(
            QPixmap(str(Path(__file__).with_name("icon.png"))).scaled(
                self.icon.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(6)
        text_layout.addStretch()
        text_layout.addWidget(self.title)
        text_layout.addWidget(self.message)
        text_layout.addStretch()

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(18)
        content_layout.addWidget(self.icon)
        content_layout.addLayout(text_layout, 1)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(24, 18, 24, 18)
        panel_layout.setSpacing(14)
        panel_layout.addLayout(content_layout, 1)
        panel_layout.addWidget(self.progress_bar)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(self.panel)

        self.download_thread: DownloadThread | None = None
        self._drag_position = None
        self._positioned = False
        self._rinui_theme_manager = None
        self._rinui_window_handle: int | None = None

        self._apply_theme(self._get_current_theme())
        self._setup_rinui_window()
        QTimer.singleShot(0, self._start)

    def _get_current_theme(self) -> str:
        if self._rinui_theme_manager is not None:
            try:
                return self._rinui_theme_manager.get_theme()
            except (AttributeError, RuntimeError):
                pass

        window_color = QApplication.palette().color(QPalette.ColorRole.Window)
        return "Dark" if window_color.value() < 128 else "Light"

    def _setup_rinui_window(self) -> None:
        if RinUIThemeManager is None:
            return

        try:
            self._rinui_theme_manager = RinUIThemeManager()
            self._rinui_theme_manager.themeChanged.connect(self._on_theme_changed)
            self._rinui_window_handle = int(self.winId())
            self._rinui_theme_manager.set_window(self)
            self._rinui_theme_manager.apply_window_effects()
            backdrop_effect = self._rinui_theme_manager.get_backdrop_effect()
            if backdrop_effect:
                self._rinui_theme_manager.apply_backdrop_effect(backdrop_effect)
        except (AttributeError, OSError, RuntimeError, TypeError):
            self._rinui_theme_manager = None
            self._rinui_window_handle = None

    @Slot(str)
    def _on_theme_changed(self, theme: str) -> None:
        self._apply_theme(theme)

    def _apply_theme(self, theme: str) -> None:
        dark = theme.casefold() == "dark"
        if dark:
            panel = "rgba(32, 32, 32, 235)"
            border = "rgba(255, 255, 255, 35)"
            text = "#F5F5F5"
            secondary_text = "#C8C8C8"
            track = "rgba(255, 255, 255, 22)"
        else:
            panel = "rgba(250, 250, 252, 238)"
            border = "rgba(0, 0, 0, 24)"
            text = "#1A1A1A"
            secondary_text = "#616161"
            track = "rgba(0, 0, 0, 16)"

        self.setStyleSheet(
            f"""
            QWidget#installer {{
                background: transparent;
            }}
            QFrame#panel {{
                background-color: {panel};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QLabel#title {{
                color: {text};
                font-size: 22px;
                font-weight: 600;
            }}
            QLabel#message {{
                color: {secondary_text};
                font-size: 13px;
            }}
            QProgressBar#progress {{
                background-color: {track};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar#progress::chunk {{
                background-color: #605ED2;
                border-radius: 4px;
            }}
            """
        )

    def showEvent(self, event):
        super().showEvent(event)
        if not self._positioned:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                self.move(screen.availableGeometry().center() - self.rect().center())
            self._positioned = True

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_position is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_position = None
        super().mouseReleaseEvent(event)

    def _start(self):
        try:
            executable = get_installed_executable()
        except RuntimeError as error:
            self._show_unsupported(str(error))
            return
        if executable is not None:
            self.progress_bar.setValue(100)
            self._launch(executable)
            return

        self.download_thread = DownloadThread(self)
        self.download_thread.progress.connect(self.progress_bar.setValue)
        self.download_thread.failed.connect(self._on_failed)
        self.download_thread.completed.connect(self._on_completed)
        self.download_thread.start()

    def _show_unsupported(self, message: str):
        QMessageBox.critical(self, "不支持的操作系统", message)
        self.close()


    @Slot(str)
    def _on_completed(self, executable: str):
        if self.download_thread is not None and self.download_thread.isInterruptionRequested():
            return
        self._launch(Path(executable))
    @Slot(str)
    def _on_failed(self, message: str):
        QMessageBox.critical(self, "安装失败", message)
        self.close()

    def _launch(self, executable: Path):
        subprocess.Popen([str(executable)])
        self.close()

    def closeEvent(self, event):
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.requestInterruption()
            self.download_thread.wait(1000)

        if self._rinui_theme_manager is not None:
            try:
                self._rinui_theme_manager.themeChanged.disconnect(self._on_theme_changed)
            except (RuntimeError, TypeError):
                pass
            if self._rinui_window_handle is not None:
                while self._rinui_window_handle in self._rinui_theme_manager.windows:
                    self._rinui_theme_manager.windows.remove(self._rinui_window_handle)
            self._rinui_window_handle = None

        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    installer = Installer()
    installer.show()
    sys.exit(app.exec())
