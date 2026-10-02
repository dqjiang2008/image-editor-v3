"""Video management page."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGroupBox,
    QLabel,
    QListWidget,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)


class VideoPage(QWidget):
    """Video list and generation controls."""

    def __init__(self, controller):
        super().__init__()
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(8)
        list_group = QGroupBox("📋 视频列表")
        list_layout = QVBoxLayout()
        self.video_list = QListWidget()
        self.video_list.setMinimumWidth(280)
        self.video_list.currentRowChanged.connect(controller._on_video_list_changed)
        self.video_list.itemDoubleClicked.connect(controller._on_video_double_clicked)
        list_layout.addWidget(self.video_list)
        import_button = QPushButton("📂 导入视频文件")
        import_button.clicked.connect(controller._on_import_video)
        list_layout.addWidget(import_button)
        list_group.setLayout(list_layout)
        left_layout.addWidget(list_group, 1)

        operations_group = QGroupBox("🎬 视频操作")
        operations_layout = QVBoxLayout()
        for text, callback in (
            ("🎥 生成分镜视频", controller._on_generate_video),
            ("🔗 合并所有视频", controller._on_merge_video),
            ("🔄 刷新列表", controller._refresh_video_list),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            operations_layout.addWidget(button)
        self.stop_task_btn = QPushButton("⏹ 停止任务")
        self.stop_task_btn.setObjectName("danger")
        self.stop_task_btn.setEnabled(False)
        self.stop_task_btn.clicked.connect(controller._on_stop_task)
        operations_layout.addWidget(self.stop_task_btn)
        operations_group.setLayout(operations_layout)
        left_layout.addWidget(operations_group)
        main_layout.addWidget(left_widget, 0)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)
        self.video_stats_label = QLabel("视频总数: 0")
        self.video_stats_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: bold;"
        )
        right_layout.addWidget(self.video_stats_label)
        tip_label = QLabel(
            "💡 提示：选择或双击视频列表中的视频，将在中间媒体预览框中播放\n"
            "使用中间媒体框的播放/暂停/停止按钮控制播放"
        )
        tip_label.setStyleSheet(
            "color: #64748b; font-size: 12px; padding: 16px; "
            "background-color: #f8fafc; border-radius: 6px;"
        )
        tip_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip_label.setWordWrap(True)
        right_layout.addWidget(tip_label)
        right_layout.addStretch()
        main_layout.addWidget(right_widget, 1)