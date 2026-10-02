"""Project overview page."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from src.config.settings import Settings


class ProjectInfoPage(QWidget):
    """Displays project metadata, progress, and aggregate statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(8)

        info_group = QGroupBox("📋 基本信息")
        info_layout = QFormLayout()
        info_layout.setSpacing(8)
        self.project_name_label = QLabel("未命名项目")
        self.project_name_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        info_layout.addRow("名称:", self.project_name_label)
        self.project_age_combo = QComboBox()
        self.project_age_combo.addItems(Settings.AGE_GROUPS)
        self.project_age_combo.setToolTip("内容类型：决定故事长度与分镜数量参考。")
        info_layout.addRow("类型:", self.project_age_combo)
        self.project_style_combo = QComboBox()
        self.project_style_combo.addItems(Settings.STYLES)
        self.project_style_combo.setToolTip("视觉风格：影响角色/场景/道具的画风基调。选择美术风格包后可覆盖此项。")
        info_layout.addRow("风格:", self.project_style_combo)
        self.project_duration_label = QLabel("0分钟")
        info_layout.addRow("时长:", self.project_duration_label)
        info_group.setLayout(info_layout)
        left_layout.addWidget(info_group)

        # 技能包选择（普通用户无感路径：一次选择，全流程自动注入）
        skill_group = QGroupBox("🎨 技能包（自动注入全流程）")
        skill_layout = QFormLayout()
        skill_layout.setSpacing(8)
        self.style_pack_combo = QComboBox()
        self.style_pack_combo.setToolTip(
            "美术风格包：自动决定角色/场景/道具/分镜帧图/视频的画风锚定词与一致性约束。\n"
            "「不使用」= 回退到项目基础风格字段。"
        )
        skill_layout.addRow("美术风格包:", self.style_pack_combo)
        self.story_pack_combo = QComboBox()
        self.story_pack_combo.setToolTip(
            "叙事手法包：故事生成分镜规划与分镜表景别/运镜/转场参考。"
        )
        skill_layout.addRow("叙事手法包:", self.story_pack_combo)
        self.production_technique_combo = QComboBox()
        self.production_technique_combo.setToolTip(
            "制作技法文档：分镜表/分镜提示词通用技法，注入分镜脚本生成约束。"
        )
        skill_layout.addRow("制作技法:", self.production_technique_combo)
        self.video_mode_combo = QComboBox()
        self.video_mode_combo.setToolTip(
            "视频提示词模式：决定视频生成时使用的提示词格式与规则。\n"
            "不同模式对应不同的 AI 视频模型和生成方式。"
        )
        skill_layout.addRow("视频模式:", self.video_mode_combo)
        skill_group.setLayout(skill_layout)
        left_layout.addWidget(skill_group)

        left_layout.addStretch()
        main_layout.addWidget(left_widget, 0)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)
        stats_group = QGroupBox("📈 统计信息")
        stats_layout = QFormLayout()
        stats_layout.setSpacing(6)
        self.stats_scene_count = QLabel("0")
        stats_layout.addRow("场景数:", self.stats_scene_count)
        self.stats_character_count = QLabel("0")
        stats_layout.addRow("角色数:", self.stats_character_count)
        self.stats_prop_count = QLabel("0")
        stats_layout.addRow("道具数:", self.stats_prop_count)
        self.stats_storyboard_count = QLabel("0")
        stats_layout.addRow("分镜数:", self.stats_storyboard_count)
        self.stats_video_count = QLabel("0")
        stats_layout.addRow("视频数:", self.stats_video_count)
        stats_group.setLayout(stats_layout)
        right_layout.addWidget(stats_group)
        right_layout.addStretch()
        main_layout.addWidget(right_widget, 1)

    def refresh_skill_pack_options(self, style_names, story_names, technique_names, video_mode_names=None):
        """填充技能包下拉框（保留当前选择，若当前值不在选项中则清空为「不使用」）"""
        _NO = "不使用"

        def _populate(combo, names, current):
            block = combo.blockSignals(True)
            try:
                current_idx = combo.findText(current) if current else -1
                combo.clear()
                combo.addItem(_NO)
                for name in names:
                    combo.addItem(name)
                if current_idx > 0 and combo.findText(current) >= 0:
                    combo.setCurrentText(current)
                combo.blockSignals(block)
            finally:
                combo.blockSignals(block)

        _populate(self.style_pack_combo, style_names,
                  getattr(self, "_current_style_pack", ""))
        _populate(self.story_pack_combo, story_names,
                  getattr(self, "_current_story_pack", ""))
        _populate(self.production_technique_combo, technique_names,
                  getattr(self, "_current_technique", ""))
        if video_mode_names is not None:
            _populate(self.video_mode_combo, video_mode_names,
                      getattr(self, "_current_video_mode", ""))

    def set_current_skill_packs(self, style_pack, story_pack, technique, video_mode=""):
        """外部（加载项目）设置当前选中值，不触发信号"""
        self._current_style_pack = style_pack or ""
        self._current_story_pack = story_pack or ""
        self._current_technique = technique or ""
        self._current_video_mode = video_mode or ""

        def _set(combo, value):
            block = combo.blockSignals(True)
            try:
                idx = combo.findText(value) if value else -1
                combo.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                combo.blockSignals(block)

        _set(self.style_pack_combo, style_pack or "")
        _set(self.story_pack_combo, story_pack or "")
        _set(self.production_technique_combo, technique or "")
        _set(self.video_mode_combo, video_mode or "")

    def filter_style_packs_by_style(self, style_name: str):
        """根据项目风格筛选美术风格包下拉框，只显示对应风格的技能包"""
        from src.core.skills import style_pack_options_for_style
        matched = style_pack_options_for_style(style_name)
        block = self.style_pack_combo.blockSignals(True)
        try:
            current = self.style_pack_combo.currentText()
            self.style_pack_combo.clear()
            self.style_pack_combo.addItem("不使用")
            for name in matched:
                self.style_pack_combo.addItem(name)
            idx = self.style_pack_combo.findText(current)
            self.style_pack_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self.style_pack_combo.blockSignals(block)