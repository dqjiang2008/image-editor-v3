"""Story editing page."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class StoryPage(QWidget):
    """Story topic controls and generated story editor."""

    def __init__(self, controller):
        super().__init__()
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(12)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(8)

        theme_group = QGroupBox("📝 故事主题")
        theme_layout = QFormLayout()
        self.story_topic_input = QLineEdit()
        self.story_topic_input.setPlaceholderText("例如：小兔子找彩虹")
        theme_layout.addRow("主题:", self.story_topic_input)
        self.story_length_combo = QComboBox()
        self.story_length_combo.addItems(
            ["超短 (3分钟)", "短 (5分钟)", "中 (10分钟)", "长 (20分钟)"]
        )
        self.story_length_combo.setToolTip(
            "视频长度参考上限\n"
            "AI 会根据故事内容自行决定分镜数量和时长，\n"
            "此选项作为上限参考，避免生成过长视频。\n"
            "处理故事后会自动更新为实际时长档位。"
        )
        theme_layout.addRow("长度:", self.story_length_combo)
        theme_group.setLayout(theme_layout)
        left_layout.addWidget(theme_group)

        story_mode_group = QGroupBox("📖 故事模式")
        story_mode_layout = QVBoxLayout()
        story_mode_layout.setSpacing(6)
        story_mode_hint = QLabel("由分镜直接生成视频，无需角色参考图")
        story_mode_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        story_mode_layout.addWidget(story_mode_hint)
        self.gen_story_btn = QPushButton("✨ 生成故事")
        self.gen_story_btn.clicked.connect(controller._on_generate_story)
        story_mode_layout.addWidget(self.gen_story_btn)
        import_button = QPushButton("📥 导入故事文件")
        import_button.setToolTip("选择 txt/md 等文本文件，导入其内容作为故事母本")
        import_button.clicked.connect(controller._on_import_story_file)
        import_button.setObjectName("secondary")
        story_mode_layout.addWidget(import_button)
        process_button = QPushButton("🔧 处理故事")
        process_button.setToolTip(
            "从故事内容中一键提取角色、场景、道具和分镜。\n"
            "故事模式下视频由分镜直接生成（text模式），不需要角色参考图。\n"
            "适用于：AI生成故事后 / 导入故事文件后 / 手动粘贴故事后。"
        )
        process_button.clicked.connect(controller._on_extract_from_story)
        process_button.setObjectName("primary")
        story_mode_layout.addWidget(process_button)
        story_mode_group.setLayout(story_mode_layout)
        left_layout.addWidget(story_mode_group)

        episode_group = QGroupBox("📚 剧集模式")
        episode_layout = QVBoxLayout()
        episode_layout.setSpacing(6)
        episode_mode_hint = QLabel("图生图模式，必须要有角色才能生成视频")
        episode_mode_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        episode_layout.addWidget(episode_mode_hint)

        parse_all_btn = QPushButton("📦 解析全剧资产")
        parse_all_btn.setToolTip(
            "打开文件选择对话框，选取剧本文件（资产总表 + 分集正文），\n"
            "一次性提取全剧的角色/造型/道具/场景\n"
            "并保存到公共资产库（data/asset_library/）的 JSON 文件中。\n"
            "用法：先选资产总表（如 1-10集-总结.md），再选分集正文（可多选）。\n"
            "执行后可在「资产页」查看/管理已提取的资产，其他项目也可导入复用。"
        )
        parse_all_btn.clicked.connect(controller._on_parse_full_drama_assets)
        episode_layout.addWidget(parse_all_btn)

        import_ep_button = QPushButton("📚 解析集数分镜")
        import_ep_button.setToolTip(
            "选择剧集文件，解析其中的分镜表格并保存到分集JSON：\n"
            "① 分集正文（第X集.md）→ 按节拆集，解析分镜表格保存到分集文件；\n"
            "② 场景资产库（主场景整合表）→ 直接建/更新共享库（55个主场景）；\n"
            "制作流程：先「解析全剧资产」→ 再「解析集数分镜」→ 生成图片 → 选节做分镜 → 视频页合并成片"
        )
        import_ep_button.clicked.connect(controller._on_import_episode_file)
        episode_layout.addWidget(import_ep_button)
        section_combo_row = QHBoxLayout()
        section_combo_row.addWidget(QLabel("选择节:"))
        self.episode_combo = QComboBox()
        self.episode_combo.setToolTip("选择已保存的集.节（如 1.1 第一集第一节），直接加载分镜内容")
        self.episode_combo.currentIndexChanged.connect(controller._on_episode_selected)
        section_combo_row.addWidget(self.episode_combo, 1)
        episode_layout.addLayout(section_combo_row)
        
        self.episode_meta_label = QLabel("")
        self.episode_meta_label.setToolTip(
            "本节目的目标时长与镜头数（来自正文「**本节目标时长**/「**本节镜头数**」行，仅供参考）"
        )
        episode_layout.addWidget(self.episode_meta_label)
        import_lib_button = QPushButton("📦 导入资产库")
        import_lib_button.setToolTip(
            "从 data/asset_library/ 选择共享资产库调入当前项目；\n"
            "库内角色/造型/道具/场景按名称合并，同名角色自动补造型。\n"
            "（个人/单个资产导入用此功能）"
        )
        import_lib_button.clicked.connect(controller._on_import_story_library)
        episode_layout.addWidget(import_lib_button)
        episode_group.setLayout(episode_layout)
        left_layout.addWidget(episode_group)
        left_layout.addStretch()
        main_layout.addWidget(left_widget, 0)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(8)
        self.story_stats_label = QLabel("故事字数: 0")
        self.story_stats_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: bold;"
        )
        right_layout.addWidget(self.story_stats_label)

        story_group = QGroupBox("📖 故事内容")
        story_layout = QVBoxLayout()
        self.story_text = QTextEdit()
        self.story_text.setPlaceholderText("生成的故事将显示在这里...")
        self.story_text.setMinimumHeight(300)
        self.story_text.setReadOnly(True)
        self.story_text.textChanged.connect(controller._update_story_stats)
        story_layout.addWidget(self.story_text)

        edit_layout = QHBoxLayout()
        self.edit_story_btn = QPushButton("✏️ 编辑")
        self.edit_story_btn.setCheckable(True)
        self.edit_story_btn.clicked.connect(controller._toggle_story_edit)
        self.edit_story_btn.setObjectName("warning")
        edit_layout.addWidget(self.edit_story_btn)
        self.ai_analyze_btn = QPushButton("📊 AI分析统计")
        self.ai_analyze_btn.clicked.connect(controller._on_ai_analyze_story)
        self.ai_analyze_btn.setObjectName("secondary")
        edit_layout.addWidget(self.ai_analyze_btn)
        edit_layout.addStretch()
        story_layout.addLayout(edit_layout)
        story_group.setLayout(story_layout)
        right_layout.addWidget(story_group, 1)
        main_layout.addWidget(right_widget, 1)