"""Storyboard editing page."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from src.config.settings import Settings


class StoryboardPage(QWidget):
    """Storyboard parameters, editing table, and generation controls."""

    def __init__(self, controller):
        super().__init__()
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(8)

        params_group = QGroupBox("🎬 分镜参数")
        params_layout = QFormLayout()
        self.scene_count_spin = QSpinBox()
        self.scene_count_spin.setRange(1, 100)
        self.scene_count_spin.setValue(Settings.DEFAULT_SCENE_COUNT)
        params_layout.addRow("场景数:", self.scene_count_spin)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(
            float(Settings.VIDEO_MIN_DURATION),
            float(Settings.VIDEO_MAX_DURATION),
        )
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setValue(5.0)
        params_layout.addRow("每帧时长(秒):", self.duration_spin)
        # 模式选择（分镜参数区）
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["auto", "text", "reference"])
        self.mode_combo.setCurrentText(Settings.AGNES_VIDEO_MODE or "auto")
        self.mode_combo.setToolTip(
            "视频生成模式：\n"
            "auto - 自动：故事模式→text（由分镜直接生成），剧集模式→reference（图生图，需角色）\n"
            "text - 纯文本生成，不需要角色参考图\n"
            "reference - 参考图生成，需要角色图片"
        )
        self.mode_combo.currentTextChanged.connect(controller._on_agnes_mode_changed)
        params_layout.addRow("生成模式:", self.mode_combo)
        params_group.setLayout(params_layout)
        left_layout.addWidget(params_group)

        # 高级选项（手动路径）：手动注入自定义风格短语，与项目技能包叠加
        advanced_group = QGroupBox("⚙️ 高级选项（手动覆盖/叠加）")
        advanced_layout = QFormLayout()
        advanced_layout.setSpacing(6)
        self.custom_style_suffix_edit = QLineEdit()
        self.custom_style_suffix_edit.setPlaceholderText("可选：追加到帧图/视频 prompt 末尾的风格短语")
        self.custom_style_suffix_edit.setToolTip(
            "手动注入风格短语，将追加到每个分镜帧图与视频 prompt 末尾。\n"
            "与项目信息页选择的美术风格包叠加使用（两者同时生效）。\n"
            "普通用户可留空，由风格包自动完成全部注入。"
        )
        self.custom_style_suffix_edit.textChanged.connect(
            controller._on_custom_style_suffix_changed
        )
        advanced_layout.addRow("自定义风格注入:", self.custom_style_suffix_edit)
        advanced_group.setLayout(advanced_layout)
        left_layout.addWidget(advanced_group)

        button_layout = QVBoxLayout()
        button_layout.setSpacing(6)
        for text, tooltip, callback, object_name in (
            ("➕\n插入分镜", "在当前位置插入一个新的分镜", controller._on_add_storyboard, ""),
            ("⬆️\n上移", "将选中的分镜向上移动", controller._on_move_storyboard_up, ""),
            ("⬇️\n下移", "将选中的分镜向下移动", controller._on_move_storyboard_down, ""),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(callback)
            if object_name:
                button.setObjectName(object_name)
            button_layout.addWidget(button)
        button_layout.addStretch()
        left_layout.addLayout(button_layout)
        left_layout.addStretch()
        # 左侧参数栏固定宽度
        left_widget.setFixedWidth(240)
        main_layout.addWidget(left_widget, 0)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)

        # 当前节信息栏
        section_info_layout = QHBoxLayout()
        self.section_title_label = QLabel("当前节: （未选择）")
        self.section_title_label.setStyleSheet(
            "color: #1e293b; font-size: 14px; font-weight: bold; "
            "background: #f1f5f9; padding: 6px 12px; border-radius: 4px;"
        )
        section_info_layout.addWidget(self.section_title_label)
        section_info_layout.addStretch()
        right_layout.addLayout(section_info_layout)

        self.storyboard_stats_label = QLabel("分镜总数: 0 | 总时长: 0秒")
        self.storyboard_stats_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: bold;"
        )
        right_layout.addWidget(self.storyboard_stats_label)

        storyboard_group = QGroupBox("📋 分镜列表（双击编辑）")
        table_layout = QVBoxLayout()
        self.storyboard_table = QTableWidget()
        self.storyboard_table.setColumnCount(10)
        self.storyboard_table.setHorizontalHeaderLabels([
            "✔", "序号", "所属场景名", "描述", "角色", "对话角色", "语音文本",
            "动作", "镜头", "时长(秒)",
        ])
        # 所属场景名列提示：值必须与「场景」页的场景名一致，分镜图据此关联场景参考图
        self.storyboard_table.horizontalHeaderItem(2).setToolTip(
            "所属场景名：必须是「场景」页里已生成的场景名（决定分镜图/视频关联哪张场景图）。\n"
            "鼠标悬停单元格可看到该镜头的「镜头标题」（如「爷爷唤孙」）。"
        )
        # 角色列提示：支持 角色名/造型名 写法
        self.storyboard_table.horizontalHeaderItem(4).setToolTip(
            "角色列：多个角色用顿号分隔；写「角色名/造型名」可引用该角色的对应造型图，"
            "如 陈炎/血衣（先在角色页为角色添加造型并生成造型图）"
        )
        self.storyboard_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        for column, width in enumerate(
            (40, 50, 50, 400, 80, 80, 200, 240, 80, 80)
        ):
            self.storyboard_table.setColumnWidth(column, width)
        self.storyboard_table.setAlternatingRowColors(True)
        self.storyboard_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.storyboard_table.setWordWrap(True)
        self.storyboard_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.storyboard_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.storyboard_table.currentCellChanged.connect(controller._on_storyboard_selected)
        self.storyboard_table.cellDoubleClicked.connect(
            controller._on_storyboard_cell_double_clicked
        )
        self.storyboard_table.cellChanged.connect(controller._on_storyboard_cell_changed)
        table_layout.addWidget(self.storyboard_table)
        self.storyboard_duration_label = QLabel()
        self.storyboard_duration_label.setStyleSheet(
            "color: #475569; font-weight: bold; padding: 6px 2px;"
        )
        table_layout.addWidget(self.storyboard_duration_label)
        storyboard_group.setLayout(table_layout)
        right_layout.addWidget(storyboard_group, 1)

        footer_layout = QVBoxLayout()
        footer_layout.setSpacing(6)
        
        # 第一行：选择操作 + 生成分镜 + 生成视频
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(6)
        select_all_btn = QPushButton("✔ 全选")
        select_all_btn.setToolTip("选中所有分镜")
        select_all_btn.clicked.connect(controller._on_select_all_storyboard)
        row1_layout.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("✖ 全不选")
        deselect_all_btn.setToolTip("取消所有分镜的选中状态")
        deselect_all_btn.clicked.connect(controller._on_deselect_all_storyboard)
        row1_layout.addWidget(deselect_all_btn)
        select_missing_img_btn = QPushButton("◻ 只选无图")
        select_missing_img_btn.setToolTip("只选中没有生成帧图的分镜")
        select_missing_img_btn.clicked.connect(controller._on_select_missing_image_storyboard)
        row1_layout.addWidget(select_missing_img_btn)
        select_missing_vid_btn = QPushButton("◻ 只选无视频")
        select_missing_vid_btn.setToolTip("只选中没有生成视频的分镜")
        select_missing_vid_btn.clicked.connect(controller._on_select_missing_video_storyboard)
        row1_layout.addWidget(select_missing_vid_btn)
        storyboard_button = QPushButton("🎬 生成分镜")
        storyboard_button.setToolTip("为选中的分镜批量生成分镜脚本")
        storyboard_button.clicked.connect(controller._on_generate_storyboard)
        row1_layout.addWidget(storyboard_button)
        video_button = QPushButton("🎥 生成视频")
        video_button.setToolTip("为选中的分镜批量生成视频")
        video_button.clicked.connect(controller._on_generate_video)
        row1_layout.addWidget(video_button)
        row1_layout.addStretch()
        footer_layout.addLayout(row1_layout)
        
        # 第二行：删除按钮（删除选中分镜移到删除视频右边）
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(6)
        del_img_btn = QPushButton("🗑️ 删除选中帧图")
        del_img_btn.setToolTip("删除已勾选分镜的帧图文件（保留分镜条目）")
        del_img_btn.clicked.connect(controller._on_delete_selected_storyboard_images)
        row2_layout.addWidget(del_img_btn)
        del_vid_btn = QPushButton("🗑️ 删除选中视频")
        del_vid_btn.setToolTip("删除已勾选分镜的视频文件（保留分镜条目）")
        del_vid_btn.clicked.connect(controller._on_delete_selected_storyboard_videos)
        row2_layout.addWidget(del_vid_btn)
        del_sb_btn = QPushButton("🗑️ 删除选中分镜")
        del_sb_btn.setToolTip("删除已勾选的分镜条目（如未勾选则删除当前光标所在行）")
        del_sb_btn.clicked.connect(controller._on_delete_storyboard)
        del_sb_btn.setObjectName("danger")
        row2_layout.addWidget(del_sb_btn)
        row2_layout.addStretch()
        footer_layout.addLayout(row2_layout)
        
        # 第三行：从文本重新解析分镜 + 清空当前节分镜 + 停止任务
        row3_layout = QHBoxLayout()
        row3_layout.setSpacing(6)
        reparse_btn = QPushButton("📥 从文本重新解析分镜")
        reparse_btn.setToolTip(
            "从当前选节的原文重新解析分镜表（覆盖已有分镜）；\n"
            "适用于：源文件分镜解析不理想时，修改文本后重新解析。"
        )
        reparse_btn.clicked.connect(controller._on_generate_episode_storyboard)
        row3_layout.addWidget(reparse_btn)
        clear_sb_btn = QPushButton("🗑️ 清空当前节分镜")
        clear_sb_btn.setToolTip("清空当前选中节的所有分镜数据（保留节文本），清空后可点击左侧「从文本重新解析分镜」重新导入。")
        clear_sb_btn.setObjectName("danger")
        clear_sb_btn.clicked.connect(controller._on_clear_current_section_storyboard)
        row3_layout.addWidget(clear_sb_btn)
        self.stop_task_btn = QPushButton("⏹ 停止任务")
        self.stop_task_btn.setObjectName("danger")
        self.stop_task_btn.setEnabled(False)
        self.stop_task_btn.clicked.connect(controller._on_stop_task)
        row3_layout.addWidget(self.stop_task_btn)
        row3_layout.addStretch()
        footer_layout.addLayout(row3_layout)
        
        right_layout.addLayout(footer_layout)
        main_layout.addWidget(right_widget, 1)