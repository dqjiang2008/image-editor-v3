"""Shared Qt styles for the application."""

from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QPen, QBrush, QColor


class HighContrastCheckDelegate(QStyledItemDelegate):
    """自定义列表项委托：绘制高对比度勾选框。

    Qt 原生勾选框在某些显示器/主题下勾选标记颜色与背景对比度不足，
    导致看不到勾选状态。此委托用蓝色背景 + 白色粗勾替代默认渲染。
    """

    def paint(self, painter, option, index):
        check_state = index.data(Qt.ItemDataRole.CheckStateRole)
        if check_state is not None:
            style = option.widget.style()
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
            super().paint(painter, opt, index)
            check_rect = style.subElementRect(
                QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, option.widget
            )
            if check_rect.isValid():
                margin = 2
                r = QRect(
                    check_rect.x() + margin,
                    check_rect.y() + margin,
                    check_rect.width() - 2 * margin,
                    check_rect.height() - 2 * margin,
                )
                painter.save()
                if check_state == Qt.CheckState.Checked.value:
                    painter.setRenderHint(painter.RenderHint.Antialiasing)
                    painter.setBrush(QBrush(QColor("#3b82f6")))
                    painter.setPen(QPen(QColor("#2563eb"), 1.5))
                    painter.drawRoundedRect(r, 3, 3)
                    pen = QPen(QColor("#ffffff"), 2.0)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.setPen(pen)
                    x1, y1 = r.x() + 3, r.y() + r.height() * 0.55
                    x2, y2 = r.x() + r.width() * 0.38, r.y() + r.height() - 4
                    x3, y3 = r.x() + r.width() - 3, r.y() + 4
                    painter.drawLine(x1, y1, x2, y2)
                    painter.drawLine(x2, y2, x3, y3)
                else:
                    painter.setRenderHint(painter.RenderHint.Antialiasing)
                    painter.setBrush(QBrush(QColor("#ffffff")))
                    painter.setPen(QPen(QColor("#94a3b8"), 1.5))
                    painter.drawRoundedRect(r, 3, 3)
                painter.restore()
        else:
            super().paint(painter, option, index)

STYLESHEET = """
/* 全局样式 */
QMainWindow {
    background-color: #f8fafc;
}

QWidget {
    font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

/* 分组框 */
QGroupBox {
    font-weight: bold;
    border: 2px solid #e2e8f0;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
    background-color: #ffffff;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 8px;
    color: #3b82f6;
}

/* 按钮 */
QPushButton {
    background-color: #3b82f6;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 6px 8px;
    font-weight: bold;
    min-height: 32px;
}

QPushButton:hover {
    background-color: #2563eb;
}

QPushButton:pressed {
    background-color: #1d4ed8;
}

QPushButton:disabled {
    background-color: #cbd5e1;
    color: #94a3b8;
}

/* 成功按钮 */
QPushButton#success {
    background-color: #22c55e;
    color: white;
}

QPushButton#success:hover {
    background-color: #16a34a;
}

/* 警告按钮 */
QPushButton#warning {
    background-color: #f59e0b;
    color: white;
}

QPushButton#warning:hover {
    background-color: #d97706;
}

/* 危险按钮 */
QPushButton#danger {
    background-color: #ef4444 !important;
    color: #ffffff !important;
}

QPushButton#danger:hover {
    background-color: #dc2626 !important;
    color: #ffffff !important;
}

/* 次要按钮 */
QPushButton#secondary {
    background-color: #e2e8f0;
    color: #1e293b;
}

QPushButton#secondary:hover {
    background-color: #cbd5e1;
}

/* 输入框 */
QLineEdit, QTextEdit, QPlainTextEdit {
    border: 2px solid #e2e8f0;
    border-radius: 6px;
    padding: 8px;
    background-color: #ffffff;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #3b82f6;
}

/* 下拉框 */
QComboBox {
    border: 2px solid #e2e8f0;
    border-radius: 6px;
    padding: 6px 12px;
    background-color: #ffffff;
    min-height: 32px;
}

QComboBox:focus {
    border-color: #3b82f6;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

/* 进度条 */
QProgressBar {
    border: 2px solid #e2e8f0;
    border-radius: 8px;
    text-align: center;
    height: 24px;
    background-color: #ffffff;
}

QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #8b5cf6);
    border-radius: 6px;
}

/* 表格 */
QTableWidget {
    border: 2px solid #e2e8f0;
    border-radius: 8px;
    background-color: #ffffff;
    gridline-color: #e2e8f0;
}

QTableWidget::item {
    padding: 8px;
}

QTableWidget::item:selected {
    background-color: #dbeafe;
    color: #1e293b;
}

QHeaderView::section {
    background-color: #f1f5f9;
    padding: 8px;
    border: none;
    border-bottom: 2px solid #e2e8f0;
    font-weight: bold;
    color: #3b82f6;
}

/* 复选框（高对比度，确保勾选标记在各种显示器上可见） */
QCheckBox {
    spacing: 6px;
    color: #1e293b;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #94a3b8;
    border-radius: 3px;
    background-color: #ffffff;
}

QCheckBox::indicator:hover {
    border-color: #3b82f6;
}

QCheckBox::indicator:checked {
    background-color: #3b82f6;
    border-color: #2563eb;
    image: none;
}

QCheckBox::indicator:unchecked {
    background-color: #ffffff;
    border-color: #94a3b8;
}

/* 列表 */
QListWidget {
    border: 2px solid #e2e8f0;
    border-radius: 8px;
    background-color: #ffffff;
}

QListWidget::item {
    padding: 8px;
    border-bottom: 1px solid #f1f5f9;
}

QListWidget::item:selected {
    background-color: #dbeafe;
    color: #1e293b;
}

QListWidget::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #94a3b8;
    border-radius: 3px;
    background-color: #ffffff;
}

QListWidget::indicator:hover {
    border-color: #3b82f6;
}

QListWidget::indicator:checked {
    background-color: #3b82f6;
    border-color: #2563eb;
}

QListWidget::indicator:unchecked {
    background-color: #ffffff;
    border-color: #94a3b8;
}

/* 树形控件 */
QTreeWidget {
    border: 2px solid #e2e8f0;
    border-radius: 8px;
    background-color: #ffffff;
}

QTreeWidget::item {
    padding: 6px;
}

QTreeWidget::item:selected {
    background-color: #dbeafe;
    color: #1e293b;
}

/* 标签 */
QLabel {
    color: #1e293b;
}

/* 滚动区域 */
QScrollArea {
    border: none;
    background-color: transparent;
}

/* 微调框 */
QSpinBox, QDoubleSpinBox {
    border: 2px solid #e2e8f0;
    border-radius: 6px;
    padding: 6px;
    background-color: #ffffff;
    min-height: 32px;
}

QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #3b82f6;
}

/* 工具栏 */
QToolBar {
    background: #ffffff;
    padding: 8px;
    border: none;
    border-bottom: 2px solid #e2e8f0;
    spacing: 8px;
}

QToolBar::separator {
    width: 2px;
    background-color: #e2e8f0;
    margin: 4px 4px;
}

/* 状态栏 */
QStatusBar {
    background-color: #ffffff;
    border-top: 2px solid #e2e8f0;
    color: #64748b;
}

/* 滑块 */
QSlider::groove:horizontal {
    border: 1px solid #e2e8f0;
    height: 8px;
    background: #f1f5f9;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background: #3b82f6;
    width: 18px;
    margin: -5px 0;
    border-radius: 9px;
}

QSlider::handle:horizontal:hover {
    background: #2563eb;
}
"""