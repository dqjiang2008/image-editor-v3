"""Character management page."""

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt


class CharacterPage(QWidget):
    """Character form, list, and image generation controls."""

    def __init__(self, controller, presets):
        super().__init__()
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)

        # ========== 左侧：角色参数 + 操作按钮 ==========
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(8)

        # 角色参数表单
        char_group = QGroupBox("👤 角色参数")
        char_layout = QFormLayout()
        self.char_name_input = QLineEdit()
        self.char_name_input.setPlaceholderText("角色名称")
        char_layout.addRow("名称:", self.char_name_input)
        self.char_type_combo = QComboBox()
        self.char_type_combo.addItems(["人类", "动物/妖兽", "群体", "能量体"])
        self.char_type_combo.setToolTip("角色类型：人类/动物/妖兽/群体/能量体")
        char_layout.addRow("类型:", self.char_type_combo)
        self.char_style_combo = QComboBox()
        self.char_style_combo.addItems(presets.keys())
        char_layout.addRow("预设:", self.char_style_combo)
        self.char_desc_input = QLineEdit()
        self.char_desc_input.setPlaceholderText("角色描述")
        char_layout.addRow("描述:", self.char_desc_input)
        self.style_pack_hint_label = QLabel("")
        self.style_pack_hint_label.setStyleSheet("color: #64748b; font-size: 11px;")
        self.style_pack_hint_label.setWordWrap(True)
        char_layout.addRow("画风提示:", self.style_pack_hint_label)
        char_group.setLayout(char_layout)
        left_layout.addWidget(char_group)

        # 操作按钮
        button_layout = QVBoxLayout()
        button_layout.setSpacing(6)

        add_button = QPushButton("➕ 添加角色")
        add_button.clicked.connect(controller._on_add_character)
        button_layout.addWidget(add_button)

        self.update_char_btn = QPushButton("✏️ 更新角色")
        self.update_char_btn.clicked.connect(controller._on_update_character)
        self.update_char_btn.setEnabled(False)
        self.update_char_btn.setObjectName("warning")
        button_layout.addWidget(self.update_char_btn)

        costume_btn = QPushButton("👗 管理造型")
        costume_btn.setToolTip(
            "为选中角色添加服装/形象变化造型，如「血衣」「成年」；"
            "弹窗内选中造型即可查看已生成的造型图（可打开大图/图片文件夹）。"
            "分镜角色列写 角色名/造型名 即引用该造型"
        )
        costume_btn.clicked.connect(controller._on_manage_character_costumes)
        costume_btn.setObjectName("info")
        self.costume_btn = costume_btn
        self.costume_btn.setEnabled(False)
        button_layout.addWidget(costume_btn)

        import_costume_btn = QPushButton("📥 批量导入造型并生成")
        import_costume_btn.setToolTip("粘贴/导入服装变化表（Markdown 表格或自由文本），自动解析后批量写入角色并生成造型图")
        import_costume_btn.clicked.connect(controller._on_import_costumes)
        import_costume_btn.setStyleSheet(
            "QPushButton { background-color: #1e40af; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #3b82f6; }"
            "QPushButton:disabled { background-color: #334155; }"
        )
        button_layout.addWidget(import_costume_btn)

        import_chars_btn = QPushButton("📂 导入角色")
        import_chars_btn.setToolTip("从文件导入角色/造型资产（支持 md/txt 等格式）")
        import_chars_btn.clicked.connect(controller._on_import_characters_from_file)
        button_layout.addWidget(import_chars_btn)

        self.stop_task_btn = QPushButton("⏹ 停止任务")
        self.stop_task_btn.setObjectName("danger")
        self.stop_task_btn.setEnabled(False)
        self.stop_task_btn.clicked.connect(controller._on_stop_task)
        button_layout.addWidget(self.stop_task_btn)

        button_layout.addStretch()
        left_layout.addLayout(button_layout)
        left_layout.addStretch()
        left_widget.setFixedWidth(240)
        main_layout.addWidget(left_widget, 0)

        # ========== 右侧：角色列表 ==========
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)

        self.character_stats_label = QLabel("角色总数: 0")
        self.character_stats_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: bold;"
        )
        right_layout.addWidget(self.character_stats_label)

        char_list_group = QGroupBox("📋 角色列表")
        char_list_layout = QVBoxLayout()

        char_filter_layout = QHBoxLayout()
        char_filter_label = QLabel("显示:")
        char_filter_label.setStyleSheet("color: #64748b; font-size: 11px;")
        char_filter_layout.addWidget(char_filter_label)
        self.char_filter_combo = QComboBox()
        self.char_filter_combo.addItems(["📌 本集", "🌐 全部"])
        self.char_filter_combo.setCurrentIndex(0)  # 默认"本集"（有选节时显示节名）
        self.char_filter_combo.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.char_filter_combo.currentIndexChanged.connect(controller._on_char_filter_changed)
        char_filter_layout.addWidget(self.char_filter_combo)
        
        char_type_filter_label = QLabel("类型:")
        char_type_filter_label.setStyleSheet("color: #64748b; font-size: 11px;")
        char_filter_layout.addWidget(char_type_filter_label)
        self.char_type_filter_combo = QComboBox()
        self.char_type_filter_combo.addItems(["全部", "人类", "动物/妖兽", "群体", "能量体"])
        self.char_type_filter_combo.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.char_type_filter_combo.currentIndexChanged.connect(controller._on_char_type_filter_changed)
        char_filter_layout.addWidget(self.char_type_filter_combo)
        
        char_filter_layout.addStretch()
        char_list_layout.addLayout(char_filter_layout)

        self.character_list = QListWidget()
        self.character_list.setToolTip(
            "勾选框（✔）= 要生成的角色；「🎨 生成角色图像 / 生成造型图像」只处理勾选的角色"
        )
        self.character_list.currentRowChanged.connect(controller._on_character_selected)
        char_list_layout.addWidget(self.character_list)

        # 角色勾选操作行
        select_row = QHBoxLayout()
        select_all_btn = QPushButton("✔ 全选")
        select_all_btn.setToolTip("勾选列表内全部角色")
        select_all_btn.clicked.connect(controller._on_select_all_characters)
        select_row.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("✖ 全不选")
        deselect_all_btn.setToolTip("取消勾选列表内全部角色")
        deselect_all_btn.clicked.connect(controller._on_deselect_all_characters)
        select_row.addWidget(deselect_all_btn)
        miss_base_btn = QPushButton("◻ 只选无底模图")
        miss_base_btn.setToolTip("只勾选还没生成角色底模图的角色")
        miss_base_btn.clicked.connect(controller._on_select_characters_missing_base)
        select_row.addWidget(miss_base_btn)
        miss_cost_btn = QPushButton("◻ 只选缺造型图")
        miss_cost_btn.setToolTip("只勾选还有造型没出图的角色（无任何造型的角色也会被选中）")
        miss_cost_btn.clicked.connect(controller._on_select_characters_missing_costume)
        select_row.addWidget(miss_cost_btn)
        select_row.addStretch()
        char_list_layout.addLayout(select_row)

        # 角色批量操作按钮行
        batch_row = QHBoxLayout()
        del_img_btn = QPushButton("🗑️ 删除选中图像")
        del_img_btn.setToolTip("删除已勾选角色的图像文件（保留角色条目）")
        del_img_btn.clicked.connect(controller._on_delete_selected_character_images)
        batch_row.addWidget(del_img_btn)
        del_char_btn = QPushButton("❌ 删除选中角色")
        del_char_btn.setToolTip("删除已勾选的角色条目及其图像文件")
        del_char_btn.clicked.connect(controller._on_delete_selected_characters)
        batch_row.addWidget(del_char_btn)
        batch_row.addStretch()
        char_list_layout.addLayout(batch_row)

        char_list_group.setLayout(char_list_layout)
        right_layout.addWidget(char_list_group, 1)

        # 底部生成按钮
        generate_row = QHBoxLayout()
        generate_button = QPushButton("🎨 生成角色图像")
        generate_button.setToolTip(
            "只生成角色列表中勾选（✔）的角色底模；已有造型图会自动跳过（可单独重出）"
        )
        generate_button.clicked.connect(controller._on_generate_characters)
        generate_row.addWidget(generate_button)
        generate_costume_btn = QPushButton("🎨 生成造型图像")
        generate_costume_btn.setToolTip(
            "只生成勾选角色的造型图，不重新出角色底模。\n"
            "已有图的造型自动跳过；可用「◻ 只选缺造型图」快速勾选缺图的角色。"
        )
        generate_costume_btn.clicked.connect(controller._on_generate_costumes)
        generate_costume_btn.setObjectName("info")
        generate_row.addWidget(generate_costume_btn)
        right_layout.addLayout(generate_row, 0)

        main_layout.addWidget(right_widget, 1)

    def update_section_label(self, section_title: str = ""):
        """更新筛选下拉框第一项，显示当前节名称而非固定的「本集」。
        
        有选节时显示「📌 2.3 月光地图」，无选节时恢复「📌 本集」。
        """
        if section_title:
            self.char_filter_combo.setItemText(0, f"📌 {section_title}")
        else:
            self.char_filter_combo.setItemText(0, "📌 本集")