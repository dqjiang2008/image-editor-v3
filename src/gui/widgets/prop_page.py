"""Prop management page."""

from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt


class PropPage(QWidget):
    """Prop list and image generation controls."""

    def __init__(self, controller):
        super().__init__()
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # ========== 道具列表（占据全部空间） ==========
        self.prop_stats_label = QLabel("道具总数: 0")
        self.prop_stats_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: bold;"
        )
        main_layout.addWidget(self.prop_stats_label, 0)
        
        prop_group = QGroupBox("🎒 道具列表")
        prop_layout = QVBoxLayout()

        prop_filter_layout = QHBoxLayout()
        prop_filter_label = QLabel("显示:")
        prop_filter_label.setStyleSheet("color: #64748b; font-size: 11px;")
        prop_filter_layout.addWidget(prop_filter_label)
        self.prop_filter_combo = QComboBox()
        self.prop_filter_combo.addItems(["📌 本集", "🌐 全部"])
        self.prop_filter_combo.setCurrentIndex(0)
        self.prop_filter_combo.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.prop_filter_combo.currentIndexChanged.connect(controller._on_prop_filter_changed)
        prop_filter_layout.addWidget(self.prop_filter_combo)
        self.prop_stats_detail_label = QLabel("")
        self.prop_stats_detail_label.setStyleSheet("color: #64748b; font-size: 11px;")
        prop_filter_layout.addWidget(self.prop_stats_detail_label)
        prop_filter_layout.addStretch()
        prop_layout.addLayout(prop_filter_layout)

        self.prop_list = QListWidget()
        self.prop_list.setToolTip("道具图生成/展示，选择可在右侧预览；分镜提到该道具时其图会自动传入视频AI")
        self.prop_list.currentRowChanged.connect(controller._on_prop_selected)
        prop_layout.addWidget(self.prop_list)

        # 道具勾选操作行 + 删除按钮
        prop_select_row = QHBoxLayout()
        prop_select_all_btn = QPushButton("✔ 全选")
        prop_select_all_btn.setToolTip("勾选列表内全部道具")
        prop_select_all_btn.clicked.connect(controller._on_select_all_props)
        prop_select_row.addWidget(prop_select_all_btn)
        prop_deselect_all_btn = QPushButton("✖ 全不选")
        prop_deselect_all_btn.setToolTip("取消勾选列表内全部道具")
        prop_deselect_all_btn.clicked.connect(controller._on_deselect_all_props)
        prop_select_row.addWidget(prop_deselect_all_btn)
        prop_miss_btn = QPushButton("◻ 只选无图")
        prop_miss_btn.setToolTip("只勾选还没生成图像的道具")
        prop_miss_btn.clicked.connect(controller._on_select_props_missing_image)
        prop_select_row.addWidget(prop_miss_btn)
        del_prop_img_btn = QPushButton("🗑️ 删除选中道具图像")
        del_prop_img_btn.setToolTip("删除已勾选道具的图像文件（保留道具条目）")
        del_prop_img_btn.clicked.connect(controller._on_delete_selected_prop_images)
        prop_select_row.addWidget(del_prop_img_btn)
        del_prop_btn = QPushButton("❌ 删除选中道具")
        del_prop_btn.setToolTip("删除已勾选的道具条目及其图像文件")
        del_prop_btn.clicked.connect(controller._on_delete_selected_props)
        prop_select_row.addWidget(del_prop_btn)
        prop_select_row.addStretch()
        prop_layout.addLayout(prop_select_row)

        prop_group.setLayout(prop_layout)
        main_layout.addWidget(prop_group, 1)

        # 底部按钮行：导入道具 + 生成道具图像 + 停止任务（各占一半）
        bottom_btn_row = QHBoxLayout()
        import_prop_btn = QPushButton("📂 导入道具")
        import_prop_btn.setToolTip("从文件导入道具资产（支持 md/txt 等格式）")
        import_prop_btn.clicked.connect(controller._on_import_props_from_file)
        bottom_btn_row.addWidget(import_prop_btn, 1)
        
        gen_props_button = QPushButton("🎨 生成道具图像")
        gen_props_button.clicked.connect(controller._on_generate_props)
        bottom_btn_row.addWidget(gen_props_button, 1)

        self.stop_task_btn = QPushButton("⏹ 停止任务")
        self.stop_task_btn.setObjectName("danger")
        self.stop_task_btn.setEnabled(False)
        self.stop_task_btn.clicked.connect(controller._on_stop_task)
        bottom_btn_row.addWidget(self.stop_task_btn, 1)
        
        main_layout.addLayout(bottom_btn_row)

    def update_section_label(self, section_title: str = ""):
        """更新筛选下拉框第一项，显示当前节名称而非固定的「本集」。
        
        有选节时显示「📌 2.3 月光地图」，无选节时恢复「📌 本集」。
        """
        if section_title:
            self.prop_filter_combo.setItemText(0, f"📌 {section_title}")
        else:
            self.prop_filter_combo.setItemText(0, "📌 本集")