"""
AI视频生成工具 v2 - 主窗口
美化界面 + 向导模式 + 图标显示
"""
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QMenuBar, QToolBar, QStatusBar, QPushButton, QLabel,
    QTabWidget, QGroupBox, QTextEdit, QComboBox, QLineEdit,
    QListWidget, QListWidgetItem, QSpinBox, QDoubleSpinBox,
    QFileDialog, QMessageBox, QSplitter, QScrollArea,
    QFormLayout, QFrame, QToolButton, QSizePolicy, QTreeWidget,
    QTreeWidgetItem, QInputDialog, QProgressBar, QStackedLayout, QStackedWidget, QSlider,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QGridLayout, QDialog, QDialogButtonBox, QMenu, QCheckBox,
    QProgressDialog, QTextBrowser
)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QUrl, QSize, QPropertyAnimation, QEasingCurve, QPoint, QMimeData
from PyQt6.QtGui import QPixmap, QAction, QColor, QFont, QIcon, QPainter, QBrush, QPen, QLinearGradient, QImage, QDrag, QDesktopServices
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
import json
import re
import sys
import time
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from src.config.settings import Settings
from src.services.agnes_client import AgnesAIClient
from src.services import asset_matcher as _am
from src.services import media_tools as _mt
from src.core.producer import AnimationProducer, _safe_filename
from src.models.project import Project, Character, Scene, Storyboard, ProjectManager
from src.gui.styles import STYLESHEET
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.widgets.fullscreen import FullscreenWindow
from src.gui.widgets.log_panel import LogPanel
from src.gui.widgets.media_panel import MediaPanel
from src.gui.widgets.main_toolbar import MainToolbar
from src.gui.widgets.navigation_panel import NavigationPanel
from src.gui.widgets.project_info_page import ProjectInfoPage
from src.gui.widgets.story_page import StoryPage
from src.gui.widgets.character_page import CharacterPage
from src.gui.widgets.prop_page import PropPage
from src.gui.widgets.scene_page import ScenePage
from src.gui.widgets.storyboard_page import StoryboardPage
from src.gui.widgets.video_page import VideoPage

from src.gui.workers import ProductionWorker, FfmpegInstallWorker


# ============================================================
# 主窗口
# ============================================================

