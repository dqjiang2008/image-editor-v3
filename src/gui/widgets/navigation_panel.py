"""Application page navigation panel."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget


class NavigationPanel(QWidget):
    """Compact navigation menu for the main work pages."""

    ITEMS = (
        ("📁", "项目"),
        ("📖", "故事"),
        ("👤", "角色"),
        ("🎒", "道具"),
        ("🏞️", "场景"),
        ("🎬", "分镜"),
        ("🎥", "视频"),
    )

    def __init__(self, on_page_changed, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(100)
        self.setMaximumWidth(140)
        self.setStyleSheet("""
            QWidget {
                background-color: #0f172a;
                border-right: 1px solid #1e293b;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(2)

        logo_label = QLabel("🎬")
        logo_label.setStyleSheet("font-size: 22px; padding: 4px 2px;")
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo_label)

        app_label = QLabel("视频生成")
        app_label.setStyleSheet(
            "color: #e2e8f0; font-size: 10px; font-weight: bold; padding: 2px;"
        )
        app_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(app_label)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #334155;")
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        self.buttons = []
        for page, (icon, name) in enumerate(self.ITEMS):
            button = QPushButton(f"{icon}\n{name}")
            button.setMinimumHeight(40)
            button.setCheckable(True)
            button.setStyleSheet("""
                QPushButton {
                    text-align: center;
                    background-color: transparent;
                    color: #94a3b8;
                    border: none;
                    border-radius: 6px;
                    font-size: 10px;
                    padding: 2px 4px;
                }
                QPushButton:hover {
                    background-color: #1e293b;
                    color: #e2e8f0;
                }
                QPushButton:checked {
                    background-color: #3b82f6;
                    color: white;
                    font-weight: bold;
                }
            """)
            button.clicked.connect(lambda checked, index=page: on_page_changed(index))
            self.buttons.append(button)
            layout.addWidget(button)

        layout.addStretch()
        version_label = QLabel("v2.0")
        version_label.setStyleSheet("color: #475569; font-size: 10px; padding: 8px;")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        if self.buttons:
            self.buttons[0].setChecked(True)