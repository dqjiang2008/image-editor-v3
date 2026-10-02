"""Shared media preview panel."""

from PyQt6.QtCore import Qt, QSize, QPoint
from PyQt6.QtGui import QPixmap, QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtMultimediaWidgets import QVideoWidget


class _ClickableImageLabel(QLabel):
    """支持双击事件的图片 QLabel，双击时在窗口中央按原图大小显示。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._source_path = None
        self._overlay = None

    def set_source_path(self, path):
        self._source_path = path

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._source_path:
            self._show_centered_overlay()
        super().mouseDoubleClickEvent(event)

    def _show_centered_overlay(self):
        from pathlib import Path
        if not self._source_path or not Path(self._source_path).exists():
            return
        pixmap = QPixmap(str(self._source_path))
        if pixmap.isNull():
            return

        top_window = self.window()
        if top_window is None:
            return

        overlay = _ImageOverlay(top_window, pixmap)
        overlay.show()
        overlay.setFocus()
        self._overlay = overlay


class _ImageOverlay(QWidget):
    """居中覆盖层：按原图大小显示图片，双击或按 ESC 关闭。"""

    def __init__(self, parent, pixmap: QPixmap):
        super().__init__(parent)
        self._pixmap = pixmap
        self._parent = parent
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 200);")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: transparent;")
        self._image_label.setPixmap(pixmap)
        layout.addWidget(self._image_label, 1)

        hint = QLabel("双击或按 ESC 关闭")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            "color: rgba(255,255,255,180); font-size: 12px; "
            "background-color: transparent; padding: 6px;"
        )
        layout.addWidget(hint, 0)

        self._resize_and_center()

    def _resize_and_center(self):
        if not self._parent:
            return
        parent_geo = self._parent.geometry()
        img_w = self._pixmap.width()
        img_h = self._pixmap.height()
        margin = 40
        max_w = parent_geo.width() - margin * 2
        max_h = parent_geo.height() - margin * 2 - 30
        display_w = min(img_w, max_w)
        display_h = min(img_h, max_h)
        if img_w > 0 and img_h > 0:
            scale_w = display_w / img_w
            scale_h = display_h / img_h
            scale = min(scale_w, scale_h, 1.0)
            display_w = int(img_w * scale)
            display_h = int(img_h * scale)
        total_w = display_w + margin * 2
        total_h = display_h + margin * 2 + 30
        self._image_label.setFixedSize(display_w, display_h)
        self._image_label.setPixmap(
            self._pixmap.scaled(
                QSize(display_w, display_h),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.setFixedSize(total_w, total_h)
        cx = parent_geo.x() + (parent_geo.width() - total_w) // 2
        cy = parent_geo.y() + (parent_geo.height() - total_h) // 2
        self.move(cx, cy)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.close()
        super().mouseDoubleClickEvent(event)


class MediaPanel(QWidget):
    """Preview image/video media using callbacks supplied by the main window."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)
        self.setStyleSheet("background-color: #ffffff; border-left: 2px solid #e2e8f0;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        title_layout = QHBoxLayout()
        title_label = QLabel("🎬 媒体预览")
        title_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #3b82f6;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        title_layout.addWidget(self._icon_button(
            "放大媒体预览", controller._media_zoom_in,
            controller._create_zoom_in_icon(), "#3b82f6", 2
        ))
        title_layout.addWidget(self._icon_button(
            "缩小媒体预览", controller._media_zoom_out,
            controller._create_zoom_out_icon(), "#64748b", 2
        ))
        title_layout.addWidget(self._icon_button(
            "全屏显示媒体（按ESC退出）", controller._media_fullscreen,
            controller._create_fullscreen_icon(), "#10b981", 4
        ))
        layout.addLayout(title_layout)

        # 图片预览区（最多两张：角色图/场景图/分镜首尾帧图），与下方视频对比
        self.preview_area = QWidget()
        self.preview_area.setFixedHeight(150)
        self.preview_area.setStyleSheet(
            "background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;"
        )
        preview_layout = QHBoxLayout(self.preview_area)
        preview_layout.setContentsMargins(4, 4, 4, 4)
        preview_layout.setSpacing(6)

        self.preview_cell_1, self.preview_image_label_1, self.preview_caption_1 = (
            self._make_preview_cell()
        )
        self.preview_cell_2, self.preview_image_label_2, self.preview_caption_2 = (
            self._make_preview_cell()
        )
        preview_layout.addWidget(self.preview_cell_1, 1)
        preview_layout.addWidget(self.preview_cell_2, 1)
        layout.addWidget(self.preview_area, 0)

        self.media_container = QWidget()
        self.media_container.setMinimumSize(320, 213)
        self.media_container.setMaximumSize(400, 267)
        self.media_container.setStyleSheet("""
            QWidget {
                background-color: #000000;
                border: 2px solid #e2e8f0;
                border-radius: 6px;
            }
        """)
        container_layout = QVBoxLayout(self.media_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        display_wrapper = QWidget()
        display_wrapper.setStyleSheet("background-color: #000000;")
        self.display_layout = QStackedLayout(display_wrapper)
        self.display_layout.setContentsMargins(0, 0, 0, 0)

        self.media_display_label = QLabel("暂无媒体")
        self.media_display_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_display_label.setStyleSheet(
            "color: #94a3b8; font-size: 14px; background-color: transparent;"
        )
        self.media_display_label.setScaledContents(True)
        self.display_layout.addWidget(self.media_display_label)

        self.media_video_widget = QVideoWidget()
        self.media_video_widget.setStyleSheet("background-color: #000000;")
        self.media_video_widget.hide()
        self.display_layout.addWidget(self.media_video_widget)
        controller.media_player.setVideoOutput(self.media_video_widget)
        controller.media_player.setAudioOutput(controller.media_audio_output)
        container_layout.addWidget(display_wrapper, 1)

        progress_overlay = QWidget()
        progress_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 0.7);")
        progress_layout = QHBoxLayout(progress_overlay)
        progress_layout.setContentsMargins(8, 4, 8, 4)
        progress_layout.setSpacing(8)

        self.media_time_label = QLabel("00:00 / 00:00")
        self.media_time_label.setStyleSheet("color: #ffffff; font-size: 11px;")
        self.media_time_label.setFixedWidth(80)
        progress_layout.addWidget(self.media_time_label)

        self.media_progress = QSlider(Qt.Orientation.Horizontal)
        self.media_progress.setRange(0, 1000)
        self.media_progress.setFixedHeight(16)
        self.media_progress.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #4a5568;
                height: 4px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #3b82f6;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QSlider::sub-page:horizontal {
                background: #3b82f6;
                height: 4px;
                border-radius: 2px;
            }
        """)
        self.media_progress.sliderMoved.connect(controller._on_media_progress_moved)
        progress_layout.addWidget(self.media_progress, 1)
        container_layout.addWidget(progress_overlay)
        layout.addWidget(self.media_container, 1)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.setContentsMargins(4, 4, 4, 4)
        self.media_play_btn = self._control_button(
            "▶ 播放", "播放/暂停", controller._media_play, "#3b82f6", "#2563eb"
        )
        self.media_pause_btn = self._control_button(
            "⏸ 暂停", "暂停", controller._media_pause, "#f59e0b", "#d97706"
        )
        self.media_stop_btn = self._control_button(
            "⏹ 停止", "停止", controller._media_stop, "#ef4444", "#dc2626"
        )
        controls.addWidget(self.media_play_btn, 1)
        controls.addWidget(self.media_pause_btn, 1)
        controls.addWidget(self.media_stop_btn, 1)
        layout.addLayout(controls)

        self.media_info_label = QLabel("")
        self.media_info_label.setStyleSheet("color: #64748b; font-size: 10px; padding: 2px;")
        self.media_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_info_label.setWordWrap(True)
        layout.addWidget(self.media_info_label)

    def _make_preview_cell(self):
        """创建一个图片预览单元格：图片 + 小标题"""
        cell = QWidget()
        cell.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #cbd5e1; border-radius: 4px;"
        )
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(2, 2, 2, 2)
        cell_layout.setSpacing(2)

        image_label = _ClickableImageLabel("暂无图片")
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setStyleSheet(
            "color: #94a3b8; font-size: 11px; background-color: transparent; border: none;"
        )
        image_label.setCursor(Qt.CursorShape.PointingHandCursor)
        caption_label = QLabel("")
        caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption_label.setStyleSheet(
            "color: #334155; font-size: 9px; font-weight: bold; "
            "background-color: transparent; border: none;"
        )
        caption_label.setFixedHeight(12)
        caption_label.hide()
        cell_layout.addWidget(image_label, 1)
        cell_layout.addWidget(caption_label, 0)
        return cell, image_label, caption_label

    def set_preview_images(self, path1=None, path2=None, caption1="", caption2=""):
        """设置上方图片预览区内容（最多两张图，与下方视频对比）"""
        self._set_preview_cell(
            self.preview_image_label_1, self.preview_caption_1, path1, caption1
        )
        self._set_preview_cell(
            self.preview_image_label_2, self.preview_caption_2, path2, caption2
        )

    def _set_preview_cell(self, image_label, caption_label, path, caption):
        """设置单个预览单元格的图片和标题"""
        from pathlib import Path

        if path and Path(path).exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                image_label.setPixmap(
                    pixmap.scaled(
                        QSize(190, 108),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                if isinstance(image_label, _ClickableImageLabel):
                    image_label.set_source_path(str(path))
                if caption:
                    caption_label.setText(caption)
                    caption_label.show()
                else:
                    caption_label.hide()
                return
        image_label.clear()
        image_label.setText("暂无图片")
        if isinstance(image_label, _ClickableImageLabel):
            image_label.set_source_path(None)
        caption_label.hide()

    def clear_preview_images(self):
        """清空上方图片预览区"""
        self.set_preview_images(None, None)

    @staticmethod
    def _icon_button(tool_tip, callback, icon, color, radius):
        button = QPushButton()
        button.setFixedSize(16, 16)
        button.setToolTip(tool_tip)
        button.clicked.connect(callback)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIcon(icon)
        button.setIconSize(QSize(12, 12))
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                border: none;
                border-radius: {radius}px;
            }}
            QPushButton:hover {{ background-color: {color}; }}
        """)
        return button

    @staticmethod
    def _control_button(text, tool_tip, callback, color, hover_color):
        button = QPushButton(text)
        button.setMinimumHeight(32)
        button.setToolTip(tool_tip)
        button.clicked.connect(callback)
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
                padding: 4px 12px;
            }}
            QPushButton:hover {{ background-color: {hover_color}; }}
        """)
        return button