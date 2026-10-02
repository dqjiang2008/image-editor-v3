"""
AI视频生成工具 v2 - 主入口
"""
import sys
import os
import io
from pathlib import Path

# 修复Windows控制台中文乱码问题
if sys.platform == 'win32':
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from src.config.settings import Settings
from src.gui.main_window import MainWindow


def main():
    """主函数"""
    # 确保目录存在并加载用户配置
    Settings.ensure_dirs()
    Settings.load_user_config()
    
    # 创建应用
    app = QApplication(sys.argv)
    app.setApplicationName(Settings.APP_NAME)
    app.setApplicationVersion(Settings.APP_VERSION)
    app.setOrganizationName("AI Assistant")
    
    # 设置全局字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)
    
    # 创建主窗口（会自动加载上次的项目）
    window = MainWindow()
    window.show()
    
    # 运行应用
    sys.exit(app.exec())


if __name__ == "__main__":
    main()