class MainWindow(QMainWindow):
    """主窗口"""
    
    log_received = pyqtSignal(str)
    model_status_changed = pyqtSignal(str, str)
    
    def __init__(self):
        super().__init__()
        self.project: Optional[Project] = None
        self.client: Optional[AgnesAIClient] = None
        self.producer: Optional[AnimationProducer] = None
        self.project_manager: Optional[ProjectManager] = None
        
        self._undo_stack = []
        self._redo_stack = []
        self._is_unsaved = False
        self._refreshing_table = False
        self._auto_save_timer = QTimer()

        # 正在运行的 QThread 强引用集合：线程运行期间若失去唯一强引用（如
        # 被新线程赋值覆盖 self._worker）会被 GC 回收，触发
        # "QThread: Destroyed while thread is still running" 并闪退。
        # 统一放入集合保持存活，线程 finished/error 后自动移除。
        self._live_workers: set = set()
        
        # 向导步骤相关
        self.step_buttons = []
        self.step_status_labels = []
        
        # 日志走信号队列：工作线程（如视频合并）中调用 _log 时，
        # 通过信号转发到主线程处理，避免跨线程操作 GUI 控件导致闪退
        self.log_received.connect(self._append_log)
        self._setup_ui()
        self._init_client()
        self._start_auto_save()
    
    def _setup_ui(self):
        """设置UI"""
        self.setWindowTitle(f"{Settings.APP_NAME} v{Settings.APP_VERSION}")
        self.setMinimumSize(Settings.WINDOW_MIN_WIDTH, Settings.WINDOW_MIN_HEIGHT)
        self.resize(Settings.WINDOW_DEFAULT_WIDTH, Settings.WINDOW_DEFAULT_HEIGHT)
        
        # 设置全局样式
        self.setStyleSheet(STYLESHEET)
        
        # 中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 顶部工具栏
        toolbar = self._create_toolbar()
        main_layout.addWidget(toolbar)
        
        # 主内容区（专业视频编辑软件布局）
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #cbd5e1;
                height: 5px;
            }
            QSplitter::handle:hover {
                background-color: #94a3b8;
            }
        """)
        main_splitter.setHandleWidth(5)
        main_splitter.setChildrenCollapsible(False)
        
        # 上方区域：水平分割器（左侧导航 + 中间工作区 + 右侧预览）
        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #e2e8f0;
                width: 3px;
            }
            QSplitter::handle:hover {
                background-color: #cbd5e1;
            }
        """)
        top_splitter.setHandleWidth(3)
        
        # 左侧：导航面板（深色主题，图标+文字）
        nav_panel = self._create_left_panel()
        nav_panel.setMinimumWidth(100)
        nav_panel.setMaximumWidth(140)
        top_splitter.addWidget(nav_panel)
        
        # 媒体播放器（提前初始化，供各页面共享预览与播放）
        self.media_player = QMediaPlayer()
        self.media_audio_output = QAudioOutput()
        self.media_audio_output.setVolume(1.0)
        self.media_player.setAudioOutput(self.media_audio_output)
        self.media_player.positionChanged.connect(self._on_media_position_changed)
        self.media_player.durationChanged.connect(self._on_media_duration_changed)

        # 中间：工作区（堆叠窗口，根据左侧选择切换）
        work_panel = self._create_right_panel()
        top_splitter.addWidget(work_panel)
        
        # 右侧：媒体预览面板
        self._media_panel = self._create_media_panel()
        self._media_panel.setMinimumWidth(340)
        self._media_panel.setMaximumWidth(420)
        top_splitter.addWidget(self._media_panel)
        
        top_splitter.setSizes([120, 900, 360])
        top_splitter.setStretchFactor(0, 0)
        top_splitter.setStretchFactor(1, 1)
        top_splitter.setStretchFactor(2, 0)
        
        main_splitter.addWidget(top_splitter)
        
        # 下方：日志面板（固定最小高度，不可折叠）
        log_panel = self._create_log_panel()
        log_panel.setMinimumHeight(150)  # 提高最小高度
        main_splitter.addWidget(log_panel)
        
        main_splitter.setSizes([600, 150])
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 0)
        main_splitter.setCollapsible(0, False)  # 上方工作区不可折叠
        main_splitter.setCollapsible(1, False)  # 日志面板不可折叠
        
        main_layout.addWidget(main_splitter, 1)
        
        # 底部状态栏
        self._setup_status_bar()
    
    def _create_toolbar(self) -> QWidget:
        toolbar = MainToolbar(self)
        self.undo_btn = toolbar.undo_btn
        self.redo_btn = toolbar.redo_btn
        self.unsaved_label = toolbar.unsaved_label
        return toolbar
    
    def _create_separator(self) -> QFrame:
        """创建分隔线"""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("color: #e2e8f0;")
        return line
    
    def _create_left_panel(self) -> QWidget:
        panel = NavigationPanel(self._switch_to_page)
        self.nav_buttons = panel.buttons
        return panel
    
    def _clear_shared_media_preview(self):
        """切页前清理右侧共用媒体预览区：视频源、图片缓存和播放器状态都回收。"""
        def _safe_get(name):
            return getattr(self, name, None)

        player = _safe_get('media_player')
        if player:
            try:
                player.stop()
                player.setSource(QUrl())
            except Exception:
                pass
            # 关键修复：QAudioOutput 的音量在部分 Windows 音频会话/驱动下会漂移为 0，
            # 导致播放器"有声变无声"。切页时强制复位为 1.0，保证任何播放入口都有声音。
            audio_output = _safe_get('media_audio_output')
            if audio_output:
                try:
                    if audio_output.volume() < 0.01:
                        audio_output.setVolume(1.0)
                except Exception:
                    pass
        for attr, args in [
            ('media_display_label', ('clear',)),
            ('media_info_label', ('setText', '')),
            ('media_play_btn', ('setText', '▶ 播放')),
        ]:
            widget = _safe_get(attr)
            if widget:
                try:
                    getattr(widget, args[0])(*args[1:])
                except Exception:
                    pass
        video_widget = _safe_get('media_video_widget')
        if video_widget:
            try:
                video_widget.hide()
            except Exception:
                pass

        panel = _safe_get('_media_panel')
        if panel and hasattr(panel, 'clear_preview_images'):
            try:
                panel.clear_preview_images()
            except Exception:
                pass

        # 将视频输出重置回右侧共享预览面板的 media_video_widget，
        # 避免切页后视频仍在不可见的控件上渲染
        if player and video_widget:
            try:
                if player.videoOutput() != video_widget:
                    player.setVideoOutput(video_widget)
                    self._log("📹 切页时重置视频输出到右侧共享预览面板")
            except Exception:
                pass

    def _switch_to_page(self, page_index: int):
        """切换到指定页面：切页前先清理右侧共用预览框的旧图像/旧视频缓存。"""
        # 先清理当前右侧共享预览区，避免跨页残留图像/视频源
        self._clear_shared_media_preview()

        self._refresh_page_data(page_index)

        if hasattr(self, 'work_stack'):
            self.work_stack.setCurrentIndex(page_index)
        
        # 更新导航按钮状态
        for i, btn in enumerate(self.nav_buttons):
            btn.blockSignals(True)
            btn.setChecked(i == page_index)
            btn.blockSignals(False)

    def _refresh_page_data(self, page_index: int):
        """统一刷新页面入口，保证跨页面修改后的数据及时同步。"""
        if not self.project:
            self._refresh_ui()
            self.video_list.clear()
            self.video_stats_label.setText("视频总数: 0")
            self._update_stats()
            return

        if page_index == 0:
            self._refresh_ui()
        elif page_index == 1:
            self.story_text.setPlainText(self.project.story)
            self.story_topic_input.setText(self.project.topic)
            length_map = {
                "very_short": "超短 (3分钟)",
                "short": "短 (5分钟)",
                "medium": "中 (10分钟)",
                "long": "长 (20分钟)",
            }
            length_index = self.story_length_combo.findText(
                length_map.get(self.project.length, "长 (20分钟)")
            )
            if length_index >= 0:
                self.story_length_combo.setCurrentIndex(length_index)
        elif page_index == 2:
            self._refresh_character_list()
        elif page_index == 3:
            self._refresh_prop_list()
        elif page_index == 4:
            self._refresh_scene_list()
        elif page_index == 5:
            self._refresh_storyboard_table()
        elif page_index == 6:
            self._refresh_video_list()
        self._update_stats()
    
    def _create_two_column_layout(self, left_widget: QWidget, right_widget: QWidget) -> QWidget:
        """创建两栏布局：左侧内容 + 右侧内容"""
        container = QWidget()
        main_layout = QHBoxLayout(container)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)
        
        # 左侧内容
        main_layout.addWidget(left_widget, 0)
        
        # 右侧内容
        main_layout.addWidget(right_widget, 1)
        
        return container
    
    def _create_media_panel(self) -> QWidget:
        panel = MediaPanel(self)
        for name in (
            "media_container",
            "media_display_label",
            "media_video_widget",
            "media_time_label",
            "media_progress",
            "media_play_btn",
            "media_pause_btn",
            "media_stop_btn",
            "media_info_label",
            "preview_area",
            "preview_cell_1",
            "preview_image_label_1",
            "preview_caption_1",
            "preview_cell_2",
            "preview_image_label_2",
            "preview_caption_2",
        ):
            setattr(self, name, getattr(panel, name))
        self.media_display_layout = panel.display_layout
        return panel
    
    def _media_zoom_in(self):
        """放大媒体 - 调整右侧面板宽度"""
        if hasattr(self, '_media_panel'):
            current_width = self._media_panel.width()
            new_width = min(current_width + 100, 800)
            self._media_panel.setFixedWidth(new_width)
            self.media_container.setMinimumSize(new_width - 40, int((new_width - 40) * 9 / 16))
    
    def _media_zoom_out(self):
        """缩小媒体 - 调整右侧面板宽度"""
        if hasattr(self, '_media_panel'):
            current_width = self._media_panel.width()
            new_width = max(current_width - 100, 340)
            self._media_panel.setFixedWidth(new_width)
            self.media_container.setMinimumSize(new_width - 40, int((new_width - 40) * 9 / 16))
    
    def _media_fullscreen(self):
        """全屏显示媒体"""
        if not hasattr(self, '_fullscreen_window'):
            self._fullscreen_window = None
        
        if self._fullscreen_window and self._fullscreen_window.isVisible():
            # 退出全屏
            self._fullscreen_window.exit_fullscreen()
        else:
            # 进入全屏
            self._fullscreen_window = FullscreenWindow(self)
            
            # 复制当前播放器状态
            if self.media_player.source().isLocalFile():
                self._fullscreen_window.player.setSource(self.media_player.source())
                if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self._fullscreen_window.player.play()
            
            self._fullscreen_window.showFullScreen()
    
    def _on_fullscreen_exited(self):
        """全屏退出后的处理"""
        self._fullscreen_window = None
    
    def _media_play(self):
        """播放媒体"""
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.media_play_btn.setText("▶ 播放")
        else:
            # 播放前强制检查音频输出音量，防止 QAudioOutput 漂移为 0 导致无声
            if hasattr(self, 'media_audio_output'):
                try:
                    if self.media_audio_output.volume() < 0.01:
                        self.media_audio_output.setVolume(1.0)
                        self._log("🔊 检测到音频输出音量为 0，已恢复为 100%")
                except Exception:
                    pass
            self.media_player.play()
            self.media_play_btn.setText("⏸ 暂停")
    
    def _media_pause(self):
        """暂停媒体"""
        self.media_player.pause()
        self.media_play_btn.setText("▶ 播放")
    
    def _media_stop(self):
        """停止媒体"""
        self.media_player.stop()
        self.media_play_btn.setText("▶ 播放")
    
    def _play_media_video(self, video_path: str, **kwargs):
        """统一的视频播放入口：确保音量正常后再播放。

        参数:
            video_path: 视频文件路径
            output_widget: 可选，指定视频输出控件（None 表示不改变当前视频输出）
            auto_play: 是否自动开始播放（默认 True）
            log_prefix: 日志前缀
        """
        if hasattr(self, 'media_audio_output'):
            try:
                # 强制检查音频输出音量，防止 QAudioOutput 漂移为 0 导致无声
                if self.media_audio_output.volume() < 0.01:
                    self.media_audio_output.setVolume(1.0)
                    self._log("🔊 检测到音频输出音量为 0，已恢复为 100%")
            except Exception:
                pass
        output_widget = kwargs.get('output_widget')
        if output_widget is not None and hasattr(self, 'media_player'):
            if self.media_player.videoOutput() != output_widget:
                self.media_player.setVideoOutput(output_widget)
        self.media_player.stop()
        self.media_player.setSource(QUrl.fromLocalFile(str(Path(video_path).resolve())))
        if kwargs.get('auto_play', True):
            # 延迟 200ms 再播放，给解码器一点时间
            QTimer.singleShot(200, self.media_player.play)
        if kwargs.get('log_prefix'):
            self._log(f"{kwargs['log_prefix']} 播放视频: {Path(video_path).name}")
    
    def _on_media_position_changed(self, position: int):
        """媒体播放位置变化"""
        if self.media_progress.maximum() > 0:
            self.media_progress.setValue(position)
        
        duration = self.media_player.duration()
        if duration > 0:
            current_sec = position // 1000
            total_sec = duration // 1000
            self.media_time_label.setText(
                f"{current_sec // 60:02d}:{current_sec % 60:02d} / "
                f"{total_sec // 60:02d}:{total_sec % 60:02d}"
            )
    
    def _on_media_duration_changed(self, duration: int):
        """媒体时长变化"""
        self.media_progress.setMaximum(duration)
        if duration > 0:
            total_sec = duration // 1000
            self.media_time_label.setText(
                f"00:00 / {total_sec // 60:02d}:{total_sec % 60:02d}"
            )
    
    def _on_media_progress_moved(self, position: int):
        """拖动进度条跳转"""
        if self.media_player.duration() > 0:
            self.media_player.setPosition(position)
    
    def show_media_image(self, image_path: str, info_text: str = ""):
        """显示图片"""
        from pathlib import Path
        path = Path(image_path)
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self.media_display_label.setPixmap(pixmap)
                self.media_display_layout.setCurrentWidget(self.media_display_label)
                self.media_display_label.show()
                self.media_video_widget.hide()
                self.media_info_label.setText(info_text or f"图片: {path.name}")
            else:
                self.media_display_label.setText("图片加载失败")
        else:
            self.media_display_label.setText("图片文件不存在")
    
    def show_media_video(self, video_path: str, info_text: str = ""):
        """显示视频"""
        from pathlib import Path
        path = Path(video_path)
        if path.exists():
            self.media_display_label.hide()
            self.media_display_layout.setCurrentWidget(self.media_video_widget)
            self.media_video_widget.show()
            self._play_media_video(str(path), auto_play=True, log_prefix=info_text or f"视频: {path.name}")
            self.media_info_label.setText(info_text or f"视频: {path.name}")
        else:
            self.media_display_label.setText("视频文件不存在")
            self.media_display_label.show()
            self.media_video_widget.hide()
    
    def _create_step_widget(self, step: dict) -> QWidget:
        """创建单个步骤组件"""
        widget = QWidget()
        widget.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                border: 2px solid #e2e8f0;
                border-radius: 8px;
            }
            QWidget:hover {
                background-color: #f1f5f9;
                border-color: #3b82f6;
            }
        """)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        
        # 图标
        icon_label = QLabel(step["icon"])
        icon_label.setStyleSheet("font-size: 24px;")
        icon_label.setFixedSize(36, 36)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)
        
        # 文字
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        
        name_label = QLabel(step["name"])
        name_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #1e293b;")
        text_layout.addWidget(name_label)
        
        desc_label = QLabel(step["desc"])
        desc_label.setStyleSheet("font-size: 11px; color: #64748b;")
        text_layout.addWidget(desc_label)
        
        layout.addLayout(text_layout, 1)
        
        # 状态图标
        status_label = QLabel("○")
        status_label.setStyleSheet("font-size: 18px; color: #cbd5e1;")
        status_label.setFixedSize(24, 24)
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(status_label)
        
        # 保存引用
        self.step_buttons.append(widget)
        self.step_status_labels.append(status_label)
        
        # 点击事件
        widget.mousePressEvent = lambda e, idx=step["index"]: self._on_step_clicked(idx)
        
        return widget
    
    def _create_right_panel(self) -> QWidget:
        """创建右侧工作区 - 堆叠窗口"""
        self.work_stack = QStackedWidget()
        self.work_stack.setStyleSheet("""
            QStackedWidget {
                border: 2px solid #e2e8f0;
                border-radius: 8px;
                background-color: #ffffff;
            }
        """)
        
        # 添加各个页面（与左侧导航对应）
        self.work_stack.addWidget(self._create_project_info_page())  # 0: 项目信息
        self.work_stack.addWidget(self._create_story_tab())          # 1: 故事
        self.work_stack.addWidget(self._create_character_tab())      # 2: 角色
        self.work_stack.addWidget(self._create_prop_tab())           # 3: 道具
        self.work_stack.addWidget(self._create_scene_tab())          # 4: 场景
        self.work_stack.addWidget(self._create_storyboard_tab())     # 5: 分镜
        self.work_stack.addWidget(self._create_video_tab())          # 6: 视频

        from src.gui.styles import HighContrastCheckDelegate
        check_delegate = HighContrastCheckDelegate()
        if hasattr(self, 'character_list'):
            self.character_list.setItemDelegate(check_delegate)
        if hasattr(self, 'prop_list'):
            self.prop_list.setItemDelegate(HighContrastCheckDelegate())
        if hasattr(self, 'scene_list'):
            self.scene_list.setItemDelegate(HighContrastCheckDelegate())

        return self.work_stack
    
    def _create_project_info_page(self) -> QWidget:
        page = ProjectInfoPage()
        self._project_info_page = page
        for name in (
            "project_name_label",
            "project_age_combo",
            "project_style_combo",
            "project_duration_label",
            "stats_scene_count",
            "stats_character_count",
            "stats_prop_count",
            "stats_storyboard_count",
            "stats_video_count",
            "style_pack_combo",
            "story_pack_combo",
            "production_technique_combo",
            "video_mode_combo",
        ):
            setattr(self, name, getattr(page, name))
        self.project_age_combo.currentTextChanged.connect(self._on_age_group_changed)
        self.project_style_combo.currentTextChanged.connect(self._on_style_changed)
        self.style_pack_combo.currentTextChanged.connect(self._on_style_pack_changed)
        self.story_pack_combo.currentTextChanged.connect(self._on_story_pack_changed)
        self.production_technique_combo.currentTextChanged.connect(self._on_production_technique_changed)
        self.video_mode_combo.currentTextChanged.connect(self._on_video_mode_changed)
        # 填充选项（首次）
        self._populate_skill_pack_options()
        return page

    def _populate_skill_pack_options(self):
        """从 resources/skills/ 扫描结果填充技能包下拉框"""
        from src.core import skills
        page = getattr(self, "_project_info_page", None)
        if page is None:
            return
        style_names = [p.display_name for p in skills.list_style_packs()]
        story_names = [p.display_name for p in skills.list_story_packs()]
        technique_names = skills.production_technique_options()
        video_mode_names = skills.video_prompt_mode_options()
        page.refresh_skill_pack_options(style_names, story_names, technique_names, video_mode_names)

    def _sync_skill_pack_ui(self):
        """项目（重新）加载后，把项目上的技能包值同步到下拉框（不触发信号）"""
        if not self.project:
            return
        style = getattr(self.project, "skill_pack", "") or ""
        story = getattr(self.project, "story_pack", "") or ""
        tech = getattr(self.project, "production_technique", "") or ""
        video_mode = getattr(self.project, "video_mode", "") or ""
        page = getattr(self, "_project_info_page", None)
        if page is not None:
            page.set_current_skill_packs(style, story, tech, video_mode)
        # 角色页画风提示：启用风格包时提示预设仅作标签
        if hasattr(self, "style_pack_hint_label"):
            if style:
                self.style_pack_hint_label.setText(
                    f"已启用「{style}」风格包，角色/场景/道具画风将按该包模板生成；上方预设仅作角色标签，不影响画风。"
                )
            else:
                self.style_pack_hint_label.setText("未启用风格包：画风由项目「风格」字段决定（可在项目信息页选择）。")

    def _on_style_pack_changed(self, name: str):
        """项目信息页：美术风格包改变 → 写入项目并自动保存"""
        if not self.project:
            return
        self.project.skill_pack = "" if (not name or name == "不使用") else name
        self._log(f"🎨 美术风格包: {self.project.skill_pack or '（未启用）'}")
        if not self._is_unsaved:
            self.project_manager.save_project(self.project)
        self._sync_skill_pack_ui()

    def _on_story_pack_changed(self, name: str):
        if not self.project:
            return
        self.project.story_pack = "" if (not name or name == "不使用") else name
        self._log(f"📖 叙事手法包: {self.project.story_pack or '（未启用）'}")
        if not self._is_unsaved:
            self.project_manager.save_project(self.project)

    def _on_production_technique_changed(self, name: str):
        if not self.project:
            return
        self.project.production_technique = "" if (not name or name == "不使用") else name
        self._log(f"🛠️ 制作技法: {self.project.production_technique or '（未启用）'}")
        if not self._is_unsaved:
            self.project_manager.save_project(self.project)

    def _on_video_mode_changed(self, name: str):
        """项目信息页：视频提示词模式改变 → 写入项目并自动保存"""
        if not self.project:
            return
        self.project.video_mode = "" if (not name or name == "不使用") else name
        self._log(f"🎬 视频模式: {self.project.video_mode or '（未启用）'}")
        if not self._is_unsaved:
            self.project_manager.save_project(self.project)

    def _on_age_group_changed(self, name: str):
        """项目信息页：内容类型改变 → 写入项目并自动保存"""
        if not self.project:
            return
        self.project.age_group = name
        self._log(f"📋 内容类型: {name}")
        if not self._is_unsaved:
            self.project_manager.save_project(self.project)

    def _on_style_changed(self, name: str):
        """项目信息页：视觉风格改变 → 联动筛选美术风格包 + 写入项目并自动保存"""
        if not self.project:
            return
        self.project.style = name
        self._log(f"🎨 视觉风格: {name}")
        page = getattr(self, "_project_info_page", None)
        if page is not None:
            page.filter_style_packs_by_style(name)
            pack_name = self.style_pack_combo.currentText()
            pack_val = "" if (not pack_name or pack_name == "不使用") else pack_name
            if self.project.skill_pack != pack_val:
                self.project.skill_pack = pack_val
        if not self._is_unsaved:
            self.project_manager.save_project(self.project)

    def _on_custom_style_suffix_changed(self, text: str):
        """分镜页高级选项：自定义风格短语 → 写入项目（叠加到帧图/视频 prompt）"""
        if not self.project:
            return
        self.project.custom_style_suffix = (text or "").strip()
        if self.project.custom_style_suffix:
            self._log(f"⚙️ 自定义风格注入已启用（{len(self.project.custom_style_suffix)} 字）")
        if not self._is_unsaved:
            self.project_manager.save_project(self.project)
    
    def _create_story_tab(self) -> QWidget:
        page = StoryPage(self)
        for name in (
            "story_topic_input",
            "story_length_combo",
            "gen_story_btn",
            "story_stats_label",
            "story_text",
            "edit_story_btn",
            "episode_combo",
            "episode_meta_label",
        ):
            setattr(self, name, getattr(page, name))
        return page
    
    def _update_story_stats(self):
        """更新故事统计"""
        if hasattr(self, 'story_text') and hasattr(self, 'story_stats_label'):
            text = self.story_text.toPlainText()
            char_count = len(text)
            word_count = len(text.split())
            self.story_stats_label.setText(f"字符数: {char_count} | 词数: {word_count}")
    
    def _create_character_tab(self) -> QWidget:
        page = CharacterPage(self, Settings.CHARACTER_PRESETS)
        for name in (
            "char_name_input",
            "char_style_combo",
            "char_desc_input",
            "char_type_combo",
            "update_char_btn",
            "costume_btn",
            "character_stats_label",
            "character_list",
            "char_filter_combo",
            "char_type_filter_combo",
        ):
            setattr(self, name, getattr(page, name))
        self.style_pack_hint_label = page.style_pack_hint_label
        return page
    
    def _create_prop_tab(self) -> QWidget:
        page = PropPage(self)
        for name in (
            "prop_stats_label",
            "prop_list",
            "prop_filter_combo",
        ):
            setattr(self, name, getattr(page, name))
        return page

    def _create_scene_tab(self) -> QWidget:
        page = ScenePage(self)
        for name in (
            "scene_name_input",
            "scene_desc_input",
            "scene_time_combo",
            "scene_priority_combo",
            "scene_category_combo",
            "scene_main_scene_input",
            "update_scene_btn",
            "scene_stats_label",
            "scene_list_group",
            "scene_list",
            "scene_filter_combo",
            "scene_priority_filter_combo",
            "scene_category_filter_combo",
            "stop_task_btn",
        ):
            setattr(self, name, getattr(page, name))
        return page
    
    def _create_storyboard_tab(self) -> QWidget:
        page = StoryboardPage(self)
        for name in (
            "scene_count_spin",
            "duration_spin",
            "storyboard_stats_label",
            "storyboard_table",
            "storyboard_duration_label",
            "custom_style_suffix_edit",
            "section_title_label",
        ):
            setattr(self, name, getattr(page, name))
        return page
    
    def _create_video_tab(self) -> QWidget:
        page = VideoPage(self)
        self.video_list = page.video_list
        self.video_stats_label = page.video_stats_label
        return page
    
    def _refresh_video_list(self):
        """刷新视频列表"""
        self._ensure_media_registered()
        if not self.project:
            self.video_list.clear()
            self.video_stats_label.setText("视频总数: 0")
            return
        
        self.video_list.clear()
        
        # 添加总视频（如果存在）
        if self.project.video_path and Path(self.project.video_path).exists():
            item = QListWidgetItem("🎬 总视频（合并后）")
            item.setData(Qt.ItemDataRole.UserRole, {"type": "final", "path": self.project.video_path})
            self.video_list.addItem(item)
        
        # 添加分镜视频
        for i, sb in enumerate(self.project.storyboard):
            if sb.video_path and Path(sb.video_path).exists():
                item = QListWidgetItem(f"🎞️ 分镜 {sb.scene_number}: {sb.scene or sb.scene_name}")
                item.setData(Qt.ItemDataRole.UserRole, {"type": "scene", "index": i, "path": sb.video_path})
                self.video_list.addItem(item)
        
        # 默认选中第一个视频
        if self.video_list.count() > 0:
            self.video_list.setCurrentRow(0)
        self.video_stats_label.setText(f"视频总数: {self.video_list.count()}")
    
    def _on_import_video(self):
        """导入视频文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入视频文件", "", "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv)"
        )
        if not file_path:
            return
        
        # 复制到项目目录
        project_dir = Settings.PROJECTS_DIR / self.project.name
        project_dir.mkdir(parents=True, exist_ok=True)
        
        import shutil
        dest_path = project_dir / Path(file_path).name
        shutil.copy2(file_path, dest_path)
        
        # 如果是第一个导入的视频，设为项目视频
        if not self.project.video_path:
            self.project.video_path = str(dest_path)
            self.project_manager.save_project(self.project)
        
        self._refresh_video_list()
        self._log(f"已导入视频: {Path(file_path).name}")
    
    def _on_video_list_changed(self, row: int):
        """视频列表选择变化时"""
        if row < 0 or row >= self.video_list.count():
            return
        
        item = self.video_list.item(row)
        video_info = item.data(Qt.ItemDataRole.UserRole)
        if not video_info:
            return
        
        video_path = video_info.get("path", "")
        if not video_path or not Path(video_path).exists():
            if hasattr(self, 'media_info_label'):
                self.media_info_label.setText("视频文件不存在")
            return
        
        # 在共用媒体显示框中显示视频
        if hasattr(self, 'show_media_video'):
            if video_info.get("type") == "final":
                self.show_media_video(video_path, "🎬 总视频（合并后）")
            else:
                idx = video_info.get("index", 0)
                if self.project and idx < len(self.project.storyboard):
                    sb = self.project.storyboard[idx]
                    self.show_media_video(video_path, f"分镜 #{sb.scene_number}: {sb.scene or sb.scene_name}")
                else:
                    self.show_media_video(video_path, video_path)
    
    def _on_video_double_clicked(self, item):
        """双击视频项时自动播放"""
        self._media_play()
    
    def _create_log_panel(self) -> QWidget:
        self._log_panel = LogPanel()
        self._log_text = self._log_panel.text_edit
        return self._log_panel
    
    def _ensure_media_registered(self):
        """自动挂载合并成片：项目未指定最终视频时，使用 videos/final.mp4"""
        if not self.project or self.project.video_path:
            return
        final = Settings.PROJECTS_DIR / self.project.name / "videos" / "final.mp4"
        if final.exists():
            self.project.video_path = str(final)
            if self.project_manager:
                self.project_manager.save_meta(self.project)
            self._log("自动加载最终视频: final.mp4")

    def _create_log_tab(self) -> QWidget:
        """创建日志标签页（保留但提示使用底部日志）"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        info_label = QLabel("💡 日志已移至窗口底部，可直接查看")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setStyleSheet("font-size: 16px; color: #64748b; padding: 40px;")
        layout.addWidget(info_label)
        
        return widget
    
    def _clear_log(self):
        """清空日志"""
        if hasattr(self, '_log_text'):
            self._log_text.clear()
    
    def closeEvent(self, ev):
        """关闭窗口时自动保存全部项目数据（分镜参数、剧集分集文件）"""
        self._release_media_players()   # 退出前释放所有播放器句柄
        # 等待运行中的生成线程自然结束（QThread.wait 在 Python 端是安全的；
        # 绝不能让窗口在后台线程仍在跑时直接退出，否则 QThread 在
        # 线程销毁前被回收 → "Destroyed while thread is still running" → 崩溃。
        # cancel() 仅置停止位，线程内的进度循环会尽快退出。）
        if getattr(self, '_live_workers', None):
            for w in list(self._live_workers):
                try:
                    w.cancel()
                except Exception:
                    pass
            for w in list(self._live_workers):
                try:
                    if w.isRunning():
                        w.wait(5000)
                except Exception:
                    pass
        if self.project:
            try:
                # 必须先快照当前集分镜到 episode_data，否则 frame_path/video_path 丢失
                self._snapshot_current_episode()
                self.project_manager.save_project(self.project)
            except Exception as e:
                self._log(f"退出时自动保存失败: {e}")
        super().closeEvent(ev)

    def _setup_status_bar(self):
        """设置状态栏"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")
        
        # 模型状态
        self.text_model_status = QLabel("文本模型: 未使用")
        self.text_model_status.setStyleSheet("padding: 2px 10px; color: #6d28d9; font-weight: bold;")
        self.status_bar.addPermanentWidget(self.text_model_status)
        
        self.image_model_status = QLabel("图像模型: 未使用")
        self.image_model_status.setStyleSheet("padding: 2px 10px; color: #b45309; font-weight: bold;")
        self.status_bar.addPermanentWidget(self.image_model_status)
        
        self.video_model_status = QLabel("视频模型: 未使用")
        self.video_model_status.setStyleSheet("padding: 2px 10px; color: #0f766e; font-weight: bold;")
        self.status_bar.addPermanentWidget(self.video_model_status)
        
        # 进度状态
        self.production_status = QLabel("制作进度: 0%")
        self.production_status.setStyleSheet("padding: 2px 10px; color: #1d4ed8; font-weight: bold;")
        self.status_bar.addPermanentWidget(self.production_status)
    
    def _init_client(self):
        """初始化AI客户端"""
        import sys
        
        Settings.ensure_dirs()
        Settings.load_user_config()
        
        # 输出初始化信息到终端
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[初始化] AI客户端配置", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"API Base URL: {Settings.API_BASE_URL}", file=sys.stderr)
        print(f"API Key: {Settings.API_KEY[:20]}...{Settings.API_KEY[-10:]}", file=sys.stderr)
        print(f"Text Model: {Settings.TEXT_MODEL}", file=sys.stderr)
        print(f"Image Model: {Settings.IMAGE_MODEL}", file=sys.stderr)
        print(f"Video Model: {Settings.VIDEO_MODEL}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        
        # 检查API Key是否配置
        if not Settings.API_KEY or Settings.API_KEY == "":
            print(f"[错误] API Key 未配置！", file=sys.stderr)
            QMessageBox.critical(self, "配置错误", 
                "API Key 未配置！\n\n"
                "请在 设置 -> API配置 中填写你的 API Key")
        
        self.client = AgnesAIClient(
            api_key=Settings.API_KEY,
            base_url=Settings.API_BASE_URL,
            log_callback=self._log,
            debug_mode=Settings.AGNES_DEBUG_MODE,
            debug_dir=Settings.AGNES_DEBUG_DIR
        )
        self.project_manager = ProjectManager(str(Settings.PROJECTS_DIR))
        
        
        # 自动加载上次的项目
        if Settings.LAST_PROJECT_NAME:
            self._load_last_project()
    
    def _load_last_project(self):
        project_name = Settings.LAST_PROJECT_NAME
        projects = self.project_manager.list_projects()
        
        if project_name in projects:
            self.project = self.project_manager.load_project(project_name)
            if self.project:
                self.producer = AnimationProducer(self.client, self.project, self._log, self.project_manager)
                self._refresh_ui()
                self._log(f"自动打开上次的项目: {project_name}")
                # ====== 下次进入项目：检查资产/图片标记 ======
                self._check_asset_image_flags()
        else:
            self._log(f"上次的项目 '{project_name}' 不存在，已跳过")

    def _check_asset_image_flags(self):
        """下次进入项目时检查资产/图片生成标记。

        如果"资产已生成"和"图片已生成"都已标记：
        - 跳过弹框②和③
        - 直接弹框④：选择集数开始创作
        如果任一标记未完成：
        - 弹框提示用户，并提供"立即生成"按钮
        """
        if not self.project:
            return
        assets_ok = getattr(self.project, 'assets_generated', False)
        images_ok = getattr(self.project, 'images_generated', False)

        if assets_ok and images_ok:
            # 全部完成 → 不弹框，直接恢复上次的集数
            self._log("✅ 资产已生成 & 图片已生成，跳过弹框②③，直接恢复上次集数")
            # 如果有 current_episode，直接加载该集数据
            cur_ep = getattr(self.project, 'current_episode', -1)
            self._log(f"🔍 启动恢复: cur_ep={cur_ep}, episodes数量={len(self.project.episodes) if self.project.episodes else 0}")
            if cur_ep >= 0 and hasattr(self, 'episode_combo'):
                self.episode_combo.blockSignals(True)
                if 0 <= cur_ep < len(self.project.episodes):
                    self.episode_combo.setCurrentIndex(cur_ep + 1)  # +1 因为有占位项
                    self._log(f"🔍 设置下拉框索引: {cur_ep + 1}")
                self.episode_combo.blockSignals(False)
                # 加载该集数据
                self._log(f"🔍 开始加载第{cur_ep+1}集数据...")
                self._load_episode_data(cur_ep)
                self._log(f"🔍 _load_episode_data 完成，project.characters数量={len(self.project.characters)}")
                self._update_asset_filter_labels()
                self._refresh_character_list()
                self._refresh_scene_list()
                if cur_ep < len(self.project.episodes):
                    self._update_episode_meta_label(cur_ep)
                self._log(f"📺 已恢复上次选择的集数: 第{cur_ep+1}集")
        else:
            # 有未完成的标记 → 弹框提示并提供生成入口
            missing = []
            if not assets_ok:
                missing.append("全局资产（角色/道具/场景）")
            if not images_ok:
                missing.append("图片（角色图/道具图/场景图）")
            self._log(
                f"⚠️ 以下内容尚未生成：{', '.join(missing)}。"
                f"请先生成完后再选择集数制作分镜。"
            )
            msg = QMessageBox(self)
            msg.setWindowTitle("⚠️ 未完成生成")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText(f"以下内容尚未生成：\n{chr(10).join('· ' + m for m in missing)}")
            msg.setInformativeText("请先生成完全局资产和图片后，再按剧集生成分镜。")
            gen_btn = msg.addButton("🔧 立即生成", QMessageBox.ButtonRole.AcceptRole)
            later_btn = msg.addButton("稍后处理", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == gen_btn:
                if not assets_ok:
                    # 已有全局资产数据则跳过重新生成
                    if self.project.global_characters or self.project.global_scenes:
                        self._log("✅ 全局资产已存在，跳过重新生成")
                    else:
                        self._generate_global_assets()
                    self.project.assets_generated = True
                    self.project_manager.save_project(self.project)
                    self._log("✅ 已标记：资产已生成")
                if not images_ok:
                    # 已有图片则跳过重新生成
                    has_images = any(
                        getattr(c, 'image_path', None) for c in self.project.global_characters
                    ) or any(
                        getattr(s, 'image_path', None) for s in self.project.global_scenes
                    )
                    if has_images:
                        self._log("✅ 图片已存在，跳过重新生成")
                    else:
                        self._generate_images()
                    self.project.images_generated = True
                    self.project_manager.save_project(self.project)
                    self._log("✅ 已标记：图片已生成")
                # 生成完成后进入弹框④
                QTimer.singleShot(300, self._prompt_episode_selection)
    
    def _start_auto_save(self):
        self._auto_save_timer.timeout.connect(self._on_auto_save)
        self._auto_save_timer.start(Settings.AUTO_SAVE_INTERVAL * 1000)
    
    @staticmethod
    def _make_icon(draw_fn) -> QIcon:
        """通用图标构造器：创建 24×24 透明画布，用 draw_fn(painter) 绘制，返回 QIcon。"""
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(Qt.GlobalColor.white)
        pen.setWidth(2)
        painter.setPen(pen)
        draw_fn(painter)
        painter.end()
        return QIcon(pixmap)

    def _create_zoom_in_icon(self) -> QIcon:
        def draw(p):
            p.drawEllipse(4, 4, 12, 12)
            p.drawLine(14, 14, 20, 20)
            p.drawLine(10, 8, 10, 12)
            p.drawLine(8, 10, 12, 10)
        return self._make_icon(draw)

    def _create_zoom_out_icon(self) -> QIcon:
        def draw(p):
            p.drawEllipse(4, 4, 12, 12)
            p.drawLine(14, 14, 20, 20)
            p.drawLine(8, 10, 12, 10)
        return self._make_icon(draw)

    def _create_fullscreen_icon(self) -> QIcon:
        def draw(p):
            p.drawLine(6, 8, 6, 6); p.drawLine(6, 6, 8, 6)
            p.drawLine(18, 8, 18, 6); p.drawLine(18, 6, 16, 6)
            p.drawLine(6, 16, 6, 18); p.drawLine(6, 18, 8, 18)
            p.drawLine(18, 16, 18, 18); p.drawLine(18, 18, 16, 18)
        return self._make_icon(draw)
    
    def _log(self, message: str):
        """记录日志（线程安全：非主线程时通过信号转发到主线程处理）。"""
        if QThread.currentThread() is not self.thread():
            self.log_received.emit(message)
            return
        self._append_log(message)

    def _append_log(self, message: str):
        """在主线程中实际写入日志控件和日志文件（仅主线程调用）。"""
        import sys
        import ctypes
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = f"[{timestamp}] {message}"
        try:
            if sys.platform == 'win32':
                try:
                    kernel32 = ctypes.windll.kernel32
                    kernel32.SetConsoleOutputCP(65001)
                except Exception:
                    pass
            print(log_msg, file=sys.stderr, flush=True)
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                console_enc = getattr(sys.stderr, 'encoding', None) or 'utf-8'
                safe_msg = log_msg.encode(console_enc, errors='replace').decode(console_enc, errors='replace')
                print(safe_msg, file=sys.stderr, flush=True)
            except Exception:
                pass
        if hasattr(self, '_log_text'):
            self._log_text.append(log_msg)
        self._write_log_file(log_msg)
    
    def _write_log_file(self, log_msg: str):
        try:
            from pathlib import Path
            log_dir = Path(Settings.DATA_DIR) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = log_dir / f"app_{today}.log"
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_msg + '\n')
        except Exception as e:
            print(f"写入日志文件失败: {e}", file=sys.stderr)

    def _update_stop_buttons(self, enabled: bool):
        """统一控制各页面停止按钮的启用/禁用"""
        if hasattr(self, "work_stack"):
            for i in range(self.work_stack.count()):
                w = self.work_stack.widget(i)
                if hasattr(w, "stop_task_btn"):
                    w.stop_task_btn.setEnabled(enabled)

    def _on_stop_task(self):
        """停止当前所有正在运行的任务"""
        if not self._live_workers:
            self._log("⚠️ 当前没有正在运行的任务")
            return
        count = len(self._live_workers)
        for w in list(self._live_workers):
            try:
                w.cancel()
            except Exception:
                pass
        self._log(f"⏹ 已发送停止信号，共 {count} 个任务正在停止中...")
        self._update_stop_buttons(False)

    def _start_worker(self, worker, finished_callback=None):
        """启动工作线程并自动管理停止按钮可见性"""
        self._live_workers.add(worker)
        worker.progress.connect(self._on_production_progress)
        worker.error.connect(self._on_production_error)
        _w = worker
        # 1. 先执行自定义回调（如 UI 恢复按钮状态）
        if finished_callback:
            worker.finished.connect(finished_callback)
            worker.error.connect(finished_callback)
        # 2. 再从集合中移除
        worker.finished.connect(lambda *a, w=_w: self._live_workers.discard(w))
        worker.error.connect(lambda *a, w=_w: self._live_workers.discard(w))
        # 3. 最后更新按钮可见性
        worker.finished.connect(lambda: self._update_stop_buttons(bool(self._live_workers)))
        worker.error.connect(lambda: self._update_stop_buttons(bool(self._live_workers)))
        worker.start()
        self._update_stop_buttons(True)
    
    def _on_new_project(self):
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
        if not ok or not name:
            return
        
        # 让用户选择时长档位
        length_dialog = QDialog(self)
        length_dialog.setWindowTitle("选择视频时长")
        length_dialog.setMinimumWidth(400)
        layout = QVBoxLayout(length_dialog)
        
        layout.addWidget(QLabel("请选择目标视频时长："))
        
        length_combo = QComboBox()
        length_combo.addItem("3分钟（短视频）", "very_short")
        length_combo.addItem("5分钟（短故事）", "short")
        length_combo.addItem("10分钟（中篇）", "medium")
        length_combo.addItem("20分钟（长篇）", "long")
        length_combo.setCurrentIndex(0)  # 默认3分钟
        layout.addWidget(length_combo)
        
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(button_box)
        button_box.accepted.connect(length_dialog.accept)
        button_box.rejected.connect(length_dialog.reject)
        
        if length_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        length = length_combo.currentData()
        
        self.project = self.project_manager.create_project(name, length=length)
        self.producer = AnimationProducer(self.client, self.project, self._log, self.project_manager)
        Settings.set_last_project(name)
        self._refresh_ui()
        self._update_wizard_steps()
        self._log(f"创建新项目: {name}，时长档位: {length}")
        
        # 提示用户确认是否自动生成故事
        QTimer.singleShot(300, self._prompt_story_generation)
    
    def _prompt_story_generation(self):
        if not self.project:
            return
        
        msg = QMessageBox(self)
        msg.setWindowTitle("📖 生成故事")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f"将使用项目名称「{self.project.name}」作为主题，自动生成故事内容。")
        msg.setInformativeText("AI 将根据主题生成故事内容，生成后可以编辑修改。\n\n是否开始生成？")
        
        gen_btn = msg.addButton("✨ 生成故事", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("稍后手动生成", QMessageBox.ButtonRole.RejectRole)
        
        msg.exec()
        
        if msg.clickedButton() == gen_btn:
            self.story_topic_input.setText(self.project.name)
            self._switch_to_page(0)
            self._on_generate_story()
    
    def _on_open_project(self):
        projects = self.project_manager.list_projects()
        if not projects:
            QMessageBox.information(self, "提示", "没有可打开的项目")
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle("打开项目")
        dialog.setMinimumWidth(650)
        dialog.setMinimumHeight(400)
        layout = QVBoxLayout(dialog)
        
        # 项目列表表格
        table = QTableWidget(len(projects), 5, dialog)
        table.setHorizontalHeaderLabels(["项目名称", "主题", "进度", "更新时间", "风格"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        
        for i, name in enumerate(projects):
            info = self.project_manager.get_project_info(name)
            if not info:
                continue
            
            table.setItem(i, 0, QTableWidgetItem(info["name"]))
            table.setItem(i, 1, QTableWidgetItem(info.get("topic", "")[:30] or "-"))
            
            # 进度图标
            steps = []
            if info["has_story"]: steps.append("📖")
            if info["has_characters"]: steps.append("👤")
            if info["has_scenes"]: steps.append("🏞️")
            if info["has_storyboard"]: steps.append("🎬")
            if info["has_audio"]: steps.append("🎵")
            progress_text = " ".join(steps) if steps else "未开始"
            table.setItem(i, 2, QTableWidgetItem(progress_text))
            
            # 更新时间
            updated = info.get("updated_at", "")
            if updated:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(updated)
                    updated = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
            table.setItem(i, 3, QTableWidgetItem(updated or "-"))
            table.setItem(i, 4, QTableWidgetItem(info.get("style", "") or "-"))
        
        layout.addWidget(table)
        
        # 按钮
        btn_layout = QHBoxLayout()
        open_btn = QPushButton("打开")
        open_btn.setObjectName("primary")
        open_btn.setEnabled(False)
        cancel_btn = QPushButton("取消")
        btn_layout.addWidget(open_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        # 事件连接
        table.currentCellChanged.connect(lambda: open_btn.setEnabled(True))
        table.doubleClicked.connect(lambda: dialog.accept())
        open_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        
        # 获取选择结果
        selected_name = None
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rows = table.selectedItems()
            if rows:
                selected_name = table.item(rows[0].row(), 0).text()
        
        if selected_name:
            self.project = self.project_manager.load_project(selected_name)
            if self.project:
                # 从资产库加载数据到 global_* 字段（如果它们为空）
                self._sync_global_from_library()
                
                self.producer = AnimationProducer(self.client, self.project, self._log, self.project_manager)
                Settings.set_last_project(selected_name)
                self._refresh_ui()
                self._update_wizard_steps()
                self._log(f"打开项目: {selected_name}")
                # 下次进入项目：检查资产/图片生成标记
                self._check_asset_image_flags()
    
    def _on_save_project(self):
        if not self.project:
            QMessageBox.warning(self, "警告", "没有可保存的项目")
            return
        self._snapshot_current_episode()
        self.project_manager.save_project(self.project)
        self._mark_saved()
        self._log(f"项目已保存: {self.project.name}")
        QMessageBox.information(self, "保存成功", f"项目 {self.project.name} 已保存")
    
    def _on_auto_save(self):
        if self.project and self._is_unsaved:
            self._snapshot_current_episode()
            self.project_manager.save_project(self.project)
            self._mark_saved()
            self._log("自动保存完成")
    
    def _refresh_ui(self):
        if not self.project:
            self.project_name_label.setText("未命名项目")
            self.project_age_combo.blockSignals(True)
            self.project_age_combo.setCurrentIndex(0)
            self.project_age_combo.blockSignals(False)
            self.project_style_combo.blockSignals(True)
            self.project_style_combo.setCurrentIndex(0)
            self.project_style_combo.blockSignals(False)
            self.story_text.clear()
            self.story_topic_input.clear()
            self.character_list.clear()
            if hasattr(self, 'prop_list'):
                self.prop_list.clear()
            self.scene_list.clear()
            self.storyboard_table.setRowCount(0)
            # 清空所有统计标签
            if hasattr(self, 'character_stats_label'):
                self.character_stats_label.setText("角色总数: 0")
            if hasattr(self, 'scene_stats_label'):
                self.scene_stats_label.setText("场景总数: 0")
            if hasattr(self, 'prop_stats_label'):
                self.prop_stats_label.setText("道具总数: 0")
            if hasattr(self, 'storyboard_stats_label'):
                self.storyboard_stats_label.setText("分镜总数: 0 | 总时长: 0秒")
            if hasattr(self, 'video_stats_label'):
                self.video_stats_label.setText("视频总数: 0")
            if hasattr(self, 'story_stats_label'):
                self.story_stats_label.setText("字符数: 0 | 词数: 0")
            # 清空列表标题
            if hasattr(self, 'scene_list_group'):
                self.scene_list_group.setTitle("📋 场景列表（共 0 个）")
            if hasattr(self, 'character_list_group'):
                self.character_list_group.setTitle("👥 角色列表（共 0 个）")
            if hasattr(self, 'prop_list_group'):
                self.prop_list_group.setTitle("📦 道具列表（共 0 个）")
            # 清空仪表盘统计
            if hasattr(self, 'stats_character_count'):
                self.stats_character_count.setText("0")
            if hasattr(self, 'stats_scene_count'):
                self.stats_scene_count.setText("0")
            if hasattr(self, 'stats_prop_count'):
                self.stats_prop_count.setText("0")
            if hasattr(self, 'stats_storyboard_count'):
                self.stats_storyboard_count.setText("0")
            if hasattr(self, 'stats_video_count'):
                self.stats_video_count.setText("0")
            return
        
        # 确保从资产库同步数据到 global_* 字段
        self._sync_global_from_library()
        
        self.project_name_label.setText(self.project.name)
        self.project_age_combo.blockSignals(True)
        idx = self.project_age_combo.findText(self.project.age_group)
        self.project_age_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.project_age_combo.blockSignals(False)
        self.project_style_combo.blockSignals(True)
        idx = self.project_style_combo.findText(self.project.style)
        self.project_style_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.project_style_combo.blockSignals(False)

        # 风格 → 美术风格包联动筛选
        page = getattr(self, "_project_info_page", None)
        if page is not None:
            page.filter_style_packs_by_style(self.project.style)

        # 技能包下拉框同步（加载/新建项目后）
        self._sync_skill_pack_ui()
        if hasattr(self, "custom_style_suffix_edit"):
            self.custom_style_suffix_edit.blockSignals(True)
            self.custom_style_suffix_edit.setText(getattr(self.project, "custom_style_suffix", ""))
            self.custom_style_suffix_edit.blockSignals(False)
        
        self.story_text.setPlainText(self.project.story)
        self.story_topic_input.setText(self.project.topic)
        self._populate_episode_combo()
        self._update_asset_filter_labels()
        
        # 同步故事长度下拉框
        if hasattr(self, 'story_length_combo'):
            length_map = {"very_short": "超短 (3分钟)", "short": "短 (5分钟)", "medium": "中 (10分钟)", "long": "长 (20分钟)"}
            length_text = length_map.get(self.project.length, "长 (20分钟)")
            index = self.story_length_combo.findText(length_text)
            if index >= 0:
                self.story_length_combo.setCurrentIndex(index)
        
        # 角色/道具/场景列表：统一使用带去重的刷新方法（数据源为 project.characters/props/scenes）
        self._refresh_character_list()
        self._refresh_scene_list()
        self._refresh_prop_list()
        
        self._refresh_storyboard_table()
        self._refresh_video_list()
        self._update_flow_hint()
        self._update_wizard_steps()
    
    def _update_wizard_steps(self):
        if not self.step_status_labels:
            return
        
        if not self.project:
            for i in range(len(self.step_status_labels)):
                self.step_status_labels[i].setText("⬜")
                self.step_status_labels[i].setStyleSheet("font-size: 18px; color: #9ca3af;")
            return
        
        if self.project.story:
            self.step_status_labels[0].setText("✅")
            self.step_status_labels[0].setStyleSheet("font-size: 18px; color: #22c55e;")
        else:
            self.step_status_labels[0].setText("⬜")
            self.step_status_labels[0].setStyleSheet("font-size: 18px; color: #9ca3af;")
        
        if self.project.characters:
            self.step_status_labels[1].setText("✅")
            self.step_status_labels[1].setStyleSheet("font-size: 18px; color: #22c55e;")
        else:
            self.step_status_labels[1].setText("⬜")
            self.step_status_labels[1].setStyleSheet("font-size: 18px; color: #9ca3af;")
        
        if self.project.scenes:
            self.step_status_labels[2].setText("✅")
            self.step_status_labels[2].setStyleSheet("font-size: 18px; color: #22c55e;")
        else:
            self.step_status_labels[2].setText("⬜")
            self.step_status_labels[2].setStyleSheet("font-size: 18px; color: #9ca3af;")
        
        if self.project.storyboard:
            self.step_status_labels[3].setText("✅")
            self.step_status_labels[3].setStyleSheet("font-size: 18px; color: #22c55e;")
        else:
            self.step_status_labels[3].setText("⬜")
            self.step_status_labels[3].setStyleSheet("font-size: 18px; color: #9ca3af;")
        
        # 更新统计数字
        self._update_stats()
    
    def _sync_global_from_library(self):
        """确保 global_* 与 characters/props/scenes 同步。

        数据源已统一为项目目录 JSON 文件（load_project 只从文件加载），
        此函数仅保证 global_* 始终是 characters/props/scenes 的副本。
        """
        if not self.project:
            return
        self.project.global_characters = list(self.project.characters) if self.project.characters else []
        self.project.global_props = list(self.project.props) if self.project.props else []
        self.project.global_scenes = list(self.project.scenes) if self.project.scenes else []
    
    def _get_section_display_name(self) -> str:
        """获取当前选定节的显示名称（如 '2.3 xx'），没有选节则返回空字符串"""
        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        if episodes and 0 <= cur_ep < len(episodes):
            title = (episodes[cur_ep].get("title") or "").strip()
            if title:
                return title[:30]  # 截取前30字符以防过长
        return ""
    
    def _update_stats(self):
        """更新项目统计数字"""
        if not self.project:
            return
        
        # 确保从资产库同步数据到 global_* 字段
        self._sync_global_from_library()
        
        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        is_episode_mode = episodes and len(episodes) > 0 and 0 <= cur_ep < len(episodes)
        
        # 统一使用 project.characters/scenes/props 作为当前集的实际数据源
        # （选集时 _on_episode_selected 已经正确加载了本集资产）
        global_chars = getattr(self.project, 'global_characters', [])
        global_scenes = getattr(self.project, 'global_scenes', [])
        global_props = getattr(self.project, 'global_props', [])
        
        # 优先使用 global 数据（characters.json 可能只保存了旧的部分数据）
        char_count = len(global_chars) if global_chars else len(self.project.characters)
        scene_count = len(global_scenes) if global_scenes else len(self.project.scenes)
        prop_count = len(global_props) if global_props else len(self.project.props)
        
        total_chars = char_count
        total_scenes = scene_count
        total_props = prop_count
        
        # 获取当前节显示名称（如 "2.3 xx"），用于代替"本集"
        section_name = self._get_section_display_name()
        
        # 更新角色统计
        if hasattr(self, 'character_stats_label'):
            if is_episode_mode:
                # 剧集模式：显示本集角色数（从 project.characters 获取）
                ep_char_count = len(self.project.characters)
                label = f"角色总数: {total_chars}"
                if section_name:
                    label += f" | {section_name}: {ep_char_count}/{total_chars}"
                else:
                    label += f" | 本集: {ep_char_count}/{total_chars}"
                self.character_stats_label.setText(label)
            else:
                # 未选集：显示全部角色，本集显示 0
                self.character_stats_label.setText(f"角色总数: {total_chars} | 本集: 0")
        
        # 更新场景统计
        if hasattr(self, 'scene_stats_label'):
            scenes = self.project.scenes if self.project.scenes else getattr(self.project, 'global_scenes', [])
            total_scene_count = len(scenes)
            if is_episode_mode:
                ep_scene_count = len(self.project.scenes)
                # 统计优先级分布
                p0_count = sum(1 for s in scenes if getattr(s, 'priority', 'P1').startswith('P0'))
                p1_count = sum(1 for s in scenes if getattr(s, 'priority', 'P1').startswith('P1'))
                p2_count = sum(1 for s in scenes if getattr(s, 'priority', 'P1').startswith('P2'))
                scene_label = f"场景总数: {total_scene_count}"
                if section_name:
                    scene_label += f" | {section_name}: {ep_scene_count}/{total_scene_count}"
                else:
                    scene_label += f" | 本集: {ep_scene_count}/{total_scene_count}"
                scene_label += f" | P0:{p0_count} P1:{p1_count} P2:{p2_count}"
                self.scene_stats_label.setText(scene_label)
            else:
                # 统计优先级分布
                p0_count = sum(1 for s in scenes if getattr(s, 'priority', 'P1').startswith('P0'))
                p1_count = sum(1 for s in scenes if getattr(s, 'priority', 'P1').startswith('P1'))
                p2_count = sum(1 for s in scenes if getattr(s, 'priority', 'P1').startswith('P2'))
                self.scene_stats_label.setText(f"场景总数: {total_scene_count} | P0:{p0_count} P1:{p1_count} P2:{p2_count}")
        
        # 更新道具统计
        if hasattr(self, 'prop_stats_label'):
            if is_episode_mode:
                ep_prop_count = len(self.project.props)
                prop_label = f"道具总数: {total_props}"
                if section_name:
                    prop_label += f" | {section_name}: {ep_prop_count}/{total_props}"
                else:
                    prop_label += f" | 本集: {ep_prop_count}/{total_props}"
                self.prop_stats_label.setText(prop_label)
            else:
                self.prop_stats_label.setText(f"道具总数: {prop_count}")
        
        # 更新场景列表标题（显示本集场景数）
        if hasattr(self, 'scene_list_group'):
            if is_episode_mode:
                ep_title_s = section_name if section_name else "本集"
                self.scene_list_group.setTitle(f"📋 场景列表（{ep_title_s} {scene_count}/{total_scenes} 个）")
            else:
                self.scene_list_group.setTitle(f"📋 场景列表（共 {scene_count} 个）")
        
        # 更新分镜统计
        if hasattr(self, 'storyboard_stats_label'):
            sb_count = len(self.project.storyboard)
            total_duration = sum(sb.duration for sb in self.project.storyboard)
            self.storyboard_stats_label.setText(f"分镜总数: {sb_count} | 总时长: {total_duration:.0f}秒 ({total_duration/60:.1f}分钟)")
        
        # 更新分镜页的当前节标题
        if hasattr(self, 'section_title_label'):
            if 0 <= cur_ep < len(episodes):
                ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}节").strip()
                self.section_title_label.setText(f"当前节: {ep_title}  (索引 {cur_ep})")
            else:
                self.section_title_label.setText("当前节: （未选择）")
        
        # 更新仪表盘统计（如果存在）：优先使用 global_*（资产库加载），其次 project.*
        global_chars = getattr(self.project, 'global_characters', []) or []
        global_props = getattr(self.project, 'global_props', []) or []
        global_scenes = getattr(self.project, 'global_scenes', []) or []
        if hasattr(self, 'stats_character_count'):
            count = len(global_chars) if global_chars else len(self.project.characters)
            self.stats_character_count.setText(str(count))
        
        if hasattr(self, 'stats_scene_count'):
            count = len(global_scenes) if global_scenes else len(self.project.scenes)
            self.stats_scene_count.setText(str(count))
        
        if hasattr(self, 'stats_prop_count'):
            count = len(global_props) if global_props else len(self.project.props)
            self.stats_prop_count.setText(str(count))

        if hasattr(self, 'stats_storyboard_count'):
            self.stats_storyboard_count.setText(str(len(self.project.storyboard)))
        
        if hasattr(self, 'stats_video_count'):
            video_count = sum(1 for sb in self.project.storyboard if sb.video_path)
            self.stats_video_count.setText(str(video_count))
    
    def _on_step_clicked(self, index: int):
        self._switch_to_page(index)
    
    def _on_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec():
            self._init_client()
            self._log("设置已更新")
    
    def _mark_unsaved(self):
        self._is_unsaved = True
        self.unsaved_label.setText("⚠️ 未保存")
        self.unsaved_label.setStyleSheet("color: #f59e0b; font-weight: bold; padding: 4px 12px; background-color: #fffbeb; border-radius: 4px;")
    
    def _mark_saved(self):
        self._is_unsaved = False
        self.unsaved_label.setText("✅ 已保存")
        self.unsaved_label.setStyleSheet("color: #22c55e; font-weight: bold; padding: 4px 12px; background-color: #f0fdf4; border-radius: 4px;")
    
    def _on_undo(self):
        """撤销"""
        pass
    
    def _on_redo(self):
        """重做"""
        pass
    
    def _on_generate_story(self):
        """生成故事"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        
        topic = self.story_topic_input.text().strip()
        if not topic:
            QMessageBox.warning(self, "警告", "请输入故事主题")
            return
        
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        
        length_map = {"超短 (3分钟)": "very_short", "短 (5分钟)": "short", "中 (10分钟)": "medium", "长 (20分钟)": "long"}
        length_text = self.story_length_combo.currentText() if hasattr(self, 'story_length_combo') else "长 (20分钟)"
        length = length_map.get(length_text, "medium")
        
        # 保存故事长度到项目，供后续提取阶段使用
        self.project.length = length
        
        # 禁用按钮，防止重复点击
        if hasattr(self, 'gen_story_btn'):
            self.gen_story_btn.setEnabled(False)
            self.gen_story_btn.setText("⏳ 生成中...")
        
        self._worker = ProductionWorker(
            self.producer, step='story',
            topic=topic, length=length
        )
        self._start_worker(self._worker, self._on_story_generated)
        
        self._log("开始生成故事...")
    
    def _toggle_story_edit(self):
        """切换故事编辑"""
        if self.edit_story_btn.isChecked():
            self.story_text.setReadOnly(False)
            self.edit_story_btn.setText("✏️ 编辑中")
        else:
            self.story_text.setReadOnly(True)
            self.edit_story_btn.setText("✏️ 编辑")
            # 保存编辑后的故事到项目
            if self.project:
                self.project.story = self.story_text.toPlainText()
    
    def _on_import_story_file(self):
        """从文本文件导入故事母本（仅导入文本到文本框，不做其他处理）"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择故事文件",
            "",
            "文本文件 (*.txt *.md *.text);;所有文件 (*)"
        )
        if not file_path:
            return

        # 读取文件内容（兼容 utf-8 / gbk / utf-16 等常见编码）
        content = None
        used_encoding = None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                used_encoding = enc
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception as e:
                QMessageBox.warning(self, "错误", f"读取文件失败:\n{e}")
                return

        if content is None:
            QMessageBox.warning(
                self, "错误", "无法识别文件编码，请将文件另存为 UTF-8 后重试"
            )
            return

        content = content.strip()
        if not content:
            QMessageBox.warning(self, "警告", "文件内容为空")
            return

        # 仅导入为故事母本到文本框显示
        self.story_text.setPlainText(content)
        self.project.story = content
        self._log(f"✅ 故事文件已导入：{Path(file_path).name}")
        self._log(f"文件编码: {used_encoding}")
        self._log(f"故事长度: {len(content)} 字符")
        self._log("💡 请点击「🔧 处理故事」按钮进行后续处理（提取角色/场景/分镜等）")

    def _generate_global_assets(self):
        """生成全局资产：角色、道具、场景

        使用全集剧本文本（script_text）提取全局角色和场景，
        存入 project.global_characters / global_scenes。
        """
        if not self.project:
            return
        # 使用全集剧本文本提取，而非当前集文本
        story_for_extract = self.project.script_text or self.project.story
        if not story_for_extract:
            QMessageBox.warning(self, "警告", "请先完成故事")
            return
        # 临时将 story 设为全集文本以便提取
        original_story = self.project.story
        self.project.story = story_for_extract
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        # 先提取角色和场景
        self._on_extract_from_story()
        # 提取完成后，将结果存入全局角色/场景
        self.project.global_characters = list(self.project.characters)
        self.project.global_props = list(getattr(self.project, 'props', []))
        self.project.global_scenes = list(self.project.scenes)
        # 恢复当前集文本
        self.project.story = original_story
        self.project_manager.save_project(self.project)
        self._log(f"✅ 全局资产已生成：{len(self.project.global_characters)} 个角色，{len(self.project.global_props)} 个道具，{len(self.project.global_scenes)} 个场景")

    def _generate_images(self):
        """生成角色图、场景图（基于全局角色/场景）。

        角色/场景 worker 串行执行：先跑角色，角色线程结束后再启动场景线程。
        两个 QThread 不能同时运行——本方法在启动第二个时立即丢弃第一个的引用，
        第一个线程对象会在 Python GC 时被销毁，触发
        "QThread: Destroyed while thread is still running" 并导致进程崩溃（闪退）。
        场景线程的 finished/error 在 _on_scenes_generated 中自动收尾并引导下一步。
        """
        if not self.project:
            return
        # 优先使用全局角色/场景生成图片
        if self.project.global_characters:
            self.project.characters = list(self.project.global_characters)
            self._log(f"🖼️ 使用全局角色({len(self.project.characters)}个)生成图片...")
        if self.project.global_props:
            self.project.props = list(self.project.global_props)
            self._log(f"🖼️ 使用全局道具({len(self.project.props)}个)生成图片...")
        if self.project.global_scenes:
            self.project.scenes = list(self.project.global_scenes)
            self._log(f"🖼️ 使用全局场景({len(self.project.scenes)}个)生成图片...")
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)

        # 生成角色图（完成后启动场景图，保持两个线程串行）
        if self.project.characters:
            self._log("🖼️ 开始生成角色图片...")
            w = ProductionWorker(self.producer, step='characters')
            w.finished.connect(self._on_characters_generated)
            w.finished.connect(lambda *a: self._start_scene_worker())
            w.error.connect(lambda *a: self._start_scene_worker())
            self._worker = w
            self._start_worker(w)
        else:
            # 无角色：直接启动场景图
            self._start_scene_worker()

    def _start_scene_worker(self):
        """启动场景图生成线程（角色线程结束后调用，保证线程串行）。"""
        if not self.project:
            return
        if not self.project.scenes:
            self._log("🖼️ 没有场景需要生成，跳过场景图片")
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        w = ProductionWorker(self.producer, step='scenes')
        w.finished.connect(self._on_scenes_generated)
        self._worker = w
        self._start_worker(w)
        self._log("🖼️ 开始生成场景图片...")

    def _prompt_episode_selection(self):
        """弹框：选择要制作的节 → 加载故事文本和分镜表"""
        if not self.project:
            return

        episodes = getattr(self.project, 'episodes', [])
        if episodes:
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QComboBox, QDialogButtonBox, QLabel
            dialog = QDialog(self)
            dialog.setWindowTitle("📺 选择节")
            dialog.setMinimumWidth(400)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("请选择要制作的节："))
            combo = QComboBox()
            for i, ep in enumerate(episodes):
                title = (ep.get("title") or f"第{i+1}节").strip()
                combo.addItem(f"{title[:50]}", i)
            layout.addWidget(combo)
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            layout.addWidget(btn_box)
            btn_box.accepted.connect(dialog.accept)
            btn_box.rejected.connect(dialog.reject)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                idx = combo.currentData()
                if idx is not None and 0 <= idx < len(episodes):
                    self._on_episode_selected(idx, is_direct_index=True)
        else:
            self._log("📺 尚无剧集数据，请先解析集数分镜")

    def _prompt_flow_after_characters(self, count: int):
        """角色图生成完成后的流程引导：

        剧集模式（多集）→ 提示用户「去角色页导入造型表」或直接进入选集做分镜；
        非剧集模式 → 提示生成分镜。
        """
        if not self.project:
            return
        episodes = getattr(self.project, 'episodes', [])
        if episodes and len(episodes) > 1:
            # 剧集模式：先引导导入造型表（可选），再选集生成分镜
            msg = QMessageBox(self)
            msg.setWindowTitle("✅ 角色图已生成")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText(f"成功生成 {count} 个角色底模")
            msg.setInformativeText(
                f"共 {len(episodes)} 个节。下一步："
                "① 去角色页「📥 批量导入造型并生成」导入造型表并批量出造型图；"
                "② 然后选择节制作分镜。"
            )
            costume_btn = msg.addButton("👗 去角色页导入造型表", QMessageBox.ButtonRole.AcceptRole)
            ep_btn = msg.addButton("📺 选节做分镜", QMessageBox.ButtonRole.ActionRole)
            skip_btn = msg.addButton("稍后处理", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == costume_btn:
                self._switch_to_page(2)
                QTimer.singleShot(300, self._on_import_costumes)
            elif clicked == ep_btn:
                QTimer.singleShot(300, self._prompt_episode_selection)
            else:
                self._log("👗 提示：造型表可随时在角色页「📥 批量导入造型并生成」导入")
        else:
            self._prompt_non_episode_storyboard(count)

    def _prompt_non_episode_storyboard(self, count: int):
        """非剧集模式：角色图生成完成后提示进入分镜"""
        if not self.project:
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("✅ 角色图已生成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(f"成功生成 {count} 个角色图像")
        msg.setInformativeText("接下来要做什么？")
        gen_sb_btn = msg.addButton("🎬 生成分镜", QMessageBox.ButtonRole.AcceptRole)
        gen_prop_btn = msg.addButton("🎒 生成道具图像", QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == gen_sb_btn:
            self._on_generate_storyboard()
        elif clicked == gen_prop_btn:
            self._on_generate_props()

    def _prompt_storyboard_for_episode(self):
        """选择集数后，提示用户是否为当前集生成分镜"""
        if not self.project:
            return
        cur_ep = getattr(self.project, 'current_episode', -1)
        episodes = getattr(self.project, 'episodes', [])
        if cur_ep < 0 or cur_ep >= len(episodes):
            return
        ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()
        has_sb = bool(self.project.storyboard)
        msg = QMessageBox(self)
        msg.setWindowTitle("🎬 生成分镜")
        msg.setIcon(QMessageBox.Icon.Question)
        if has_sb:
            msg.setText(f"当前集 [{ep_title}] 已有 {len(self.project.storyboard)} 个分镜。")
            msg.setInformativeText("是否重新生成分镜？（将覆盖现有分镜）")
        else:
            msg.setText(f"当前集 [{ep_title}] 尚无分镜。")
            msg.setInformativeText("是否立即为该集生成分镜？")
        gen_btn = msg.addButton("🎬 生成分镜", QMessageBox.ButtonRole.AcceptRole)
        skip_btn = msg.addButton("稍后手动生成", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == gen_btn:
            self._on_generate_storyboard()

    def _apply_story_content(self, content: str, file_path: str):
        """把导入的文本作为故事母本写入项目（单集导入与剧集回退共用）"""
        from pathlib import Path
        self.story_text.setPlainText(content)
        self.project.story = content
        if not (self.project.topic or "").strip() and not self.story_topic_input.text().strip():
            default_topic = Path(file_path).stem
            self.story_topic_input.setText(default_topic)
            self.project.topic = default_topic
        self._mark_unsaved()
        self._log(f"📥 已导入故事母本: {Path(file_path).name} ({len(content)}字符)")

    def _on_parse_full_drama_assets(self):
        """选择剧本文件，提取角色/造型/道具/场景到公共资产库。"""
        # 打开文件选择对话框，支持多选 .md 文件
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择剧本文件（资产总表 + 分集正文）",
            "",
            "Markdown 文件 (*.md);;所有文件 (*.*)",
        )
        if not file_paths:
            return

        from pathlib import Path

        # === 推断库名（用于检查是否已被用户清空） ===
        def _infer_lib_name(paths):
            for fp in paths:
                name = Path(fp).name
                if any(kw in name for kw in ("总结", "汇总", "总表")):
                    return Path(fp).stem
            return Path(paths[0]).stem

        inferred_lib_name = _infer_lib_name(file_paths)

        # === 检查资产库是否为空（用户已清空则允许重新解析同名文件） ===
        from src.services.library_manager import load_library
        existing_lib = load_library(inferred_lib_name)
        lib_is_empty = True
        if existing_lib:
            chars = existing_lib.get("characters", []) or []
            props = existing_lib.get("props", []) or []
            scenes = existing_lib.get("scenes", []) or []
            lib_is_empty = not chars and not props and not scenes

        if lib_is_empty and getattr(self.project, 'parsed_files', []):
            # 库已被清空，清除已解析记录以允许重新解析
            self._log("🗑️ 检测到资产库已被清空，允许重新解析同名文件")
            self.project.parsed_files = []

        # === 检查是否有文件已解析过 ===
        already_parsed = []
        new_files = []
        parsed_set = set(getattr(self.project, 'parsed_files', []) or [])
        for fp in file_paths:
            abs_path = str(Path(fp).resolve())
            if abs_path in parsed_set:
                already_parsed.append(Path(fp).name)
            else:
                new_files.append(fp)
        if not new_files:
            names = "、".join(already_parsed)
            QMessageBox.warning(
                self, "重复解析",
                f"以下文件已经解析过了，不可重复解析：\n{names}\n\n"
                "如需重新解析，请先到 data/asset_library/ 目录删除对应资产库 JSON 文件。"
            )
            return
        if already_parsed:
            names = "、".join(already_parsed)
            reply = QMessageBox.question(
                self, "部分文件已解析",
                f"以下文件已经解析过了，将跳过：\n{names}\n\n是否继续解析未解析的文件？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            file_paths = new_files
        from src.services.library_manager import extract_library_assets, save_library

        all_chars = {}
        all_props = {}
        all_scenes = {}
        errors = []
        file_results = []

        # 按文件名排序：含"总结/汇总/总表"的优先处理
        sorted_paths = sorted(file_paths, key=lambda p: (
            0 if any(kw in Path(p).name for kw in ("总结", "汇总", "总表")) else 1,
            p,
        ))

        self._log("━━━ 📦 开始解析全剧资产 ━━━")
        for fpath_str in sorted_paths:
            fpath = Path(fpath_str)
            try:
                content = fpath.read_text(encoding="utf-8-sig")
            except Exception:
                try:
                    content = fpath.read_text(encoding="gb18030")
                except Exception as e:
                    errors.append(f"{fpath.name}: 编码识别失败 - {e}")
                    continue

            try:
                extracted = extract_library_assets(content)
            except Exception as e:
                errors.append(f"{fpath.name}: 提取失败 - {e}")
                continue

            chars = extracted.get("characters", [])
            props = extracted.get("props", [])
            scenes = extracted.get("scenes", [])

            # 记录本文件新增（去重前）
            new_chars = sum(1 for c in chars if c.name not in all_chars)
            new_props = sum(1 for p in props if p.name not in all_props)
            new_scenes = sum(1 for s in scenes if s.name not in all_scenes)

            for c in chars:
                all_chars[c.name] = c
            for p in props:
                all_props[p.name] = p
            for s in scenes:
                all_scenes[s.name] = s

            file_results.append({
                "name": fpath.name,
                "raw_chars": len(chars),
                "raw_props": len(props),
                "raw_scenes": len(scenes),
                "new_chars": new_chars,
                "new_props": new_props,
                "new_scenes": new_scenes,
                "char_names": [c.name for c in chars],
                "prop_names": [p.name for p in props],
                "scene_names": [s.name for s in scenes],
            })

            # 日志输出本文件详细清单
            self._log(f"  📄 {fpath.name}:")
            if chars:
                self._log(f"     角色({len(chars)}): {'、'.join(c.name for c in chars)}")
            if props:
                self._log(f"     道具({len(props)}): {'、'.join(p.name for p in props)}")
            if scenes:
                self._log(f"     场景({len(scenes)}): {'、'.join(s.name for s in scenes)}")

        if not all_chars and not all_props and not all_scenes:
            QMessageBox.warning(self, "解析结果", "未能从所选文件中提取到任何资产。")
            return

        # 推断库名：优先用总表文件名，否则用第一个文件名
        lib_name = Path(file_paths[0]).stem
        for fp in file_paths:
            name = Path(fp).name
            if any(kw in name for kw in ("总结", "汇总", "总表")):
                lib_name = Path(fp).stem
                break

        try:
            lib_path, duplicates = save_library(
                lib_name,
                list(all_chars.values()),
                list(all_props.values()),
                list(all_scenes.values()),
                merge=True,
            )
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入资产库失败：\n{e}")
            return

        dup_chars = duplicates.get("duplicate_chars", [])
        dup_props = duplicates.get("duplicate_props", [])
        dup_scenes = duplicates.get("duplicate_scenes", [])

        # 检查资产总表是否应该包含道具但没提取到
        summary_files = [r for r in file_results if any(kw in r["name"] for kw in ("总结", "汇总", "总表"))]
        warnings = []
        for sf in summary_files:
            if sf["raw_props"] == 0:
                warnings.append(f"⚠️ {sf['name']}: 未提取到道具（检查是否包含「关键道具表」表格）")
            if sf["raw_chars"] == 0:
                warnings.append(f"⚠️ {sf['name']}: 未提取到角色（检查是否包含角色参考卡或出场总表）")

        # 构建汇总明细
        detail_lines = ["📄 各文件提取明细："]
        for r in file_results:
            parts = []
            if r["raw_chars"] > 0:
                parts.append(f"角色{r['raw_chars']}")
            if r["raw_props"] > 0:
                parts.append(f"道具{r['raw_props']}")
            if r["raw_scenes"] > 0:
                parts.append(f"场景{r['raw_scenes']}")
            detail_lines.append(f"  · {r['name']}: {', '.join(parts) if parts else '（无）'}")

        # 去重后总计
        new_char_count = len(all_chars) - len(dup_chars)
        new_prop_count = len(all_props) - len(dup_props)
        new_scene_count = len(all_scenes) - len(dup_scenes)

        summary = (
            f"✅ 资产解析完成！\n\n"
            f"📦 资产库: {lib_name}\n"
            f"📂 位置: {lib_path}\n\n"
            f"📊 总计（去重后）：\n"
            f"  👤 角色: {len(all_chars)} 个"
            + (f"（新增 {new_char_count}，合并 {len(dup_chars)}）" if dup_chars else "（全部新增）")
            + f"\n  🎒 道具: {len(all_props)} 个"
            + (f"（新增 {new_prop_count}，合并 {len(dup_props)}）" if dup_props else "（全部新增）")
            + f"\n  🏞️ 场景: {len(all_scenes)} 个"
            + (f"（新增 {new_scene_count}，合并 {len(dup_scenes)}）" if dup_scenes else "（全部新增）")
            + "\n\n" + "\n".join(detail_lines)
        )

        if warnings:
            summary += "\n\n⚠️ 异常提示（可能遗漏）：\n" + "\n".join(warnings)

        if errors:
            summary += "\n\n❌ 处理出错：\n" + "\n".join(errors)

        # 末尾列出资产库中全部名称
        summary += f"\n\n📋 资产库全部清单（详见运行日志）："
        summary += f"\n  👤 角色（{len(all_chars)}）：{'、'.join(sorted(all_chars.keys()))}"
        if all_props:
            summary += f"\n  🎒 道具（{len(all_props)}）：{'、'.join(sorted(all_props.keys()))}"
        if all_scenes:
            summary += f"\n  🏞️ 场景（{len(all_scenes)}）：{'、'.join(sorted(all_scenes.keys()))}"

        QMessageBox.information(self, "📦 解析全剧资产", summary)

        # === 绑定资产库到项目 ===
        self.project.asset_library_name = lib_name

        # 将解析出的资产加载到项目的 global_* 字段（供角色/道具/场景页显示和统计）
        self.project.global_characters = list(all_chars.values())
        self.project.global_props = list(all_props.values())
        self.project.global_scenes = list(all_scenes.values())

        # 记录已解析的文件路径（防止重复解析）
        parsed_files = set(getattr(self.project, 'parsed_files', []) or [])
        for fp in file_paths:
            parsed_files.add(str(Path(fp).resolve()))
        self.project.parsed_files = list(parsed_files)

        self._mark_unsaved()

        # 刷新所有页面列表 + 项目页统计
        self._refresh_ui()
        self._refresh_character_list()
        self._refresh_prop_list()
        self._refresh_scene_list()
        self._update_stats()

        self._log(f"📦 全剧资产解析完成: {lib_path}")
        self._log(f"   角色 {len(all_chars)}（新增{new_char_count}，合并{len(dup_chars)}），"
                  f"道具 {len(all_props)}（新增{new_prop_count}，合并{len(dup_props)}），"
                  f"场景 {len(all_scenes)}（新增{new_scene_count}，合并{len(dup_scenes)}）")
        self._log(f"   角色清单: {'、'.join(sorted(all_chars.keys()))}")
        if all_props:
            self._log(f"   道具清单: {'、'.join(sorted(all_props.keys()))}")
        if all_scenes:
            self._log(f"   场景清单: {'、'.join(sorted(all_scenes.keys()))}")

    def _on_import_episode_file(self):
        """解析剧集文件中的分镜表格并保存到分集JSON（剧集模式）"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择剧集文件",
            "",
            "文本文件 (*.txt *.md *.text);;所有文件 (*)"
        )
        if not file_path:
            return

        content = None
        used_encoding = None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                used_encoding = enc
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception as e:
                QMessageBox.warning(self, "错误", f"读取文件失败:\n{e}")
                return

        if content is None:
            QMessageBox.warning(self, "错误", "无法识别文件编码，请将文件另存为 UTF-8 后重试")
            return
        content = content.strip()
        if not content:
            QMessageBox.warning(self, "警告", "文件内容为空")
            return

        from pathlib import Path
        from src.services.episode_splitter import (
            is_storyboard_script,
            is_episode_body,
            is_scene_library,
            split_script_into_episodes,
            split_script_into_episodes_by_section,
            split_script_by_storyboard_code,
        )

        # 场景资产库（主场景整合表 一类）：纯场景文件，无需拆集/故事框，
        # 直接走「导入共享资产库」增量补场景/角色
        if is_scene_library(content):
            ret = QMessageBox.question(
                self, "🏞️ 检测为场景资产库",
                "检测到该文件为「场景资产库」（含 55 个主场景清单 / 场景归属总表）。\n"
                "是否据此建立/更新共享资产库，并合并进当前项目？\n\n"
                "选择「否」则取消导入。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret == QMessageBox.StandardButton.Yes:
                self._sync_scene_library(content, Path(file_path))
            return

        if is_storyboard_script(content):
            # 已含分镜表的整部剧本（逆天布衣_剧本.md）：
            # 节标题兼容「第X节：名称」与「E0X-S0Y 名称」两种格式
            episodes = split_script_by_storyboard_code(content)
            split_mode = "按节拆集（分镜编码版）"
        elif is_episode_body(content):
            # 分集正文（第X集.md）：无附录总表，只有分镜节；
            # 按 Ecode 自报集号分组，增量补场景
            episodes = split_script_by_storyboard_code(content)
            split_mode = "分集正文按节拆集（增量补场景）"
        else:
            episodes = split_script_into_episodes_by_section(content)
            split_mode = "按节拆集"
        if not episodes:
            episodes = split_script_into_episodes(content)
            split_mode = "按集拆分"
        if not episodes:
            QMessageBox.information(
                self,
                "未能分集",
                "未识别到剧集标记（第X集/话/回、EP01、【第X集】等）。\n"
                "已将全文作为单集导入故事框，可直接使用「提取角色/场景/分镜」。"
            )
            self._apply_story_content(content, file_path)
            return

        # === 追加模式：检测冲突，无冲突直接追加，有冲突由用户选择 ===
        existing_episodes = getattr(self.project, 'episodes', None) or []
        conflicts, added = self.project_manager.append_sections(self.project, episodes)
        
        if conflicts:
            conflict_names = "\n".join(f"  · {c['title']}" for c in conflicts)
            reply = QMessageBox.question(
                self, "⚠️ 节键冲突",
                f"新文件包含以下节与已有节键冲突（如 1.1）：\n{conflict_names}\n\n"
                "选择「是」→ 覆盖冲突节（新数据替换旧数据），其他节保留\n"
                "选择「否」→ 跳过冲突节，只追加不冲突的新节",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                # 覆盖冲突节：从已有列表中移除冲突节，再加入新节
                conflict_keys = {c["key"] for c in conflicts}
                keep = [e for e in existing_episodes
                        if self.project_manager._extract_section_key(
                            e.get("title", "")) not in conflict_keys]
                # 清理被覆盖节的 episode_data
                for ep in existing_episodes:
                    key = self.project_manager._extract_section_key(ep.get("title", ""))
                    if key in conflict_keys:
                        old_idx = self.project.episodes.index(ep) if ep in self.project.episodes else -1
                        if old_idx >= 0 and self.project.episode_data:
                            self.project.episode_data.pop(str(old_idx), None)
                self.project.episodes = keep + episodes
                self.project.script_text = (getattr(self.project, "script_text", "") or "") + "\n\n" + content
                self._log(f"🔄 已覆盖 {len(conflicts)} 个冲突节，追加 {len(episodes)} 个节")
            else:
                # 跳过冲突，只追加无冲突的新节
                self.project.episodes = existing_episodes + added
                self.project.script_text = (getattr(self.project, "script_text", "") or "") + "\n\n" + content
                self._log(f"⏭️ 跳过 {len(conflicts)} 个冲突节，追加 {len(added)} 个新节")
        elif existing_episodes:
            # 无冲突，直接追加
            self.project.episodes = existing_episodes + episodes
            self.project.script_text = (getattr(self.project, "script_text", "") or "") + "\n\n" + content
            self._log(f"➕ 无冲突，追加 {len(episodes)} 个新节（已有 {len(existing_episodes)} 个节）")
        else:
            # 首次导入
            self.project.episodes = episodes
            self.project.script_text = content
        
        self.project.current_episode = -1
        if not existing_episodes:
            self.project.episode_data = {}
        self._populate_episode_combo()
        self._update_asset_filter_labels()
        if hasattr(self, 'episode_meta_label'):
            self.episode_meta_label.setText("")
        self._mark_unsaved()
        
        # 根据拆分模式决定显示单位
        is_section_mode = "按节拆" in split_mode
        unit = "节" if is_section_mode else "集"
        
        # 确定实际要解析的节
        if conflicts and reply != QMessageBox.StandardButton.Yes:
            process_episodes = added
        else:
            process_episodes = episodes

        # 统计汇总信息
        total_sections = 0
        section_details = []
        for i, ep in enumerate(process_episodes):
            title = ep.get("title", f"第{i+1}{unit}")
            text = ep.get("text", "")
            from src.services.episode_splitter import parse_section_shot_count
            shots = parse_section_shot_count(text) or 1
            total_sections += shots
            section_details.append(f"  {title}: {shots} 个分镜")
        
        summary_text = (
            f"📚 剧集文件: {Path(file_path).name}\n"
            f"📊 识别到 {len(process_episodes)} {unit}，共 {total_sections} 个分镜"
            + (f"\n⚠️ {len(conflicts)} 个冲突节将被覆盖" if conflicts and reply == QMessageBox.StandardButton.Yes else "")
            + (f"\n⏭️ {len(conflicts)} 个冲突节已跳过" if conflicts and reply != QMessageBox.StandardButton.Yes else "")
            + "\n\n" + "\n".join(section_details[:10])
            + (f"\n  ... 等共 {len(process_episodes)} {unit}" if len(process_episodes) > 10 else "")
            + "\n\n确认导入后，将解析分镜并保存到分集JSON文件，\n"
            f"之后只需选择哪个节即可直接制作视频。"
        )
        
        dialog_title = "📺 确认解析节数分镜"
        reply2 = QMessageBox.question(
            self, dialog_title, summary_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply2 != QMessageBox.StandardButton.Yes:
            self._log(f"❌ 用户取消解析分镜")
            return
        
        self._log(
            f"📚 已解析剧集文件: {Path(file_path).name}，{split_mode}，识别到 {len(episodes)} {unit}，共 {total_sections} 个分镜"
            f"（编码: {used_encoding}）"
        )
        # 同步刷新角色页流程提示条（剧集模式状态已变化）
        self._update_flow_hint()

        # 已含分镜表的剧本：引导先导入共享资产库（角色/造型/道具/场景），
        # 库文件保存在 data/asset_library/<库名>/，其他项目也可调入复用
        if is_storyboard_script(content) and not is_episode_body(content):
            ret = QMessageBox.question(
                self, "📦 导入共享资产库",
                f"检测到该文件已含分镜表（{len(episodes)} 节）。\n"
                f"文件中同时包含角色/造型/道具/场景定义，\n"
                f"是否将这些资产写入共享库「{Path(file_path).stem}」？\n\n"
                f"共享库保存于 data/asset_library/ 目录，"
                f"以后其他剧本也可导入复用。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret == QMessageBox.StandardButton.Yes:
                self._sync_script_assets_to_library(content, Path(file_path).stem, Path(file_path))

        # 分集正文：增量补场景到已绑定的共享库（或新建同名库）
        elif is_episode_body(content):
            self._sync_episode_body_assets(content, Path(file_path), merge=True)

        # 剧集导入后，直接解析剧本中的分镜表格并保存到分集文件
        self._log("📋 开始解析剧本中的分镜表格并保存到分集文件...")
        self._parse_and_save_storyboards_from_script(process_episodes, content)

    def _on_clear_current_section_storyboard(self):
        """清空当前选中节的分镜数据（保留节文本）"""
        if not self.project:
            return
        cur_ep = getattr(self.project, 'current_episode', -1)
        episodes = getattr(self.project, 'episodes', [])
        if cur_ep < 0 or cur_ep >= len(episodes):
            QMessageBox.information(self, "提示", "请先在故事页选择一个节。")
            return
        ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}节").strip()
        reply = QMessageBox.question(
            self, "⚠️ 确认清空",
            f"确定要清空节「{ep_title}」的所有分镜数据吗？\n\n"
            "节文本文字将保留，清空后可点击「从文本重新解析分镜」重新导入。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # 清空当前节的分镜数据
        ep_data = (getattr(self.project, 'episode_data', None) or {}).get(str(cur_ep))
        if ep_data and "storyboard" in ep_data:
            ep_data["storyboard"] = []
        # 同时清空内存中的 storyboard 列表
        self.project.storyboard = []
        # 删除分集文件
        d = self.project_manager._project_dir(self.project.name)
        episodes_dir = d / "episodes"
        if episodes_dir.exists():
            for f in episodes_dir.glob(f"EP{cur_ep + 1:04d}_*.json"):
                try:
                    f.unlink()
                except OSError:
                    pass
        self._refresh_storyboard_table()
        self._update_stats()
        self._mark_unsaved()
        self._log(f"🗑️ 已清空节「{ep_title}」的分镜数据")

    def _parse_and_save_storyboards_from_script(self, episodes: list, content: str):
        """解析剧本中的分镜表格并保存到分集文件。
        
        剧本文件中已经包含完整的分镜表格（Markdown格式），
        直接解析并保存到分集JSON文件，不需要调用AI生成分镜。
        
        同时提取每节中的道具引用并保存到 episode_data["prop_names"]，
        并在解析完成后反向更新每个 Prop 对象的 episodes 字段。
        """
        import re
        from src.models.project import Storyboard
        
        total_storyboards = 0
        # 追加模式下新节的全局起始索引
        start_offset = len(self.project.episodes) - len(episodes)
        if start_offset < 0:
            start_offset = 0
        
        # 收集所有节的道具名，用于反向更新 Prop.episodes
        episode_prop_map = {}  # actual_idx -> set(prop_names)
        
        # 获取全局道具（如果有的话），用于从正文匹配道具名
        global_props = getattr(self.project, 'global_props', []) or []
        if not global_props:
            global_props = getattr(self.project, 'props', [])
        
        for i, ep in enumerate(episodes):
            ep_title = ep.get("title", f"第{i+1}集")
            ep_text = ep.get("text", "")
            actual_idx = start_offset + i
            
            # 解析分镜表格
            storyboards = self._parse_storyboard_table(ep_text)
            
            if storyboards:
                # 设置当前集数据
                self.project.current_episode = actual_idx
                self.project.story = ep_text
                self.project.storyboard = storyboards
                
                # 设置 episode_data
                if not hasattr(self.project, 'episode_data') or self.project.episode_data is None:
                    self.project.episode_data = {}
                
                # 从解析的分镜中提取角色和场景名称
                chars = set()
                scenes = set()
                for sb in storyboards:
                    if sb.dialogue_role:
                        chars.add(sb.dialogue_role)
                    if sb.scene:
                        scenes.add(sb.scene)
                
                # 从正文提取道具名称（如果已有全局道具列表）
                prop_names = []
                if global_props:
                    matched_props = self._match_props_from_story(ep_text, global_props)
                    if matched_props:
                        prop_names = [p.name for p in matched_props]
                
                self.project.episode_data[str(actual_idx)] = {
                    "character_names": list(chars),
                    "scene_names": list(scenes),
                    "prop_names": prop_names,
                    "storyboard": [sb.to_dict() for sb in storyboards],
                }
                
                # 记录本节道具映射
                episode_prop_map[actual_idx] = set(prop_names)
                
                # 保存到分集文件
                if self.project_manager:
                    self.project_manager.save_episode_file(self.project, actual_idx)
                
                total_storyboards += len(storyboards)
                self._log(f"✅ [{ep_title}] 解析到 {len(storyboards)} 个分镜 (全局索引 {actual_idx})")
                if prop_names:
                    self._log(f"  道具引用({len(prop_names)}): {'、'.join(prop_names)}")
            else:
                self._log(f"⚠️ [{ep_title}] 未找到分镜表格")
        
        # === 反向更新每个 Prop 对象的 episodes 字段 ===
        if episode_prop_map and global_props:
            # 构建 prop -> set(ep_titles) 映射
            prop_episodes = {}  # prop_name -> set(ep_titles)
            episodes_list = getattr(self.project, 'episodes', [])
            for ep_idx, prop_set in episode_prop_map.items():
                if 0 <= ep_idx < len(episodes_list):
                    ep_title = episodes_list[ep_idx].get("title", f"第{ep_idx+1}集")
                    # 提取节号（如 "1.1"）
                    ep_section = ep_title.split()[0] if ep_title else f"第{ep_idx+1}集"
                    for pname in prop_set:
                        prop_episodes.setdefault(pname, set()).add(ep_section)
            
            # 更新每个 Prop 的 episodes 字段
            updated_count = 0
            for gp in global_props:
                ep_titles = sorted(prop_episodes.get(gp.name, []))
                if ep_titles and set(ep_titles) != set(gp.episodes or []):
                    gp.episodes = ep_titles
                    updated_count += 1
            
            if updated_count:
                self._log(f"📌 已更新 {updated_count} 个道具的集节标记（episodes 字段）")
                # 同步到 project.props
                if getattr(self.project, 'props', None):
                    prop_map = {p.name: p for p in self.project.props}
                    for gp in global_props:
                        if gp.name in prop_map:
                            prop_map[gp.name].episodes = gp.episodes
                self._mark_unsaved()
        
        self._log(f"✅ 分镜解析完成：共 {total_storyboards} 个分镜，{len(episodes)} 集")
        QMessageBox.information(
            self, "✅ 分镜解析完成",
            f"已成功解析 {total_storyboards} 个分镜，{len(episodes)} 集。\n\n"
            f"分镜已保存到分集JSON文件，之后只需选择哪集哪节即可制作视频。"
        )
    
    def _parse_storyboard_table(self, text: str) -> list:
        """解析Markdown分镜表格，返回Storyboard对象列表。
        
        表格格式：
        | 镜号 | 场景/区域 | 景别 | 运镜 | 画面内容 | 台词/对白 | 说话人 | 特效 | 音效 | 时长 |
        | E01-S01-C01 | A 镇外山脊 | 大远景 | 缓推 | ... | 无 | 无 | ... | ... | 8s |
        """
        import re
        from src.models.project import Storyboard
        
        storyboards = []
        
        # 匹配Markdown表格行
        lines = text.split('\n')
        in_table = False
        header_parsed = False
        
        for line in lines:
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 检测表格开始（以 | 开头的行）
            if line.startswith('|') and not in_table:
                in_table = True
                header_parsed = False
                continue
            
            # 跳过分隔行（|---|---|...）
            if in_table and not header_parsed and re.match(r'^\|[\s\-:|]+\|$', line):
                header_parsed = True
                continue
            
            # 解析表格数据行
            if in_table and header_parsed and line.startswith('|'):
                # 分割单元格
                cells = [cell.strip() for cell in line.split('|')[1:-1]]
                
                if len(cells) >= 10:
                    # 解析镜号（如 E01-S01-C01）
                    mirror_id = cells[0]
                    match = re.search(r'C(\d+)', mirror_id)
                    scene_number = int(match.group(1)) if match else len(storyboards) + 1
                    
                    # 解析场景/区域
                    scene_area = cells[1]
                    
                    # 解析景别
                    camera = cells[2]
                    
                    # 解析运镜
                    camera_move = cells[3]
                    
                    # 解析画面内容
                    description = cells[4]
                    
                    # 解析台词/对白
                    dialogue = cells[5]
                    if dialogue == "无":
                        dialogue = ""
                    
                    # 解析说话人
                    dialogue_role = cells[6]
                    if dialogue_role == "无":
                        dialogue_role = ""
                    
                    # 解析特效
                    effects = cells[7]
                    
                    # 解析音效
                    sound_effects = cells[8]
                    
                    # 解析时长（如 8s）
                    duration_str = cells[9]
                    duration_match = re.search(r'(\d+)s', duration_str)
                    duration = float(duration_match.group(1)) if duration_match else 5.0
                    
                    # 构建完整描述（画面内容 + 运镜 + 特效 + 音效）
                    full_description = description
                    if camera_move and camera_move != "无":
                        full_description += f"\n运镜：{camera_move}"
                    if effects and effects != "无":
                        full_description += f"\n特效：{effects}"
                    if sound_effects and sound_effects != "无":
                        full_description += f"\n音效：{sound_effects}"
                    
                    sb = Storyboard(
                        scene_number=scene_number,
                        scene_name=scene_area,
                        scene=scene_area,
                        description=full_description,
                        dialogue=dialogue,
                        dialogue_role=dialogue_role,
                        camera=f"{camera}，{camera_move}" if camera_move and camera_move != "无" else camera,
                        action=description if description else "",
                        duration=duration,
                        status="pending"
                    )
                    storyboards.append(sb)
            
            # 检测表格结束（不以 | 开头的行）
            elif in_table and not line.startswith('|'):
                in_table = False
                header_parsed = False
        
        return storyboards

    def _generate_all_episodes_storyboards(self):
        """批量生成所有集的分镜并保存到分集文件。
        
        在导入剧集文件后调用，自动为每集生成分镜脚本并保存到分集JSON文件。
        这样之后只需要选择哪集哪节即可，不需要重新生成分镜。
        """
        if not self.project:
            return
        
        episodes = getattr(self.project, 'episodes', [])
        if not episodes:
            return
        
        # 检查资产和图片是否生成完成
        assets_ok = getattr(self.project, 'assets_generated', False)
        images_ok = getattr(self.project, 'images_generated', False)
        
        if not (assets_ok and images_ok):
            missing = []
            if not assets_ok:
                missing.append("全局资产（角色/道具/场景）")
            if not images_ok:
                missing.append("图片（角色图/道具图/场景图）")
            self._log(
                f"⚠️ 以下内容尚未生成：{', '.join(missing)}。\n"
                f"请先生成完后再手动点击分镜页的「从文本重新解析分镜」按钮。"
            )
            QMessageBox.information(
                self, "提示",
                f"以下内容尚未生成：\n{', '.join(missing)}\n\n"
                f"请先生成完全局资产和图片后，再逐集生成分镜。\n"
                f"生成完成后，分镜将自动保存到分集JSON文件，之后只需选择集数即可。"
            )
            return
        
        # 资产和图片已生成完成，开始批量生成分镜
        self._log(f"🎬 开始为 {len(episodes)} 集批量生成分镜...")
        
        # 使用后台线程批量生成
        from PyQt6.QtCore import QThread, pyqtSignal
        from src.services.animation_producer import AnimationProducer
        
        class BatchStoryboardWorker(QThread):
            progress = pyqtSignal(str, int, int)  # message, current, total
            finished = pyqtSignal(int)  # total_generated
            error = pyqtSignal(str)
            
            def __init__(self, producer, episodes, project, log_func):
                super().__init__()
                self.producer = producer
                self.episodes = episodes
                self.project = project
                self.log_func = log_func
                self.total_generated = 0
                self._running = True

            def cancel(self):
                self._running = False
            
            def run(self):
                try:
                    for i, ep in enumerate(self.episodes):
                        if not self._running:
                            self.log_func("⏹ 批量分镜已停止")
                            break
                        ep_title = ep.get("title", f"第{i+1}集")
                        self.progress.emit(f"正在生成 [{ep_title}] 分镜...", i + 1, len(self.episodes))
                        
                        # 设置当前集
                        self.project.current_episode = i
                        self.project.story = ep.get("text", "")
                        
                        # 从全局资产中匹配当前集的角色和场景
                        self._sync_global_from_library()
                        global_chars = getattr(self.project, 'global_characters', [])
                        global_scenes = getattr(self.project, 'global_scenes', [])
                        global_props = getattr(self.project, 'global_props', [])
                        
                        # 从分镜的 dialogue_role 和 scene 字段提取角色/场景名
                        ep_storyboard = getattr(self.project, 'storyboard', [])
                        char_names = set()
                        scene_names = set()
                        for sb in ep_storyboard:
                            if getattr(sb, 'dialogue_role', ''):
                                char_names.add(sb.dialogue_role)
                            if getattr(sb, 'scene', ''):
                                scene_names.add(sb.scene)
                        
                        self.project.characters = [c for c in global_chars if c.name in char_names]
                        self.project.scenes = [s for s in global_scenes if s.name in scene_names]
                        self.project.props = list(global_props)
                        
                        # 生成分镜脚本
                        if not self.project.characters or not self.project.scenes:
                            self.log_func(f"⚠️ [{ep_title}] 缺少角色或场景，跳过分镜生成")
                            continue
                        
                        # 调用AI生成分镜脚本
                        try:
                            storyboard_script = self.producer.generate_storyboard_script(
                                self.project.story,
                                self.project.characters,
                                self.project.scenes,
                                self.project.props
                            )
                            
                            if storyboard_script:
                                self.project.storyboard = storyboard_script
                                self.total_generated += 1
                                
                                # 保存到分集文件
                                if hasattr(self, 'project_manager') and self.project_manager:
                                    self.project_manager.save_episode_file(self.project, i)
                                
                                self.log_func(f"✅ [{ep_title}] 分镜生成完成：{len(storyboard_script)} 个分镜")
                            else:
                                self.log_func(f"⚠️ [{ep_title}] 分镜生成失败")
                        except Exception as e:
                            self.log_func(f"⚠️ [{ep_title}] 分镜生成异常: {e}")
                    
                    self.finished.emit(self.total_generated)
                except Exception as e:
                    self.error.emit(str(e))
        
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        
        self._batch_worker = BatchStoryboardWorker(self.producer, episodes, self.project, self._log)
        self._batch_worker.progress.connect(lambda msg, cur, total: self._log(f"[{cur}/{total}] {msg}"))
        self._batch_worker.finished.connect(self._on_batch_storyboard_finished)
        self._batch_worker.error.connect(self._on_batch_storyboard_error)
        _bw = self._batch_worker
        self._batch_worker.finished.connect(lambda *a, w=_bw: self._live_workers.discard(w))
        self._batch_worker.error.connect(lambda *a, w=_bw: self._live_workers.discard(w))
        self._batch_worker.finished.connect(lambda: self._update_stop_buttons(bool(self._live_workers)))
        self._batch_worker.error.connect(lambda: self._update_stop_buttons(bool(self._live_workers)))
        self._live_workers.add(self._batch_worker)
        self._batch_worker.start()
        self._update_stop_buttons(True)
    
    def _on_batch_storyboard_finished(self, total_generated: int):
        """批量分镜生成完成"""
        episodes = getattr(self.project, 'episodes', [])
        self._log(f"✅ 批量分镜生成完成：共 {total_generated}/{len(episodes)} 集")
        QMessageBox.information(
            self, "✅ 批量分镜完成",
            f"已成功生成 {total_generated}/{len(episodes)} 集的分镜。\n\n"
            f"分镜已保存到分集JSON文件，之后只需选择哪集哪节即可制作视频。"
        )
    
    def _on_batch_storyboard_error(self, error: str):
        """批量分镜生成出错"""
        self._log(f"❌ 批量分镜生成出错: {error}")
        QMessageBox.critical(self, "错误", f"批量分镜生成出错:\n{error}")

    def _update_episode_meta_label(self, ep_idx: int):
        """在剧集下拉框下方显示本节目的目标时长/镜头数（「**本节目标时长**」「**本节镜头数**」）。"""
        label = getattr(self, 'episode_meta_label', None)
        if label is None:
            return
        try:
            ep = getattr(self.project, 'episodes', [])[ep_idx]
            from src.services.episode_splitter import (
                parse_section_target_duration,
                parse_section_shot_count,
            )
            dur = parse_section_target_duration(ep.get('text', ''))
            shots = parse_section_shot_count(ep.get('text', ''))
            parts = []
            if dur:
                parts.append(f"目标 {int(dur)} 秒")
            if shots:
                parts.append(f"{shots} 镜")
            label.setText(f"📏 {' · '.join(parts)}" if parts else "")
        except Exception:
            label.setText("")

    def _update_asset_filter_labels(self):
        """将当前选节名称同步到角色/道具/场景页的筛选下拉框。

        有选节时显示「📌 2.3 月光地图」，无选节时恢复「📌 本集」。
        """
        section_title = ""
        cur_ep = getattr(self.project, 'current_episode', -1)
        episodes = getattr(self.project, 'episodes', [])
        if 0 <= cur_ep < len(episodes):
            section_title = episodes[cur_ep].get("title", "").strip()
        # 同步到三个页面的筛选下拉框
        for page_attr in ('char_filter_combo', 'scene_filter_combo', 'prop_filter_combo'):
            combo = getattr(self, page_attr, None)
            if combo is not None:
                if section_title:
                    combo.setItemText(0, f"📌 {section_title}")
                else:
                    combo.setItemText(0, "📌 本集")

    def _populate_episode_combo(self):
        """根据项目剧集数据填充「选择节」下拉框
        
        每个 episode 条目已经是"集.节"粒度（如 "1.1 铁匠铺的黄昏"），
        直接列出所有条目，选中后加载该节的故事文本和分镜表。
        """
        if not hasattr(self, 'episode_combo'):
            return
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        episodes = getattr(self.project, 'episodes', []) if self.project else []
        if episodes:
            self.episode_combo.addItem("（请选择集.节）", -1)
            for i, ep in enumerate(episodes):
                title = (ep.get("title") or f"第{i+1}节").strip()
                self.episode_combo.addItem(f"{title[:50]}", i)
            cur = getattr(self.project, 'current_episode', -1)
            if 0 <= cur < len(episodes):
                self.episode_combo.setCurrentIndex(cur + 1)
        self.episode_combo.blockSignals(False)

    def _check_assets_complete(self) -> bool:
        """检查资产数据是否已导入完整（角色/道具/场景至少有一项）"""
        if not self.project:
            return False
        
        has_chars = bool(self.project.characters)
        has_props = bool(self.project.props)
        has_scenes = bool(self.project.scenes)
        
        if not (has_chars or has_props or has_scenes):
            self._log("⚠️ 尚未导入资产数据（角色/道具/场景）。可查看分镜，但生成分镜图前需先导入资产。")
        
        return True

    def _on_episode_selected(self, index: int, is_direct_index: bool = False):
        """选择节：直接加载该节的故事文本和分镜表。

        下拉框中每个条目对应一个"集.节"（如 "1.1 铁匠铺的黄昏"），
        选中后加载该节的故事文本到文本框，并显示分镜表。
        """
        if not self.project or not getattr(self.project, 'episodes', []):
            return

        # 判断 index 是 combo box 索引还是剧集索引
        if is_direct_index:
            ep_idx = index
        else:
            ep_idx = None
            if hasattr(self, 'episode_combo') and self.episode_combo.count() > 0:
                if 0 <= index < self.episode_combo.count():
                    ep_idx = self.episode_combo.itemData(index)
            if ep_idx is None or ep_idx < 0:
                ep_idx = index
        if ep_idx is None or ep_idx < 0 or ep_idx >= len(self.project.episodes):
            return

        # 检查资产完整性（仅提示，不阻止选节）
        self._check_assets_complete()

        # 检查资产/图片生成状态（仅提示）
        assets_ok = getattr(self.project, 'assets_generated', False)
        images_ok = getattr(self.project, 'images_generated', False)
        if not (assets_ok and images_ok):
            missing = []
            if not assets_ok:
                missing.append("全局资产（角色/道具/场景）")
            if not images_ok:
                missing.append("图片（角色图/道具图/场景图）")
            self._log(
                f"⚠️ 尚未生成完：{', '.join(missing)}。"
                f"可查看分镜，但生成分镜图前需先生成资产和图片。"
            )

        # 快照当前节数据
        self._snapshot_current_episode()

        ep = self.project.episodes[ep_idx]
        self.project.current_episode = ep_idx
        self.project.story = ep.get("text", "")
        self.story_text.setPlainText(self.project.story)

        # 恢复该节已有数据
        self._load_episode_data(ep_idx)

        # 自愈：按本节正文用增强匹配重算引用并修正
        ep_data = self.project.episode_data.get(str(ep_idx), {})
        self._heal_episode_asset_refs(ep_idx, ep_data)
        ep_data = self.project.episode_data.get(str(ep_idx), ep_data)

        # 如果该节尚无场景引用，且全局场景已生成，自动从故事文本中匹配
        if not ep_data.get("scene_names") and self.project.global_scenes:
            matched_scenes = self._match_scenes_from_story(
                self.project.story, self.project.global_scenes
            )
            if matched_scenes:
                self.project.scenes = matched_scenes
                ep_data["scene_names"] = [s.name for s in matched_scenes]
                self.project.episode_data[str(ep_idx)] = ep_data

        # 如果该节尚无角色引用，且全局角色已生成，自动从故事文本中匹配
        if not ep_data.get("character_names") and self.project.global_characters:
            matched_chars = self._match_characters_from_story(
                self.project.story, self.project.global_characters
            )
            if matched_chars:
                self.project.characters = matched_chars
                ep_data["character_names"] = [c.name for c in matched_chars]
                self.project.episode_data[str(ep_idx)] = ep_data

        # 如果该节尚无道具引用，且全局道具已生成，自动从故事文本中匹配
        if not ep_data.get("prop_names") and self.project.global_props:
            matched_props = self._match_props_from_story(
                self.project.story, self.project.global_props
            )
            if matched_props:
                self.project.props = matched_props
                ep_data["prop_names"] = [p.name for p in matched_props]
                self.project.episode_data[str(ep_idx)] = ep_data

        # 更新资产页筛选下拉框的节名称，并重置为"本集"模式
        self._update_asset_filter_labels()
        # 选节时默认显示本集资产，将下拉框重置到索引0（本集）
        for combo_attr in ('prop_filter_combo', 'scene_filter_combo', 'char_filter_combo'):
            combo = getattr(self, combo_attr, None)
            if combo is not None and combo.count() > 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)

        self._refresh_character_list()
        self._refresh_prop_list()
        self._refresh_scene_list()
        self._refresh_storyboard_table()
        self._update_stats()

        # 同步 combo box 选中项
        if hasattr(self, 'episode_combo') and self.episode_combo.count() > 0:
            self.episode_combo.blockSignals(True)
            self.episode_combo.setCurrentIndex(ep_idx + 1)
            self.episode_combo.blockSignals(False)

        # 显示本节目的目标时长/镜头数
        self._update_episode_meta_label(ep_idx)

        title = (ep.get("title") or f"第{ep_idx + 1}节").strip()
        scene_names = [s.name for s in self.project.scenes]
        char_names = [c.name for c in self.project.characters]
        prop_names = [p.name for p in getattr(self.project, 'props', [])]
        self._log(f"📺 已切换到 {title}")
        if scene_names:
            self._log(f"  场景({len(scene_names)}): {', '.join(scene_names)}")
        if char_names:
            self._log(f"  角色({len(char_names)}): {', '.join(char_names)}")
        if prop_names:
            self._log(f"  道具({len(prop_names)}): {', '.join(prop_names)}")
        sb_count = len(self.project.storyboard) if self.project.storyboard else 0
        self._log(f"  分镜: {sb_count} 个")
        self._mark_unsaved()

    def _match_scenes_from_story(self, story_text: str, global_scenes: list) -> list:
        """从故事文本中匹配全局场景名称，返回匹配到的场景对象列表

        与分镜生成路径使用同一套判定（避免「同一数据两处算不一样」）：
        ① 增强名匹配（去括号别名 + 归一化「·」等分隔符 + 场景名主段匹配）；
        ② 名匹配漏掉时用「场景名片段覆盖率」兜底（≥50% 且至少命中 2 段）：
           剧本用词与场景名不同形时不再整批漏配（1.6「天断山脉·峡谷」→
           场景「天断山脉峡谷狼战」），也就不会退回「全量场景」的污染状态。
        """
        if not story_text or not global_scenes:
            return []
        text = _am.normalize_text(story_text)
        matched = [
            scene for scene in global_scenes
            if scene.name and _am.match_asset_in_text(scene.name, text)
        ]
        seen = {id(s) for s in matched}
        for scene in global_scenes:
            if id(scene) in seen or not getattr(scene, 'name', ''):
                continue
            hits, _total = _am.scene_text_hits(scene.name, text)
            if (hits >= _am.SCENE_COVERAGE_MIN_HITS
                    and _am.scene_text_coverage(scene.name, text)
                    >= _am.SCENE_COVERAGE_MIN_RATIO):
                matched.append(scene)
                seen.add(id(scene))
        return matched

    def _match_characters_from_story(self, story_text: str, global_characters: list) -> list:
        """从故事文本中匹配全局角色名称，返回匹配到的角色对象列表

        使用增强匹配：全局角色名常带括号别名（如「陈老栓（爷爷）」），
        正文用短名「陈老栓」，旧版全名子串匹配会漏配。
        """
        if not story_text or not global_characters:
            return []
        text = _am.normalize_text(story_text)
        return [
            char for char in global_characters
            if char.name and _am.match_asset_in_text(char.name, text)
        ]

    def _match_props_from_story(self, story_text: str, global_props: list) -> list:
        """从故事文本中匹配全局道具名称，返回匹配到的道具对象列表"""
        if not story_text or not global_props:
            return []
        text = _am.normalize_text(story_text)
        return [
            prop for prop in global_props
            if prop.name and _am.match_asset_in_text(prop.name, text)
        ]

    def _filter_assets_by_episodes_field(self, assets: list, ep_title: str) -> list:
        """按资产 episodes 字段筛选当前集子集。

        兼容语义与旧版一致：字段为空（None）视为全剧可用；有标注且命中的保留。
        全部不命中时返回原列表（由调用方决定是否降级）。
        """
        if not assets:
            return []
        if not any(getattr(a, 'episodes', None) for a in assets):
            return list(assets)  # 全部无标注 → 无法区分，原样返回
        hits = [
            a for a in assets
            if _am.episodes_field_hits(getattr(a, 'episodes', None), ep_title) is True
        ]
        return hits if hits else list(assets)

    def _align_storyboard_scene_names(self, ep_idx: int, ep_data: dict):
        """把分镜的「所属场景名」对齐到全局场景列表（修复分镜与场景列表名称不一致）。

        契约（唯一真相）：``asset_matcher.resolve_scene_assignment``
        - 源字段只取 ``sb.scene``（所属场景），**不再取 sb.scene_name**：
          后者是镜头小标题（如「长跪坟前」「赵伯叹息」），旧版把它当场景名匹配，
          匹配成功后还把它写回 scene，等于用脏值覆盖销毁了正确的场景引用；
        - 只写 ``sb.scene``，``sb.scene_name``（镜头标题）保持不动；
        - 匹配不上（场景库里本就没有该场景）时保留原值，**不写脏数据**；
        - 幂等：同一数据再调一次，changed == 0。
        """
        if not self.project or not self.project.storyboard:
            return
        global_scenes = self.project.scenes or []
        if not global_scenes:
            return
        scene_names = [s.name for s in global_scenes if s.name]
        if not scene_names:
            return
        name_set = set(scene_names)
        changed = unmatched = 0
        for sb in self.project.storyboard:
            cur = (getattr(sb, 'scene', '') or '').strip()
            if cur and cur in name_set:
                continue  # 已与场景列表一致（幂等：第二遍调用不会再有改动）
            shot_text = " ".join([
                getattr(sb, 'description', '') or '',
                getattr(sb, 'action', '') or '',
            ])
            best_name, _score, _reason = _am.resolve_scene_assignment(
                cur,
                scene_names,
                shot_text=shot_text,
                raw_title=(getattr(sb, 'scene_name', '') or ''),
            )
            if best_name:
                if sb.scene != best_name:
                    shown = cur or (getattr(sb, 'scene_name', '') or '')
                    self._log(
                        f"  🔧 分镜 {sb.scene_number} 所属场景对齐: "
                        f"'{shown}' -> '{best_name}'"
                    )
                    sb.scene = best_name  # 只写所属场景，不动镜头标题
                    changed += 1
            else:
                unmatched += 1
        if unmatched:
            self._log(
                f"⚠️ 本集 {unmatched} 个分镜的所属场景不在场景列表中（已保留原值）："
                f"分镜图将无法关联场景参考图，可在分镜页第2列改为场景列表中的名称。"
            )
        if changed:
            self._log(f"🔧 本集共对齐 {changed} 个分镜的所属场景到场景列表")
            ep_data["storyboard"] = [sb.to_dict() for sb in self.project.storyboard]
            if not getattr(self.project, 'episode_data', None):
                self.project.episode_data = {}
            self.project.episode_data[str(ep_idx)] = ep_data
            self._mark_unsaved()
            if self.project_manager and hasattr(self.project_manager, "save_episode_file"):
                self.project_manager.save_episode_file(self.project, ep_idx)

    def _heal_episode_asset_refs(self, ep_idx: int, ep_data: dict):
        """自愈被污染的 episode_data 资产引用快照。

        旧版本用「全名子串匹配」自动匹配本集资产：带括号别名的角色
        （如「陈老栓（爷爷）」）永远匹配不上，快照只剩个别角色（如 1.2 只剩
        「陈炎」）；而场景无匹配时又把全部场景兜底进快照（如 1.2 塞入全部
        25 个场景）。这里按本集正文用增强匹配重算并修正引用，
        在载入剧集数据（切集/启动恢复）时自动触发。
        """
        if not self.project or ep_idx is None or ep_idx < 0:
            return
        story = self.project.story or ""
        if not story.strip():
            return
        global_chars = getattr(self.project, 'global_characters', [])
        global_scenes = getattr(self.project, 'global_scenes', [])
        global_props = getattr(self.project, 'global_props', [])
        changed = False

        if global_chars:
            matched_names = {
                c.name for c in self._match_characters_from_story(story, global_chars)
            }
            snap_names = set(ep_data.get("character_names", []))
            if matched_names and snap_names != matched_names:
                # 快照与正文实际出场角色不一致 → 以正文为准修正
                # 旧版本有两种污染：① 快照缺少角色（弱匹配漏配）；② 快照有多余角色（50%启发式误塞入全部）
                ep_data["character_names"] = [
                    c.name for c in global_chars if c.name in matched_names
                ]
                self.project.characters = [
                    c for c in global_chars if c.name in matched_names
                ]
                changed = True
                self._log(
                    f"🛠️ 已按本集正文修正角色引用（{len(snap_names)} → {len(matched_names)}）: "
                    f"{', '.join(sorted(matched_names))}"
                )

        if global_scenes:
            matched_names = {
                s.name for s in self._match_scenes_from_story(story, global_scenes)
            }
            snap_names = set(ep_data.get("scene_names", []))
            if matched_names and snap_names != matched_names:
                # 快照与正文实际出场场景不一致 → 以正文为准修正
                # 旧版本两种污染：① 空快照/漏配；② 快照≈全集（旧版"没匹配到就用全部"兜底）
                ep_data["scene_names"] = [
                    s.name for s in global_scenes if s.name in matched_names
                ]
                self.project.scenes = [
                    s for s in global_scenes if s.name in matched_names
                ]
                changed = True
                self._log(
                    f"🛠️ 已按本集正文修正场景引用（{len(snap_names)} → {len(matched_names)}）: "
                    f"{', '.join(sorted(matched_names))}"
                )

        if global_props:
            matched_names = {
                p.name for p in self._match_props_from_story(story, global_props)
            }
            snap_names = set(ep_data.get("prop_names", []))
            if matched_names and snap_names != matched_names:
                # 快照与正文实际出场道具不一致 → 以正文为准修正
                ep_data["prop_names"] = [
                    p.name for p in global_props if p.name in matched_names
                ]
                self.project.props = [
                    p for p in global_props if p.name in matched_names
                ]
                changed = True
                self._log(
                    f"🛠️ 已按本集正文修正道具引用（{len(snap_names)} → {len(matched_names)}）: "
                    f"{', '.join(sorted(matched_names))}"
                )

        if changed:
            self.project.episode_data[str(ep_idx)] = ep_data
            self._mark_unsaved()


    def _snapshot_current_episode(self):
        """保存当前集数据到 episode_data。

        角色、道具和场景采用引用模式：只存名称列表，指向 global_characters / global_props / global_scenes。
        分镜仍完整快照（因为每集分镜独立）。
        同时把该集快照写入独立分集文件（episodes/EPxxxx_标题.json），
        供切换剧集时直接调用、并作为批量生成的持久化单元。
        """
        if not self.project:
            return
        idx = getattr(self.project, 'current_episode', -1)
        if idx is None or idx < 0 or idx >= len(getattr(self.project, 'episodes', [])):
            return
        self.project.episode_data[str(idx)] = {
            "character_names": [c.name for c in self.project.characters],
            "scene_names": [s.name for s in self.project.scenes],
            "prop_names": [p.name for p in getattr(self.project, 'props', [])],
            "storyboard": [sb.to_dict() for sb in self.project.storyboard],
        }
        if (
            self.project_manager
            and hasattr(self.project_manager, "save_episode_file")
            and self.project.storyboard  # 有分镜才落分集文件，避免覆盖批量生成的文件
        ):
            self.project_manager.save_episode_file(self.project, idx)

    def _load_episode_data(self, ep_idx: int):
        """载入指定集的数据。

        角色和场景从 global_characters / global_scenes 中按名称查找恢复。
        道具从 global_props 中按名称查找恢复。
        分镜从 episode_data 快照恢复。
        """
        # 确保 global_* 数据与资产库文件一致
        self._sync_global_from_library()
        from src.models.project import Prop, Storyboard
        data = (getattr(self.project, 'episode_data', {}) or {}).get(str(ep_idx), {})

        # 兼容旧格式：如果存的是完整对象而非名称引用
        if "characters" in data and "character_names" not in data:
            from src.models.project import Character, Scene
            self.project.characters = [Character.from_dict(c) for c in data.get("characters", [])]
            self.project.scenes = [Scene.from_dict(s) for s in data.get("scenes", [])]
            self.project.props = [Prop.from_dict(p) for p in data.get("props", [])]
        else:
            # 新格式：按名称引用全局角色/道具/场景
            char_names = set(data.get("character_names", []))
            scene_names = set(data.get("scene_names", []))
            prop_names = set(data.get("prop_names", []))
            global_chars = getattr(self.project, 'global_characters', [])
            global_props = getattr(self.project, 'global_props', [])
            global_scenes = getattr(self.project, 'global_scenes', [])
            
            # 获取当前集标题（用于 episodes 字段匹配）
            episodes = getattr(self.project, 'episodes', [])
            ep_title = episodes[ep_idx].get("title", f"第{ep_idx+1}集") if 0 <= ep_idx < len(episodes) else ""
            
            if char_names:
                # 有快照数据：直接按名称恢复（快照是解析剧本时从原文准确提取的）
                matched = [c for c in global_chars if c.name in char_names]
                if matched:
                    self.project.characters = matched
                    self._log(f"🔍 从快照恢复角色: {len(self.project.characters)}个")
                elif global_chars:
                    # 快照名称与全局角色不匹配（如旧版用简称 vs 新版全名），改 episodes 筛选
                    self._log(f"⚠️ 快照名称与全局角色不匹配，改用episodes筛选")
                    self.project.characters = self._filter_assets_by_episodes_field(
                        global_chars, ep_title
                    )
                    if not self.project.characters:
                        self.project.characters = list(global_chars)
                        self._log(f"⚠️ 降级显示全部角色: {len(self.project.characters)}个")
            elif global_chars:
                # 无快照：根据 episodes 字段筛选（兼容旧数据：episodes 为空时显示全部）
                self._log(f"🔍 无快照，根据episodes筛选: global_chars={len(global_chars)}, ep_title='{ep_title}'")
                self.project.characters = self._filter_assets_by_episodes_field(
                    global_chars, ep_title
                )
                self._log(f"🔍 筛选后角色数: {len(self.project.characters)}")
                if not self.project.characters:
                    # 没匹配到：降级为显示全部
                    self.project.characters = list(global_chars)
                    self._log(f"⚠️ 降级显示全部角色: {len(self.project.characters)}个")
            else:
                self.project.characters = []
            
            if prop_names:
                self.project.props = [p for p in global_props if p.name in prop_names]
            elif global_props:
                self.project.props = list(global_props)
            else:
                self.project.props = []
            
            if scene_names:
                # 有快照数据：先尝试按名称精确匹配
                matched_scenes = [s for s in global_scenes if s.name in scene_names]
                
                if matched_scenes:
                    self.project.scenes = matched_scenes
                    self._log(f"🔍 从快照恢复场景: {len(self.project.scenes)}个")
                else:
                    # 快照名称与全局场景完全不匹配（如旧版121个分场景 vs 新版55个主场景）
                    # 降级为按 episodes 字段筛选
                    self._log(
                        f"⚠️ 场景快照名称与全局场景不匹配（快照{len(scene_names)}个，全局{len(global_scenes)}个），"
                        f"改用episodes筛选"
                    )
                    self.project.scenes = self._filter_assets_by_episodes_field(
                        global_scenes, ep_title
                    )
                    if not self.project.scenes:
                        self.project.scenes = list(global_scenes)
                    self._log(f"🔍 episodes筛选后场景数: {len(self.project.scenes)}")
            elif global_scenes:
                # 无快照：根据 episodes 字段筛选（兼容旧数据：episodes 为空时显示全部）
                self.project.scenes = self._filter_assets_by_episodes_field(
                    global_scenes, ep_title
                )
                if not self.project.scenes:
                    # 没匹配到：降级为显示全部
                    self.project.scenes = list(global_scenes)
            else:
                self.project.scenes = []

        self.project.storyboard = [Storyboard.from_dict(sb) for sb in data.get("storyboard", [])]

        # 自愈：磁盘上存在帧图/视频文件但 JSON 中路径为 null 时，
        # 自动回填 frame_path / video_path 并修正 status
        self._heal_storyboard_paths(ep_idx)

        # 场景名纠偏：旧版生成的分镜可能带着 AI 原始场景名（与场景列表不一致），
        # 载入时用最佳匹配把分镜场景名对齐到全局场景名，并持久化到分集文件
        self._align_storyboard_scene_names(ep_idx, data)

        # 自愈：旧版弱匹配可能留下污染的引用快照（角色过少/场景全量兜底），
        # 按本集正文用增强匹配重算并修正（切集与启动恢复两条路径都会经过这里）
        self._heal_episode_asset_refs(ep_idx, data)

    def _heal_storyboard_paths(self, ep_idx: int):
        """自愈：磁盘上存在帧图/视频文件但 JSON 中路径为 null 时自动回填。

        旧版 _auto_save("storyboard") 会从过时的 episode_data 读取分镜写回磁盘，
        导致生成后的 frame_path/video_path 被覆盖为 null。此方法在加载时扫描
        frames/ 和 videos/ 目录，把遗漏的路径补回并修正 status。
        """
        if not self.project or not self.project.storyboard:
            return
        episodes = getattr(self.project, 'episodes', [])
        if not (0 <= ep_idx < len(episodes)):
            return
        ep_title = (episodes[ep_idx].get("title") or f"第{ep_idx+1}集").strip()
        project_dir = Settings.get_project_dir(self.project.name)

        frames_dir = project_dir / "frames" / f"第{ep_idx+1}集_{ep_title}"
        videos_dir = project_dir / "videos" / f"第{ep_idx+1}集_{ep_title}"

        healed = 0
        for sb in self.project.storyboard:
            # 修复 frame_path
            if not sb.frame_path or not Path(sb.frame_path).exists():
                candidate = frames_dir / f"frame_{sb.scene_number:03d}.png"
                if candidate.exists():
                    sb.frame_path = str(candidate)
                    if sb.status in ("pending", "generating", "failed"):
                        sb.status = "done"
                    healed += 1
            # 修复 video_path
            if not sb.video_path or not Path(sb.video_path).exists():
                candidate = videos_dir / f"scene_{sb.scene_number:02d}.mp4"
                if candidate.exists():
                    sb.video_path = str(candidate)
                    if sb.status in ("pending", "generating", "failed"):
                        sb.status = "done"
                    healed += 1

        if healed > 0:
            self._log(f"🔧 自愈：回填了 {healed} 个遗漏的帧图/视频路径")
            # 把修复后的数据回写到 episode_data 和分集文件
            ep_data = (getattr(self.project, 'episode_data', None) or {})
            cur = ep_data.get(str(ep_idx), {})
            cur["storyboard"] = [sb.to_dict() for sb in self.project.storyboard]
            ep_data[str(ep_idx)] = cur
            self.project.episode_data = ep_data
            if self.project_manager and hasattr(self.project_manager, "save_episode_file"):
                self.project_manager.save_episode_file(self.project, ep_idx)

    def _sync_script_assets_to_library(self, content: str, library_name: str, source_path=None):
        """把已含分镜表的剧本资产（角色/造型/道具/场景）写入共享库并合并进当前项目。

        共享库保存于 data/asset_library/<库名>/（与 projects 同级），
        其他剧本/项目可用「📦 导入资产库」调入复用。
        """
        from src.services.library_manager import (
            extract_library_assets,
            import_to_project,
            save_library,
        )
        try:
            extracted = extract_library_assets(content)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"提取资产失败：\n{e}")
            return
        chars = extracted["characters"]
        props = extracted["props"]
        scenes = extracted["scenes"]
        if not chars and not props and not scenes:
            QMessageBox.information(
                self, "提示",
                "未从文件中提取到可识别的角色/造型/道具/场景资产。"
            )
            return
        lib_path, duplicates = save_library(library_name, chars, props, scenes)
        costumes_total = sum(len(c.costumes) for c in chars)
        
        # 构建重复提示
        dup_msg = ""
        dup_chars = duplicates.get("duplicate_chars", [])
        dup_props = duplicates.get("duplicate_props", [])
        dup_scenes = duplicates.get("duplicate_scenes", [])
        if dup_chars or dup_props or dup_scenes:
            dup_parts = []
            if dup_chars:
                dup_parts.append(f"角色 ({len(dup_chars)})")
            if dup_props:
                dup_parts.append(f"道具 ({len(dup_props)})")
            if dup_scenes:
                dup_parts.append(f"场景 ({len(dup_scenes)})")
            dup_msg = "\n\n⚠️ 以下资产已存在（已合并，未覆盖）：" + "、".join(dup_parts)
        
        self._log(
            f"📦 共享资产库「{library_name}」已写入 {lib_path}\n"
            f"   角色 {len(chars)}（含造型 {costumes_total}），道具 {len(props)}，场景 {len(scenes)}{dup_msg}"
        )
        success_msg = (
            f"「{library_name}」共享库已保存至 data/asset_library/：\n"
            f"  👤 角色 {len(chars)}（造型 {costumes_total}）\n"
            f"  🎒 道具 {len(props)}\n"
            f"  🏞️ 场景 {len(scenes)}\n\n"
            f"资产已保存，可在「故事页 → 导入资产库」调入项目使用。"
        )
        if dup_msg:
            success_msg += dup_msg
        QMessageBox.information(self, "✅ 资产库导入完成", success_msg)

    def _sync_episode_body_assets(self, content: str, source_path, merge: bool = True):
        """分集正文增量补场景/角色到共享库（不重抽服装总表）。

        库名取项目已绑定的 asset_library_name；未绑定时用文件 stem。
        角色/道具只补充本集正文里新出现的名字，场景用本集分镜主场景；
        merge=True 时走 upsert，保留已生成图片与已生成造型。
        """
        from src.services.library_manager import (
            extract_library_assets,
            import_to_project,
            save_library,
        )
        bound = getattr(self.project, "asset_library_name", None)
        library_name = bound or Path(source_path).stem
        try:
            extracted = extract_library_assets(content)
        except Exception as e:
            self._log(f"⚠️ 分集正文资产提取失败（跳过库同步）：{e}")
            return
        chars = extracted["characters"]
        props = extracted["props"]
        scenes = extracted["scenes"]
        if not scenes and not chars:
            self._log(f"📚 分集正文「{Path(source_path).name}」无可增量资产（无场景/角色行），已跳过库同步")
            return
        try:
            lib_path, duplicates = save_library(library_name, chars, props, scenes, merge=merge)
            
            # 构建重复提示
            dup_msg = ""
            dup_chars = duplicates.get("duplicate_chars", [])
            dup_props = duplicates.get("duplicate_props", [])
            dup_scenes = duplicates.get("duplicate_scenes", [])
            if dup_chars or dup_props or dup_scenes:
                dup_parts = []
                if dup_chars:
                    dup_parts.append(f"角色 ({len(dup_chars)})")
                if dup_props:
                    dup_parts.append(f"道具 ({len(dup_props)})")
                if dup_scenes:
                    dup_parts.append(f"场景 ({len(dup_scenes)})")
                dup_msg = "（重复：" + "、".join(dup_parts) + "）"
            
            self._log(
                f"📦 分集正文「{Path(source_path).name}」增量补场景 → 共享库「{library_name}」 {lib_path}\n"
                f"   角色 {len(chars)}，道具 {len(props)}，新场景 {len(scenes)}{dup_msg}"
            )
        except Exception as e:
            self._log(f"⚠️ 增量补场景失败：{e}")

    def _sync_scene_library(self, content: str, source_path):
        """场景资产库（主场景整合表）：建立/更新共享库并合并进当前项目。

        库名取项目已绑定的 asset_library_name；未绑定时用文件 stem
        （如「《逆天布衣》场景资产库 - 主场景整合表（55个主场景）」）。
        merge=True 走 upsert，保留已生成图片。
        """
        from src.services.library_manager import (
            extract_library_assets,
            save_library,
        )
        library_name = Path(source_path).stem
        try:
            extracted = extract_library_assets(content)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"提取资产失败：\n{e}")
            return
        chars = extracted["characters"]
        props = extracted["props"]
        scenes = extracted["scenes"]
        if not scenes:
            QMessageBox.information(self, "提示", "未从场景资产库文件中提取到场景。")
            return
        try:
            lib_path, duplicates = save_library(library_name, chars, props, scenes, merge=True)
            
            # 构建重复提示
            dup_msg = ""
            dup_chars = duplicates.get("duplicate_chars", [])
            dup_scenes = duplicates.get("duplicate_scenes", [])
            if dup_chars or dup_scenes:
                dup_parts = []
                if dup_chars:
                    dup_parts.append(f"角色 ({len(dup_chars)})")
                if dup_scenes:
                    dup_parts.append(f"场景 ({len(dup_scenes)})")
                dup_msg = "\n\n⚠️ 以下资产已存在（已合并，未覆盖）：" + "、".join(dup_parts)
            
            self._log(
                f"🏞️ 场景资产库「{Path(source_path).name}」→ 共享库「{library_name}」 {lib_path}\n"
                f"   角色 {len(chars)}，场景 {len(scenes)}{dup_msg}"
            )
            
            success_msg = f"已成功导入资产：\n角色: {len(chars)}个\n场景: {len(scenes)}个"
            if dup_msg:
                success_msg += dup_msg
            QMessageBox.information(self, "导入成功", success_msg)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"场景资产库同步失败：\n{e}")

    def _show_import_preview_dialog(self, chars: list, props: list, scenes: list) -> bool:
        """导入前弹出统计确认框，让用户核对数据无误后再继续。

        Returns:
            True → 确认导入；False → 取消导入
        """
        # 统计各类角色
        total_chars = len(chars)
        total_costumes = sum(len(getattr(c, 'costumes', []) or c.get('costumes', [])) for c in chars)
        
        human_count = 0
        nonhuman_count = 0
        human_with_costume = 0
        nonhuman_with_costume = 0
        
        for c in chars:
            ctype = getattr(c, 'char_type', '') or c.get('char_type', '') or ''
            costume_count = len(getattr(c, 'costumes', []) or c.get('costumes', []))
            if ctype in ('能量体', '动物/妖兽', '群体'):
                nonhuman_count += 1
                if costume_count > 0:
                    nonhuman_with_costume += 1
            else:
                human_count += 1
                if costume_count > 0:
                    human_with_costume += 1

        # 构建详细列表
        detail_lines = []
        for c in chars:
            cname = getattr(c, 'name', '') or c.get('name', '')
            ctype = getattr(c, 'char_type', '') or c.get('char_type', '') or ''
            costumes = getattr(c, 'costumes', []) or c.get('costumes', [])
            costume_names = ", ".join(
                getattr(x, 'name', '') or x.get('name', '') for x in costumes
            )
            if costume_names:
                detail_lines.append(f"  [{ctype}] {cname}: {costume_names}")
            else:
                detail_lines.append(f"  [{ctype}] {cname}: (无造型)")

        msg = (
            f"📊 **导入数据预览**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  角色总计: {total_chars} 个\n"
            f"  造型总计: {total_costumes} 个\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  👤 人物角色: {human_count} 个（含造型 {human_with_costume} 个）\n"
            f"  👻 非人物类型: {nonhuman_count} 个（含造型 {nonhuman_with_costume} 个）\n"
            f"  🛠️ 道具: {len(props)} 个\n"
            f"  🏘️ 场景: {len(scenes)} 个\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**详细角色列表：**\n"
            + "\n".join(detail_lines) +
            "\n\n确认数据无误后点击「✅ 确认导入」"
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("📊 导入数据预览")
        dlg.resize(520, 480)
        layout = QVBoxLayout(dlg)

        text_browser = QTextBrowser()
        text_browser.setMarkdown(msg)
        text_browser.setStyleSheet("font-size: 12px;")
        layout.addWidget(text_browser, 1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("❌ 取消")
        ok_btn = QPushButton("✅ 确认导入")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        return dlg.exec() == QDialog.DialogCode.Accepted

    def _on_import_story_library(self):
        """导入共享资产库到当前项目（角色/造型/道具/场景，按名称合并）"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        from src.services.library_manager import import_to_project, list_libraries
        names = list_libraries()
        if not names:
            QMessageBox.information(
                self, "无可用资产库",
                "data/asset_library/ 下暂无共享资产库。\n"
                "请先导入含分镜表的剧本（如 逆天布衣_剧本.md），"
                "选择「导入共享资产库」生成。"
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("📦 导入共享资产库")
        dialog.setMinimumWidth(380)
        dl = QVBoxLayout(dialog)
        dl.addWidget(QLabel("选择要调入当前项目的资产库："))
        combo = QComboBox()
        for n in names:
            combo.addItem(n, n)
        dl.addWidget(combo)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("✅ 导入")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        dl.addLayout(btn_row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        lib_name = combo.currentData()
        try:
            from src.services.library_manager import load_library
            lib_data = load_library(lib_name)
            if not lib_data:
                QMessageBox.warning(self, "错误", f"资产库「{lib_name}」数据为空")
                return
            
            chars = lib_data.get("characters", [])
            props = lib_data.get("props", [])
            scenes = lib_data.get("scenes", [])
            
            # 导入前弹出统计确认框
            if not self._show_import_preview_dialog(chars, props, scenes):
                self._log("⏹️ 用户取消导入资产库")
                return
            
            # 将资产加载到项目内存中（不写入项目文件）
            self.project.global_characters = chars
            self.project.global_props = props
            self.project.global_scenes = scenes
            
            self._log(
                f"📦 已从资产库「{lib_name}」加载：角色 {len(chars)}，"
                f"道具 {len(props)}，场景 {len(scenes)}"
            )
            self._refresh_character_list()
            self._refresh_prop_list()
            self._refresh_scene_list()
            self._update_stats()
            
            QMessageBox.information(
                self, "导入成功",
                f"已从资产库「{lib_name}」加载：\n"
                f"角色: {len(chars)}个\n"
                f"道具: {len(props)}个\n"
                f"场景: {len(scenes)}个"
            )
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导入资产库失败：{e}")

    def _on_import_assets_from_file(self, asset_type: str = "all"):
        """从文件导入资产（统一入口，各页共用）
        
        Args:
            asset_type: 要导入的资产类型
                - "all": 导入全部资产（角色/道具/场景）
                - "characters": 只导入角色
                - "props": 只导入道具
                - "scenes": 只导入场景
        """
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择剧本文件",
            "",
            "文本文件 (*.txt *.md *.text);;所有文件 (*)"
        )
        if not file_path:
            return

        content = None
        used_encoding = None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                used_encoding = enc
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception as e:
                QMessageBox.warning(self, "错误", f"读取文件失败:\n{e}")
                return

        if content is None:
            QMessageBox.warning(
                self, "错误", "无法识别文件编码，请将文件另存为 UTF-8 后重试"
            )
            return

        content = content.strip()
        if not content:
            QMessageBox.warning(self, "警告", "文件内容为空")
            return

        from src.services.library_manager import (
            extract_library_assets,
            save_library,
        )
        try:
            extracted = extract_library_assets(content)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"提取资产失败：\n{e}")
            return

        chars = extracted.get("characters", [])
        props = extracted.get("props", [])
        scenes = extracted.get("scenes", [])
        
        # 根据asset_type过滤
        if asset_type == "characters":
            props = []
            scenes = []
        elif asset_type == "props":
            chars = []
            scenes = []
        elif asset_type == "scenes":
            chars = []
            props = []
        
        if not chars and not props and not scenes:
            QMessageBox.information(
                self, "提示",
                "未从文件中提取到可识别的资产。"
            )
            return

        # 导入前弹出统计确认框
        if chars and not self._show_import_preview_dialog(chars, props, scenes):
            self._log("⏹️ 用户取消导入资产文件")
            return

        library_name = Path(file_path).stem
        costumes_total = sum(len(c.costumes) for c in chars)
        
        try:
            lib_path, duplicates = save_library(library_name, chars, props, scenes, merge=True)
            
            # 构建重复提示
            dup_msg = ""
            dup_chars = duplicates.get("duplicate_chars", [])
            dup_props = duplicates.get("duplicate_props", [])
            dup_scenes = duplicates.get("duplicate_scenes", [])
            
            if dup_chars or dup_props or dup_scenes:
                dup_parts = []
                if dup_chars:
                    dup_parts.append(f"角色 ({len(dup_chars)}): {', '.join(dup_chars[:5])}{'...' if len(dup_chars) > 5 else ''}")
                if dup_props:
                    dup_parts.append(f"道具 ({len(dup_props)}): {', '.join(dup_props[:5])}{'...' if len(dup_props) > 5 else ''}")
                if dup_scenes:
                    dup_parts.append(f"场景 ({len(dup_scenes)}): {', '.join(dup_scenes[:5])}{'...' if len(dup_scenes) > 5 else ''}")
                dup_msg = "\n\n⚠️ 以下资产已存在（已合并，未覆盖）：\n" + "\n".join(dup_parts)
            
            # 根据导入类型显示不同的日志
            if asset_type == "characters":
                self._log(
                    f"📦 角色资产库「{library_name}」已写入 {lib_path}\n"
                    f"   角色 {len(chars)}（含造型 {costumes_total}）{dup_msg}"
                )
            elif asset_type == "props":
                self._log(
                    f"📦 道具资产库「{library_name}」已写入 {lib_path}\n"
                    f"   道具 {len(props)}{dup_msg}"
                )
            elif asset_type == "scenes":
                self._log(
                    f"📦 场景资产库「{library_name}」已写入 {lib_path}\n"
                    f"   场景 {len(scenes)}{dup_msg}"
                )
            else:
                self._log(
                    f"📦 资产库「{library_name}」已写入 {lib_path}\n"
                    f"   角色 {len(chars)}（含造型 {costumes_total}），道具 {len(props)}，场景 {len(scenes)}{dup_msg}"
                )
            
            # 提示用户（包含重复信息）
            success_msg = f"已成功导入资产：\n角色: {len(chars)}个\n道具: {len(props)}个\n场景: {len(scenes)}个"
            if dup_msg:
                success_msg += dup_msg
            QMessageBox.information(self, "导入成功", success_msg)
            
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"导入资产失败: {e}")
            self._log(f"❌ 导入资产失败: {e}")

    def _on_import_characters_from_file(self):
        """从文件导入角色资产（角色页独立导入按钮）"""
        self._on_import_assets_from_file("characters")

    def _on_import_props_from_file(self):
        """从文件导入道具资产（道具页独立导入按钮）"""
        self._on_import_assets_from_file("props")

    def _on_import_scenes_from_file(self):
        """从文件导入场景资产（场景页独立导入按钮）"""
        self._on_import_assets_from_file("scenes")

    def _on_process_external_story(self):
        """处理外部故事（粘贴的故事）"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        
        story = self.story_text.toPlainText().strip()
        if not story:
            QMessageBox.warning(self, "警告", "请先粘贴或输入故事内容")
            return
        
        # 将外部故事保存到项目
        self.project.story = story
        self._log(f"📋 已粘贴外部故事（{len(story)}字符）")
        
        # 提示用户是否提取
        msg = QMessageBox(self)
        msg.setWindowTitle("🔧 处理故事")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText("是否立即从故事中提取角色、场景和分镜？")
        msg.setInformativeText("也可以稍后点击「提取角色/场景/分镜」按钮手动处理。")
        
        extract_btn = msg.addButton("✅ 立即提取", QMessageBox.ButtonRole.AcceptRole)
        later_btn = msg.addButton("稍后处理", QMessageBox.ButtonRole.RejectRole)
        
        msg.exec()
        
        if msg.clickedButton() == extract_btn:
            self._on_extract_from_story()
    
    def _on_extract_from_story(self):
        """从故事提取角色、场景、分镜"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        story = self.story_text.toPlainText().strip()
        if story and not self.project.story:
            self.project.story = story
            self._log(f"📋 已同步故事内容（{len(story)}字符）")
        story = self.project.story or story
        if not story or not story.strip():
            QMessageBox.warning(self, "警告", "请先完成故事")
            return

        # 检查是否已有全局资产数据，如有则让用户确认是否重新提取
        has_existing = (
            getattr(self.project, 'global_characters', [])
            or getattr(self.project, 'global_props', [])
            or getattr(self.project, 'global_scenes', [])
        )
        if has_existing:
            gc = len(getattr(self.project, 'global_characters', []))
            gp = len(getattr(self.project, 'global_props', []))
            gs = len(getattr(self.project, 'global_scenes', []))
            msg = QMessageBox(self)
            msg.setWindowTitle("⚠️ 已有全局资产数据")
            msg.setIcon(QMessageBox.Icon.Question)
            msg.setText(
                f"当前项目已有全局资产数据：\n"
                f"👤 角色: {gc} 个\n"
                f"🎒 道具: {gp} 个\n"
                f"🏞️ 场景: {gs} 个"
            )
            msg.setInformativeText("是否要重新从故事提取所有角色、道具、场景？\n重新提取将覆盖现有全局资产数据。")
            re_btn = msg.addButton("🔄 重新提取", QMessageBox.ButtonRole.AcceptRole)
            keep_btn = msg.addButton("保留现有，仅生成分镜", QMessageBox.ButtonRole.ActionRole)
            cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == cancel_btn:
                return
            elif clicked == keep_btn:
                self.project._force_reextract_assets = False
            else:
                self.project._force_reextract_assets = True
        else:
            self.project._force_reextract_assets = True

        topic = self.project.topic or self.story_topic_input.text() or ""

        # 确保 producer 存在
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        
        # 同步故事到项目
        self.project.story = story
        if topic:
            self.project.topic = topic
        
        # 读取故事长度设置：以故事页下拉框为准，并更新到项目
        length_map = {"超短 (3分钟)": "very_short", "短 (5分钟)": "short", "中 (10分钟)": "medium", "长 (20分钟)": "long"}
        if hasattr(self, 'story_length_combo'):
            length_text = self.story_length_combo.currentText()
            length = length_map.get(length_text, "medium")
            # 将故事页的选择更新到项目（如果用户修改了下拉框）
            self.project.length = length
        elif hasattr(self.project, 'length') and self.project.length:
            # 如果下拉框不存在，使用项目中保存的长度
            length = self.project.length
        else:
            length = "medium"
        
        self._log(f"使用时长档位: {length} 进行提取")
        
        # 使用 ProductionWorker 异步提取
        self._worker = ProductionWorker(
            self.producer, step='extract',
            story=story, topic=topic, length=length
        )
        self._start_worker(self._worker, self._on_extract_finished)
        
        self._log("开始从故事提取角色/场景/分镜...")
    
    def _on_extract_finished(self, result: dict):
        extract_data = result.get('extract', {})
        self._log(f"✅ 故事提取完成")
        # 剧集模式：提取完成立即快照当前集数据（保存时写入 episodes.json）
        self._snapshot_current_episode()

        # 根据项目模式自动设置视频生成模式
        is_episode_mode = bool(getattr(self.project, 'episodes', None))
        if Settings.AGNES_VIDEO_MODE == "auto":
            effective = "reference" if is_episode_mode else "text"
            self._log(f"auto 模式 → {'剧集模式' if is_episode_mode else '故事模式'}，视频生成使用 {effective} 模式")
            if hasattr(self, 'storyboard_page') and hasattr(self.storyboard_page, 'mode_combo'):
                self.storyboard_page.mode_combo.setCurrentText("auto")

        if self.project and self.project.story:
            self.story_text.setPlainText(self.project.story)

        # 同步长度下拉框（AI 可能已根据故事内容更新了项目长度）
        if hasattr(self, 'story_length_combo') and self.project:
            length_map = {"very_short": "超短 (3分钟)", "short": "短 (5分钟)", "medium": "中 (10分钟)", "long": "长 (20分钟)"}
            length_text = length_map.get(self.project.length, "中 (10分钟)")
            idx = self.story_length_combo.findText(length_text)
            if idx >= 0:
                self.story_length_combo.setCurrentIndex(idx)

        self._refresh_character_list()
        self._refresh_prop_list()
        self._refresh_scene_list()
        self._refresh_storyboard_table()
        self._update_wizard_steps()

        if self.project_manager and self.project:
            self.project_manager.save_project(self.project)

        # 在剧集模式下优先显示全局资产数量
        is_episode_mode = (
            getattr(self.project, 'episodes', [])
            and len(self.project.episodes) > 1
        )
        if is_episode_mode:
            char_count = len(getattr(self.project, 'global_characters', []))
            prop_count = len(getattr(self.project, 'global_props', []))
            scene_count = len(getattr(self.project, 'global_scenes', []))
        else:
            char_count = len(self.project.characters) if self.project else 0
            prop_count = len(getattr(self.project, 'props', [])) if self.project else 0
            scene_count = len(self.project.scenes) if self.project else 0
        sb_count = len(self.project.storyboard) if self.project else 0

        msg = QMessageBox(self)
        msg.setWindowTitle("✅ 提取完成")
        msg.setIcon(QMessageBox.Icon.Information)
        summary = f"👤 角色: {char_count} 个\n🎒 道具: {prop_count} 个\n🏞️ 场景: {scene_count} 个"
        if is_episode_mode:
            ep_count = len(self.project.episodes)
            summary = f"🌐 全局资产（全剧{ep_count}集共享）:\n{summary}"
        summary += f"\n🎬 本集分镜: {sb_count} 个"
        msg.setText(f"从故事中提取完成：\n{summary}")
        msg.setInformativeText("接下来要做什么？")
        
        gen_char_btn = msg.addButton("👤 生成角色图片", QMessageBox.ButtonRole.ActionRole)
        gen_scene_btn = msg.addButton("🏞️ 生成场景图片", QMessageBox.ButtonRole.ActionRole)
        gen_sb_btn = msg.addButton("🎬 生成分镜图片", QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
        
        msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == gen_char_btn:
            self._on_generate_characters()
        elif clicked == gen_scene_btn:
            self._on_generate_scenes()
        elif clicked == gen_sb_btn:
            self._on_generate_storyboard()
    
    def _on_story_generated(self, result: dict):
        # 恢复按钮
        if hasattr(self, 'gen_story_btn'):
            self.gen_story_btn.setEnabled(True)
            self.gen_story_btn.setText("✨ 生成故事")
        
        story = result.get('story', '')
        if story and self.project:
            self.project.story = story
            self.story_text.setPlainText(story)
        self._log("✅ 故事生成完成！")
        self._update_wizard_steps()
        
        msg = QMessageBox(self)
        msg.setWindowTitle("✅ 故事生成完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("故事已生成，接下来要做什么？")
        msg.setInformativeText("您可以提取角色、场景和分镜，也可以手动编辑故事内容。")
        
        extract_btn = msg.addButton("🔍 提取角色/场景/分镜", QMessageBox.ButtonRole.ActionRole)
        edit_btn = msg.addButton("✏️ 编辑故事", QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
        
        msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == extract_btn:
            self._on_extract_from_story()
        elif clicked == edit_btn:
            self._switch_to_page(0)
            if hasattr(self, 'edit_story_btn'):
                self.edit_story_btn.setChecked(True)
                self.story_text.setReadOnly(False)
                self.edit_story_btn.setText("✏️ 编辑中")
    
    def _on_ai_analyze_story(self):
        """AI 分析统计故事内容"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        
        story = self.story_text.toPlainText().strip()
        if not story:
            QMessageBox.warning(self, "警告", "故事内容为空，请先生成或导入故事")
            return
        
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        
        # 获取目标时长（默认3分钟）
        length_map = {"超短 (3分钟)": 3, "短 (5分钟)": 5, "中 (10分钟)": 10, "长 (20分钟)": 20}
        length_text = self.story_length_combo.currentText() if hasattr(self, 'story_length_combo') else "超短 (3分钟)"
        target_minutes = length_map.get(length_text, 3)
        
        # 禁用按钮
        if hasattr(self, 'ai_analyze_btn'):
            self.ai_analyze_btn.setEnabled(False)
            self.ai_analyze_btn.setText("⏳ 分析中...")
        
        self._worker = ProductionWorker(
            self.producer,
            step='analyze_story',
            story=story,
            target_minutes=target_minutes,
        )
        self._start_worker(self._worker, lambda r: self._on_story_analyzed(r, target_minutes))
        self._log(f"开始 AI 分析故事（目标每段约{target_minutes}分钟）...")
    
    def _on_story_analyzed(self, result, target_minutes=3):
        """AI 分析完成回调"""
        if isinstance(result, str):
            self._log(f"❌ 故事分析失败: {result}")
            QMessageBox.critical(self, "错误", f"故事分析失败:\n{result}")
            if hasattr(self, 'ai_analyze_btn'):
                self.ai_analyze_btn.setEnabled(True)
                self.ai_analyze_btn.setText("📊 AI分析统计")
            return
        
        # 恢复按钮
        if hasattr(self, 'ai_analyze_btn'):
            self.ai_analyze_btn.setEnabled(True)
            self.ai_analyze_btn.setText("📊 AI分析统计")
        
        analysis = result.get('analyze_story', {})
        if not analysis:
            self._log("❌ AI 分析返回结果为空")
            QMessageBox.warning(self, "警告", "AI 分析返回结果为空，请稍后重试")
            return
        
        summary = analysis.get('summary', '无摘要')
        total_words = analysis.get('total_word_count', 0)
        estimated_duration = analysis.get('estimated_total_duration_minutes', 0)
        split_count = analysis.get('suggested_split_count', 1)
        reason = analysis.get('reason', '')
        split_plan = analysis.get('split_plan', [])
        
        # 构建分析详情
        detail_text = (
            f"📖 故事汇总\n{summary}\n\n"
            f"📊 数据统计\n"
            f"  总字数: {total_words:,}\n"
            f"  估算总时长: 约 {estimated_duration} 分钟\n\n"
            f"💡 拆分建议\n"
            f"  建议拆分为 {split_count} 个约{target_minutes}分钟的视频文件\n"
            f"  理由: {reason}\n\n"
        )
        
        if split_plan:
            detail_text += "📋 拆分计划\n"
            for part in split_plan:
                detail_text += (
                    f"  第{part.get('part_number', '?')}部分: {part.get('title', '')}\n"
                    f"    {part.get('description', '')}\n"
                )
        
        self._log(f"✅ AI 分析完成: {summary[:100]}...")
        
        # 弹出对话框，询问是否拆分
        msg = QMessageBox(self)
        msg.setWindowTitle("📊 AI 分析统计")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(detail_text)
        
        split_btn = msg.addButton(f"✅ 拆分为 {split_count} 个文件", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        
        msg.exec()
        
        if msg.clickedButton() == split_btn and split_plan:
            # 用户同意拆分，执行拆分
            self._execute_story_split(split_plan)
    
    def _execute_story_split(self, split_plan: list):
        """执行故事拆分"""
        if not self.project:
            return
        
        story = self.story_text.toPlainText().strip()
        if not story:
            QMessageBox.warning(self, "警告", "故事内容为空，无法拆分")
            return
        
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        
        # 获取故事文件路径（如果有）
        story_file_path = getattr(self.project, 'story_file_path', None)
        
        # 禁用按钮
        if hasattr(self, 'ai_analyze_btn'):
            self.ai_analyze_btn.setEnabled(False)
            self.ai_analyze_btn.setText("⏳ 拆分中...")
        
        self._worker = ProductionWorker(
            self.producer,
            step='split_story',
            story=story,
            split_plan=split_plan,
            story_file_path=story_file_path,
        )
        self._start_worker(self._worker, self._on_story_split_finished)
        self._log(f"开始拆分故事为 {len(split_plan)} 个文件...")
    
    def _on_story_split_finished(self, result):
        """故事拆分完成回调"""
        if isinstance(result, str):
            self._log(f"❌ 故事拆分失败: {result}")
            QMessageBox.critical(self, "错误", f"故事拆分失败:\n{result}")
            if hasattr(self, 'ai_analyze_btn'):
                self.ai_analyze_btn.setEnabled(True)
                self.ai_analyze_btn.setText("📊 AI分析统计")
            return
        
        # 恢复按钮
        if hasattr(self, 'ai_analyze_btn'):
            self.ai_analyze_btn.setEnabled(True)
            self.ai_analyze_btn.setText("📊 AI分析统计")
        
        split_result = result.get('split_story', [])
        if not split_result:
            self._log("❌ 故事拆分返回结果为空")
            QMessageBox.warning(self, "警告", "故事拆分返回结果为空，请稍后重试")
            return
        
        # 构建结果信息
        save_dir = Path(split_result[0]['path']).parent if split_result else Path()
        info_text = (
            f"✅ 故事拆分完成！\n\n"
            f"共拆分为 {len(split_result)} 个文件：\n"
        )
        for part in split_result:
            part_num = part.get('part_number', '?')
            part_title = part.get('title', f'第{part_num}部分')
            info_text += f"  📄 {part_title}\n"
        
        info_text += f"\n📁 保存目录: {save_dir}\n\n"
        info_text += "💡 您可以导入这些拆分后的故事分文件，分别进行分镜制作和视频生成。"
        
        self._log(f"✅ 故事拆分完成: {len(split_result)} 个文件，保存在 {save_dir}")
        
        msg = QMessageBox(self)
        msg.setWindowTitle("🎉 拆分完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(info_text)
        
        open_folder_btn = msg.addButton("📂 打开文件夹", QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton("完成", QMessageBox.ButtonRole.AcceptRole)
        
        msg.exec()
        
        if msg.clickedButton() == open_folder_btn:
            try:
                import subprocess
                subprocess.Popen(['explorer', str(save_dir)])
            except Exception as e:
                self._log(f"打开文件夹失败: {e}")
                QMessageBox.warning(self, "警告", f"无法打开文件夹: {e}")
    
    def _on_characters_generated(self, result: dict):
        # 兼容旧工作线程结果，同时确保成功生成后不会显示为 0
        names = result.get('characters', [])
        count = result.get('character_count', len(names))
        if not count and self.project:
            count = len(self.project.characters)
        self._log(f"✅ 角色生成完成，共 {count} 个角色")
        # 显式同步 image_path 到 global_characters（虽然实例共享机制已保底，但统一处理更安全）
        if self.project and getattr(self.project, 'characters', None):
            cmap = {c.name: c for c in self.project.characters}
            for gc in getattr(self.project, "global_characters", []) or []:
                src = cmap.get(gc.name)
                if src:
                    if src.image_path and not gc.image_path:
                        gc.image_path = src.image_path
                    if src.seed is not None and gc.seed is None:
                        gc.seed = src.seed
                    # 同步造型图路径
                    if src.costumes and gc.costumes:
                        src_costs = {k.name: k for k in src.costumes}
                        for cost in gc.costumes:
                            sc = src_costs.get(cost.name)
                            if sc:
                                if sc.image_path and not cost.image_path:
                                    cost.image_path = sc.image_path
                                if sc.seed is not None and cost.seed is None:
                                    cost.seed = sc.seed
        if self.project and not getattr(self.project, 'global_characters', []):
            self.project.global_characters = list(self.project.characters)
        self._refresh_character_list()
        # 角色图/造型图的 image_path 必须同时落盘（project.json 的 global_characters
        # + characters.json），否则重启后丢失，造型图在分镜/视频生成时会被引用
        if self.project_manager:
            self.project_manager.save_meta(self.project)
            self.project_manager.save_characters(self.project)
        self._sync_asset_images_to_library()
        self._update_wizard_steps()

        # 统一流程引导：剧集模式提示导入造型表/选集做分镜；非剧集模式提示做分镜
        self._prompt_flow_after_characters(count)
    
    def _on_generate_props(self):
        """生成道具图像（独立道具产品摄影）"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        if not getattr(self.project, 'props', []):
            QMessageBox.warning(
                self, "警告",
                "当前没有道具列表。\n请先「提取角色/场景/分镜」从故事中提取道具。"
            )
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        self._worker = ProductionWorker(self.producer, step='props')
        self._start_worker(self._worker, self._on_props_generated)
        self._log("开始生成道具图像...")

    def _on_import_costumes(self):
        """批量导入造型：粘贴/导入服装表 → 解析 → 写入角色 → 直接生成造型图"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        # 确保从资产库拿到最新角色数据（含所有造型）
        self._sync_global_from_library()

        dialog = QDialog(self)
        dialog.setWindowTitle("📥 批量导入造型并生成")
        dialog.resize(560, 460)
        dlg_layout = QVBoxLayout(dialog)

        hint = QLabel(
            "支持两种格式：\n"
            "① 粘贴/导入「服装变化表」Markdown 表格（| 集数 | 角色 | 造型 | 颜色 | 关键特征 | 状态标记 |）\n"
            "② 粘贴自由文本：每行「角色名：造型名，特征描述」\n\n"
            "自动按角色名匹配已有角色；找不到的角色名会自动新建（描述留空）。\n"
            "解析完成后直接开始出图（风格包启用时以角色底模 img2img 锚定面容）。"
        )
        hint.setStyleSheet("color: #94a3b8; font-size: 11px;")
        dlg_layout.addWidget(hint)

        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("粘贴表格或自由文本到这里…")
        dlg_layout.addWidget(text_edit, 1)

        btn_row = QHBoxLayout()
        file_btn = QPushButton("📂 导入文件 (.md/.txt)")

        def _import_file():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "选择造型表文件", "", "文本文件 (*.md *.txt *.csv)"
            )
            if not path:
                return
            try:
                content = Path(path).read_text(encoding="utf-8")
                text_edit.setPlainText(text_edit.toPlainText() + "\n" + content)
            except Exception as e:
                QMessageBox.warning(dialog, "提示", f"读取文件失败：{e}")

        file_btn.clicked.connect(_import_file)

        def _accept_and_generate():
            """「解析并生成」：内容为空时留在对话框内提示，避免点了没反应"""
            if not text_edit.toPlainText().strip():
                QMessageBox.warning(
                    dialog, "提示", "请先粘贴或导入造型表内容，再点「解析并生成」"
                )
                return
            dialog.accept()

        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("✅ 解析并生成")
        ok_btn.setDefault(True)
        # 必须显式连接 accept/reject：否则按钮点击后对话框不关闭，
        # dialog.exec() 永不返回 Accepted，表现为「点解析并生成没有任何反映」
        ok_btn.clicked.connect(_accept_and_generate)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(file_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        dlg_layout.addLayout(btn_row)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        text = text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先粘贴或导入造型表内容")
            return

        from src.core.producer import parse_costume_table
        entries = parse_costume_table(text)
        if not entries:
            QMessageBox.warning(self, "提示", "未能解析出任何造型条目，请检查格式后重试")
            return

        from src.models.project import Costume
        # 剧集模式下全剧角色统一存放在 global_characters，直接在该列表上查找/新建
        # （该列表中的 Character 对象与 project.characters 共用同一实例，写回后随项目保存）
        global_chars = list(getattr(self.project, 'global_characters', []) or [])
        if global_chars:
            all_chars = global_chars
        else:
            all_chars = list(self.project.characters)

        char_map = {}
        for c in all_chars:
            char_map.setdefault(c.name, c)

        # 角色名模糊匹配：md 服装表常带括号注释（如「老叫花子（九指神丐）」），
        # 项目角色名是无括号的标准名（「老叫花子」）。按「原文精确 > 去括号归一 > 括号内别名」三级回退，
        # 命中的直接挂到已有角色，避免为同一角色重复建号。
        from src.core.producer import _norm_character_name

        def _find_char(cname):
            hit = char_map.get(cname)
            if hit is not None:
                return hit
            norm = _norm_character_name(cname)
            if norm:
                for known in char_map:
                    if _norm_character_name(known) == norm:
                        return char_map[known]
            m = re.search(r"[（(]([^（）()]+)[）)]", cname)
            if m:
                inner = m.group(1).strip()
                hit = char_map.get(inner)
                if hit is not None:
                    return hit
            return None

        # 先统计预览，让用户确认后再实际写入
        preview_data = {"entries": [], "new_chars": set(), "matched_chars": {}}
        for e in entries:
            cname = e["character"].strip()
            cost_name = e["name"].strip()
            if not cname or not cost_name:
                continue
            char = _find_char(cname)
            if char is None:
                preview_data["new_chars"].add(cname)
                preview_data["entries"].append((cname, cost_name, "🆕 新建角色"))
            else:
                matched = next((c for c in getattr(char, 'costumes', []) if c.name == cost_name), None)
                if matched:
                    preview_data["entries"].append((cname, cost_name, "🔄 更新描述"))
                else:
                    preview_data["entries"].append((cname, cost_name, "➕ 新增造型"))
                preview_data["matched_chars"].setdefault(cname, set()).add(cost_name)

        # 统计预览
        new_char_count_preview = len(preview_data["new_chars"])
        added_count_preview = sum(1 for _, _, t in preview_data["entries"] if t in ("➕ 新增造型", "🆕 新建角色"))
        updated_count_preview = sum(1 for _, _, t in preview_data["entries"] if t == "🔄 更新描述")

        preview_msg = (
            f"📊 **造型表解析预览**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  解析条目: {len(entries)} 条\n"
            f"  新增造型: {added_count_preview} 个\n"
            f"  更新描述: {updated_count_preview} 个\n"
            f"  新建角色: {new_char_count_preview} 个\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**详细列表：**\n"
            + "\n".join(
                f"  [{tag}] {cname} → {cost_name}"
                for cname, cost_name, tag in preview_data["entries"]
            ) +
            "\n\n确认数据无误后点击「✅ 确认导入」"
        )

        preview_dlg = QDialog(self)
        preview_dlg.setWindowTitle("📊 造型表解析预览")
        preview_dlg.resize(520, 480)
        preview_layout = QVBoxLayout(preview_dlg)
        try:
            preview_text = QTextBrowser()
        except Exception as e:
            self._log(f"❌ QTextBrowser 创建失败: {e}")
            QMessageBox.critical(self, "错误", f"预览对话框创建失败：{e}\n\n请重启程序后重试。")
            return
        preview_text.setMarkdown(preview_msg)
        preview_text.setStyleSheet("font-size: 12px;")
        preview_layout.addWidget(preview_text, 1)
        preview_btn_row = QHBoxLayout()
        preview_cancel_btn = QPushButton("❌ 取消")
        preview_ok_btn = QPushButton("✅ 确认导入")
        preview_ok_btn.setDefault(True)
        preview_ok_btn.clicked.connect(preview_dlg.accept)
        preview_cancel_btn.clicked.connect(preview_dlg.reject)
        preview_btn_row.addStretch()
        preview_btn_row.addWidget(preview_cancel_btn)
        preview_btn_row.addWidget(preview_ok_btn)
        preview_layout.addLayout(preview_btn_row)

        if preview_dlg.exec() != QDialog.DialogCode.Accepted:
            self._log("⏹️ 用户取消导入造型表")
            return

        # 用户确认后，正式写入
        new_char_count = 0
        added_count = 0
        updated_count = 0
        for e in entries:
            cname = e["character"].strip()
            cost_name = e["name"].strip()
            if not cname or not cost_name:
                continue
            char = _find_char(cname)
            if char is None:
                char = Character(name=cname, style="person")
                char_map[cname] = char
                if global_chars:
                    # 新建角色写入全局列表，剧集模式下才能被后续集引用。
                    # 注意：global_chars 是副本，append 后必须重新赋值给
                    # self.project.global_characters，否则新建角色对后续
                    # save/生成流程不可见（造型会静默丢失）。
                    global_chars.append(char)
                    self.project.global_characters = global_chars
                else:
                    self.project.characters.append(char)
                new_char_count += 1
            if not getattr(char, 'costumes', None):
                char.costumes = []
            matched = next((c for c in char.costumes if c.name == cost_name), None)
            if matched:
                if e["description"]:
                    matched.description = e["description"]
                updated_count += 1
            else:
                char.costumes.append(Costume(name=cost_name, description=e["description"]))
                added_count += 1

        self._log(
            f"📥 造型表导入：解析 {len(entries)} 条 | 新增 {added_count}，更新 {updated_count}，"
            f"自动新建角色 {new_char_count} 个"
        )
        if self.project_manager:
            self.project_manager.save_meta(self.project)
            self.project_manager.save_characters(self.project)
        self._refresh_character_list()
        # 「批量导入造型并生成」语义是"把刚导入的造型出图"，直接全量补缺图
        # （已有图自动跳过），不依赖列表勾选状态；后续可用「🎨 生成造型图像」按勾选精修
        self._generate_costumes()

    def _on_generate_costumes(self):
        """批量生成造型图：只处理角色列表中勾选（✔）的角色的造型，不重新出角色底模"""
        if not self.project:
            return
        selected = self._get_checked_character_names()
        if not selected:
            QMessageBox.information(
                self, "提示",
                "请先在角色列表里勾选要出造型图的角色（每行前面的勾选框 ✔），"
                "再点「🎨 生成造型图像」。\n"
                "可用「◻ 只选缺造型图」快速勾选还没出图的角色。"
            )
            return
        self._generate_costumes(selected_characters=selected)

    def _generate_costumes(self, selected_characters=None, selected_costumes=None, force=False):
        """启动造型生成（支持选择性范围）

        selected_characters: 只处理这些角色的造型；selected_costumes: {角色名: [造型名]} 更细的范围
        （优先级更高，供「👗 管理造型」弹窗内对单个造型出图）；force=True 强制重出（忽略已有图）。
        """
        if not self.project:
            return
        has_costumes = any(
            getattr(c, 'costumes', None)
            for c in (list(getattr(self.project, 'global_characters', []) or [])
                     + list(self.project.characters))
        )
        if not has_costumes:
            QMessageBox.warning(
                self, "提示",
                "当前没有任何造型。\n请先在角色页「管理造型」添加，或使用「批量导入造型」从服装表导入。"
            )
            return
        # 确保 producer 存在（带 project_manager，造型 image_path 才能随生成落盘）
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log, self.project_manager)
        self._worker = ProductionWorker(
            self.producer, step='costumes',
            selected_characters=selected_characters,
            selected_costumes=selected_costumes,
            force=force,
        )
        self._start_worker(self._worker, self._on_costumes_generated)

        if selected_costumes:
            scope = "：" + "、".join(
                f"{char}/{name}" for char, names in selected_costumes.items() for name in names
            )
        elif selected_characters:
            scope = f"（勾选 {len(selected_characters)} 个角色）"
        else:
            scope = ""
        self._log(f"👗 开始生成造型图像{scope}...")

    def _on_costumes_generated(self, result: dict):
        results = result.get('costume_results', [])
        generated = sum(1 for r in results if r.get('status') == 'generated')
        failed = sum(1 for r in results if r.get('status') == 'failed')
        skipped = sum(1 for r in results if r.get('status') == 'skipped')
        self._log(f"✅ 造型生成完成：成功 {generated}，失败 {failed}，跳过 {skipped}")
        self._refresh_character_list()
        if failed:
            failed_items = "\n".join(
                f"  ✖ {r['character']}/{r['costume']}: {r.get('error', '')[:60]}"
                for r in results if r.get('status') == 'failed'
            )
            QMessageBox.information(
                self, "造型生成完成（部分失败）",
                f"成功 {generated} 个，失败 {failed} 个，跳过 {skipped} 个\n\n{failed_items}\n\n"
                "失败的造型可重新点击「批量导入造型」重试（已有图会自动跳过）。"
            )
            return

        # 全部成功：引导进入分镜制作
        episodes = getattr(self.project, 'episodes', []) if self.project else []
        if episodes and len(episodes) > 1:
            msg = QMessageBox(self)
            msg.setWindowTitle("✅ 造型生成完成")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText(f"成功生成 {generated} 个造型（跳过 {skipped} 个已有图）")
            msg.setInformativeText(
                "造型已保存。下一步：选择集数制作分镜，分镜角色列可写「角色名/造型名」引用造型。"
            )
            ep_btn = msg.addButton("📺 选集做分镜", QMessageBox.ButtonRole.AcceptRole)
            ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == ep_btn:
                QTimer.singleShot(300, self._prompt_episode_selection)
        else:
            msg = QMessageBox(self)
            msg.setWindowTitle("✅ 造型生成完成")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText(f"成功生成 {generated} 个造型（跳过 {skipped} 个已有图）")
            msg.setInformativeText("造型已保存，下一步生成对应集的分镜图片。")
            sb_btn = msg.addButton("🎬 生成分镜图片", QMessageBox.ButtonRole.AcceptRole)
            ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == sb_btn:
                self._on_generate_storyboard()

    def _on_props_generated(self, result: dict):
        count = result.get('prop_count', 0)
        # 同步 image_path 到 global_props（生成时写到了 project.props 实例）
        if self.project and getattr(self.project, 'props', None):
            pmap = {p.name: p for p in self.project.props}
            for gp in getattr(self.project, "global_props", []) or []:
                src = pmap.get(gp.name)
                if src and src.image_path and not gp.image_path:
                    gp.image_path = src.image_path
        if self.project and not getattr(self.project, 'global_props', []):
            self.project.global_props = list(self.project.props)
        self._refresh_prop_list()
        self._update_stats()
        self._log(f"✅ 道具图像生成完成，共 {count} 个")
        self._sync_asset_images_to_library()
        self.project_manager.save_meta(self.project)
        self.project_manager.save_props(self.project)
        
        msg = QMessageBox(self)
        msg.setWindowTitle("✅ 道具生成完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(f"成功生成 {count} 个道具图像")
        gen_scene_btn = msg.addButton("🏞️ 生成场景图片", QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
        msg.exec()
        if msg.clickedButton() == gen_scene_btn:
            self._on_generate_scenes()

    def _sync_asset_images_to_library(self):
        """项目绑定资产库后，把项目内资产图同步进共享库（新目录 + 旧目录迁移）。

        在任何资产图生成/迁移完成后调用，保证库的 library.json
        和 image_path 始终指向 data/asset_library/<库名>/。
        """
        if not self.project or not getattr(self.project, "asset_library_name", ""):
            return
        try:
            from src.services.library_manager import sync_library_images
            stats = sync_library_images(self.project)
            total = sum(stats.values())
            if total:
                self.project_manager.save_meta(self.project)
                self.project_manager.save_characters(self.project)
                detail = "、".join(f"{k} {v}" for k, v in stats.items() if v)
                self._log(f"📚 已同步资产图到共享库「{self.project.asset_library_name}」：{detail}")
        except Exception as e:
            self._log(f"⚠️ 同步共享库资产图失败: {e}")

    def _load_library_assets(self):
        """从绑定的资产库加载资产数据，返回 (characters, props, scenes)"""
        if not self.project or not getattr(self.project, "asset_library_name", ""):
            return [], [], []
        try:
            from src.services.library_manager import load_library
            lib = load_library(self.project.asset_library_name)
            if lib:
                return lib.get("characters", []), lib.get("props", []), lib.get("scenes", [])
        except Exception as e:
            self._log(f"⚠️ 加载资产库数据失败: {e}")
        return [], [], []

    def _refresh_prop_list(self):
        """刷新角色页的道具列表，支持'本集/全部'切换"""
        if not hasattr(self, 'prop_list'):
            return
        self.prop_list.clear()
        if not self.project:
            if hasattr(self, 'prop_stats_label'):
                self.prop_stats_label.setText("道具总数: 0")
            return

        # 数据源：project.props（load_project 已从 JSON 文件加载并同步）
        global_props = getattr(self.project, 'props', []) or []

        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        is_episode_selected = cur_ep >= 0
        is_episode_mode = episodes and len(episodes) > 1 and is_episode_selected

        show_all = False
        if hasattr(self, 'prop_filter_combo'):
            if is_episode_selected:
                show_all = self.prop_filter_combo.currentIndex() == 1
            else:
                # 未选集：强制显示全部
                if self.prop_filter_combo.currentIndex() == 0:
                    self.prop_filter_combo.setCurrentIndex(1)
                show_all = True

        ep_prop_names = set()
        ep_section = ""  # 当前节号（如 "1.1"），用于 Prop.episodes 兜底筛选
        if is_episode_mode:
            ep_data = (getattr(self.project, 'episode_data', {}) or {}).get(str(cur_ep), {})
            ep_prop_names = set(ep_data.get("prop_names", []))
            # 尝试文本匹配也是在 _on_episode_selected 中做的，此处不再重复调用
            # 如果 episode_data 中无道具记录 → 本节无已知道具引用，留空
            # 不调用 _filter_assets_by_episodes_field：道具没有 episodes 字段时会返回全部
            if 0 <= cur_ep < len(episodes):
                ep_title = episodes[cur_ep].get("title", "").strip()
                ep_section = ep_title.split()[0] if ep_title else ""

        global_prop_names = set(p.name for p in global_props)

        # 未选集时默认显示 global_props，已选集时根据筛选模式决定
        if not is_episode_mode:
            display_props = global_props
        elif show_all and global_props:
            display_props = global_props
        elif ep_prop_names:
            display_props = [p for p in global_props if p.name in ep_prop_names]
        else:
            # 选节模式 + 无已知 episode_data 引用 → 用 Prop.episodes 字段兜底筛选
            if ep_section:
                display_props = [
                    p for p in global_props
                    if p.episodes and ep_section in p.episodes
                ]
                if not display_props:
                    display_props = []  # 无匹配时显示空
            else:
                display_props = []
        
        # 去重：按道具名去重，保留第一个出现的
        seen_prop_names = set()
        unique_props = []
        for p in display_props:
            if p.name not in seen_prop_names:
                seen_prop_names.add(p.name)
                unique_props.append(p)
        display_props = unique_props
        
        # 更新道具统计详情
        if hasattr(self, 'prop_stats_detail_label'):
            ep_count = len(display_props) if not show_all and is_episode_mode else 0
            global_count = len(global_props)
            if not is_episode_mode:
                self.prop_stats_detail_label.setText(f"🌐 全部 {global_count} 个")
            elif show_all:
                self.prop_stats_detail_label.setText(f"🌐 全部 {global_count} 个")
            else:
                section_title = episodes[cur_ep].get("title", "").strip() if (0 <= cur_ep < len(episodes)) else ""
                label_text = f"📌 {section_title} {ep_count}" if section_title else f"📌 本集 {ep_count}"
                self.prop_stats_detail_label.setText(f"{label_text} / 🌐 全部 {global_count} 个")

        for prop in display_props:
            status = "🖼️" if prop.image_path and Path(prop.image_path).exists() else "📄"
            tag = ""
            prop_section_title = episodes[cur_ep].get("title", "").strip() if is_episode_mode and 0 <= cur_ep < len(episodes) else ""
            if is_episode_mode:
                if ep_prop_names:
                    # 有 episode_data 引用：精确标注本集/非本集
                    if prop.name in ep_prop_names:
                        tag = f" 📌{prop_section_title}" if prop_section_title else " 📌本集"
                    else:
                        tag = " 🔗非本集"
                elif ep_section and prop.episodes and ep_section in prop.episodes:
                    # 无 episode_data 引用，但 Prop.episodes 字段匹配上了
                    tag = f" 📌{prop_section_title}" if prop_section_title else " 📌本集"
            # ep_prop_names 为空时（无已知道具引用），不标"本集"避免误导
            if prop.name in global_prop_names:
                tag += " 🌐全局"
            # 显示"有图/无图"状态，无勾选框
            item_text = f"{status} {prop.name}{tag}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, prop.name)
            # 与角色列表一致：Qt 原生勾选框
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.prop_list.addItem(item)

        self._update_stats()

    def _on_prop_selected(self, row: int):
        """选择道具（数据源必须和 _refresh_prop_list 一致，否则点击错位）"""
        if not self.project or row < 0:
            return
        
        # 与 _refresh_prop_list 使用一致的数据源
        global_props = getattr(self.project, 'props', []) or []

        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        is_episode_selected = cur_ep >= 0
        is_episode_mode = episodes and len(episodes) > 1 and is_episode_selected

        show_all = hasattr(self, 'prop_filter_combo') and self.prop_filter_combo.currentIndex() == 1
        
        # 构建与 _refresh_prop_list 一致的 props_list（行索引要对应）
        if not is_episode_mode or show_all:
            props_list = global_props
        else:
            # 已选集且非"全部"模式：获取本期道具列表
            ep_data = (getattr(self.project, 'episode_data', {}) or {}).get(str(cur_ep), {})
            ep_prop_names = set(ep_data.get("prop_names", []))
            if ep_prop_names:
                props_list = [p for p in global_props if p.name in ep_prop_names]
            else:
                # 用 Prop.episodes 字段兜底
                ep_title = episodes[cur_ep].get("title", "").strip() if (0 <= cur_ep < len(episodes)) else ""
                ep_section = ep_title.split()[0] if ep_title else ""
                if ep_section:
                    props_list = [p for p in global_props if p.episodes and ep_section in p.episodes]
                else:
                    props_list = global_props  # 无筛选条件时回退
        
        if row >= len(props_list):
            return
        prop = props_list[row]
        self._update_preview("prop", prop)
        if getattr(prop, 'image_path', None) and Path(prop.image_path).exists():
            self.show_media_image(str(prop.image_path), f"道具: {prop.name}")
    
    def _on_scenes_generated(self, result: dict):
        scenes = result.get('scenes', [])
        count = len(scenes) if isinstance(scenes, list) else 0
        self._log(f"✅ 场景生成完成，共 {count} 个场景")
        # 同步 image_path 到 global_scenes（生成时写到了 project.scenes 实例）
        if self.project and getattr(self.project, 'scenes', None):
            smap = {s.name: s for s in self.project.scenes}
            for gs in getattr(self.project, "global_scenes", []) or []:
                src = smap.get(gs.name)
                if src and src.image_path and not gs.image_path:
                    gs.image_path = src.image_path
        if self.project and not getattr(self.project, 'global_scenes', []):
            self.project.global_scenes = list(self.project.scenes)
        self._refresh_scene_list()
        self._update_wizard_steps()
        self._sync_asset_images_to_library()
        # 保存两份数据，确保重启后恢复
        if self.project_manager:
            self.project_manager.save_meta(self.project)
            self.project_manager.save_scenes(self.project)
        
        
        # 自动选中第一个场景以更新预览
        if count > 0 and hasattr(self, 'scene_list'):
            self.scene_list.setCurrentRow(0)
        
        msg = QMessageBox(self)
        msg.setWindowTitle("✅ 场景生成完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(f"成功生成 {count} 个场景")
        msg.setInformativeText("接下来要做什么？")
        
        gen_sb_btn = msg.addButton("🎬 生成分镜图片", QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
        
        msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == gen_sb_btn:
            self._on_generate_storyboard()
    
    def _on_storyboard_generated(self, result: dict):
        count = result.get('storyboard', 0)
        self._log(f"✅ 分镜生成完成，共 {count} 个分镜")
        self._refresh_storyboard_table()
        self._update_wizard_steps()
        
        # 自动选中第一个分镜以更新预览
        if count > 0 and hasattr(self, 'storyboard_table'):
            self.storyboard_table.setCurrentCell(0, 0)
        
        # 检查分镜是否已有帧图像
        frames_count = sum(1 for sb in self.project.storyboard if sb.frame_path) if self.project else 0
        has_frames = frames_count > 0
        
        msg = QMessageBox(self)
        msg.setWindowTitle("✅ 分镜生成完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(f"成功生成 {count} 个分镜")
        
        if has_frames:
            msg.setInformativeText(f"已有 {frames_count}/{count} 个分镜帧图像。接下来要做什么？")
            gen_image_btn = msg.addButton("🖼️ 重新生成分镜图片", QMessageBox.ButtonRole.ActionRole)
            gen_video_btn = msg.addButton("🎥 生成视频", QMessageBox.ButtonRole.ActionRole)
        else:
            msg.setInformativeText("分镜还没有帧图像，建议先生成分镜图片。")
            gen_image_btn = msg.addButton("🖼️ 生成分镜图片", QMessageBox.ButtonRole.ActionRole)
            gen_video_btn = None
        ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
        
        msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == gen_image_btn:
            self._start_storyboard_image_generation()
        elif gen_video_btn and clicked == gen_video_btn:
            self._on_generate_video()

    def _selected_storyboard_index(self) -> Optional[int]:
        """返回当前选中的分镜索引。"""
        row = self.storyboard_table.currentRow()
        if not self.project or row < 0 or row >= len(self.project.storyboard):
            QMessageBox.warning(self, "提示", "请先选择一个分镜")
            return None
        return row

    def _on_generate_single_storyboard_image(self):
        """生成当前选中分镜的图片。"""
        scene_index = self._selected_storyboard_index()
        if scene_index is None:
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        self._worker = ProductionWorker(
            self.producer,
            step="single_storyboard_image",
            scene_index=scene_index,
        )
        self._start_worker(self._worker, self._on_single_storyboard_image_generated)
        self._log(f"开始生成单个分镜图: #{scene_index + 1}")

    def _on_single_storyboard_image_generated(self, result: dict):
        scene_index = result["single_storyboard_image"]
        self._refresh_storyboard_table()
        
        # 使用 setCurrentCell 而不是 selectRow，确保触发 currentCellChanged 信号
        self.storyboard_table.setCurrentCell(scene_index, 0)
        
        # 调试：打印frame_path信息
        sb = self.project.storyboard[scene_index]
        self._log(f"📋 分镜 #{scene_index + 1} frame_path: {sb.frame_path}")
        
        self._update_preview("storyboard", sb)
        self._log(f"✅ 单个分镜图生成完成: #{scene_index + 1}")

    def _on_generate_single_storyboard_video(self):
        """生成当前选中分镜的视频（按设定的参数）。"""
        scene_index = self._selected_storyboard_index()
        if scene_index is None:
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log, self.project_manager)
        self._worker = ProductionWorker(
            self.producer,
            step="single_storyboard_video",
            scene_index=scene_index,
        )
        self._start_worker(self._worker, self._on_single_storyboard_video_generated)
        self._log(f"开始生成单个分镜视频: #{scene_index + 1}")

    def _on_play_single_storyboard_video(self):
        """播放当前选中分镜已有的视频，没有视频则生成后播放。"""
        scene_index = self._selected_storyboard_index()
        if scene_index is None:
            return
        storyboard = self.project.storyboard[scene_index]
        if storyboard.video_path and Path(storyboard.video_path).exists():
            self._update_preview("storyboard", storyboard)
            self._log(f"播放单个分镜视频: #{scene_index + 1}")
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        self._worker = ProductionWorker(
            self.producer,
            step="single_storyboard_video",
            scene_index=scene_index,
        )
        self._start_worker(self._worker, self._on_single_storyboard_video_generated)
        self._log(f"开始生成单个分镜视频: #{scene_index + 1}")

    def _on_single_storyboard_video_generated(self, result: dict):
        scene_index = result["single_storyboard_video"]
        self._refresh_storyboard_table()
        self._refresh_video_list()
        self.storyboard_table.selectRow(scene_index)
        self._update_preview("storyboard", self.project.storyboard[scene_index])
        self._log(f"✅ 单个分镜视频生成完成并开始播放: #{scene_index + 1}")
    
    def _on_video_generated(self, result: dict):
        # 兼容两种数据格式：直接返回或嵌套在 'video' 键下
        if 'video' in result:
            result = result['video']
        
        success_count = result.get('success_count', 0)
        total_count = result.get('total_count', 0)
        failed_indices = result.get('failed_indices', [])
        
        self._log(f"✅ 视频生成完成！成功: {success_count}/{total_count}")
        self._refresh_storyboard_table()
        self._refresh_video_list()
        
        if failed_indices:
            failed_names = []
            for idx in failed_indices:
                if idx < len(self.project.storyboard):
                    sb = self.project.storyboard[idx]
                    failed_names.append(f"#{idx} {sb.scene_name}")
            
            msg = QMessageBox(self)
            msg.setWindowTitle("⚠️ 视频生成完成（部分失败）")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText(f"成功生成 {success_count}/{total_count} 个分镜视频")
            msg.setInformativeText(
                f"以下分镜生成失败：\n{', '.join(failed_names)}\n\n"
                f"失败原因已在日志中显示。是否重试失败的分镜？"
            )
            
            retry_btn = msg.addButton("🔄 重试失败的分镜", QMessageBox.ButtonRole.ActionRole)
            merge_btn = msg.addButton("🎥 合并已有视频", QMessageBox.ButtonRole.ActionRole)
            ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
            
            msg.exec()
            
            clicked = msg.clickedButton()
            if clicked == retry_btn:
                self._on_retry_failed_videos(failed_indices)
            elif clicked == merge_btn:
                self._on_merge_video()
        else:
            msg = QMessageBox(self)
            msg.setWindowTitle("✅ 视频生成完成")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText(f"成功生成 {success_count}/{total_count} 个分镜视频！")
            msg.setInformativeText("您可以添加旁白和音效，或者合并最终视频。")
            
            merge_btn = msg.addButton("🎥 合并视频", QMessageBox.ButtonRole.ActionRole)
            ok_btn = msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
            
            msg.exec()
            
            clicked = msg.clickedButton()
            if clicked == merge_btn:
                self._on_merge_video()
    
    def _on_retry_failed_videos(self, failed_indices: list):
        """重试失败的分镜视频"""
        if not self.project or not failed_indices:
            return
        
        self._log(f"开始重试 {len(failed_indices)} 个失败的分镜视频...")
        
        # 创建临时 worker 只重试失败的分镜
        self._worker = ProductionWorker(self.producer, step='retry_video', failed_indices=failed_indices)
        self._start_worker(self._worker, self._on_video_generated)
    
    def _on_add_character(self):
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或加载项目")
            return
        
        # 创建自定义对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("添加角色")
        dialog.setMinimumWidth(560)
        dialog.setMinimumHeight(480)
        layout = QVBoxLayout(dialog)
        
        tab_widget = QTabWidget()
        
        # ============ 选项卡1: 新建角色 ============
        new_tab = QWidget()
        new_layout = QVBoxLayout(new_tab)
        new_layout.setSpacing(10)
        
        # 名称
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("角色名称:"))
        name_input = QLineEdit()
        name_input.setPlaceholderText("请输入角色名称")
        name_row.addWidget(name_input, 1)
        new_layout.addLayout(name_row)
        
        # 角色类型
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("角色类型:"))
        char_type_combo = QComboBox()
        char_type_combo.addItems(["人类", "动物/妖兽", "群体", "能量体"])
        char_type_combo.setToolTip("角色类型：人类/动物/妖兽/群体/能量体")
        type_row.addWidget(char_type_combo, 1)
        new_layout.addLayout(type_row)
        
        # 数量（非人类角色使用）
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("数量描述:"))
        count_input = QLineEdit()
        count_input.setPlaceholderText("仅非人类角色使用，如：7匹、一头、数百头")
        count_row.addWidget(count_input, 1)
        new_layout.addLayout(count_row)
        
        # 描述
        new_layout.addWidget(QLabel("角色描述:"))
        desc_input = QPlainTextEdit()
        desc_input.setPlaceholderText("请输入角色外观描述（用于AI生成角色图）")
        desc_input.setMinimumHeight(160)
        new_layout.addWidget(desc_input, 1)
        
        tab_widget.addTab(new_tab, "🆕 新建角色")
        
        # ============ 选项卡2: 从资产库选择 ============
        lib_tab = QWidget()
        lib_layout = QVBoxLayout(lib_tab)
        lib_layout.setSpacing(8)
        
        # 资产库选择
        lib_sel_row = QHBoxLayout()
        lib_sel_row.addWidget(QLabel("资产库:"))
        lib_combo = QComboBox()
        lib_combo.setMinimumWidth(250)
        from src.services.library_manager import list_libraries, load_library
        lib_names = list_libraries()
        if lib_names:
            lib_combo.addItems(lib_names)
        else:
            lib_combo.addItem("（无可用资产库）")
            lib_combo.setEnabled(False)
        lib_sel_row.addWidget(lib_combo, 1)
        lib_layout.addLayout(lib_sel_row)
        
        # 角色列表
        lib_layout.addWidget(QLabel("选择要导入的角色（双击或选中后点确定）:"))
        lib_char_list = QListWidget()
        lib_char_list.setAlternatingRowColors(True)
        lib_layout.addWidget(lib_char_list, 1)
        
        # 角色详情预览
        lib_preview = QTextEdit()
        lib_preview.setReadOnly(True)
        lib_preview.setMaximumHeight(130)
        lib_preview.setPlaceholderText("选中角色后在此显示详情...")
        lib_preview.setStyleSheet("QTextEdit { background-color: #1e293b; color: #e2e8f0; font-size: 12px; }")
        lib_layout.addWidget(lib_preview)
        
        # 加载资产库角色
        def _load_lib_characters():
            lib_char_list.clear()
            lib_preview.clear()
            name = lib_combo.currentText()
            if not name or name == "（无可用资产库）":
                return
            data = load_library(name)
            if data:
                for c in data.get("characters", []):
                    label = f"{c.name}  [{c.char_type}]"
                    if c.creature_count:
                        label += f"  ({c.creature_count})"
                    item = QListWidgetItem(label)
                    item.setData(Qt.ItemDataRole.UserRole, c)
                    lib_char_list.addItem(item)
                if lib_char_list.count() > 0:
                    lib_char_list.setCurrentRow(0)
        
        # 显示角色详情
        def _on_lib_char_preview():
            item = lib_char_list.currentItem()
            if not item:
                lib_preview.clear()
                # 清除主页右侧预览
                self.media_display_label.setText("暂无媒体")
                self.media_display_label.show()
                self.media_video_widget.hide()
                self.media_info_label.setText("")
                return
            c = item.data(Qt.ItemDataRole.UserRole)
            lines = [f"名称: {c.name}", f"类型: {c.char_type}"]
            if c.creature_count:
                lines.append(f"数量: {c.creature_count}")
            if c.costumes:
                lines.append(f"造型: {', '.join(cost.name for cost in c.costumes)}")
            if c.description:
                desc_preview = c.description[:300]
                if len(c.description) > 300:
                    desc_preview += "..."
                lines.append(f"描述: {desc_preview}")
            lib_preview.setPlainText("\n".join(lines))

            # 在主页右侧预览区显示角色图像
            from pathlib import Path
            if c.image_path and Path(c.image_path).exists():
                self.show_media_image(c.image_path, f"角色: {c.name}")
            else:
                self.media_display_label.setText("无图")
                self.media_display_label.show()
                self.media_video_widget.hide()
                self.media_info_label.setText(f"角色: {c.name}（无图像）")
        
        lib_combo.currentIndexChanged.connect(lambda: _load_lib_characters())
        lib_char_list.currentItemChanged.connect(lambda: _on_lib_char_preview())
        lib_char_list.itemDoubleClicked.connect(dialog.accept)  # 双击快速选择
        
        tab_widget.addTab(lib_tab, "📂 从资产库选择")
        
        layout.addWidget(tab_widget)
        
        # 确定/取消按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)
        
        # 初始加载资产库角色列表
        if lib_names:
            _load_lib_characters()
        
        # 显示对话框
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        current_tab = tab_widget.currentIndex()
        style = self.char_style_combo.currentText() if hasattr(self, 'char_style_combo') else "cartoon"
        
        if current_tab == 0:
            # ========== 新建角色 ==========
            name = name_input.text().strip()
            if not name:
                QMessageBox.warning(self, "错误", "角色名称不能为空")
                return
            desc = desc_input.toPlainText().strip()
            char_type = char_type_combo.currentText()
            creature_count = count_input.text().strip()
            
            character = Character(
                name=name,
                description=desc,
                style=style,
                char_type=char_type,
                creature_count=creature_count,
            )
        else:
            # ========== 从资产库选择 ==========
            item = lib_char_list.currentItem()
            if not item:
                QMessageBox.warning(self, "错误", "请从资产库中选择一个角色")
                return
            src = item.data(Qt.ItemDataRole.UserRole)
            # 创建副本，避免直接引用资产库数据
            character = Character(
                name=src.name,
                style=src.style or style,
                description=src.description,
                image_path=src.image_path,
                images=src.images[:] if src.images else [],
                seed=src.seed,
                costumes=[Costume.from_dict(c.to_dict()) for c in (src.costumes or [])],
                episodes=[],
                char_type=src.char_type,
                creature_count=src.creature_count,
            )
        
        # 保存到项目
        if self.project and getattr(self.project, "asset_library_name", ""):
            try:
                from src.services.library_manager import load_library, save_library
                lib_data = load_library(self.project.asset_library_name)
                if lib_data:
                    chars = lib_data.get("characters", [])
                    chars.append(character)
                    save_library(
                        self.project.asset_library_name,
                        chars,
                        lib_data.get("props", []),
                        lib_data.get("scenes", []),
                        merge=False
                    )
                    self._log(f"✅ 已添加角色到资产库: {character.name}")
                else:
                    self.project.characters.append(character)
                    self._log(f"⚠️ 资产库为空，角色仅添加到项目内存: {character.name}")
            except Exception as e:
                self._log(f"⚠️ 保存到资产库失败: {e}，角色仅添加到项目内存")
                self.project.characters.append(character)
        else:
            self.project.characters.append(character)
            self._log(f"✅ 已添加角色: {character.name}（未绑定资产库）")
        
        self._refresh_character_list()
    
    def _on_update_character(self):
        """更新角色信息：使用自定义对话框同时编辑名称和描述，可单独修改任一项"""
        if not self.project:
            return
        
        row = self.character_list.currentRow()
        if row < 0:
            return
        
        # 数据源：project.characters（与 _refresh_character_list 一致）
        chars = self.project.characters or []
        if row >= len(chars):
            return
        character = chars[row]
        
        # 创建自定义对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("更新角色")
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        
        # 名称输入
        name_layout = QHBoxLayout()
        name_label = QLabel("角色名称:")
        name_label.setMinimumWidth(80)
        name_layout.addWidget(name_label)
        name_input = QLineEdit(character.name)
        name_input.setPlaceholderText("请输入角色名称")
        name_layout.addWidget(name_input)
        layout.addLayout(name_layout)
        
        # 描述输入（多行文本）
        desc_label = QLabel("角色描述:")
        layout.addWidget(desc_label)
        desc_input = QPlainTextEdit()
        desc_input.setPlainText(character.description or "")
        desc_input.setMinimumHeight(150)
        desc_input.setPlaceholderText("请输入角色外观描述（用于AI生成角色图）")
        layout.addWidget(desc_input)
        
        # 按钮
        button_box = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        button_box.addWidget(cancel_btn)
        button_box.addWidget(ok_btn)
        layout.addLayout(button_box)
        
        # 显示对话框
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_name = name_input.text().strip()
            new_desc = desc_input.toPlainText().strip()
            
            if not new_name:
                QMessageBox.warning(self, "错误", "角色名称不能为空")
                return
            
            # 只更新有变化的字段
            updated_fields = []
            if new_name != character.name:
                character.name = new_name
                updated_fields.append("名称")
            if new_desc != character.description:
                character.description = new_desc
                updated_fields.append("描述")
            
            # 保存到资产库
            if self.project and getattr(self.project, "asset_library_name", ""):
                try:
                    from src.services.library_manager import load_library, save_library
                    lib_data = load_library(self.project.asset_library_name)
                    if lib_data:
                        save_library(
                            self.project.asset_library_name,
                            lib_data.get("characters", []),
                            lib_data.get("props", []),
                            lib_data.get("scenes", []),
                            merge=False
                        )
                except Exception as e:
                    self._log(f"⚠️ 保存到资产库失败: {e}")
            
            self._refresh_character_list()
            
            if updated_fields:
                self._log(f"✅ 已更新角色 {character.name} 的: {'、'.join(updated_fields)}")
            else:
                self._log(f"ℹ️ 角色 {character.name} 未做任何修改")
    
    def _on_delete_character(self):
        """删除角色（保留兼容，但建议使用列表下的批量删除按钮）"""
        if not self.project:
            return
        
        # 剧集模式下角色存储在 global_characters 中
        global_chars = getattr(self.project, 'global_characters', [])
        chars = global_chars or self.project.characters
        
        row = self.character_list.currentRow()
        self._log(f"🔍 删除调试: currentRow={row}, global_chars数量={len(global_chars)}, characters数量={len(self.project.characters)}")
        
        if row < 0 or row >= len(chars):
            self._log(f"⚠️ 删除失败: 行号 {row} 超出范围 (0-{len(chars)-1})")
            return
        
        character = chars[row]
        self._log(f"🔍 准备删除角色: '{character.name}' (位于 {'global_characters' if global_chars else 'characters'} 列表)")
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除角色 '{character.name}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            chars.pop(row)
            self._log(f"🔍 删除后: global_chars数量={len(global_chars)}, characters数量={len(self.project.characters)}")
            self._refresh_character_list()
            self._clear_preview()  # 清空预览
            if self.project_manager:
                self.project_manager.save_project(self.project)
            self._log(f"🗑️ 已删除角色: {character.name}")
    
    def _get_checked_character_names(self) -> list:
        """从角色列表勾选框中获取选中的角色名列表"""
        names = []
        for i in range(self.character_list.count()):
            item = self.character_list.item(i)
            if item is None:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                name = item.data(Qt.ItemDataRole.UserRole)
                if name:
                    names.append(name)
        self._log(f"🔍 共找到 {len(names)} 个勾选角色: {names[:5]}{'...' if len(names) > 5 else ''}")
        return names
    
    def _find_character_by_name(self, name: str):
        """按名称查找角色对象（本集 + 全局）"""
        if not self.project or not name:
            return None
        for c in (list(self.project.characters or [])
                  + list(getattr(self.project, 'global_characters', []) or [])):
            if c.name == name:
                return c
        return None
    
    def _on_delete_selected_character_images(self):
        """删除已勾选角色的图像文件（保留角色条目）"""
        if not self.project:
            return
        
        checked_names = self._get_checked_character_names()
        if not checked_names:
            QMessageBox.information(self, "提示", "请先勾选要删除图像的角色")
            return
        
        # 统计有图像的角色数
        chars_with_img = []
        for name in checked_names:
            char = self._find_character_by_name(name)
            if char and getattr(char, 'image_path', None) and Path(char.image_path).exists():
                chars_with_img.append((name, char.image_path))
        
        if not chars_with_img:
            QMessageBox.information(self, "提示", "勾选的角色都没有生成底模图")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {len(chars_with_img)} 个角色的底模图像文件吗？\n"
            f"（角色条目会保留，只是删除图片，可以重新生成）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        deleted = 0
        for name, img_path in chars_with_img:
            try:
                Path(img_path).unlink()
                # 清除 image_path
                char = self._find_character_by_name(name)
                if char:
                    char.image_path = None
                deleted += 1
            except Exception as e:
                self._log(f"删除角色 {name} 的图像失败: {e}")
        
        self._refresh_character_list()
        if self.project_manager:
            self.project_manager.save_project(self.project)
        self._log(f"✅ 已删除 {deleted} 个角色的底模图像文件")
    
    def _on_delete_selected_characters(self):
        """删除已勾选的角色条目及其图像文件"""
        self._log(f"🔍 开始删除选中角色...")
        if not self.project:
            self._log("⚠️ 没有项目")
            return
        
        checked_names = self._get_checked_character_names()
        self._log(f"🔍 _get_checked_character_names 返回: {checked_names}")
        if not checked_names:
            QMessageBox.information(self, "提示", "请先勾选要删除的角色")
            self._log("⚠️ 未找到勾选的角色，已提示用户")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {len(checked_names)} 个角色吗？\n"
            f"（包括角色条目、底模图和所有造型图）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        deleted = 0
        
        # 优先从资产库删除
        if self.project and getattr(self.project, "asset_library_name", ""):
            try:
                from src.services.library_manager import load_library, save_library
                lib_data = load_library(self.project.asset_library_name)
                if lib_data:
                    chars = lib_data.get("characters", [])
                    new_chars = [c for c in chars if c.name not in checked_names]
                    deleted = len(chars) - len(new_chars)
                    
                    # 删除图像文件
                    for c in chars:
                        if c.name in checked_names:
                            # 删除底模图
                            if getattr(c, 'image_path', None) and Path(c.image_path).exists():
                                try:
                                    Path(c.image_path).unlink()
                                except Exception as e:
                                    self._log(f"删除角色 {c.name} 的底模图失败: {e}")
                            # 删除造型图
                            for costume in (getattr(c, 'costumes', []) or []):
                                if getattr(costume, 'image_path', None) and Path(costume.image_path).exists():
                                    try:
                                        Path(costume.image_path).unlink()
                                    except Exception as e:
                                        self._log(f"删除角色 {c.name} 的造型 {costume.name} 图失败: {e}")
                    
                    # 保存资产库
                    save_library(
                        self.project.asset_library_name,
                        new_chars,
                        lib_data.get("props", []),
                        lib_data.get("scenes", []),
                        merge=False
                    )
            except Exception as e:
                self._log(f"⚠️ 从资产库删除角色失败: {e}")
        
        # 也从项目内存中删除（如果存在）
        for name in checked_names:
            char = self._find_character_by_name(name)
            if char:
                # 删除底模图
                if getattr(char, 'image_path', None) and Path(char.image_path).exists():
                    try:
                        Path(char.image_path).unlink()
                    except Exception as e:
                        self._log(f"删除角色 {name} 的底模图失败: {e}")
                # 删除造型图
                for costume in (getattr(char, 'costumes', []) or []):
                    if getattr(costume, 'image_path', None) and Path(costume.image_path).exists():
                        try:
                            Path(costume.image_path).unlink()
                        except Exception as e:
                            self._log(f"删除角色 {name} 的造型 {costume.name} 图失败: {e}")
                # 从项目列表中删除
                if hasattr(self.project, 'characters') and char in self.project.characters:
                    self.project.characters.remove(char)
                global_chars = getattr(self.project, 'global_characters', [])
                if char in global_chars:
                    global_chars.remove(char)
                deleted += 1
        
        self._refresh_character_list()
        self._clear_preview()
        self._log(f"✅ 已删除 {deleted} 个角色")
    
    def _get_checked_prop_names(self) -> list:
        """从道具列表勾选框获取选中的道具名列表"""
        names = []
        for i in range(self.prop_list.count()):
            item = self.prop_list.item(i)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            name = item.data(Qt.ItemDataRole.UserRole)
            if name:
                names.append(name)
        return names
    
    def _find_prop_by_name(self, name: str):
        """按名称查找道具对象（本集 + 全局）"""
        if not self.project or not name:
            return None
        for p in (list(self.project.props or [])
                  + list(getattr(self.project, 'global_props', []) or [])):
            if p.name == name:
                return p
        return None
    
    def _on_delete_selected_prop_images(self):
        """删除已勾选道具的图像文件（保留道具条目）"""
        if not self.project:
            return
        
        checked_names = self._get_checked_prop_names()
        if not checked_names:
            QMessageBox.information(self, "提示", "请先勾选要删除图像的道具")
            return
        
        # 统计有图像的道具数
        props_with_img = []
        for name in checked_names:
            prop = self._find_prop_by_name(name)
            if prop and getattr(prop, 'image_path', None) and Path(prop.image_path).exists():
                props_with_img.append((name, prop.image_path))
        
        if not props_with_img:
            QMessageBox.information(self, "提示", "勾选的道具都没有生成图像")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {len(props_with_img)} 个道具的图像文件吗？\n"
            f"（道具条目会保留，只是删除图片，可以重新生成）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        deleted = 0
        for name, img_path in props_with_img:
            try:
                Path(img_path).unlink()
                # 清除 image_path
                prop = self._find_prop_by_name(name)
                if prop:
                    prop.image_path = None
                deleted += 1
            except Exception as e:
                self._log(f"删除道具 {name} 的图像失败: {e}")
        
        self._refresh_prop_list()
        if self.project_manager:
            self.project_manager.save_project(self.project)
        self._log(f"✅ 已删除 {deleted} 个道具的图像文件")
    
    def _on_delete_selected_props(self):
        """删除已勾选的道具条目及其图像文件"""
        if not self.project:
            return
        
        checked_names = self._get_checked_prop_names()
        if not checked_names:
            QMessageBox.information(self, "提示", "请先勾选要删除的道具")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {len(checked_names)} 个道具吗？\n"
            f"（包括道具条目和对应的图像文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        deleted = 0
        
        # 优先从资产库删除
        if self.project and getattr(self.project, "asset_library_name", ""):
            try:
                from src.services.library_manager import load_library, save_library
                lib_data = load_library(self.project.asset_library_name)
                if lib_data:
                    props = lib_data.get("props", [])
                    new_props = [p for p in props if p.name not in checked_names]
                    deleted = len(props) - len(new_props)
                    
                    # 删除图像文件
                    for p in props:
                        if p.name in checked_names:
                            if getattr(p, 'image_path', None) and Path(p.image_path).exists():
                                try:
                                    Path(p.image_path).unlink()
                                except Exception as e:
                                    self._log(f"删除道具 {p.name} 的图像失败: {e}")
                    
                    # 保存资产库
                    save_library(
                        self.project.asset_library_name,
                        lib_data.get("characters", []),
                        new_props,
                        lib_data.get("scenes", []),
                        merge=False
                    )
            except Exception as e:
                self._log(f"⚠️ 从资产库删除道具失败: {e}")
        
        # 也从项目内存中删除（如果存在）
        for name in checked_names:
            prop = self._find_prop_by_name(name)
            if prop:
                # 删除图像文件
                if getattr(prop, 'image_path', None) and Path(prop.image_path).exists():
                    try:
                        Path(prop.image_path).unlink()
                    except Exception as e:
                        self._log(f"删除道具 {name} 的图像失败: {e}")
                # 从项目列表中删除
                if hasattr(self.project, 'props') and prop in self.project.props:
                    self.project.props.remove(prop)
                global_props = getattr(self.project, 'global_props', [])
                if prop in global_props:
                    global_props.remove(prop)
                deleted += 1
        
        self._refresh_prop_list()
        self._clear_preview()
        self._log(f"✅ 已删除 {deleted} 个道具")
    
    def _on_manage_character_costumes(self):
        """管理选中角色的造型（服装/形象变化）。

        弹窗中可添加/删除造型；每个造型有 名称 + 变化描述。
        生成角色图像时会为每个造型单独出一张图（seed 由 角色名+造型名 决定）。
        分镜角色列写 "角色名/造型名" 即引用该造型参考图。
        """
        if not self.project:
            return
        # 确保从资产库同步最新数据（含角色的所有造型）
        self._sync_global_from_library()
        
        row = self.character_list.currentRow()
        if row < 0:
            # 兜底：列表非空但未选中时默认操作第一个角色，避免按钮点了没反应
            if self.character_list.count() > 0:
                self.character_list.setCurrentRow(0)
                row = 0
            else:
                QMessageBox.information(
                    self, "提示",
                    "角色列表为空。请先「➕ 添加角色」，或在故事页「🔍 提取角色/场景/分镜」生成全局资产。"
                )
                return
        # 定位实际角色对象（考虑筛选模式：本集/全部）
        show_all = hasattr(self, 'char_filter_combo') and self.char_filter_combo.currentIndex() == 1
        global_chars = getattr(self.project, 'global_characters', [])
        
        # 获取本集角色名称列表（与 _refresh_character_list 保持一致）
        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        is_episode_mode = episodes and len(episodes) > 1 and 0 <= cur_ep < len(episodes)
        ep_char_names = set()
        if is_episode_mode:
            ep_data = (getattr(self.project, 'episode_data', {}) or {}).get(str(cur_ep), {})
            ep_char_names = set(ep_data.get("character_names", []))
        
        # 决定使用哪个角色列表（与 _refresh_character_list 逻辑一致）
        if show_all:
            chars_list = global_chars if global_chars else self.project.characters
        else:
            if is_episode_mode and ep_char_names:
                if global_chars:
                    chars_list = [c for c in global_chars if c.name in ep_char_names]
                else:
                    chars_list = [c for c in self.project.characters if c.name in ep_char_names]
            else:
                chars_list = self.project.characters
        if row >= len(chars_list):
            return
        # 用可变容器包裹，方便角色选择切换时内部闭包函数能读到最新角色
        current_char = [chars_list[row]]

        dialog = QDialog(self)
        dialog.setWindowTitle(f"造型管理")
        dialog.resize(800, 560)
        dlg_layout = QVBoxLayout(dialog)

        # === 顶部：角色选择器 + 底模图状态 ===
        top_row = QHBoxLayout()
        char_combo = QComboBox()
        char_combo.setMinimumWidth(180)
        # 填充角色列表
        all_char_names = []
        if global_chars:
            all_char_names = [c.name for c in global_chars]
        elif self.project and self.project.characters:
            all_char_names = [c.name for c in self.project.characters]
        for cn in all_char_names:
            char_combo.addItem(cn)
        # 预选当前角色
        combo_idx = char_combo.findText(current_char[0].name)
        if combo_idx >= 0:
            char_combo.setCurrentIndex(combo_idx)
        char_combo.setToolTip("选择角色查看/管理其造型")
        top_row.addWidget(QLabel("角色："))
        top_row.addWidget(char_combo)
        top_row.addStretch()

        # 底模图状态
        def _update_base_status():
            """更新底模图状态显示（角色切换后调用）"""
            cp = getattr(current_char[0], 'image_path', None)
            ok = bool(cp and Path(cp).exists())
            base_label.setText(
                f"🧍 底模图：{'✅ 已生成' if ok else '❌ 未生成（点角色页「🎨 生成角色图像」出图）'}"
                + (f"　{Path(cp).name}" if ok else "")
            )
        base_label = QLabel()
        base_label.setStyleSheet("color: #cbd5e1; font-size: 12px; font-weight: bold;")
        top_row.addWidget(base_label)
        dlg_layout.addLayout(top_row)

        hint = QLabel(
            "为角色添加造型（如换装、年龄变化）。生成角色图像时会为每个造型单独出图；\n"
            "选中左侧造型即可在右侧查看已生成的造型图；分镜角色列写「角色名/造型名」即引用该造型。"
        )
        hint.setStyleSheet("color: #94a3b8; font-size: 11px;")
        dlg_layout.addWidget(hint)

        # 左：造型列表；右：造型图预览（出图后可直接在这里查看）
        body_row = QHBoxLayout()
        costume_list = QListWidget()
        costume_list.setObjectName("costume_list")
        costume_list.setMinimumWidth(300)
        costume_list.setToolTip("选中造型查看右侧图片；双击用系统看图工具打开大图")
        body_row.addWidget(costume_list, 1)

        preview_col = QVBoxLayout()
        costume_preview = QLabel("选中左侧造型后在这里查看造型图")
        costume_preview.setObjectName("costume_preview")
        costume_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        costume_preview.setMinimumSize(320, 360)
        costume_preview.setWordWrap(True)
        costume_preview.setStyleSheet(
            "background-color: #0f172a; color: #94a3b8; "
            "border: 1px solid #334155; border-radius: 6px; font-size: 11px;"
        )
        preview_col.addWidget(costume_preview, 1)

        preview_btns = QHBoxLayout()
        open_img_btn = QPushButton("🖼️ 打开大图")
        open_img_btn.setToolTip("用系统默认看图工具打开当前造型图")
        open_folder_btn = QPushButton("📂 打开图片文件夹")
        open_folder_btn.setToolTip("打开本项目 characters 文件夹，查看全部角色图与造型图")
        preview_btns.addWidget(open_img_btn)
        preview_btns.addWidget(open_folder_btn)
        preview_col.addLayout(preview_btns)

        # 最小粒度的选择性生成：单个造型重出 / 该角色补缺图（在弹窗里选，不用回到列表勾选）
        gen_btns = QHBoxLayout()
        gen_one_btn = QPushButton("🎨 生成该造型图")
        gen_one_btn.setToolTip("只出当前选中造型的图（已有图也会重出并覆盖）")
        gen_missing_btn = QPushButton("🎨 生成该角色缺图造型")
        gen_missing_btn.setToolTip("只出这个角色还没图的造型（已有图自动跳过）")
        gen_all_btn = QPushButton("🎨 重出全部造型")
        gen_all_btn.setToolTip("该角色所有造型强制重新出图并覆盖（含已有图的造型）")
        gen_btns.addWidget(gen_one_btn)
        gen_btns.addWidget(gen_missing_btn)
        gen_btns.addWidget(gen_all_btn)
        preview_col.addLayout(gen_btns)

        pending_gen = {"kind": None, "costume": None}
        body_row.addLayout(preview_col, 2)
        dlg_layout.addLayout(body_row, 1)

        def _costume_image_path():
            """当前选中造型的图片路径（未出图或文件缺失时返回 None）"""
            idx = costume_list.currentRow()
            costs = getattr(current_char[0], 'costumes', None) or []
            if not (0 <= idx < len(costs)):
                return None
            path = getattr(costs[idx], 'image_path', None)
            return str(path) if path and Path(path).exists() else None

        def _show_costume_preview():
            """预览当前选中造型的图片；未出图时提示如何出图"""
            idx = costume_list.currentRow()
            costs = getattr(current_char[0], 'costumes', None) or []
            costume_preview.clear()
            costume_preview.setPixmap(QPixmap())  # 清掉上一张图，避免残留
            if not (0 <= idx < len(costs)):
                costume_preview.setText("选中左侧造型后在这里查看造型图")
                return
            costume = costs[idx]
            image_path = _costume_image_path()
            if not image_path:
                costume_preview.setText(
                    f"「{costume.name}」尚未生成造型图\n\n"
                    "点角色页「🎨 生成造型图像」即可出图\n（已有图的造型会自动跳过）"
                )
                return
            pixmap = QPixmap(image_path)
            if pixmap.isNull():
                costume_preview.setText(f"图片加载失败：\n{Path(image_path).name}")
                return
            costume_preview.setPixmap(
                pixmap.scaled(
                    QSize(320, 360),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            costume_preview.setToolTip(f"{costume.name}\n{image_path}")

        def _reload_costume_list(keep_row: int = None):
            """重建造型列表（名称 + 描述 + 出图状态），并同步右侧预览"""
            keep = costume_list.currentRow() if keep_row is None else keep_row
            costume_list.clear()
            for c in (getattr(current_char[0], 'costumes', None) or []):
                label = f"👗「{c.name}」 {c.description}" if c.description else f"👗「{c.name}」"
                path = getattr(c, 'image_path', None)
                if path and Path(path).exists():
                    label += "   ✅已出图"
                elif path:
                    label += "   ⚠️图片文件丢失"
                else:
                    label += "   未出图"
                costume_list.addItem(label)
            if costume_list.count():
                costume_list.setCurrentRow(min(max(keep, 0), costume_list.count() - 1))
            _show_costume_preview()
            _update_base_status()

        def _open_costume_image():
            """打开当前造型的大图（系统默认看图工具）"""
            image_path = _costume_image_path()
            if not image_path:
                QMessageBox.information(
                    dialog, "提示",
                    "该造型尚无图片。请先点角色页「🎨 生成造型图像」出图。"
                )
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(image_path))

        def _open_costume_folder():
            """打开本项目 characters 文件夹（角色底模 + 造型图都在这里）"""
            folder = Settings.get_project_dir(self.project.name) / "characters"
            folder.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.Popen(['explorer', str(folder)])
            except Exception as e:
                QMessageBox.warning(dialog, "警告", f"无法打开文件夹: {e}")

        def _gen_selected_costume():
            """只生成/重出当前选中造型的图（关窗后由主窗口启动，避免模态弹窗阻塞进度）"""
            idx = costume_list.currentRow()
            costs = getattr(current_char[0], 'costumes', None) or []
            if not (0 <= idx < len(costs)):
                QMessageBox.information(dialog, "提示", "请先在左侧选择一个造型")
                return
            pending_gen["kind"] = "single"
            pending_gen["costume"] = costs[idx].name
            dialog.accept()

        def _gen_missing_costumes():
            """只生成该角色还没有图的造型"""
            if not (getattr(current_char[0], 'costumes', None) or []):
                QMessageBox.information(dialog, "提示", "该角色还没有造型，请先添加造型")
                return
            pending_gen["kind"] = "missing"
            dialog.accept()

        def _gen_all_costumes():
            """强制重出该角色全部造型图（覆盖已有图）"""
            if not (getattr(current_char[0], 'costumes', None) or []):
                QMessageBox.information(dialog, "提示", "该角色还没有造型，请先添加造型")
                return
            pending_gen["kind"] = "all"
            dialog.accept()

        costume_list.currentRowChanged.connect(lambda _row: _show_costume_preview())
        costume_list.itemDoubleClicked.connect(lambda _item: _open_costume_image())
        open_img_btn.clicked.connect(_open_costume_image)
        open_folder_btn.clicked.connect(_open_costume_folder)
        gen_one_btn.clicked.connect(_gen_selected_costume)
        gen_missing_btn.clicked.connect(_gen_missing_costumes)
        gen_all_btn.clicked.connect(_gen_all_costumes)
        _reload_costume_list()

        input_row = QHBoxLayout()
        cost_name_input = QLineEdit()
        cost_name_input.setPlaceholderText("造型名称，如 血衣")
        input_row.addWidget(cost_name_input, 1)
        cost_desc_input = QLineEdit()
        cost_desc_input.setPlaceholderText("造型变化描述，如 换黑色血衣、短发束起、左臂缠绷带")
        input_row.addWidget(cost_desc_input, 1)
        dlg_layout.addLayout(input_row)

        btn_row = QHBoxLayout()
        add_cost_btn = QPushButton("➕ 添加造型")
        del_cost_btn = QPushButton("🗑️ 删除选中造型")
        ok_btn = QPushButton("✅ 保存")
        cancel_btn = QPushButton("取消")

        def _add_costume():
            cn = cost_name_input.text().strip()
            cd = cost_desc_input.text().strip()
            if not cn:
                QMessageBox.warning(dialog, "提示", "请填写造型名称")
                return
            if not getattr(current_char[0], 'costumes', None):
                current_char[0].costumes = []
            # 同名造型更新描述（不重复添加）
            for existing_idx, existing in enumerate(current_char[0].costumes):
                if existing.name == cn:
                    existing.description = cd
                    _reload_costume_list(existing_idx)
                    self._log(f"📝 造型「{cn}」描述已更新")
                    return
            from src.models.project import Costume
            current_char[0].costumes.append(Costume(name=cn, description=cd))
            cost_name_input.clear()
            cost_desc_input.clear()
            # 刷新列表并选中新造型（右侧同步显示预览）
            _reload_costume_list(len(current_char[0].costumes) - 1)

        def _del_costume():
            idx = costume_list.currentRow()
            if idx < 0 or idx >= len(getattr(current_char[0], 'costumes', [])):
                return
            removed = current_char[0].costumes.pop(idx)
            _reload_costume_list()
            self._log(f"🗑️ 已删除造型「{removed.name}」（图片文件保留）")

        # 角色选择切换
        def _on_char_combo_changed(combo_idx: int):
            """切换当前管理的角色，重新加载造型列表"""
            if combo_idx < 0 or not global_chars or combo_idx >= len(global_chars):
                return
            new_char = global_chars[combo_idx]
            current_char[0] = new_char
            dialog.setWindowTitle(f"造型管理 - {new_char.name}")
            _reload_costume_list()

        char_combo.currentIndexChanged.connect(_on_char_combo_changed)
        
        add_cost_btn.clicked.connect(_add_costume)
        del_cost_btn.clicked.connect(_del_costume)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(add_cost_btn)
        btn_row.addWidget(del_cost_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        dlg_layout.addLayout(btn_row)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_character_list()
            if self.project_manager:
                self.project_manager.save_project(self.project)
            cnt = len(getattr(current_char[0], 'costumes', []) or [])
            self._log(f"✅ 角色 {current_char[0].name} 造型已保存（共 {cnt} 个），点击「生成角色图像」可为造型出图")

            # 弹窗内的生成按钮：数据已保存，这里按选择范围启动出图
            if pending_gen["kind"] == "single" and pending_gen["costume"]:
                self._generate_costumes(
                    selected_costumes={current_char[0].name: [pending_gen["costume"]]},
                    force=True,
                )
            elif pending_gen["kind"] == "missing":
                self._generate_costumes(selected_characters=[current_char[0].name])
            elif pending_gen["kind"] == "all":
                self._generate_costumes(selected_characters=[current_char[0].name], force=True)

    def _on_character_selected(self, row: int):
        """选择角色（数据源必须和 _refresh_character_list 一致，否则点击错位）"""
        if not self.project or row < 0:
            self.update_char_btn.setEnabled(False)
            if hasattr(self, 'costume_btn'):
                self.costume_btn.setEnabled(False)
            return

        # 与 _refresh_character_list 使用完全一致的数据源
        global_chars = getattr(self.project, 'characters', []) or []

        show_all = hasattr(self, 'char_filter_combo') and self.char_filter_combo.currentIndex() == 1
        
        # 获取本集角色名称列表
        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        is_episode_mode = episodes and len(episodes) > 1 and 0 <= cur_ep < len(episodes)
        ep_char_names = set()
        if is_episode_mode:
            ep_data = (getattr(self.project, 'episode_data', {}) or {}).get(str(cur_ep), {})
            ep_char_names = set(ep_data.get("character_names", []))
        
        if show_all:
            chars_list = list(global_chars)
        else:
            if is_episode_mode and ep_char_names:
                chars_list = [c for c in global_chars if c.name in ep_char_names]
            else:
                chars_list = list(global_chars)

        # 应用类型筛选（与 _refresh_character_list 完全一致）
        if hasattr(self, 'char_type_filter_combo'):
            type_filter_index = self.char_type_filter_combo.currentIndex()
            if type_filter_index > 0:
                type_map = {1: "人类", 2: "动物/妖兽", 3: "群体", 4: "能量体"}
                target_type = type_map.get(type_filter_index, "")
                if target_type:
                    chars_list = [c for c in chars_list if getattr(c, 'char_type', '人类') == target_type]

        if row < len(chars_list):
            self.update_char_btn.setEnabled(True)
            if hasattr(self, 'costume_btn'):
                self.costume_btn.setEnabled(True)

            character = chars_list[row]
            if hasattr(self, 'char_name_input'):
                self.char_name_input.setText(character.name)
            if hasattr(self, 'char_desc_input'):
                self.char_desc_input.setText(character.description)
                self.char_desc_input.setToolTip(character.description)

            # 同步更新类型和预设下拉框
            if hasattr(self, 'char_type_combo'):
                cidx = self.char_type_combo.findText(character.char_type)
                if cidx >= 0:
                    self.char_type_combo.setCurrentIndex(cidx)
            if hasattr(self, 'char_style_combo'):
                sidx = self.char_style_combo.findText(character.style)
                if sidx >= 0:
                    self.char_style_combo.setCurrentIndex(sidx)

            if character.image_path:
                from pathlib import Path
                img_path = Path(character.image_path)
                if img_path.exists():
                    self.show_media_image(str(img_path), f"角色: {character.name}")
                else:
                    self._log(f"⚠️ 角色图片不存在: {character.image_path}")

            self._update_preview("character", character)
    
    def _on_char_filter_changed(self, index: int):
        """角色列表筛选切换：0=本集, 1=全部"""
        self._refresh_character_list()

    def _on_prop_filter_changed(self, index: int):
        """道具列表筛选切换：0=本集, 1=全部"""
        self._refresh_prop_list()

    def _on_scene_filter_changed(self, index: int):
        """场景列表筛选切换：0=本集, 1=全部"""
        self._refresh_scene_list()

    def _on_scene_priority_filter_changed(self, index: int):
        """场景优先级筛选切换"""
        self._refresh_scene_list()

    def _on_scene_category_filter_changed(self, index: int):
        """场景类型筛选切换"""
        self._refresh_scene_list()

    def _on_scene_type_filter_changed(self, index: int):
        """主/分场景筛选切换：0=全部, 1=主场景, 2=分场景"""
        self._refresh_scene_list()

    def _on_char_type_filter_changed(self, index: int):
        """角色类型筛选切换"""
        self._refresh_character_list()

    def _character_has_base_image(self, character) -> bool:
        """角色底模图是否已存在（用于勾选状态与「只选无底模图」）"""
        path = getattr(character, 'image_path', None)
        return bool(path and Path(path).exists())

    def _character_missing_costume_count(self, character) -> int:
        """该角色还没有造型图的造型数量（无任何造型也视为缺，返回1让按钮能选中它）"""
        costumes = getattr(character, 'costumes', None) or []
        if not costumes:
            return 1
        missing = 0
        for c in costumes:
            path = getattr(c, 'image_path', None)
            if not (path and Path(path).exists()):
                missing += 1
        return missing

    def _character_check_states(self) -> dict:
        """读取角色列表当前的勾选状态 {角色名: 是否勾选}（刷新时用于恢复）"""
        states = {}
        for i in range(self.character_list.count()):
            item = self.character_list.item(i)
            if item is None:
                continue
            name = item.data(Qt.ItemDataRole.UserRole)
            if name:
                states[name] = item.checkState() == Qt.CheckState.Checked
        return states

    def _set_all_characters_checked(self, checked: bool):
        """全选 / 全不选角色勾选框"""
        for i in range(self.character_list.count()):
            item = self.character_list.item(i)
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )

    def _on_select_all_characters(self):
        """全选角色勾选框"""
        self._set_all_characters_checked(True)

    def _on_deselect_all_characters(self):
        """全不选角色勾选框"""
        self._set_all_characters_checked(False)

    def _find_character_by_name(self, name: str):
        """按名称查找角色对象（本集 + 全局）"""
        if not self.project or not name:
            return None
        for c in (list(self.project.characters or [])
                  + list(getattr(self.project, 'global_characters', []) or [])):
            if c.name == name:
                return c
        return None

    def _select_characters_by(self, predicate):
        """按条件重置勾选：命中的勾上，其余取消（仅处理列表内已显示的角色）"""
        for i in range(self.character_list.count()):
            item = self.character_list.item(i)
            if item is None:
                continue
            char = self._find_character_by_name(item.data(Qt.ItemDataRole.UserRole))
            item.setCheckState(
                Qt.CheckState.Checked
                if (char is not None and predicate(char))
                else Qt.CheckState.Unchecked
            )

    def _on_select_characters_missing_base(self):
        """只选还没生成角色底模图的角色"""
        self._select_characters_by(lambda c: not self._character_has_base_image(c))

    def _on_select_characters_missing_costume(self):
        """只选还有造型未出图的角色（无任何造型的角色也会被选中，促使用户去定义造型）"""
        self._select_characters_by(lambda c: self._character_missing_costume_count(c) > 0)
    
    def _on_select_all_props(self):
        """全选道具（与角色列表一致）"""
        for i in range(self.prop_list.count()):
            item = self.prop_list.item(i)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
    
    def _on_deselect_all_props(self):
        """全不选道具（与角色列表一致）"""
        for i in range(self.prop_list.count()):
            item = self.prop_list.item(i)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
    
    def _on_select_props_missing_image(self):
        """只选还没生成图像的道具（与角色列表一致）"""
        if not self.project:
            return
        for i in range(self.prop_list.count()):
            item = self.prop_list.item(i)
            if not item:
                continue
            name = item.data(Qt.ItemDataRole.UserRole)
            if not name:
                continue
            prop = self._find_prop_by_name(name)
            has_img = getattr(prop, 'image_path', None) and Path(prop.image_path).exists() if prop else False
            item.setCheckState(Qt.CheckState.Checked if not has_img else Qt.CheckState.Unchecked)

    def _refresh_character_list(self):
        """刷新角色列表，支持'本集/全部'切换
        
        逻辑：
        - 未选集（current_episode == -1）：显示全部角色，筛选器默认"全部"
        - 已选集：显示本集角色/总数，筛选器默认"本集"，可切换查看全部
        """
        if not self.project:
            return

        # 记住刷新前选中的角色（用 item 上存的角色名，无需解析显示文本）
        prev_item = self.character_list.currentItem()
        prev_name = prev_item.data(Qt.ItemDataRole.UserRole) if prev_item else None
        # 勾选状态也要跨刷新保留：{} 表示还没填充过（首次填充按"默认需要出图"规则）
        prev_checked = self._character_check_states()

        self.character_list.clear()

        # 数据源：project.characters（load_project 已从 JSON 文件加载并同步）
        global_chars = getattr(self.project, 'characters', []) or []

        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        # 判断是否已选集：current_episode >= 0 表示已选集
        is_episode_selected = cur_ep >= 0
        is_episode_mode = episodes and len(episodes) > 1 and is_episode_selected

        # 获取筛选模式：0=本集, 1=全部
        # 未选集时默认显示全部，已选集时默认显示本集
        show_all = True
        if hasattr(self, 'char_filter_combo'):
            if is_episode_selected:
                # 已选集：默认显示本集（索引0），但用户可切换到全部（索引1）
                show_all = self.char_filter_combo.currentIndex() == 1
            else:
                # 未选集：强制显示全部
                if self.char_filter_combo.currentIndex() == 0:
                    self.char_filter_combo.setCurrentIndex(1)
                show_all = True

        ep_char_names = set()
        if is_episode_mode:
            ep_data = (getattr(self.project, 'episode_data', {}) or {}).get(str(cur_ep), {})
            ep_char_names = set(ep_data.get("character_names", []))
            # 如果 episode_data 为空，根据 episodes 字段筛选
            if not ep_char_names and global_chars:
                ep_title = episodes[cur_ep].get("title", f"第{cur_ep+1}集") if 0 <= cur_ep < len(episodes) else ""
                filtered = self._filter_assets_by_episodes_field(global_chars, ep_title)
                ep_char_names = set(c.name for c in filtered)
                # 降级：没匹配到则显示全部
                if not ep_char_names:
                    ep_char_names = set(c.name for c in global_chars)

        global_char_names = set(c.name for c in global_chars)

        # 决定显示哪些角色（去重）
        if show_all:
            # 显示全部全局角色
            display_chars = global_chars
        else:
            # 显示本集角色
            if is_episode_mode and ep_char_names:
                display_chars = [c for c in global_chars if c.name in ep_char_names]
            else:
                # 非剧集模式或无分集数据：优先显示 global_characters
                display_chars = global_chars
        
        # 去重：按角色名去重，保留第一个出现的
        seen_names = set()
        unique_chars = []
        for c in display_chars:
            if c.name not in seen_names:
                seen_names.add(c.name)
                unique_chars.append(c)
        display_chars = unique_chars

        for char in display_chars:
            # 应用类型筛选
            if hasattr(self, 'char_type_filter_combo'):
                type_filter_index = self.char_type_filter_combo.currentIndex()
                if type_filter_index > 0:
                    type_map = {1: "人类", 2: "动物/妖兽", 3: "群体", 4: "能量体"}
                    target_type = type_map.get(type_filter_index, "")
                    if getattr(char, 'char_type', '人类') != target_type:
                        continue
            
            tag = ""
            section_title = episodes[cur_ep].get("title", "").strip() if is_episode_mode and 0 <= cur_ep < len(episodes) else ""
            if is_episode_mode:
                if ep_char_names:
                    if char.name in ep_char_names:
                        tag = f" 📌{section_title}" if section_title else " 📌本集"
                    else:
                        tag = " 🔗非本集"
                else:
                    tag = f" 📌{section_title}" if section_title else " 📌本集"
            if char.name in global_char_names:
                tag += " 🌐全局"
            # 显示完整描述（截取前80个字符以保证列表可读性）
            if char.description and char.description.strip():
                desc_display = char.description[:80] + "..." if len(char.description) > 80 else char.description
            else:
                desc_display = "（暂无描述）"
            costume_cnt = len(getattr(char, 'costumes', None) or [])
            costume_tag = f" 👗×{costume_cnt}" if costume_cnt else ""
            base_tag = "✅底模" if self._character_has_base_image(char) else "底模"
            missing_costumes = self._character_missing_costume_count(char)
            if costume_cnt:
                cost_tag = f" ✅造型×{costume_cnt}" if not missing_costumes else f" ⬜造型缺{missing_costumes}"
            else:
                cost_tag = ""
            item = QListWidgetItem(f"👤 {char.name} - {desc_display}{tag}{costume_tag}")
            # 勾选框（✔）：勾选的角色才会被「🎨 生成角色图像 / 生成造型图像」处理，
            # 与分镜页的勾选列行为一致
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if char.name in prev_checked:
                checked = prev_checked[char.name]
            else:
                # 首次看到该角色：已有全套图的默认不勾（避免重复消耗），缺图的默认勾上
                checked = (not self._character_has_base_image(char)) or bool(missing_costumes)
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            # 存角色名，刷新后可恢复选中/勾选（也供「管理造型」定位角色）
            item.setData(Qt.ItemDataRole.UserRole, char.name)
            item.setToolTip(
                f"{base_tag}  {cost_tag}\n勾选后点「🎨 生成角色图像」只会生成勾选的角色"
            )
            self.character_list.addItem(item)

        # 刷新后恢复/自动选中一个角色：否则 clear() 会把 currentRow 置为 -1，
        # 「✏️ 更新角色」「👗 管理造型」会一直是灰的（点不了）
        count = self.character_list.count()
        action_btns = [
            getattr(self, 'update_char_btn', None),
            getattr(self, 'costume_btn', None),
        ]
        if count:
            target_row = 0
            if prev_name:
                for i in range(count):
                    if self.character_list.item(i).data(Qt.ItemDataRole.UserRole) == prev_name:
                        target_row = i
                        break
            self.character_list.setCurrentRow(target_row)
            for btn in action_btns:
                if btn is not None:
                    btn.setEnabled(True)
        else:
            for btn in action_btns:
                if btn is not None:
                    btn.setEnabled(False)

        self._update_flow_hint()
        self._update_stats()
    
    def _update_flow_hint(self):
        """更新角色页的制作流程提示条：根据当前项目状态，告诉用户「现在该做什么、下一步是什么」。"""
        label = getattr(self, 'flow_hint_label', None)
        if label is None:
            return
        if not self.project:
            label.setText("📖 制作流程：解析全剧资产 → 生成角色/道具/场景图片 → 解析集数分镜（可追加多集）→ 分镜页查看/重新解析分镜 → 生成图/视频 → 视频页合并成片")
            return

        episodes = getattr(self.project, 'episodes', [])
        is_episode_mode = bool(episodes) and len(episodes) > 1
        has_char = bool(self.project.characters)
        has_char_img = any(
            getattr(c, 'image_path', None)
            for c in self.project.characters
        )
        has_costume = any(
            getattr(c, 'costumes', None)
            for c in self.project.characters
        )
        has_costume_img = any(
            getattr(cst, 'image_path', None)
            for c in self.project.characters
            for cst in (getattr(c, 'costumes', None) or [])
        )

        if not is_episode_mode:
            # 非剧集模式：流程更简单
            if not has_char:
                label.setText("📖 流程：① 提取角色/场景 → ② 生成角色图 → ③（可选）导入造型表 → ④ 生成分镜")
            elif not has_char_img:
                label.setText("📖 当前步骤：点「🎨 生成角色图像」出角色底模；之后可在左侧「📥 批量导入造型并生成」导入造型表")
            elif not has_costume:
                label.setText("📖 下一步：左侧「📥 批量导入造型并生成」导入造型表并批量出图（可直接粘贴表格或导入 .md/.txt 文件，无需重新导入剧集）；完成后生成分镜")
            else:
                label.setText("📖 下一步：点「🎨 生成造型图像」出造型图（已有图自动跳过），然后「🎬 生成分镜」")
            return

        # 剧集模式
        if not has_char:
            label.setText(
                f"📖 制作流程（全剧{len(episodes)}集）：① 生成全局资产 → ② 生成图片 → ③ 导入造型表生成造型图 → ④ 选集做分镜 → ⑤ 视频页合并成片\n"
                "当前步骤：在故事页「🔧 处理故事」或「🔍 提取角色/场景/分镜」生成全局资产"
            )
        elif not has_char_img:
            label.setText(
                f"📖 制作流程（全剧{len(episodes)}集）：① 全局资产 ✅ → ② 生成图片 → ③ 造型表 → ④ 选集分镜 → ⑤ 视频页合并成片\n"
                "当前步骤：点右侧「🎨 生成角色图像」出角色底模（造型图会以底模 img2img 锚定面容）"
            )
        elif not has_costume:
            label.setText(
                f"📖 制作流程（全剧{len(episodes)}集）：① 全局资产 ✅ → ② 角色图 ✅ → ③ 造型表 → ④ 选集分镜 → ⑤ 视频页合并成片\n"
                "当前步骤：左侧「📥 批量导入造型并生成」→ 导入造型表文件（.md/.txt，无需重新导入剧集）→ 自动批量出造型图"
            )
        elif not has_costume_img:
            label.setText(
                f"📖 制作流程（全剧{len(episodes)}集）：① 全局资产 ✅ → ② 角色图 ✅ → ③ 造型数据 ✅ → ④ 选集分镜 → ⑤ 视频页合并成片\n"
                "当前步骤：点「🎨 生成造型图像」出造型图（已有图自动跳过），然后回故事页选集做分镜"
            )
        else:
            ep_data_map = getattr(self.project, 'episode_data', {}) or {}
            sb_done = sum(
                1 for i in range(len(episodes))
                if (ep_data_map.get(str(i), {}) or {}).get("storyboard")
            )
            if sb_done >= len(episodes):
                label.setText(
                    f"📖 制作流程（全剧{len(episodes)}集）：① 全局资产 ✅ → ② 角色图 ✅ → ③ 造型图 ✅ → ④ 本节分镜（{sb_done}/{len(episodes)}集已就绪）→ ⑤ 分镜图/视频\n"
                    "下一步：逐集生成「分镜图」和「视频」；切换剧集会直接调用已保存的分镜，无需重新生成"
                )
            else:
                label.setText(
                    f"📖 制作流程（全剧{len(episodes)}集）：① 全局资产 ✅ → ② 角色图 ✅ → ③ 造型图 ✅ → ④ 本节分镜（已就绪 {sb_done}/{len(episodes)} 集）→ ⑤ 视频页合并成片\n"
                    "下一步：分镜页点「📥 从文本重新解析分镜」生成该节分镜脚本（保存到分集文件）；"
                    "或回故事页选其他节做分镜"
                )
    
    def _on_add_scene(self):
        """添加场景"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或加载项目")
            return
        
        name, ok = QInputDialog.getText(self, "添加场景", "场景名称:")
        if not ok or not name:
            return
        
        desc, ok = QInputDialog.getText(self, "场景描述", "场景描述:")
        if not ok:
            desc = ""
        
        time_map = {"白天": "day", "夜晚": "night", "黄昏": "dusk", "早晨": "dawn"}
        time_text = self.scene_time_combo.currentText() if hasattr(self, 'scene_time_combo') else "白天"
        time_of_day = time_map.get(time_text, "day")
        
        scene = Scene(
            name=name,
            description=desc,
            time_of_day=time_of_day
        )
        
        # 保存到资产库
        if self.project and getattr(self.project, "asset_library_name", ""):
            try:
                from src.services.library_manager import load_library, save_library
                lib_data = load_library(self.project.asset_library_name)
                if lib_data:
                    scenes = lib_data.get("scenes", [])
                    scenes.append(scene)
                    save_library(
                        self.project.asset_library_name,
                        lib_data.get("characters", []),
                        lib_data.get("props", []),
                        scenes,
                        merge=False
                    )
                    self._log(f"✅ 已添加场景到资产库: {name}")
                else:
                    self._log(f"⚠️ 资产库为空，场景仅添加到项目内存")
                    self.project.scenes.append(scene)
            except Exception as e:
                self._log(f"⚠️ 保存到资产库失败: {e}，场景仅添加到项目内存")
                self.project.scenes.append(scene)
        else:
            self.project.scenes.append(scene)
            self._log(f"✅ 已添加场景: {name}（未绑定资产库）")
        
        self._refresh_scene_list()
    
    def _on_update_scene(self):
        """更新场景"""
        if not self.project:
            return
        
        row = self.scene_list.currentRow()
        if row < 0:
            return
        
        # 数据源：project.scenes（与 _refresh_scene_list 一致）
        scenes = self.project.scenes or []
        if row >= len(scenes):
            return
        scene = scenes[row]
        
        name, ok = QInputDialog.getText(self, "更新场景", "场景名称:", text=scene.name)
        if not ok or not name:
            return
        
        desc, ok = QInputDialog.getText(self, "场景描述", "场景描述:", text=scene.description)
        if not ok:
            desc = scene.description
        
        # 只更新有变化的字段
        updated_fields = []
        if name != scene.name:
            scene.name = name
            updated_fields.append("名称")
        if desc != scene.description:
            scene.description = desc
            updated_fields.append("描述")
        
        # 保存到资产库
        if self.project and getattr(self.project, "asset_library_name", ""):
            try:
                from src.services.library_manager import load_library, save_library
                lib_data = load_library(self.project.asset_library_name)
                if lib_data:
                    save_library(
                        self.project.asset_library_name,
                        lib_data.get("characters", []),
                        lib_data.get("props", []),
                        lib_data.get("scenes", []),
                        merge=False
                    )
            except Exception as e:
                self._log(f"⚠️ 保存到资产库失败: {e}")
        
        self._refresh_scene_list()
        
        if updated_fields:
            self._log(f"✅ 已更新场景 {scene.name} 的: {'、'.join(updated_fields)}")
        else:
            self._log(f"ℹ️ 场景 {scene.name} 未做任何修改")
    
    def _on_delete_scene(self):
        """删除场景"""
        if not self.project:
            return
        
        row = self.scene_list.currentRow()
        if row < 0:
            return
        
        # 数据源：project.scenes（与 _refresh_scene_list 一致）
        scenes = self.project.scenes or []
        if row >= len(scenes):
            return
        scene = scenes[row]
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除场景 '{scene.name}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            # 从资产库删除
            if self.project and getattr(self.project, "asset_library_name", ""):
                try:
                    from src.services.library_manager import load_library, save_library
                    lib_data = load_library(self.project.asset_library_name)
                    if lib_data:
                        scenes = lib_data.get("scenes", [])
                        scenes.pop(row)
                        save_library(
                            self.project.asset_library_name,
                            lib_data.get("characters", []),
                            lib_data.get("props", []),
                            scenes,
                            merge=False
                        )
                except Exception as e:
                    self._log(f"⚠️ 从资产库删除场景失败: {e}")
            
            # 也从项目内存中删除
            if scene in self.project.scenes:
                self.project.scenes.remove(scene)
            global_scenes = getattr(self.project, 'global_scenes', [])
            if scene in global_scenes:
                global_scenes.remove(scene)
            
            self._refresh_scene_list()
            self._clear_preview()
            self._log(f"🗑️ 已删除场景: {scene.name}")
    
    def _on_scene_selected(self, row: int):
        """选择场景（数据源必须和 _refresh_scene_list 一致，否则点击错位）"""
        if not self.project or row < 0:
            self.update_scene_btn.setEnabled(False)
            return

        # 与 _refresh_scene_list 使用一致的数据源
        global_scenes = getattr(self.project, 'scenes', []) or []

        show_all = hasattr(self, 'scene_filter_combo') and self.scene_filter_combo.currentIndex() == 1
        if show_all and global_scenes:
            scenes_list = global_scenes
        else:
            scenes_list = self.project.scenes

        if row < len(scenes_list):
            self.update_scene_btn.setEnabled(True)

            scene = scenes_list[row]
            if hasattr(self, 'scene_name_input'):
                self.scene_name_input.setText(scene.name)
            if hasattr(self, 'scene_desc_input'):
                self.scene_desc_input.setText(scene.description)

            self._update_preview("scene", scene)
    
    def _scene_check_states(self) -> dict:
        """读取场景列表当前的勾选状态 {场景名: 是否勾选}（刷新时用于恢复）"""
        states = {}
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            if item is None:
                continue
            name = item.data(Qt.ItemDataRole.UserRole)
            if name:
                states[name] = item.checkState() == Qt.CheckState.Checked
        return states

    def _get_checked_scene_names(self) -> list:
        """从场景列表勾选框中获取选中的场景名列表（生成时只处理这些场景）"""
        names = []
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            name = item.data(Qt.ItemDataRole.UserRole)
            if name:
                names.append(name)
        return names

    def _set_all_scenes_checked(self, checked: bool):
        """全选 / 全不选场景勾选框"""
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )

    def _on_select_all_scenes(self):
        """全选场景勾选框"""
        self._set_all_scenes_checked(True)

    def _on_deselect_all_scenes(self):
        """全不选场景勾选框"""
        self._set_all_scenes_checked(False)

    def _on_select_scenes_missing_image(self):
        """只选还没生成图片的场景"""
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            if item is None:
                continue
            scene = self._find_scene_by_name(item.data(Qt.ItemDataRole.UserRole))
            has_img = bool(
                scene is not None
                and getattr(scene, 'image_path', None)
                and Path(scene.image_path).exists()
            )
            item.setCheckState(
                Qt.CheckState.Unchecked if has_img else Qt.CheckState.Checked
            )

    def _find_scene_by_name(self, name: str):
        """按名称查找场景对象（本集 + 全局）"""
        if not self.project or not name:
            return None
        for s in (list(self.project.scenes or [])
                  + list(getattr(self.project, 'global_scenes', []) or [])):
            if s.name == name:
                return s
        return None
    
    def _on_delete_selected_scene_images(self):
        """删除已勾选场景的图像文件（保留场景条目）"""
        if not self.project:
            return
        
        checked_names = self._get_checked_scene_names()
        if not checked_names:
            QMessageBox.information(self, "提示", "请先勾选要删除图像的场景")
            return
        
        # 统计有图像的场景数
        scenes_with_img = []
        for name in checked_names:
            scene = self._find_scene_by_name(name)
            if scene and getattr(scene, 'image_path', None) and Path(scene.image_path).exists():
                scenes_with_img.append((name, scene.image_path))
        
        if not scenes_with_img:
            QMessageBox.information(self, "提示", "勾选的场景都没有生成图像")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {len(scenes_with_img)} 个场景的图像文件吗？\n"
            f"（场景条目会保留，只是删除图片，可以重新生成）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        deleted = 0
        for name, img_path in scenes_with_img:
            try:
                Path(img_path).unlink()
                # 清除 image_path
                scene = self._find_scene_by_name(name)
                if scene:
                    scene.image_path = None
                deleted += 1
            except Exception as e:
                self._log(f"删除场景 {name} 的图像失败: {e}")
        
        self._refresh_scene_list()
        if self.project_manager:
            self.project_manager.save_project(self.project)
        self._log(f"✅ 已删除 {deleted} 个场景的图像文件")
    
    def _on_delete_selected_scenes(self):
        """删除已勾选的场景条目及其图像文件"""
        if not self.project:
            return
        
        checked_names = self._get_checked_scene_names()
        if not checked_names:
            QMessageBox.information(self, "提示", "请先勾选要删除的场景")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {len(checked_names)} 个场景吗？\n"
            f"（包括场景条目和对应的图像文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        deleted = 0
        
        # 优先从资产库删除
        if self.project and getattr(self.project, "asset_library_name", ""):
            try:
                from src.services.library_manager import load_library, save_library
                lib_data = load_library(self.project.asset_library_name)
                if lib_data:
                    scenes = lib_data.get("scenes", [])
                    new_scenes = [s for s in scenes if s.name not in checked_names]
                    deleted = len(scenes) - len(new_scenes)
                    
                    # 删除图像文件
                    for s in scenes:
                        if s.name in checked_names:
                            if getattr(s, 'image_path', None) and Path(s.image_path).exists():
                                try:
                                    Path(s.image_path).unlink()
                                except Exception as e:
                                    self._log(f"删除场景 {s.name} 的图像失败: {e}")
                    
                    # 保存资产库
                    save_library(
                        self.project.asset_library_name,
                        lib_data.get("characters", []),
                        lib_data.get("props", []),
                        new_scenes,
                        merge=False
                    )
            except Exception as e:
                self._log(f"⚠️ 从资产库删除场景失败: {e}")
        
        # 也从项目内存中删除（如果存在）
        for name in checked_names:
            scene = self._find_scene_by_name(name)
            if scene:
                # 删除图像文件
                if getattr(scene, 'image_path', None) and Path(scene.image_path).exists():
                    try:
                        Path(scene.image_path).unlink()
                    except Exception as e:
                        self._log(f"删除场景 {name} 的图像失败: {e}")
                # 从项目列表中删除
                if hasattr(self.project, 'scenes') and scene in self.project.scenes:
                    self.project.scenes.remove(scene)
                global_scenes = getattr(self.project, 'global_scenes', [])
                if scene in global_scenes:
                    global_scenes.remove(scene)
                deleted += 1
        
        self._refresh_scene_list()
        self._clear_preview()
        self._log(f"✅ 已删除 {deleted} 个场景")

    def _refresh_scene_list(self):
        """刷新场景列表，支持'本集/全部'切换和主/分场景筛选
        
        逻辑：
        - 未选集（current_episode == -1）：显示全部场景，筛选器默认"全部"
        - 已选集：显示本集场景/总数，筛选器默认"本集"，可切换查看全部
        - 主场景和分场景分开统计和显示
        """
        if not self.project:
            return

        # 记住刷新前选中的场景（用 item 上存的场景名）
        prev_item = self.scene_list.currentItem()
        prev_name = prev_item.data(Qt.ItemDataRole.UserRole) if prev_item else None
        # 勾选状态跨刷新保留：{} 表示还没填充过（首次填充按"无图默认勾选"规则）
        prev_checked = self._scene_check_states()

        self.scene_list.clear()

        # 数据源：project.scenes（load_project 已从 JSON 文件加载并同步）
        global_scenes = getattr(self.project, 'scenes', []) or []

        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        # 判断是否已选集：current_episode >= 0 表示已选集
        is_episode_selected = cur_ep >= 0
        is_episode_mode = episodes and len(episodes) > 1 and is_episode_selected

        # 获取筛选模式：0=本集, 1=全部
        # 未选集时默认显示全部，已选集时默认显示本集
        show_all = False
        if hasattr(self, 'scene_filter_combo'):
            if is_episode_selected:
                # 已选集：默认显示本集（索引0），但用户可切换到全部（索引1）
                show_all = self.scene_filter_combo.currentIndex() == 1
            else:
                # 未选集：默认显示全部（索引1）
                if self.scene_filter_combo.currentIndex() == 0:
                    self.scene_filter_combo.setCurrentIndex(1)
                show_all = True

        # 获取主/分场景筛选模式：0=全部, 1=主场景, 2=分场景
        scene_type_filter = 0
        if hasattr(self, 'scene_type_filter_combo'):
            scene_type_filter = self.scene_type_filter_combo.currentIndex()

        ep_scene_names = set()
        ep_section = ""  # 当前节号（如 "1.1"），用于 Scene.episodes 兜底筛选
        if is_episode_mode:
            ep_data = (getattr(self.project, 'episode_data', {}) or {}).get(str(cur_ep), {})
            ep_scene_names = set(ep_data.get("scene_names", []))
            # 如果 episode_data 中无场景记录 → 本节无已知场景引用，留空
            # 不调用 _filter_assets_by_episodes_field：场景没有 episodes 字段时会返回全部
            if 0 <= cur_ep < len(episodes):
                ep_title = episodes[cur_ep].get("title", "").strip()
                ep_section = ep_title.split()[0] if ep_title else ""

        global_scene_names = set(s.name for s in global_scenes)

        # 决定显示哪些场景
        if show_all and global_scenes:
            display_scenes = global_scenes
        elif ep_scene_names and is_episode_mode and not show_all:
            display_scenes = [s for s in global_scenes if s.name in ep_scene_names]
        elif ep_section and is_episode_mode and not show_all:
            display_scenes = [
                s for s in global_scenes
                if s.episodes and ep_section in s.episodes
            ]
            if not display_scenes:
                display_scenes = []
        else:
            display_scenes = global_scenes

        # 去重：按场景名去重，保留第一个出现的
        seen_scene_names = set()
        unique_scenes = []
        for s in display_scenes:
            if s.name not in seen_scene_names:
                seen_scene_names.add(s.name)
                unique_scenes.append(s)
        display_scenes = unique_scenes

        # 统计主场景和分场景数量
        main_scene_count = 0
        sub_scene_count = 0
        added_count = 0
        
        # 先收集所有场景，按主场景分组
        main_scenes = []
        sub_scenes_map = {}  # main_scene_name -> [sub_scenes]
        orphan_sub_scenes = []  # 主场景已删除的分场景
        
        for scene in display_scenes:
            is_main = not getattr(scene, 'main_scene', '')
            if is_main:
                main_scenes.append(scene)
                main_scene_count += 1
                sub_scenes_map[scene.name] = []
            else:
                sub_scene_count += 1
                main_name = scene.main_scene
                if main_name in sub_scenes_map:
                    sub_scenes_map[main_name].append(scene)
                else:
                    orphan_sub_scenes.append(scene)
        
        # 按顺序添加：主场景 + 其分场景（缩进显示）
        for scene in main_scenes:
            # 应用主/分场景筛选
            if scene_type_filter == 1:
                # 只显示主场景
                pass
            elif scene_type_filter == 2:
                # 只显示分场景，跳过主场景
                continue
            
            # 应用优先级筛选
            if hasattr(self, 'scene_priority_filter_combo'):
                priority_filter_index = self.scene_priority_filter_combo.currentIndex()
                if priority_filter_index > 0:
                    priority_map = {1: "P0", 2: "P1", 3: "P2"}
                    target_priority = priority_map.get(priority_filter_index, "")
                    scene_priority = getattr(scene, 'priority', 'P1')
                    if not scene_priority.startswith(target_priority):
                        continue
            
            # 应用类型筛选
            if hasattr(self, 'scene_category_filter_combo'):
                category_filter_index = self.scene_category_filter_combo.currentIndex()
                if category_filter_index > 0:
                    category_map = {1: "室内", 2: "室外", 3: "地下", 4: "特殊空间"}
                    target_category = category_map.get(category_filter_index, "")
                    if getattr(scene, 'category', '') != target_category:
                        continue
            
            tag = ""
            scene_section_title = episodes[cur_ep].get("title", "").strip() if is_episode_mode and 0 <= cur_ep < len(episodes) else ""
            if is_episode_mode and ep_scene_names:
                if scene.name in ep_scene_names:
                    tag = f" 📌{scene_section_title}" if scene_section_title else " 📌本集"
                else:
                    tag = " 🔗非本集"
            # ep_scene_names 为空时（无已知场景引用），不标"本集"避免误导
            if scene.name in global_scene_names:
                tag += " 🌐全局"
            img_tag = " 🖼️" if (
                getattr(scene, 'image_path', None)
                and Path(scene.image_path).exists()
            ) else " ⬜"
            
            # 显示分场景数量
            sub_count = len(sub_scenes_map.get(scene.name, []))
            sub_info = f"（{sub_count}分场景）" if sub_count > 0 else ""
            
            item = QListWidgetItem(f"🏛️ {scene.name}{img_tag}{sub_info} - {scene.description[:30]}{tag}")
            item.setData(Qt.ItemDataRole.UserRole, scene.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if scene.name in prev_checked:
                checked = prev_checked[scene.name]
            else:
                checked = not (scene.image_path and Path(scene.image_path).exists())
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.scene_list.addItem(item)
            added_count += 1
            
            # 添加该主场景下的分场景（缩进显示）
            if scene_type_filter != 1:  # 不是只显示主场景
                for sub_scene in sub_scenes_map.get(scene.name, []):
                    # 应用优先级筛选
                    if hasattr(self, 'scene_priority_filter_combo'):
                        priority_filter_index = self.scene_priority_filter_combo.currentIndex()
                        if priority_filter_index > 0:
                            priority_map = {1: "P0", 2: "P1", 3: "P2"}
                            target_priority = priority_map.get(priority_filter_index, "")
                            scene_priority = getattr(sub_scene, 'priority', 'P1')
                            if not scene_priority.startswith(target_priority):
                                continue
                    
                    # 应用类型筛选
                    if hasattr(self, 'scene_category_filter_combo'):
                        category_filter_index = self.scene_category_filter_combo.currentIndex()
                        if category_filter_index > 0:
                            category_map = {1: "室内", 2: "室外", 3: "地下", 4: "特殊空间"}
                            target_category = category_map.get(category_filter_index, "")
                            if getattr(sub_scene, 'category', '') != target_category:
                                continue
                    
                    sub_tag = tag
                    sub_img_tag = " 🖼️" if (
                        getattr(sub_scene, 'image_path', None)
                        and Path(sub_scene.image_path).exists()
                    ) else " ⬜"
                    
                    # 分场景缩进显示
                    item = QListWidgetItem(f"  └─📍 {sub_scene.name}{sub_img_tag} - {sub_scene.description[:30]}{sub_tag}")
                    item.setData(Qt.ItemDataRole.UserRole, sub_scene.name)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    if sub_scene.name in prev_checked:
                        checked = prev_checked[sub_scene.name]
                    else:
                        checked = not (sub_scene.image_path and Path(sub_scene.image_path).exists())
                    item.setCheckState(
                        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                    )
                    self.scene_list.addItem(item)
                    added_count += 1
        
        # 显示孤立分场景（主场景已删除）
        if scene_type_filter != 1:
            for sub_scene in orphan_sub_scenes:
                # 应用筛选...
                sub_img_tag = " 🖼️" if (
                    getattr(sub_scene, 'image_path', None)
                    and Path(sub_scene.image_path).exists()
                ) else " ⬜"
                
                item = QListWidgetItem(f"  ⚠️📍 {sub_scene.name}{sub_img_tag}（主场景已删除）- {sub_scene.description[:30]}")
                item.setData(Qt.ItemDataRole.UserRole, sub_scene.name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                if sub_scene.name in prev_checked:
                    checked = prev_checked[sub_scene.name]
                else:
                    checked = not (sub_scene.image_path and Path(sub_scene.image_path).exists())
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                self.scene_list.addItem(item)
                added_count += 1

        # 恢复刷新前选中的场景；没有则选中第一项
        restore_row = -1
        for i in range(self.scene_list.count()):
            if self.scene_list.item(i).data(Qt.ItemDataRole.UserRole) == prev_name:
                restore_row = i
                break
        if restore_row < 0 and self.scene_list.count() > 0:
            restore_row = 0
        if restore_row >= 0:
            self.scene_list.setCurrentRow(restore_row)

        # 更新统计标签：主场景 | 分场景
        if hasattr(self, 'scene_stats_label'):
            self.scene_stats_label.setText(f"🏛️ 主场景: {main_scene_count} | 📍 分场景: {sub_scene_count}")

        title_parts = [f"共 {added_count} 个"]
        if is_episode_mode:
            ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()
            total_scenes = len(global_scenes) if global_scenes else len(self.project.scenes)
            title_parts.insert(0, f"本集 {len(ep_scene_names)}/{total_scenes} 个")
            title_parts.append(f"当前集: {ep_title}")
        if hasattr(self, "scene_list_group"):
            self.scene_list_group.setTitle(
                f"📋 场景列表（{' | '.join(title_parts)}）"
            )

        self._update_stats()
    
    def _on_generate_characters(self):
        """生成角色图像：只生成角色列表中勾选（✔）的角色，与分镜的勾选列行为一致"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        
        if not self.project.story:
            QMessageBox.warning(self, "警告", "请先完成故事")
            return
        
        # 只能选择性地生成：只处理角色列表里勾选的角色（✔）
        selected = self._get_checked_character_names()
        if not selected:
            QMessageBox.information(
                self, "提示",
                "请先在角色列表里勾选要生成的角色（每行前面的勾选框 ✔），"
                "再点「🎨 生成角色图像」。\n"
                "可用「✔ 全选」或「◻ 只选无底模图」快速勾选。"
            )
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)

        self._worker = ProductionWorker(
            self.producer, step='characters', selected_characters=selected
        )
        self._start_worker(self._worker, self._on_characters_generated)
        
        self._log(f"开始生成角色图像（已勾选 {len(selected)} 个角色）...")
    
    def _on_generate_scenes(self):
        """生成场景图像：只生成场景列表中勾选（✔）的场景，与角色/分镜的勾选行为一致"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        if not self.project.story:
            QMessageBox.warning(self, "警告", "请先完成故事")
            return

        # 选择性生成：只处理场景列表里勾选的场景（✔）
        selected = self._get_checked_scene_names()
        if not selected:
            QMessageBox.information(
                self, "提示",
                "请先在场景列表里勾选要生成的场景（每行前面的勾选框 ✔），"
                "再点「🖼️ 生成场景图像」。\n"
                "可用「✔ 全选」或「◻ 只选无图」快速勾选。"
            )
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)

        self._worker = ProductionWorker(
            self.producer, step='scenes', selected_scenes=selected
        )
        self._start_worker(self._worker, self._on_scenes_generated)

        self._log(f"开始生成场景图像（已勾选 {len(selected)} 个场景）...")
    
    def _on_add_storyboard(self):
        """添加分镜"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或加载项目")
            return
        
        number = len(self.project.storyboard) + 1
        storyboard = Storyboard(scene_number=number)
        self.project.storyboard.append(storyboard)
        self._refresh_storyboard_table()
        if self.project_manager:
            self.project_manager.save_project(self.project)
        self._log(f"✅ 已添加分镜 #{number}")
    
    def _on_delete_storyboard(self):
        """删除勾选的分镜（批量删除）"""
        if not self.project:
            return
        
        # 获取所有勾选的分镜索引
        checked = self._get_checked_storyboard_indices()
        
        if not checked:
            # 如果没有勾选，则删除当前光标选中的行
            row = self.storyboard_table.currentRow()
            if row < 0 or row >= len(self.project.storyboard):
                return
            
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定要删除分镜 #{row + 1} 吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.project.storyboard.pop(row)
                for i, sb in enumerate(self.project.storyboard):
                    sb.scene_number = i + 1
                self._refresh_storyboard_table()
                self._clear_preview()  # 清空预览
                if self.project_manager:
                    self.project_manager.save_project(self.project)
                self._log(f"🗑️ 已删除分镜 #{row + 1}")
        else:
            # 批量删除勾选的分镜
            count = len(checked)
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定要删除已勾选的 {count} 个分镜吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                # 从后往前删除，避免索引变化
                for row in sorted(checked, reverse=True):
                    if row < len(self.project.storyboard):
                        self.project.storyboard.pop(row)
                
                # 重新编号
                for i, sb in enumerate(self.project.storyboard):
                    sb.scene_number = i + 1
                
                self._refresh_storyboard_table()
                self._clear_preview()  # 清空预览
                if self.project_manager:
                    self.project_manager.save_project(self.project)
                self._log(f"🗑️ 已批量删除 {count} 个分镜")
    
    def _on_move_storyboard_up(self):
        """上移分镜"""
        if not self.project:
            return
        
        row = self.storyboard_table.currentRow()
        if row <= 0 or row >= len(self.project.storyboard):
            return
        
        self.project.storyboard[row], self.project.storyboard[row - 1] = \
            self.project.storyboard[row - 1], self.project.storyboard[row]
        
        for i, sb in enumerate(self.project.storyboard):
            sb.scene_number = i + 1
        
        self._refresh_storyboard_table()
        self.storyboard_table.selectRow(row - 1)
        if self.project_manager:
            self.project_manager.save_project(self.project)
        self._log(f"⬆️ 分镜 #{row + 1} 已上移")
    
    def _on_move_storyboard_down(self):
        """下移分镜"""
        if not self.project:
            return
        
        row = self.storyboard_table.currentRow()
        if row < 0 or row >= len(self.project.storyboard) - 1:
            return
        
        self.project.storyboard[row], self.project.storyboard[row + 1] = \
            self.project.storyboard[row + 1], self.project.storyboard[row]
        
        for i, sb in enumerate(self.project.storyboard):
            sb.scene_number = i + 1
        
        self._refresh_storyboard_table()
        self.storyboard_table.selectRow(row + 1)
        if self.project_manager:
            self.project_manager.save_project(self.project)
        self._log(f"⬇️ 分镜 #{row + 1} 已下移")
    
    def _get_selected_storyboard_indices(self) -> list:
        """从分镜表格的勾选框中获取选中的行索引列表。"""
        selected = []
        if not self.project:
            return selected
        for row in range(self.storyboard_table.rowCount()):
            widget = self._storyboard_cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    selected.append(row)
        return selected

    def _storyboard_cellWidget(self, row: int, col: int):
        """安全获取 cellWidget（兼容 _button_cell 包裹）。"""
        widget = self.storyboard_table.cellWidget(row, col)
        return widget

    def _on_select_all_storyboard(self):
        """全选分镜勾选框。"""
        for row in range(self.storyboard_table.rowCount()):
            widget = self.storyboard_table.cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb:
                    cb.setChecked(True)

    def _on_deselect_all_storyboard(self):
        """全不选分镜勾选框。"""
        for row in range(self.storyboard_table.rowCount()):
            widget = self.storyboard_table.cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb:
                    cb.setChecked(False)

    def _on_select_missing_image_storyboard(self):
        """只选未生成图片的分镜。"""
        if not self.project:
            return
        for row in range(self.storyboard_table.rowCount()):
            widget = self.storyboard_table.cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb and row < len(self.project.storyboard):
                    sb = self.project.storyboard[row]
                    has_image = bool(sb.frame_path and Path(sb.frame_path).exists())
                    cb.setChecked(not has_image)

    def _on_select_missing_video_storyboard(self):
        """只选未生成视频的分镜。"""
        if not self.project:
            return
        for row in range(self.storyboard_table.rowCount()):
            widget = self.storyboard_table.cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb and row < len(self.project.storyboard):
                    sb = self.project.storyboard[row]
                    has_video = bool(getattr(sb, 'video_path', None) and Path(sb.video_path).exists())
                    cb.setChecked(not has_video)

    def _get_checked_storyboard_indices(self) -> list[int]:
        """获取勾选的分镜行号列表。"""
        checked = []
        for row in range(self.storyboard_table.rowCount()):
            widget = self.storyboard_table.cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    checked.append(row)
        return checked

    def _on_delete_selected_storyboard_images(self):
        """删除已勾选分镜的帧图文件（保留分镜条目）。"""
        if not self.project:
            return
        checked = self._get_checked_storyboard_indices()
        if not checked:
            QMessageBox.warning(self, "提示", "请先勾选要删除帧图的分镜")
            return
        count = 0
        for row in checked:
            if row < len(self.project.storyboard):
                sb = self.project.storyboard[row]
                img_path = getattr(sb, 'frame_path', None) or getattr(sb, 'image_path', None)
                if img_path and Path(img_path).exists():
                    try:
                        Path(img_path).unlink()
                        sb.frame_path = None
                        count += 1
                    except Exception as e:
                        self._log(f"⚠️ 删除帧图失败 {img_path}: {e}")
        if count > 0:
            self._log(f"✅ 已删除 {count} 个分镜的帧图文件")
            self._refresh_storyboard_table()
            self.project_manager.save_project(self.project)

    def _on_delete_selected_storyboard_videos(self):
        """删除已勾选分镜的视频文件（保留分镜条目）。"""
        if not self.project:
            return
        checked = self._get_checked_storyboard_indices()
        if not checked:
            QMessageBox.warning(self, "提示", "请先勾选要删除视频的分镜")
            return
        count = 0
        for row in checked:
            if row < len(self.project.storyboard):
                sb = self.project.storyboard[row]
                vid_path = getattr(sb, 'video_path', None)
                if vid_path and Path(vid_path).exists():
                    try:
                        Path(vid_path).unlink()
                        sb.video_path = None
                        count += 1
                    except Exception as e:
                        self._log(f"⚠️ 删除视频失败 {vid_path}: {e}")
        if count > 0:
            self._log(f"✅ 已删除 {count} 个分镜的视频文件")
            self._refresh_storyboard_table()
            self.project_manager.save_project(self.project)

    def _on_storyboard_cell_edit(self, row: int, col: int):
        """编辑分镜单元格"""
        if col <= 1:
            return
        self.storyboard_table.editItem(self.storyboard_table.item(row, col))
    
    def _on_storyboard_cell_double_clicked(self, row: int, col: int):
        """双击分镜单元格：音频参数列弹出选择/修改框"""
        if col <= 1:
            return
        if not self.project or row >= len(self.project.storyboard):
            return
        
        sb = self.project.storyboard[row]
        
    def _on_storyboard_cell_changed(self, row: int, col: int):
        """分镜单元格变化"""
        if self._refreshing_table:
            return
        if col <= 1:
            return
        if not self.project or row >= len(self.project.storyboard):
            return
        
        sb = self.project.storyboard[row]
        item = self.storyboard_table.item(row, col)
        if not item:
            return
        
        value = item.text()
        if col == 2:
            # 第2列 = 所属场景名（Storyboard.scene，必须与场景列表名一致）
            sb.scene = value
        elif col == 3:
            sb.description = value
        elif col == 4:
            sb.characters = [c.strip() for c in value.split(",") if c.strip()]
        elif col == 5:
            sb.dialogue_role = value
        elif col == 6:
            sb.voice_text = value
            sb.dialogue = value
        elif col == 7:
            sb.action = value
        elif col == 8:
            sb.camera = value
        elif col == 9:
            sb.duration = Settings.normalize_video_duration(value)
        
        if self.project_manager:
            self.project_manager.save_project(self.project)
        self._update_storyboard_duration_summary()
    
    def _refresh_storyboard_table(self):
        """刷新分镜表格"""
        if not self.project:
            return
        
        self._refreshing_table = True
        try:
            self.storyboard_table.setRowCount(len(self.project.storyboard))
            
            for row, sb in enumerate(self.project.storyboard):
                # 第0列：勾选框
                select_cb = QCheckBox()
                has_image = bool(sb.frame_path and Path(sb.frame_path).exists())
                has_video = bool(getattr(sb, 'video_path', None) and Path(sb.video_path).exists())
                select_cb.setChecked(not has_image)
                img_tag = "✅图" if has_image else "⬜图"
                vid_tag = "✅视频" if has_video else "⬜视频"
                select_cb.setToolTip(f"{img_tag}  {vid_tag}")
                cb_wrap = QWidget()
                cb_lay = QHBoxLayout(cb_wrap)
                cb_lay.setContentsMargins(2, 2, 2, 2)
                cb_lay.addStretch()
                cb_lay.addWidget(select_cb)
                cb_lay.addStretch()
                self.storyboard_table.setCellWidget(row, 0, cb_wrap)

                self.storyboard_table.setItem(row, 1, QTableWidgetItem(str(sb.scene_number)))
                # 第2列：所属场景名（sb.scene）；镜头小标题（sb.scene_name）作为悬浮提示展示
                scene_item = QTableWidgetItem(sb.scene or "")
                scene_title = (getattr(sb, 'scene_name', '') or '').strip()
                if scene_title and scene_title != (sb.scene or ''):
                    scene_item.setToolTip(f"镜头标题：{scene_title}")
                self.storyboard_table.setItem(row, 2, scene_item)
                self.storyboard_table.setItem(row, 3, QTableWidgetItem(sb.description))
                self.storyboard_table.setItem(row, 4, QTableWidgetItem(", ".join(sb.characters)))
                self.storyboard_table.setItem(row, 5, QTableWidgetItem(sb.dialogue_role))
                self.storyboard_table.setItem(row, 6, QTableWidgetItem(sb.voice_text or sb.dialogue))
                self.storyboard_table.setItem(row, 7, QTableWidgetItem(sb.action))
                self.storyboard_table.setItem(row, 8, QTableWidgetItem(sb.camera))
                self.storyboard_table.setItem(row, 9, QTableWidgetItem(str(sb.duration)))
                
                # 设置单元格文本换行
                for col in range(10):
                    item = self.storyboard_table.item(row, col)
                    if item:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            
            # 自动调整行高以显示完整内容
            self.storyboard_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            # 设置最小行高
            self.storyboard_table.verticalHeader().setMinimumSectionSize(60)
        finally:
            self._refreshing_table = False
        self._update_storyboard_duration_summary()
        # 更新统计
        self._update_stats()
    def _update_storyboard_duration_summary(self):
        """更新分镜时长合计，并与当前故事长度目标进行对比。"""
        if not hasattr(self, "storyboard_duration_label"):
            return
        total_seconds = sum(
            Settings.normalize_video_duration(sb.duration)
            for sb in (self.project.storyboard if self.project else [])
        )
        target_seconds = {
            "very_short": 180,
            "short": 300,
            "medium": 600,
            "long": 1200,
        }.get(getattr(self.project, "length", ""), 300)

        def format_duration(seconds: float) -> str:
            minutes = int(seconds // 60)
            remaining = seconds - minutes * 60
            return f"{minutes}分{remaining:04.1f}秒"

        difference = total_seconds - target_seconds
        if abs(difference) < 0.05:
            status = "已达到目标"
        elif difference < 0:
            status = f"还差 {format_duration(-difference)}"
        else:
            status = f"超出 {format_duration(difference)}"
        self.storyboard_duration_label.setText(
            f"分镜总时长：{format_duration(total_seconds)}"
            f" ｜目标：{format_duration(target_seconds)} ｜{status}"
        )
    
    def _on_storyboard_selected(self, row: int, col: int, previous_row: int, previous_col: int):
        """选择分镜"""
        self._log(f"🔍 分镜选择事件: row={row}, col={col}, previous_row={previous_row}")
        if not self.project or row < 0:
            return
        
        if row < len(self.project.storyboard):
            sb = self.project.storyboard[row]
            self._log(f"📋 选中分镜 #{sb.scene_number}: {sb.scene_name}, frame_path={sb.frame_path}")
            # 更新预览
            self._update_preview("storyboard", sb)
    
    def _update_preview(self, item_type: str, item):
        """更新预览区域（和视频页一样的逻辑）"""
        self._log(f"🔍 _update_preview 被调用: item_type={item_type}")
        
        # 同步更新上方的图片预览区（角色图/场景图/分镜首尾帧图，与下方视频对比）
        if hasattr(self, '_media_panel') and hasattr(self._media_panel, 'set_preview_images'):
            try:
                self._update_image_preview(item_type, item)
            except Exception as exc:
                self._log(f"⚠️ 更新图片预览区失败: {exc}")
        
        image_path = None
        video_path = None
        info_text = ""
        
        if item_type == "character":
            image_path = getattr(item, 'image_path', None)
            info_text = f"角色: {item.name}"
        elif item_type == "prop":
            image_path = getattr(item, 'image_path', None)
            info_text = f"道具: {getattr(item, 'name', '')}"
        elif item_type == "scene":
            image_path = getattr(item, 'image_path', None)
            info_text = f"场景: {item.name}"
        elif item_type == "storyboard":
            image_path = getattr(item, 'frame_path', None)
            video_path = getattr(item, 'video_path', None)
            self._log(f"🔍 预览检查: frame_path={image_path}, video_path={video_path}")
            info_text = f"分镜 #{item.scene_number}: {item.scene or item.scene_name}"
        
        # 优先显示视频
        if video_path and Path(video_path).exists():
            self._log(f"🎬 显示视频: {video_path}")
            # 统一使用右侧共享媒体面板播放视频
            if self.media_player.videoOutput() != self.media_video_widget:
                self.media_player.setVideoOutput(self.media_video_widget)
            self.media_display_layout.setCurrentWidget(self.media_video_widget)
            self.media_display_label.hide()
            self.media_video_widget.show()
            self.media_info_label.setText(f"{info_text} (视频)")
            # 播放前强制检查音量，防止 QAudioOutput 漂移为 0 导致无声
            if hasattr(self, 'media_audio_output'):
                try:
                    if self.media_audio_output.volume() < 0.01:
                        self.media_audio_output.setVolume(1.0)
                        self._log("🔊 检测到音频输出音量为 0，已恢复为 100%")
                except Exception:
                    pass
            self.media_player.stop()
            self.media_player.setSource(QUrl.fromLocalFile(str(Path(video_path).resolve())))
            from PyQt6.QtCore import QTimer
            # 启动前强制恢复音量（双保险）
            if hasattr(self, 'media_audio_output'):
                try:
                    if self.media_audio_output.volume() < 0.01:
                        self.media_audio_output.setVolume(1.0)
                        self._log("🔊 检测到音频输出音量为 0，已恢复为 100%")
                except Exception:
                    pass
            QTimer.singleShot(300, lambda: self.media_player.play())
            return
        
        # 显示图片
        if image_path and Path(image_path).exists():
            self._log(f"🖼️ 显示图片: {image_path}")
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                self.media_player.stop()
                self.media_display_label.setPixmap(pixmap)
                self.media_display_layout.setCurrentWidget(self.media_display_label)
                self.media_display_label.show()
                self.media_video_widget.hide()
                self.media_info_label.setText(info_text)
            else:
                self.media_display_label.setText("图片加载失败")
        else:
            self.media_display_label.setText("暂无预览")
    
    def _update_image_preview(self, item_type: str, item):
        """更新媒体面板上方的图片预览区（最多两张图，与下方视频对比）。

        规则：
        - 角色页：显示角色图
        - 场景页：显示场景图
        - 分镜页：有首帧+尾帧图则显示两张帧图；否则显示 角色图 + 首/尾帧图
        """
        from pathlib import Path

        panel = self._media_panel

        if item_type == "character":
            image_path = getattr(item, 'image_path', None)
            panel.set_preview_images(
                image_path, caption1=f"角色: {getattr(item, 'name', '')}"
            )
        elif item_type == "prop":
            image_path = getattr(item, 'image_path', None)
            panel.set_preview_images(
                image_path, caption1=f"道具: {getattr(item, 'name', '')}"
            )
        elif item_type == "scene":
            image_path = getattr(item, 'image_path', None)
            panel.set_preview_images(
                image_path, caption1=f"场景: {getattr(item, 'name', '')}"
            )
        elif item_type == "storyboard":
            frame_path = getattr(item, 'frame_path', None)
            last_frame_path = self._find_storyboard_last_frame(item)

            # 情况1：首帧和尾帧图都存在 -> 显示两张帧图
            if (
                frame_path and last_frame_path
                and Path(frame_path).exists() and Path(last_frame_path).exists()
            ):
                panel.set_preview_images(frame_path, last_frame_path, "首帧", "尾帧")
                return

            # 情况2：只有单帧图 -> 显示 角色图 + 首/尾帧图
            frame_to_show = None
            frame_caption = ""
            if frame_path and Path(frame_path).exists():
                frame_to_show, frame_caption = frame_path, "首帧"
            elif last_frame_path and Path(last_frame_path).exists():
                frame_to_show, frame_caption = last_frame_path, "尾帧"

            character_image = self._find_storyboard_character_image(item)
            char_name = self._storyboard_main_character_name(item)

            if frame_to_show and character_image:
                panel.set_preview_images(
                    character_image, frame_to_show, f"角色: {char_name}", frame_caption
                )
            elif frame_to_show:
                panel.set_preview_images(frame_to_show, caption1=frame_caption)
            elif character_image:
                panel.set_preview_images(character_image, caption1=f"角色: {char_name}")
            else:
                panel.set_preview_images(None, None)

    def _find_storyboard_last_frame(self, sb):
        """查找分镜的尾帧图（约定文件名 frame_XXX_last.png，可能不存在）"""
        from pathlib import Path

        frame_path = getattr(sb, 'frame_path', None)
        if not frame_path:
            return None
        frame_file = Path(frame_path)
        candidate = frame_file.parent / f"frame_{sb.scene_number:03d}_last.png"
        if candidate.exists():
            return str(candidate)
        return None

    def _storyboard_main_character_name(self, sb) -> str:
        """取分镜第一个角色名"""
        characters = getattr(sb, 'characters', None) or []
        return characters[0] if characters else ""

    def _find_storyboard_character_image(self, sb):
        """取分镜第一个角色的图片路径（存在时返回，否则 None）"""
        from pathlib import Path

        characters = getattr(sb, 'characters', None) or []
        if not characters or not getattr(self, 'project', None):
            return None
        for char in self.project.characters:
            if char.name == characters[0] and char.image_path:
                if Path(char.image_path).exists():
                    return char.image_path
                return None
        return None
    
    def _show_video_preview_fallback(self, video_path: str):
        """备用视频预览方案（不依赖cv2）- 在右侧预览框播放"""
        from PyQt6.QtCore import QTimer
        
        # 统一使用右侧共享媒体面板播放
        self.media_player.stop()
        video_url = QUrl.fromLocalFile(str(Path(video_path).resolve()))
        if self.media_player.videoOutput() != self.media_video_widget:
            self.media_player.setVideoOutput(self.media_video_widget)
            self._log("📹 重新设置视频输出控件")
        self.media_display_layout.setCurrentWidget(self.media_video_widget)
        self.media_display_label.hide()
        self.media_video_widget.show()

        # 播放前强制检查音量，防止 QAudioOutput 漂移为 0 导致无声
        if hasattr(self, 'media_audio_output'):
            try:
                if self.media_audio_output.volume() < 0.01:
                    self.media_audio_output.setVolume(1.0)
                    self._log("🔊 检测到音频输出音量为 0，已恢复为 100%")
            except Exception:
                pass
        
        self.media_player.setSource(video_url)
        self._log(f"🎬 右侧预览播放器已加载分镜视频: {Path(video_path).name}")
        
        # 添加错误处理
        if not hasattr(self, '_preview_error_connected'):
            self.media_player.errorOccurred.connect(self._on_preview_error)
            self.media_player.playbackStateChanged.connect(self._on_preview_state_changed)
            self._preview_error_connected = True
        
        
        # 延迟播放，确保视频控件已完全初始化
        QTimer.singleShot(200, lambda: self._start_video_preview(video_path))
        
    def _start_video_preview(self, video_path: str):
        """延迟开始播放视频预览"""
        self._log("▶ 自动开始播放分镜视频")
        self.media_player.play()

    def _on_preview_error(self, error):
        """预览播放器错误处理"""
        self._log(f"⚠️ 预览播放错误: {error}")

    def _on_preview_state_changed(self, state):
        """预览播放器状态变化"""
        from PyQt6.QtMultimedia import QMediaPlayer
        state_names = {
            QMediaPlayer.PlaybackState.StoppedState: "停止",
            QMediaPlayer.PlaybackState.PlayingState: "播放中",
            QMediaPlayer.PlaybackState.PausedState: "暂停",
        }
        self._log(f"📹 预览播放状态: {state_names.get(state, str(state))}")

    def _clear_preview(self, placeholder_text: str = "暂无预览"):
        """清除预览"""
        self.media_display_label.setText(placeholder_text)
        self.media_display_label.setStyleSheet("""
            QLabel {
                background-color: #f1f5f9;
                border: 2px dashed #cbd5e1;
                border-radius: 8px;
                color: #94a3b8;
                font-size: 14px;
            }
        """)
        if hasattr(self, 'preview_info_label'):
            self.preview_info_label.setText("")
    
    def _on_generate_episode_storyboard(self):
        """从文本重新解析分镜：按当前节内容与场景生成，场景名与全局资产保持一致。

        流程：
        1. 检查全局资产（角色/场景）—— 没有则引导先生成资产
        2. 有资产时，按剧集文件「场景：」行解析预设分镜（跳过 AI 生成）
        3. 场景名对齐全局场景列表，缺失时新建场景条目
        4. 保存分镜快照并跳转到分镜页
        """
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        episodes = getattr(self.project, 'episodes', [])
        if not episodes or len(episodes) <= 1:
            QMessageBox.warning(
                self, "提示", "当前项目不是剧集模式（未导入分集列表），\n请先在故事页「导入剧集文件」后再生成本节分镜。"
            )
            return
        cur_ep = getattr(self.project, 'current_episode', -1)
        if not (0 <= cur_ep < len(episodes)):
            QMessageBox.warning(
                self, "提示", "请先在「当前集」下拉框中选择要制作的剧集，\n分镜将按所选集（节）的内容生成。"
            )
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)

        ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()

        # ① 检查全局资产
        assets_ok = (
            self.project.characters
            and self.project.scenes
        )
        if not assets_ok:
            ret = QMessageBox.question(
                self, "⚠️ 缺少全局资产",
                f"当前集 [{ep_title}] 尚未生成全局资产（角色/场景/道具）。\n\n"
                f"是否需要先前往「项目页」生成全局资产？\n"
                f"生成完成后，再按文件预设场景转成分镜。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret == QMessageBox.StandardButton.Yes:
                self._switch_to_page(0)
            return

        # ② 按文件解析预设分镜（跳过 AI 生成）：
        #    优先「E0X-S0Y-C0Z」分镜表格式（逆天布衣_剧本.md 一类），
        #    回退旧版「场景：」行格式（1-3.md / 4-7.md / 8-10.md）
        from src.services.episode_splitter import is_storyboard_script  # noqa: F401 (保留：供日志/判断复用)
        storyboards = None
        ep_text = (episodes[cur_ep].get("text") or "").strip()
        if re.search(r"\|\s*E\d+-S\d+-C\d+", ep_text):
            try:
                storyboards = self.producer.import_section_storyboard(cur_ep)
            except ValueError as e:
                QMessageBox.warning(self, "解析失败", str(e))
                return
        if storyboards is None:
            try:
                storyboards = self.producer.parse_episode_storyboard(cur_ep)
            except ValueError:
                storyboards = None
        if not storyboards:
            QMessageBox.information(
                self, "提示",
                "未从当前集文本中解析到分镜表或「场景：」行，无法生成分镜。"
                "请确认该集文本含「镜号 E0X-S0Y-C0Z」分镜表或「场景：xxx」行。"
            )
            return

        # ③ 确认导入
        scene_names = [sb.scene_name for sb in storyboards]
        ret = QMessageBox.question(
            self, "📋 转成分镜",
            f"当前集 [{ep_title}] 已在全局资产中，检测到文件预设场景：\n\n"
            + "\n".join(f"  {i+1}. {n}" for i, n in enumerate(scene_names))
            + f"\n\n是否按文件场景导入分镜（跳过 AI 生成，共 {len(storyboards)} 个场景）？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self.project.storyboard = storyboards
        self.producer._save_episode_snapshot(cur_ep)
        self._refresh_storyboard_table()
        self._update_stats()
        self._switch_to_page(4)
        QMessageBox.information(
            self, "导入完成",
            f"已为 [{ep_title}] 导入 {len(storyboards)} 个分镜条目：\n"
            + "\n".join(
                f"  {sb.scene_number}. {sb.scene_name} — {sb.duration}秒"
                for sb in storyboards
            )
            + "\n\n后续请在「分镜页」生成视频，完成后到「视频页」合并成片。",
        )
        self._log(f"✅ [{ep_title}] 按文件场景导入分镜完成，共 {len(storyboards)} 个")

    def _on_episode_storyboard_generated(self, result: dict):
        """本节分镜生成完成：刷新界面并恢复当前集数据"""
        count = (result or {}).get('episode_storyboard', 0)
        cur_ep = getattr(self.project, 'current_episode', -1)
        episodes = getattr(self.project, 'episodes', [])
        ep_title = (
            (episodes[cur_ep].get("title") if 0 <= cur_ep < len(episodes) else "")
            or f"第{cur_ep+1}集"
        )
        self._log(f"✅ 本节 [{ep_title}] 分镜生成完成：{count} 个，已保存到分集文件")

        # 恢复当前集的界面状态（引用 + 分镜直接来自已保存的快照/分集文件）
        if 0 <= cur_ep < len(episodes):
            self._load_episode_data(cur_ep)
        self._refresh_character_list()
        self._refresh_prop_list()
        self._refresh_scene_list()
        self._refresh_storyboard_table()
        self._update_stats()
        self._mark_unsaved()

        QMessageBox.information(
            self, "✅ 本节分镜完成",
            f"[{ep_title}] 分镜生成完成：共 {count} 个分镜。\n\n"
            f"下一步：在分镜页生成「分镜图」和「视频」。"
        )

    def _on_generate_storyboard(self):
        """生成分镜（按当前选择的集数生成分镜，而非所有场景）"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        if not self.project.story:
            QMessageBox.warning(self, "警告", "请先完成故事")
            return

        # 剧集模式下，必须先选择一集才能生成分镜
        episodes = getattr(self.project, 'episodes', [])
        if episodes and len(episodes) > 1:
            cur_ep = getattr(self.project, 'current_episode', -1)
            if cur_ep < 0 or cur_ep >= len(episodes):
                QMessageBox.warning(
                    self, "提示",
                    "请先在「当前集」下拉框中选择要制作的剧集，\n"
                    "分镜将按所选集数的内容生成。"
                )
                return
            ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()
            self._log(f"📺 按当前集 [{ep_title}] 生成分镜")

        # 检查资产/图片生成状态
        assets_ok = getattr(self.project, 'assets_generated', False)
        images_ok = getattr(self.project, 'images_generated', False)
        if not (assets_ok and images_ok):
            missing = []
            if not assets_ok:
                missing.append("全局资产（角色/道具/场景）")
            if not images_ok:
                missing.append("图片（角色图/道具图/场景图）")
            self._log(
                f"⚠️ 尚未生成完：{', '.join(missing)}。"
                f"可查看分镜，但生成分镜图前需先生成资产和图片。"
            )

        if not self.project.characters:
            QMessageBox.warning(self, "警告", "请先提取角色")
            return

        if not self.project.scenes:
            QMessageBox.warning(self, "警告", "请先提取场景")
            return
        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)
        
        # 没有分镜脚本时，先根据当前场景数量生成脚本
        if not self.project.storyboard:
            self._worker = ProductionWorker(self.producer, step='storyboard_script')
            self._start_worker(self._worker, self._on_storyboard_generated)
            self._log("开始根据场景生成分镜脚本...")
            return

        # 已有分镜脚本 → 从表格勾选框读取选中的分镜
        selected = self._get_selected_storyboard_indices()
        if not selected:
            QMessageBox.information(self, "提示", "请在分镜列表中勾选要生成的分镜（✔列），然后再点击生成分镜。")
            return
        self._worker = ProductionWorker(self.producer, step='storyboard', selected_indices=selected)
        self._start_worker(self._worker, self._on_storyboard_generated)
        self._log(f"开始生成 {len(selected)} 个分镜图片...")
    
    def _start_storyboard_image_generation(self):
        """直接启动分镜图片生成（跳过资产检查，分镜已存在时使用）"""
        if not self.project or not self.project.storyboard:
            QMessageBox.warning(self, "警告", "请先生成分镜脚本")
            return

        # 软提醒：如果没有角色图/场景图，提醒用户但允许继续
        images_ok = getattr(self.project, 'images_generated', False)
        if not images_ok:
            ret = QMessageBox.question(
                self, "提示",
                "尚未生成角色图/道具图/场景图，\n分镜图片质量可能不佳。\n\n是否继续生成？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        if not self.producer:
            self.producer = AnimationProducer(self.client, self.project, self._log)

        # 从表格勾选框读取选中的分镜
        selected = self._get_selected_storyboard_indices()
        if not selected:
            QMessageBox.information(self, "提示", "请在分镜列表中勾选要生成的分镜（✔列），然后再点击生成分镜。")
            return
        self._worker = ProductionWorker(self.producer, step='storyboard', selected_indices=selected)
        self._start_worker(self._worker, self._on_storyboard_generated)
        self._log(f"开始生成 {len(selected)} 个分镜图片...")
    
    def _on_generate_video(self):
        """生成视频"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        
        if not self.project.storyboard:
            QMessageBox.warning(self, "警告", "请先生成分镜")
            return
        
        # 剧集模式 + reference/auto 模式：必须要有角色才能图生图
        is_episode_mode = bool(getattr(self.project, 'episodes', None))
        user_mode = Settings.AGNES_VIDEO_MODE
        effective_mode = user_mode
        if user_mode == "auto":
            effective_mode = "reference" if is_episode_mode else "text"
        if effective_mode == "reference" and is_episode_mode:
            all_chars = list(self.project.characters or [])
            global_chars = getattr(self.project, 'global_characters', []) or []
            if not all_chars and not global_chars:
                QMessageBox.warning(
                    self,
                    "警告",
                    "剧集模式下使用参考图生成（reference）必须要有角色！\n\n"
                    "请先「处理故事」或「解析全剧资产」提取角色，并生成角色图片。\n"
                    "或者将生成模式切换为 text（纯文本生成，不需要角色参考图）。"
                )
                return
        
        # 检查分镜是否有帧图像
        frames_count = sum(1 for sb in self.project.storyboard if sb.frame_path)
        if frames_count == 0:
            QMessageBox.warning(
                self, 
                "警告", 
                "分镜还没有帧图像，请先生成分镜帧！\n\n"
                "点击'🎬 生成分镜'按钮生成帧图像。"
            )
            return
        
        # 从表格勾选框读取选中的分镜
        selected = self._get_selected_storyboard_indices()
        if not selected:
            QMessageBox.information(self, "提示", "请在分镜列表中勾选要生成视频的分镜（✔列），然后再点击生成视频。")
            return

        # 使用 ProductionWorker 异步生成
        self._worker = ProductionWorker(self.producer, step='video', selected_indices=selected)
        self._start_worker(self._worker, self._on_video_generated)
        
        self._log(f"开始生成 {len(selected)} 个分镜视频...")
    
    def _ensure_ffmpeg(self, context="FFmpeg", retry=None) -> bool:
        """FFmpeg 预检：存在返回 True；缺失时 Offer 一键自动安装。

        安装成功且提供了 retry，则自动重试一次调用方的操作。
        """
        if _mt.find_ffmpeg(refresh=True):
            return True
        self._warn_ffmpeg_missing(context, retry=retry)
        return False

    def _warn_ffmpeg_missing(self, context="FFmpeg", retry=None):
        """弹出「FFmpeg 未安装」对话框：自动下载 / 打开目录 / 取消。"""
        hint = _mt.install_hint()
        msg = QMessageBox(self)
        msg.setWindowTitle("FFmpeg 未安装")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText("「%s」需要 FFmpeg，但未在系统中检测到。" % context)
        msg.setInformativeText("是否自动下载安装 FFmpeg 静态构建？")
        msg.setDetailedText(hint)
        btn_install = msg.addButton("⬇ 安装 FFmpeg", QMessageBox.ButtonRole.AcceptRole)
        btn_open = msg.addButton("📁 打开文件夹", QMessageBox.ButtonRole.ActionRole)
        btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)

        worker_ref = {"w": None}
        progress_ref = {"pd": None}

        def start_install():
            btn_install.setEnabled(False)
            btn_install.setText("下载中…")
            worker = FfmpegInstallWorker()
            worker_ref["w"] = worker
            pd = QProgressDialog("正在下载/安装 FFmpeg…", "取消", 0, 100, self)
            progress_ref["pd"] = pd
            pd.setWindowModality(Qt.WindowModality.ApplicationModal)
            pd.setMinimumDuration(0)
            pd.setMinimumWidth(460)

            def on_progress(done, total, label):
                total = total or 100
                if pd.maximum() != total:
                    pd.setRange(0, total)
                pd.setValue(done)
                pd.setLabelText("%s （%d / %d）" % (label, done, total))

            def on_finished(ok, detail):
                try:
                    pd.close()
                except Exception:
                    pass
                try:
                    btn_install.setEnabled(True)
                    btn_install.setText("⬇ 安装 FFmpeg")
                except RuntimeError:
                    pass  # 按钮已被销毁，忽略
                # 刷新 locator 缓存，让后续 find_ffmpeg(refresh=True) 立即可见
                _mt.find_ffmpeg(refresh=True)
                _mt.find_ffprobe(refresh=True)
                if ok:
                    QMessageBox.information(self, "FFmpeg 就绪",
                                            "FFmpeg 安装完成！\\n" + detail)
                    if retry:
                        try:
                            retry()
                        except Exception as e:
                            self._log("重试 %s 失败: %s" % (context, e))
                elif detail == "已取消安装 FFmpeg":
                    pass  # 用户取消，静默
                else:
                    QMessageBox.warning(self, "安装失败",
                                        "FFmpeg 安装失败，无法执行「%s」。" % context)
                    # 再次把 hint 显示给用户，方便手动安装
                    QMessageBox.warning(self, "安装指引", hint)

            def on_pd_cancel():
                worker.cancel()

            pd.canceled.connect(on_pd_cancel)
            worker.progress.connect(on_progress)
            worker.finished_install.connect(on_finished)
            # 登记强引用，防止运行中被 GC 回收（同一类闪退风险）
            self._live_workers.add(worker)
            worker.finished_install.connect(
                lambda *a, w=worker: self._live_workers.discard(w))
            pd.show()
            worker.start()

        def open_folder():
            try:
                _mt.open_dir(_mt.ffmpeg_dir())
            except Exception as e:
                QMessageBox.warning(self, "错误", "无法打开文件夹: %s" % e)

        btn_install.clicked.connect(start_install)
        btn_open.clicked.connect(open_folder)
        msg.exec()

    def _on_install_finished(self, ok, msg, retry=None, context="FFmpeg"):
        """安装结束的兜底处理（供外部信号直连时使用）。"""
        _mt.find_ffmpeg(refresh=True)
        _mt.find_ffprobe(refresh=True)
        if ok:
            QMessageBox.information(self, "FFmpeg 就绪", "FFmpeg 安装完成！\\n%s" % msg)
            if retry:
                retry()
        elif msg != "已取消安装 FFmpeg":
            QMessageBox.warning(self, "安装失败", "FFmpeg 安装失败，无法执行「%s」。" % context)

    def _on_merge_video(self):
        """合并视频"""
        if not self.project:
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return
        
        # 检查是否有视频文件
        video_count = sum(1 for sb in self.project.storyboard if sb.video_path)
        if video_count == 0:
            QMessageBox.warning(self, "警告", "没有可合并的视频，请先生成视频")
            return

        # 合并视频需要 FFmpeg：缺失时 Offer 一键安装
        if not self._ensure_ffmpeg("合并视频", retry=lambda: self._on_merge_video()):
            return

        
        # 使用 ProductionWorker 异步合并
        self._worker = ProductionWorker(self.producer, step='merge_video')
        self._start_worker(self._worker, self._on_merge_video_finished)
        
        self._log(f"开始合并 {video_count} 个视频...")
    
    def _on_agnes_mode_changed(self, mode):
        """AGNES 视频生成模式改变"""
        Settings.AGNES_VIDEO_MODE = mode
        Settings.save_user_config()

    def _on_production_progress(self, message: str, current: int, total: int):
        """制作进度"""
        self._log(f"进度: {message} ({current}/{total})")
        
        # 更新状态栏进度
        if hasattr(self, 'production_status'):
            pct = int((current / total) * 100) if total > 0 else 0
            self.production_status.setText(f"制作进度: {pct}%")
        
    def _on_production_error(self, error_msg: str):
        """制作错误"""
        # 恢复按钮
        if hasattr(self, 'gen_story_btn'):
            self.gen_story_btn.setEnabled(True)
            self.gen_story_btn.setText("✨ 生成故事")
        
        self._log(f"❌ 制作失败: {error_msg}")
        QMessageBox.critical(self, "错误", f"制作失败:\n{str(error_msg)}")
    
    def _on_merge_video_finished(self, result):
        # 兼容错误信号传入的字符串
        if isinstance(result, str):
            self._log(f"❌ 视频合并失败: {result}")
            QMessageBox.critical(self, "错误", f"视频合并失败:\n{result}")
            return

        video_path = result.get('video', '')
        
        # 检查是否真的合并成功
        if not video_path:
            self._log("❌ 视频合并失败：返回路径为空")
            QMessageBox.critical(self, "错误", "视频合并失败，请查看日志了解详细原因")
            return
        
        self._log(f"✅ 视频合并完成: {video_path}")
        
        # 验证文件是否真的存在
        if not Path(video_path).exists():
            self._log(f"❌ 视频文件不存在: {video_path}")
            QMessageBox.critical(self, "错误", f"视频合并失败，文件不存在:\n{video_path}")
            return
        
        # 更新项目视频路径并刷新列表
        if self.project:
            self.project.video_path = video_path
            self.project_manager.save_meta(self.project)
            self._refresh_video_list()
        
        msg = QMessageBox(self)
        msg.setWindowTitle("🎉 制作完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("恭喜！视频制作完成！")
        msg.setInformativeText(f"输出路径：\n{video_path}")
        
        open_folder_btn = msg.addButton("📂 打开文件夹", QMessageBox.ButtonRole.ActionRole)
        ok_btn = msg.addButton("完成", QMessageBox.ButtonRole.AcceptRole)
        
        msg.exec()
        
        if msg.clickedButton() == open_folder_btn:
            try:
                import subprocess
                folder_path = str(Path(video_path).parent)
                # 使用更安全的方式打开文件夹
                subprocess.Popen(['explorer', folder_path])
            except Exception as e:
                self._log(f"打开文件夹失败: {e}")
                QMessageBox.warning(self, "警告", f"无法打开文件夹: {e}")
    
    # ---------------- 音频页（表格模式） ----------------
    def _parse_dialogue(self, dialogue: str):
        """解析分镜对白为 (角色名, 台词) 列表。

        AI 生成的对白可能把多个角色的台词连在同一行，例如：
          "白白：我迷路了……小麻雀：别着急。老乌龟：上来吧。"
        因此除按行拆分外，还需在行内按 "角色：" 标记二次拆分。
        """
        result = []
        if not dialogue:
            return result
        # 角色名特征：出现在行首或句读符号之后、本身不含句读、后跟冒号的短文本
        role_pattern = re.compile(
            r'(?:^|[。！？!?\n…"……，,\s])([^：:\n，,。！？!?"“”…]{1,15})[：:]')
        for line in dialogue.split('\n'):
            line = line.strip()
            if not line:
                continue
            matches = list(role_pattern.finditer(line))
            if not matches:
                continue
            for i, m in enumerate(matches):
                role = m.group(1).strip()
                start = m.end()
                end = matches[i + 1].start(1) if i + 1 < len(matches) else len(line)
                text = line[start:end].strip()
                if role and text:
                    result.append((role, text))
        return result

    def _read_only_item(self, text: str, color: str = "#1e293b"):
        """创建只读的文本单元格（与普通文本同样显示）"""
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        item.setForeground(QColor(color))
        return item

    def _button_cell(self, btn: QPushButton) -> QWidget:
        """用容器包裹按钮，使其在单元格内水平垂直居中，与行对齐"""
        # 覆盖全局 QPushButton 样式（min-height:32px + padding 8px 导致按钮比行高还高、显示偏下）
        btn.setFixedHeight(34)
        btn.setStyleSheet(
            "QPushButton { padding: 2px 10px; font-size: 12px; min-height: 0; }"
        )
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addStretch()
        lay.addWidget(btn)
        lay.addStretch()
        lay.activate()
        return wrap

    def _on_tab_changed(self, index: int):
        """兼容其他页面容器的切换信号，统一执行页面刷新。"""
        self._refresh_page_data(index)
    
    def _release_media_players(self):
        """释放所有媒体的播放器对视频/音频文件的句柄占用（解决 WinError 32 文件被占用）。
        别处播放过视频后，QMediaPlayer 会保留对文件的句柄，直到源被清空或销毁。"""
        import time as _t
        for player in (getattr(self, 'media_player', None),
                         getattr(self, 'audio_player', None),
                         getattr(self, 'preview_player', None)):
            try:
                if player is None:
                    continue
                player.stop()
                player.setSource(QUrl())   # 清空源，释放文件句柄
            except Exception:
                pass
        QApplication.processEvents()   # 给底层编解码器释放句柄的时间
        _t.sleep(0.5)              # 稍等确保 Windows 释放文件锁


    def _on_delete_project(self):
        projects = self.project_manager.list_projects()
        if not projects:
            QMessageBox.information(self, "提示", "没有可删除的项目")
            return
        name, ok = QInputDialog.getItem(self, "删除项目", "选择要删除的项目:", projects)
        if not ok or not name:
            return
        reply = QMessageBox.question(
            self,
            "确认删除项目",
            f"确定要删除项目 '{name}' 吗？\n\n将删除项目目录及所有数据。\n\n此操作不可撤销！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 释放所有媒体播放器句柄，避免 WinError 32 文件被占用
        self._release_media_players()
        
        # 额外等待时间，确保 Windows 释放文件锁
        import time
        time.sleep(1)
        QApplication.processEvents()
        
        try:
            self.project_manager.delete_project(name)
            self._log(f"已删除项目: {name}")
            if self.project and self.project.name == name:
                self.project = None
                self.producer = None
                self._refresh_ui()
                self._mark_saved()
            if Settings.LAST_PROJECT_NAME == name:
                Settings.set_last_project("")
            QMessageBox.information(self, "删除成功", f"项目 '{name}' 及其所有数据已删除")
        except Exception as e:
            self._log(f"删除项目失败: {e}")
            QMessageBox.critical(self, "删除失败", f"无法删除项目:\n{e}\n\n请确保没有音频正在播放，然后重试。")