"""Main application toolbar."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget


class MainToolbar(QWidget):
    """Toolbar for project commands and edit history controls."""

    def __init__(self, controller):
        super().__init__()
        self.setStyleSheet(
            "MainToolbar { background-color: #ffffff; border-bottom: 2px solid #e2e8f0; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        logo = QLabel("🎬 AI视频生成工具")
        logo.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #3b82f6; padding: 4px 12px;"
        )
        layout.addWidget(logo)
        layout.addWidget(self._separator())

        commands = (
            ("📁 新建项目", "创建一个新项目", "secondary", controller._on_new_project, 90),
            ("📂 打开项目", "打开已存在的项目", "secondary", controller._on_open_project, 90),
            ("💾 保存项目", "保存当前项目", "success", controller._on_save_project, 90),
            ("🗑️ 删除项目", "删除选中的项目及其所有数据", "danger", controller._on_delete_project, 90),
            ("⚙️ 系统设置", "配置API和模型参数", "secondary", controller._on_settings, 90),
        )
        for text, tip, object_name, callback, width in commands:
            button = QPushButton(text)
            button.setObjectName(object_name)
            button.setToolTip(tip)
            button.setMinimumWidth(width)
            button.clicked.connect(callback)
            layout.addWidget(button)
            if text in ("🗑️ 删除项目", "⚙️ 系统设置"):
                layout.addWidget(self._separator())

        self.undo_btn = self._history_button("↩️ 撤销", "撤销上一步操作", controller._on_undo)
        self.redo_btn = self._history_button("↪️ 重做", "重做已撤销的操作", controller._on_redo)
        layout.addWidget(self.undo_btn)
        layout.addWidget(self.redo_btn)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(spacer)

        self.unsaved_label = QLabel("✅ 已保存")
        self.unsaved_label.setStyleSheet(
            "color: #22c55e; font-weight: bold; padding: 4px 12px; "
            "background-color: #f0fdf4; border-radius: 4px;"
        )
        layout.addWidget(self.unsaved_label)

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("color: #e2e8f0;")
        return line

    @staticmethod
    def _history_button(text, tip, callback):
        button = QPushButton(text)
        button.setObjectName("secondary")
        button.setToolTip(tip)
        button.setMinimumWidth(75)
        button.setEnabled(False)
        button.clicked.connect(callback)
        return button