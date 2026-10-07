"""Video management page."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QListWidget,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
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
        
        # 使用表格替代列表，添加勾选列（与分镜页一致）
        self.video_table = QTableWidget()
        self.video_table.setColumnCount(4)
        self.video_table.setHorizontalHeaderLabels(["✔", "序号", "视频名称", "文件路径"])
        self.video_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((40, 50, 400, 650)):
            self.video_table.setColumnWidth(column, width)
        self.video_table.setAlternatingRowColors(True)
        self.video_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.video_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.video_table.setWordWrap(True)
        self.video_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.video_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.video_table.cellDoubleClicked.connect(controller._on_video_double_clicked)
        list_layout.addWidget(self.video_table)
        
        # 选择按钮区域
        select_layout = QHBoxLayout()
        select_all_btn = QPushButton("✔ 全选")
        select_all_btn.clicked.connect(lambda: self._set_all_check_state(Qt.CheckState.Checked))
        select_layout.addWidget(select_all_btn)
        
        deselect_all_btn = QPushButton("✖ 全不选")
        deselect_all_btn.clicked.connect(lambda: self._set_all_check_state(Qt.CheckState.Unchecked))
        select_layout.addWidget(deselect_all_btn)
        
        select_layout.addStretch()
        list_layout.addLayout(select_layout)
        
        import_button = QPushButton("📂 导入视频文件")
        import_button.clicked.connect(controller._on_import_video)
        list_layout.addWidget(import_button)
        list_group.setLayout(list_layout)
        left_layout.addWidget(list_group, 1)

        operations_group = QGroupBox("🎬 视频操作")
        operations_layout = QVBoxLayout()
        
        merge_btn = QPushButton("🔗 合并选中视频")
        merge_btn.setStyleSheet("background-color: #3b82f6; color: white; font-weight: bold; padding: 8px 16px;")
        merge_btn.setToolTip("合并已勾选的视频文件，文件名自动使用增量序号")
        merge_btn.clicked.connect(controller._on_merge_video)
        operations_layout.addWidget(merge_btn)
        
        del_vid_btn = QPushButton("🗑️ 删除选中视频")
        del_vid_btn.setObjectName("danger")
        del_vid_btn.setToolTip("删除已勾选的视频文件")
        del_vid_btn.clicked.connect(controller._on_delete_selected_videos)
        operations_layout.addWidget(del_vid_btn)
        
        for text, callback in (
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
        main_layout.addWidget(left_widget, 3)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)
        self.video_stats_label = QLabel("视频总数: 0")
        self.video_stats_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: bold;"
        )
        right_layout.addWidget(self.video_stats_label)
        tip_label = QLabel(
            "💡 提示：勾选要合并的视频，点击「合并选中视频」按钮即可合并\n"
            "双击视频列表中的视频，将在中间媒体预览框中播放"
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
    
    def _set_all_check_state(self, state):
        """设置所有行的勾选状态（使用 cellWidget 中的 QCheckBox）"""
        for row in range(self.video_table.rowCount()):
            widget = self.video_table.cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb:
                    cb.setChecked(state == Qt.CheckState.Checked)