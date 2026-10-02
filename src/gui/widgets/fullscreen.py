"""Fullscreen media playback widget."""

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget


class FullscreenWindow(QWidget):
    """全屏媒体显示窗口，按ESC键可退出全屏"""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("background-color: #000000;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: #000000;")
        self.video_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.video_widget, 1)

        control_widget = QWidget()
        control_widget.setStyleSheet("background-color: rgba(0, 0, 0, 0.7);")
        control_layout = QHBoxLayout(control_widget)
        control_layout.setContentsMargins(20, 8, 20, 8)

        exit_btn = QPushButton("退出全屏 (ESC)")
        exit_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton:hover { background-color: #dc2626; }
        """)
        exit_btn.clicked.connect(self.exit_fullscreen)
        control_layout.addWidget(exit_btn)
        control_layout.addStretch()

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: #ffffff; font-size: 14px;")
        control_layout.addWidget(self.time_label)
        layout.addWidget(control_widget)

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

    def event(self, event):
        """重写event方法以捕获所有事件"""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.exit_fullscreen()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        """处理键盘事件，ESC键退出全屏"""
        if event.key() == Qt.Key.Key_Escape:
            self.exit_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def exit_fullscreen(self):
        """退出全屏"""
        if self.player:
            self.player.stop()
        self.close()
        if hasattr(self.main_window, "_on_fullscreen_exited"):
            self.main_window._on_fullscreen_exited()
