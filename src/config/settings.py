"""
应用配置管理
"""
import json
import os
from pathlib import Path
from typing import Optional


class Settings:
    """应用配置"""
    
    # 应用信息
    APP_NAME = "AI视频生成工具"
    APP_VERSION = "2.0.0"
    APP_AUTHOR = "AI Assistant"
    
    # 目录配置
    BASE_DIR = Path(__file__).parent.parent.parent
    DATA_DIR = BASE_DIR / "data"
    PROJECTS_DIR = DATA_DIR / "projects"
    LOGS_DIR = DATA_DIR / "logs"
    RESOURCES_DIR = BASE_DIR / "resources"
    ICONS_DIR = RESOURCES_DIR / "icons"
    CONFIG_FILE = DATA_DIR / "config.json"  # 用户配置文件
    
    # API 配置
    API_BASE_URL = "https://apihub.agnes-ai.com"
    API_KEY = os.getenv("AGNES_API_KEY", "")  # 优先从环境变量读取，默认空，用户通过设置页面输入
    
    # 模型配置（参考旧工程正确名称）
    TEXT_MODEL = "agnes-2.5-flash"
    IMAGE_MODEL = "agnes-image-2.5-flash"
    VIDEO_MODEL = "agnes-video-2.5-flash"

    # 模型可选列表（设置对话框下拉框用）
    TEXT_MODEL_OPTIONS = [
        "agnes-2.5-flash",
        "agnes-2.5-pro",
        "agnes-2.5-turbo",
        "agnes-2.5-8k",
    ]
    IMAGE_MODEL_OPTIONS = [
        "agnes-image-2.5-flash",
        "agnes-image-2.5-pro",
        "agnes-image-2.5-turbo",
        "agnes-image-2.5-hd",
        "agnes-image-2.5-4k",
    ]
    VIDEO_MODEL_OPTIONS = [
        "agnes-video-2.5-flash",
        "agnes-video-2.5-pro",
        "agnes-video-2.5-turbo",
        "agnes-video-2.5-hd",
        "wan2.6-text-to-video",
        "wan2.6-image-to-video",
    ]
    
    AGNES_VIDEO_MODE = "auto"  # 视频生成模式：auto/text/reference（auto按项目模式自动选择）
    AGNES_DEBUG_MODE = False  # 调试模式：不实际发送请求，只保存JSON
    AGNES_DEBUG_DIR = "data/debug"  # 调试文件保存目录
    
    # 默认项目设置
    DEFAULT_AGE_GROUP = "短视频"
    DEFAULT_STYLE = "写实"
    DEFAULT_SCENE_COUNT = 100
    DEFAULT_DURATION = 5.0
    VIDEO_MIN_DURATION = 4
    VIDEO_MAX_DURATION = 12
    
    # 故事长度 → 期望分镜数映射
    # 用于从故事提取分镜时，自动确定目标场景数量，
    # 保证分镜数量与故事时长匹配（每个分镜最长12秒）
    LENGTH_SCENE_COUNT = {
        "very_short": 15,  # 超短 (3分钟) → 至少15个分镜
        "short": 25,       # 短 (5分钟)  → 至少25个分镜
        "medium": 50,      # 中 (10分钟) → 至少50个分镜
        "long": 100,       # 长 (20分钟) → 至少100个分镜
    }
    
    EPISODE_DURATION = 20  # 每集时长（分钟）

    # 内容类型选项（替代原年龄组）
    AGE_GROUPS = ["短视频", "宣传片", "微电影", "纪录片"]
    
    # 风格选项
    STYLES = ["写实", "卡通", "水彩", "手绘", "3D", "像素", "油画"]
    
    # 预设角色（通用）
    CHARACTER_PRESETS = {
        "人物": {"style": "person", "age_range": "通用"},
        "动物": {"style": "animal", "age_range": "通用"},
        "风景": {"style": "landscape", "age_range": "通用"},
        "建筑": {"style": "building", "age_range": "通用"},
    }
    
    # UI 配置
    WINDOW_MIN_WIDTH = 1280
    WINDOW_MIN_HEIGHT = 800
    WINDOW_DEFAULT_WIDTH = 1440
    WINDOW_DEFAULT_HEIGHT = 900
    
    # 自动保存
    AUTO_SAVE_INTERVAL = 120  # 秒
    
    # 上次打开的项目
    LAST_PROJECT_NAME = ""
    
    @classmethod
    def ensure_dirs(cls):
        """确保所有必要的目录存在"""
        for dir_path in [cls.DATA_DIR, cls.PROJECTS_DIR, cls.LOGS_DIR]:
            dir_path.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def get_project_dir(cls, project_name: str) -> Path:
        return cls.PROJECTS_DIR / project_name

    @classmethod
    def normalize_video_duration(cls, value) -> float:
        """将分镜时长限制在视频模型支持的范围内。"""
        try:
            duration = float(value)
        except (TypeError, ValueError):
            duration = cls.DEFAULT_DURATION
        return max(cls.VIDEO_MIN_DURATION, min(duration, cls.VIDEO_MAX_DURATION))
    
    @classmethod
    def load_user_config(cls):
        """加载用户配置文件"""
        cls.ensure_dirs()
        if cls.CONFIG_FILE.exists():
            try:
                with open(cls.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                cls.LAST_PROJECT_NAME = config.get('last_project_name', '')
                cls.API_KEY = config.get('api_key', cls.API_KEY)
                cls.API_BASE_URL = config.get('api_base_url', cls.API_BASE_URL)
                cls.TEXT_MODEL = config.get('text_model', cls.TEXT_MODEL)
                cls.IMAGE_MODEL = config.get('image_model', cls.IMAGE_MODEL)
                cls.VIDEO_MODEL = config.get('video_model', cls.VIDEO_MODEL)
                cls.AGNES_VIDEO_MODE = config.get('agnes_video_mode', cls.AGNES_VIDEO_MODE)
                cls.AGNES_DEBUG_MODE = config.get('agnes_debug_mode', cls.AGNES_DEBUG_MODE)
                cls.AGNES_DEBUG_DIR = config.get('agnes_debug_dir', cls.AGNES_DEBUG_DIR)
                # 加载刷新获取的模型选项列表（如果存在）
                fetched = config.get('fetched_model_options', None)
                if fetched:
                    if fetched.get('text'):
                        cls.TEXT_MODEL_OPTIONS = fetched['text']
                    if fetched.get('image'):
                        cls.IMAGE_MODEL_OPTIONS = fetched['image']
                    if fetched.get('video'):
                        cls.VIDEO_MODEL_OPTIONS = fetched['video']
            except Exception as e:
                print(f"加载配置文件失败: {e}")
    
    @classmethod
    def save_user_config(cls):
        """保存用户配置文件"""
        cls.ensure_dirs()
        config = {
            'last_project_name': cls.LAST_PROJECT_NAME,
            'api_key': cls.API_KEY,
            'api_base_url': cls.API_BASE_URL,
            'text_model': cls.TEXT_MODEL,
            'image_model': cls.IMAGE_MODEL,
            'video_model': cls.VIDEO_MODEL,
            'agnes_video_mode': cls.AGNES_VIDEO_MODE,
            'agnes_debug_mode': cls.AGNES_DEBUG_MODE,
            'agnes_debug_dir': cls.AGNES_DEBUG_DIR,
            'fetched_model_options': {
                'text': cls.TEXT_MODEL_OPTIONS,
                'image': cls.IMAGE_MODEL_OPTIONS,
                'video': cls.VIDEO_MODEL_OPTIONS,
            },
        }
        try:
            with open(cls.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置文件失败: {e}")
    
    @classmethod
    def set_last_project(cls, project_name: str):
        """设置上次打开的项目"""
        cls.LAST_PROJECT_NAME = project_name
        cls.save_user_config()