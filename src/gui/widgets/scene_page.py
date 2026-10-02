"""Scene management page."""

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt


class ScenePage(QWidget):
    """Scene form, list, and image generation controls."""

    def __init__(self, controller):
        super().__init__()
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(8)
        scene_group = QGroupBox("🏞️ 场景参数")
        scene_layout = QFormLayout()
        self.scene_name_input = QLineEdit()
        self.scene_name_input.setPlaceholderText("场景名称")
        scene_layout.addRow("名称:", self.scene_name_input)
        self.scene_desc_input = QLineEdit()
        self.scene_desc_input.setPlaceholderText("场景描述")
        scene_layout.addRow("描述:", self.scene_desc_input)
        self.scene_time_combo = QComboBox()
        self.scene_time_combo.addItems(["白天", "夜晚", "黄昏", "早晨"])
        scene_layout.addRow("时间:", self.scene_time_combo)
        self.scene_priority_combo = QComboBox()
        self.scene_priority_combo.addItems(["P0-核心", "P1-重要", "P2-辅助"])
        self.scene_priority_combo.setCurrentIndex(1)
        self.scene_priority_combo.setToolTip("场景优先级：P0-核心场景（必须生成）/P1-重要场景（建议生成）/P2-辅助场景（可简化）")
        scene_layout.addRow("优先级:", self.scene_priority_combo)
        self.scene_category_combo = QComboBox()
        self.scene_category_combo.addItems(["室外", "室内", "地下", "特殊空间"])
        self.scene_category_combo.setToolTip("场景分类：室内/室外/地下/特殊空间")
        scene_layout.addRow("类型:", self.scene_category_combo)
        self.scene_main_scene_input = QLineEdit()
        self.scene_main_scene_input.setPlaceholderText("留空表示主场景，填写表示分场景所属的主场景")
        self.scene_main_scene_input.setToolTip("分场景填写主场景名（如：青阳镇·铁匠铺），主场景留空")
        scene_layout.addRow("所属主场景:", self.scene_main_scene_input)
        scene_group.setLayout(scene_layout)
        left_layout.addWidget(scene_group)

        button_layout = QVBoxLayout()
        button_layout.setSpacing(6)
        add_button = QPushButton("➕ 添加场景")
        add_button.clicked.connect(controller._on_add_scene)
        button_layout.addWidget(add_button)
        self.update_scene_btn = QPushButton("✏️ 更新场景")
        self.update_scene_btn.clicked.connect(controller._on_update_scene)
        self.update_scene_btn.setEnabled(False)
        self.update_scene_btn.setObjectName("warning")
        button_layout.addWidget(self.update_scene_btn)
        import_scenes_btn = QPushButton("📂 导入场景")
        import_scenes_btn.setToolTip("从文件导入场景资产（支持 md/txt 等格式）")
        import_scenes_btn.clicked.connect(controller._on_import_scenes_from_file)
        button_layout.addWidget(import_scenes_btn)
        button_layout.addStretch()
        left_layout.addLayout(button_layout)
        left_layout.addStretch()
        left_widget.setFixedWidth(240)
        main_layout.addWidget(left_widget, 0)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)
        self.scene_stats_label = QLabel("主场景: 0 | 分场景: 0")
        self.scene_stats_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: bold;"
        )
        right_layout.addWidget(self.scene_stats_label)
        self.scene_list_group = QGroupBox("📋 场景列表")
        list_layout = QVBoxLayout()
        scene_filter_layout = QHBoxLayout()
        scene_filter_label = QLabel("显示:")
        scene_filter_label.setStyleSheet("color: #64748b; font-size: 11px;")
        scene_filter_layout.addWidget(scene_filter_label)
        self.scene_filter_combo = QComboBox()
        self.scene_filter_combo.addItems(["📌 本集", "🌐 全部"])
        self.scene_filter_combo.setCurrentIndex(0)
        self.scene_filter_combo.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.scene_filter_combo.currentIndexChanged.connect(controller._on_scene_filter_changed)
        scene_filter_layout.addWidget(self.scene_filter_combo)
        
        # 主/分场景筛选
        self.scene_type_filter_combo = QComboBox()
        self.scene_type_filter_combo.addItems(["全部", "🏛️ 主场景", "📍 分场景"])
        self.scene_type_filter_combo.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.scene_type_filter_combo.setToolTip("筛选主场景或分场景")
        self.scene_type_filter_combo.currentIndexChanged.connect(controller._on_scene_type_filter_changed)
        scene_filter_layout.addWidget(self.scene_type_filter_combo)
        
        scene_priority_filter_label = QLabel("优先级:")
        scene_priority_filter_label.setStyleSheet("color: #64748b; font-size: 11px;")
        scene_filter_layout.addWidget(scene_priority_filter_label)
        self.scene_priority_filter_combo = QComboBox()
        self.scene_priority_filter_combo.addItems(["全部", "P0-核心", "P1-重要", "P2-辅助"])
        self.scene_priority_filter_combo.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.scene_priority_filter_combo.currentIndexChanged.connect(controller._on_scene_priority_filter_changed)
        scene_filter_layout.addWidget(self.scene_priority_filter_combo)
        
        scene_category_filter_label = QLabel("类型:")
        scene_category_filter_label.setStyleSheet("color: #64748b; font-size: 11px;")
        scene_filter_layout.addWidget(scene_category_filter_label)
        self.scene_category_filter_combo = QComboBox()
        self.scene_category_filter_combo.addItems(["全部", "室内", "室外", "地下", "特殊空间"])
        self.scene_category_filter_combo.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.scene_category_filter_combo.currentIndexChanged.connect(controller._on_scene_category_filter_changed)
        scene_filter_layout.addWidget(self.scene_category_filter_combo)
        
        scene_filter_layout.addStretch()
        list_layout.addLayout(scene_filter_layout)
        self.scene_list = QListWidget()
        self.scene_list.currentRowChanged.connect(controller._on_scene_selected)
        list_layout.addWidget(self.scene_list)
        # 勾选式选择性生成：全选/全不选/只选无图 + 删除按钮
        select_row = QHBoxLayout()
        select_all_btn = QPushButton("✔ 全选")
        select_all_btn.setToolTip("勾选当前显示的全部场景")
        select_all_btn.clicked.connect(controller._on_select_all_scenes)
        select_row.addWidget(select_all_btn)
        select_none_btn = QPushButton("☐ 全不选")
        select_none_btn.clicked.connect(controller._on_deselect_all_scenes)
        select_row.addWidget(select_none_btn)
        miss_img_btn = QPushButton("◻ 只选无图")
        miss_img_btn.setToolTip("只勾选还没生成图片的场景")
        miss_img_btn.clicked.connect(controller._on_select_scenes_missing_image)
        select_row.addWidget(miss_img_btn)
        del_img_btn = QPushButton("🗑️ 删除选中图像")
        del_img_btn.setToolTip("删除已勾选场景的图像文件（保留场景条目）")
        del_img_btn.clicked.connect(controller._on_delete_selected_scene_images)
        select_row.addWidget(del_img_btn)
        del_scene_btn = QPushButton("❌ 删除选中场景")
        del_scene_btn.setToolTip("删除已勾选的场景条目及其图像文件")
        del_scene_btn.clicked.connect(controller._on_delete_selected_scenes)
        select_row.addWidget(del_scene_btn)
        select_row.addStretch()
        list_layout.addLayout(select_row)
        self.scene_list_group.setLayout(list_layout)
        right_layout.addWidget(self.scene_list_group, 1)

        # 底部按钮行：生成场景图像 + 停止任务
        bottom_btn_row = QHBoxLayout()
        generate_button = QPushButton("🖼️ 生成场景图像")
        generate_button.clicked.connect(controller._on_generate_scenes)
        bottom_btn_row.addWidget(generate_button, 1)

        self.stop_task_btn = QPushButton("⏹ 停止任务")
        self.stop_task_btn.setObjectName("danger")
        self.stop_task_btn.setEnabled(False)
        self.stop_task_btn.clicked.connect(controller._on_stop_task)
        bottom_btn_row.addWidget(self.stop_task_btn, 1)

        right_layout.addLayout(bottom_btn_row)

        main_layout.addWidget(right_widget, 1)

    def update_section_label(self, section_title: str = ""):
        """更新筛选下拉框第一项，显示当前节名称而非固定的「本集」。
        
        有选节时显示「📌 2.3 月光地图」，无选节时恢复「📌 本集」。
        """
        if section_title:
            self.scene_filter_combo.setItemText(0, f"📌 {section_title}")
        else:
            self.scene_filter_combo.setItemText(0, "📌 本集")