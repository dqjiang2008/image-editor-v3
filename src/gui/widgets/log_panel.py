"""Bottom log output panel."""

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget


class LogPanel(QWidget):
    """Compact log viewer used by the main window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        title_layout = QHBoxLayout()
        title_label = QLabel("📋 日志输出")
        title_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #64748b;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        clear_button = QPushButton("清空日志")
        clear_button.setObjectName("secondary")
        clear_button.setFixedSize(70, 20)
        clear_button.setToolTip("清空所有日志内容")
        clear_button.clicked.connect(self.clear)
        clear_button.setStyleSheet("""
            QPushButton {
                background-color: #64748b;
                color: white;
                border: none;
                border-radius: 3px;
                font-size: 10px;
                padding: 1px 6px;
            }
            QPushButton:hover { background-color: #475569; }
        """)
        title_layout.addWidget(clear_button)
        layout.addLayout(title_layout)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.document().setMaximumBlockCount(500)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #1e293b;
                color: #e2e8f0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                padding: 8px;
                border: 1px solid #334155;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.text_edit)

    def append(self, message: str):
        self.text_edit.append(message)

    def clear(self):
        self.text_edit.clear()
