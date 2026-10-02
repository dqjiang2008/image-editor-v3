"""Application settings dialog."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.config.settings import Settings
from src.services.agnes_client import fetch_available_models


class SettingsDialog(QDialog):
    """设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 设置")
        self.setMinimumSize(500, 400)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        api_group = QGroupBox("🔑 API 配置")
        api_layout = QFormLayout()
        api_layout.setSpacing(12)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("请输入 API Key")
        api_layout.addRow("API Key:", self.api_key_input)

        self.api_url_input = QLineEdit()
        self.api_url_input.setPlaceholderText("API 基础 URL")
        api_layout.addRow("API URL:", self.api_url_input)

        self.text_model_input = QComboBox()
        self.text_model_input.addItems(Settings.TEXT_MODEL_OPTIONS)
        api_layout.addRow("文本模型:", self.text_model_input)

        self.image_model_input = QComboBox()
        self.image_model_input.addItems(Settings.IMAGE_MODEL_OPTIONS)
        api_layout.addRow("图像模型:", self.image_model_input)

        self.video_model_input = QComboBox()
        self.video_model_input.addItems(Settings.VIDEO_MODEL_OPTIONS)
        api_layout.addRow("视频模型:", self.video_model_input)

        refresh_btn = QPushButton("🔄 从 API 刷新模型列表")
        refresh_btn.setObjectName("secondary")
        refresh_btn.clicked.connect(self._on_refresh_models)
        api_layout.addRow("", refresh_btn)

        refresh_hint = QLabel("点击后将从 API 获取最新可用模型列表，自动更新三个模型下拉框的选项")
        refresh_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        api_layout.addRow("", refresh_hint)

        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        # AGNES 调试模式
        debug_group = QGroupBox("🐛 AGNES 调试模式")
        debug_layout = QFormLayout()
        debug_layout.setSpacing(12)

        self.agnes_debug_checkbox = QCheckBox("启用调试模式（不实际发送请求，只保存JSON）")
        self.agnes_debug_checkbox.setStyleSheet("font-size: 12px;")
        debug_layout.addRow(self.agnes_debug_checkbox)

        debug_hint = QLabel("启用后，生成视频时会将请求JSON保存到 data/debug/ 目录，便于检查上传内容。已开始生成的任务不受影响。")
        debug_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        debug_layout.addRow("", debug_hint)

        debug_group.setLayout(debug_layout)
        layout.addWidget(debug_group)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        save_btn = QPushButton("💾 保存")
        save_btn.setObjectName("success")
        save_btn.clicked.connect(self._on_save)
        button_layout.addWidget(save_btn)

        cancel_btn = QPushButton("❌ 取消")
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _on_refresh_models(self):
        """从 API 刷新模型列表"""
        api_key = self.api_key_input.text().strip() or Settings.API_KEY
        api_url = self.api_url_input.text().strip() or Settings.API_BASE_URL

        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            models = fetch_available_models(api_key=api_key, api_base_url=api_url)
            if not models:
                QMessageBox.warning(
                    self,
                    "刷新失败",
                    "无法从 API 获取模型列表，将保留现有选项。\n\n"
                    "可能原因：\n"
                    "• API Key 无效\n"
                    "• API 服务暂不可用\n"
                    "• 当前 API 不支持列出模型（/v1/models）"
                )
                return

            # 保存当前选中的模型名
            cur_text = self.text_model_input.currentText()
            cur_image = self.image_model_input.currentText()
            cur_video = self.video_model_input.currentText()

            # 更新下拉框选项
            self.text_model_input.clear()
            self.text_model_input.addItems(models.get("text", Settings.TEXT_MODEL_OPTIONS))

            self.image_model_input.clear()
            self.image_model_input.addItems(models.get("image", Settings.IMAGE_MODEL_OPTIONS))

            self.video_model_input.clear()
            self.video_model_input.addItems(models.get("video", Settings.VIDEO_MODEL_OPTIONS))

            # 恢复当前选中的模型（如果还在列表中）
            self.text_model_input.setCurrentText(cur_text)
            self.image_model_input.setCurrentText(cur_image)
            self.video_model_input.setCurrentText(cur_video)

            # 持久化：保存到 Settings 类并写入配置文件
            Settings.TEXT_MODEL_OPTIONS = models.get("text", Settings.TEXT_MODEL_OPTIONS)
            Settings.IMAGE_MODEL_OPTIONS = models.get("image", Settings.IMAGE_MODEL_OPTIONS)
            Settings.VIDEO_MODEL_OPTIONS = models.get("video", Settings.VIDEO_MODEL_OPTIONS)
            Settings.save_user_config()

            QMessageBox.information(
                self,
                "刷新成功",
                f"模型列表已刷新！\n\n"
                f"文本模型: {len(models.get('text', []))} 个\n"
                f"图像模型: {len(models.get('image', []))} 个\n"
                f"视频模型: {len(models.get('video', []))} 个"
            )
        except Exception as e:
            QMessageBox.warning(self, "刷新失败", f"刷新模型列表时出错：{e}")
        finally:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _load_settings(self):
        """加载设置"""
        self.api_key_input.setText(Settings.API_KEY)
        self.api_url_input.setText(Settings.API_BASE_URL)
        self.text_model_input.setCurrentText(Settings.TEXT_MODEL)
        self.image_model_input.setCurrentText(Settings.IMAGE_MODEL)
        self.video_model_input.setCurrentText(Settings.VIDEO_MODEL)
        self.agnes_debug_checkbox.setChecked(Settings.AGNES_DEBUG_MODE)

    def _on_save(self):
        """保存设置"""
        Settings.API_KEY = self.api_key_input.text()
        Settings.API_BASE_URL = self.api_url_input.text()
        Settings.TEXT_MODEL = self.text_model_input.currentText()
        Settings.IMAGE_MODEL = self.image_model_input.currentText()
        Settings.VIDEO_MODEL = self.video_model_input.currentText()
        Settings.AGNES_DEBUG_MODE = self.agnes_debug_checkbox.isChecked()
        Settings.save_user_config()

        QMessageBox.information(self, "保存成功", "设置已保存！")
        self.accept()