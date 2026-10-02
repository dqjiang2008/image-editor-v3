"""
数据模型定义 - 模块化存储架构
项目目录结构:
  data/projects/<项目名>/
    project.json      - 项目元数据（含全局角色/道具/场景）
    characters.json   - 角色数据
    scenes.json       - 场景数据
    storyboard.json   - 分镜数据（非剧集模式）
    audio.json        - 音频数据
    episodes.json     - 剧集模式：全剧结构 + 各集资产引用（不含分镜）
    episodes/EPxxxx_标题.json - 剧集模式：每集分镜（分镜的唯一存储）
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime
from pathlib import Path
import json
import os
import re
import shutil
from src.config.settings import Settings


@dataclass
class Costume:
    """角色造型：同一角色在不同集/节中的服装与形象变化。

    例：「陈炎」有 costumes=[{name:"血衣", description:"换黑色血衣...", image_path:...}]
    分镜 characters 中写 "陈炎/血衣" 即可引用该造型的参考图。
    """
    name: str
    description: str = ""
    image_path: Optional[str] = None
    seed: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "image_path": self.image_path,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Costume":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            image_path=data.get("image_path"),
            seed=data.get("seed"),
        )


@dataclass
class Character:
    name: str
    style: str = "cartoon"
    description: str = ""
    image_path: Optional[str] = None
    images: List[str] = field(default_factory=list)
    seed: Optional[int] = None
    costumes: List[Costume] = field(default_factory=list)
    episodes: List[str] = field(default_factory=list)  # 适用集数：["1.1", "1.2"] 或 ["全剧"]
    char_type: str = "人类"  # 角色类型：人类/动物/妖兽/群体/能量体
    creature_count: str = ""  # 数量（非人类角色使用，如：7匹、一头、数百头）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "style": self.style,
            "description": self.description,
            "image_path": self.image_path,
            "images": self.images,
            "seed": self.seed,
            "costumes": [c.to_dict() if isinstance(c, Costume) else c for c in (self.costumes or [])],
            "episodes": self.episodes,
            "char_type": getattr(self, "char_type", "人类"),
            "creature_count": getattr(self, "creature_count", ""),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Character":
        costumes_raw = data.get("costumes", []) or []
        costumes = []
        for c in costumes_raw:
            if isinstance(c, dict):
                costumes.append(Costume.from_dict(c))
            elif isinstance(c, Costume):
                costumes.append(c)
        
        # 角色类型直接从 JSON 读取，不做关键词推断（避免"碎片""妖兽"等普通词误判）
        char_type = data.get("char_type", "人类")
        desc = data.get("description", "")
        
        return cls(
            name=data.get("name") or "",
            style=data.get("style", "cartoon"),
            description=desc,
            image_path=data.get("image_path"),
            images=data.get("images", []),
            seed=data.get("seed"),
            costumes=costumes,
            episodes=data.get("episodes", []),
            char_type=char_type,
            creature_count=data.get("creature_count", ""),
        )


@dataclass
class Prop:
    """道具（关键物品/物件）：名称、产品摄影风格描述、图片路径"""
    name: str
    style: str = "写实"
    description: str = ""
    image_path: Optional[str] = None
    seed: Optional[int] = None
    episodes: List[str] = field(default_factory=list)  # 适用集数：["1.1", "1.2"] 或 ["全剧"]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "style": self.style,
            "description": self.description,
            "image_path": self.image_path,
            "seed": self.seed,
            "episodes": self.episodes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Prop":
        return cls(
            name=data.get("name", ""),
            style=data.get("style", "写实"),
            description=data.get("description", ""),
            image_path=data.get("image_path"),
            seed=data.get("seed"),
            episodes=data.get("episodes", []),
        )


@dataclass
class Scene:
    name: str
    description: str = ""
    style: str = "cartoon"
    time_of_day: str = "day"
    image_path: Optional[str] = None
    seed: Optional[int] = None
    episodes: List[str] = field(default_factory=list)  # 适用集数：["1.1", "1.2"] 或 ["全剧"]
    priority: str = "P1"  # 优先级：P0-核心/P1-重要/P2-辅助
    category: str = "室外"  # 场景分类：室内/室外/地下/特殊空间
    main_scene: str = ""  # 所属主场景名（分场景填写主场景名，主场景留空）
    sub_scenes: List[str] = field(default_factory=list)  # 分场景名称列表（仅主场景使用，如["外景", "内景", "里屋"]）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "style": self.style,
            "time_of_day": self.time_of_day,
            "image_path": self.image_path,
            "seed": self.seed,
            "episodes": self.episodes,
            "priority": getattr(self, "priority", "P1"),
            "category": getattr(self, "category", "室外"),
            "main_scene": getattr(self, "main_scene", ""),
            "sub_scenes": getattr(self, "sub_scenes", []),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Scene":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            style=data.get("style", "cartoon"),
            time_of_day=data.get("time_of_day", "day"),
            image_path=data.get("image_path"),
            seed=data.get("seed"),
            episodes=data.get("episodes", []),
            priority=data.get("priority", "P1"),
            category=data.get("category", "室外"),
            main_scene=data.get("main_scene", ""),
            sub_scenes=data.get("sub_scenes", []),
        )


@dataclass
class Storyboard:
    scene_number: int
    scene_name: str = ""
    scene: str = ""  # 所属场景名（来自场景列表，用于场景图参考与一致性）
    description: str = ""
    characters: List[str] = field(default_factory=list)
    dialogue: str = ""
    dialogue_role: str = ""
    voice_text: str = ""
    action: str = ""
    camera: str = "\u4e2d\u666f"
    frame_path: Optional[str] = None
    video_path: Optional[str] = None
    duration: float = 5.0
    status: str = "pending"

    def to_dict(self) -> dict:
        return {
            "scene_number": self.scene_number,
            "scene_name": self.scene_name,
            "scene": self.scene,
            "description": self.description,
            "characters": self.characters,
            "dialogue": self.dialogue,
            "dialogue_role": self.dialogue_role,
            "voice_text": self.voice_text,
            "action": self.action,
            "camera": self.camera,
            "frame_path": self.frame_path,
            "video_path": self.video_path,
            "duration": self.duration,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Storyboard":
        return cls(
            scene_number=data.get("scene_number", 1),
            scene_name=data.get("scene_name", ""),
            # 兼容旧数据：scene 为空时退回 scene_name，只为「有值可显示」；
            # 载入剧集时 _align_storyboard_scene_names 会用统一契约
            # （asset_matcher.resolve_scene_assignment）把它纠正为场景列表中的名称，
            # 匹配不到则保留原值不写脏数据。
            scene=data.get("scene", "") or data.get("scene_name", ""),
            description=data.get("description", ""),
            characters=data.get("characters", []),
            dialogue=data.get("dialogue", ""),
            dialogue_role=data.get("dialogue_role", ""),
            voice_text=data.get("voice_text", data.get("dialogue", "")),
            action=data.get("action", ""),
            camera=data.get("camera", "\u4e2d\u666f"),
            frame_path=data.get("frame_path"),
            video_path=data.get("video_path"),
            duration=Settings.normalize_video_duration(data.get("duration", 5.0)),
            status=data.get("status", "pending"),
        )


@dataclass
class AudioTrack:
    name: str
    track_type: str = "voice"
    file_path: Optional[str] = None
    start_time: float = 0.0
    duration: float = 0.0
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "track_type": self.track_type,
            "file_path": self.file_path,
            "start_time": self.start_time,
            "duration": self.duration,
            "volume": self.volume,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AudioTrack":
        return cls(
            name=data.get("name", ""),
            track_type=data.get("track_type", "voice"),
            file_path=data.get("file_path"),
            start_time=data.get("start_time", 0.0),
            duration=data.get("duration", 0.0),
            volume=data.get("volume", 1.0),
            fade_in=data.get("fade_in", 0.0),
            fade_out=data.get("fade_out", 0.0),
        )


@dataclass
class TextCue:
    """时间轴文本条目：一段在指定时间显示指定时长的对话/旁白字幕"""
    text: str
    start_time: float = 0.0
    duration: float = 2.0
    scene_number: int = 0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "start_time": self.start_time,
            "duration": self.duration,
            "scene_number": self.scene_number,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TextCue":
        return cls(
            text=data.get("text", ""),
            start_time=data.get("start_time", 0.0),
            duration=data.get("duration", 2.0),
            scene_number=data.get("scene_number", 0),
        )


@dataclass
class Project:
    name: str = "\u672a\u547d\u540d\u9879\u76ee"
    age_group: str = "\u77ed\u89c6\u9891"
    style: str = "\u5199\u5b9e"
    story: str = ""
    topic: str = ""
    length: str = "long"  # 故事长度: short(5分钟) / medium(10分钟) / long(20分钟)
    characters: List[Character] = field(default_factory=list)
    props: List[Prop] = field(default_factory=list)
    scenes: List[Scene] = field(default_factory=list)
    storyboard: List[Storyboard] = field(default_factory=list)
    audio_tracks: List[AudioTrack] = field(default_factory=list)
    subtitle_cues: List[TextCue] = field(default_factory=list)
    video_path: Optional[str] = None
    exported_files: List[str] = field(default_factory=list)
    preview_with_audio: bool = False
    # 全局资产生成标记
    assets_generated: bool = False
    images_generated: bool = False
    # 全局角色/道具/场景（所有集共用，生成一次）
    global_characters: List[Character] = field(default_factory=list)
    global_props: List[Prop] = field(default_factory=list)
    global_scenes: List[Scene] = field(default_factory=list)
    # 绑定的共享资产库名（data/asset_library/<库名>/）：
    # 生成角色/造型/道具/场景图时直接写入库内图片目录，其他项目导入库可复用图片
    asset_library_name: str = ""
    # 剧集模式：整部剧原文 + 分集列表 + 当前集索引 + 每集数据
    # episode_data 结构: { "0": {"scene_names": [...], "character_names": [...], "prop_names": [...], "storyboard": [...]} }
    # scene_names/character_names/prop_names 引用全局角色/道具/场景的名称，分镜按当前集引用的场景生成
    script_text: str = ""
    episodes: List[dict] = field(default_factory=list)
    current_episode: int = -1
    episode_data: Dict[str, dict] = field(default_factory=dict)
    # 技能包（resources/skills/）：风格包展示名 + 叙事包展示名 + 制作技法展示名，未启用为空字符串
    skill_pack: str = ""
    story_pack: str = ""
    production_technique: str = ""
    video_mode: str = ""
    # 高级用户手动注入的风格短语（分镜页高级选项，追加到帧图/视频 prompt 末尾）
    custom_style_suffix: str = ""
    # 已解析过的剧本文件路径列表（防止重复解析产生相同资产）
    parsed_files: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()


class ProjectManager:
    """\u9879\u76ee\u7ba1\u7406\u5668 - \u6a21\u5757\u5316\u5b58\u50a8"""

    SUB_FILES = {
        "characters": "characters.json",
        "scenes": "scenes.json",
        "storyboard": "storyboard.json",
        "audio": "audio.json",
    }

    def __init__(self, projects_dir: str):
        self.projects_dir = Path(projects_dir)
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, name: str) -> Path:
        safe_name = name.replace('\n', '').replace('\r', '').strip()
        return self.projects_dir / safe_name

    def _read_json(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.parent / f".tmp_{path.name}"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
        shutil.move(str(temp_path), str(path))

    def create_project(self, name: str, **kwargs) -> Project:
        project = Project(name=name, **kwargs)
        d = self._project_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        self.save_project(project)
        return project

    def save_project(self, project: Project):
        project.updated_at = datetime.now().isoformat()
        name = project.name.replace('\n', '').replace('\r', '').strip() if isinstance(project.name, str) else '\u672a\u547d\u540d\u9879\u76ee'
        project.name = name
        d = self._project_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        # 保存前：global_* 始终同步为 characters/props/scenes 的副本
        project.global_characters = list(project.characters) if project.characters else []
        project.global_props = list(project.props) if project.props else []
        project.global_scenes = list(project.scenes) if project.scenes else []
        self._save_meta(project)
        self._save_characters(project)
        self._save_props(project)
        self._save_scenes(project)
        self._save_storyboard(project)
        self._save_audio(project)
        self._save_timeline(project)
        self._save_episodes(project)

    def _save_meta(self, project: Project):
        d = self._project_dir(project.name)
        meta = {
            "name": project.name,
            "age_group": project.age_group,
            "style": project.style,
            "story": project.story,
            "topic": project.topic,
            "length": project.length,
            "video_path": project.video_path,
            "exported_files": project.exported_files,
            "preview_with_audio": project.preview_with_audio,
            "assets_generated": getattr(project, "assets_generated", False),
            "images_generated": getattr(project, "images_generated", False),
            "global_characters": [c.to_dict() for c in getattr(project, "global_characters", [])],
            "global_props": [p.to_dict() for p in getattr(project, "global_props", [])],
            "global_scenes": [s.to_dict() for s in getattr(project, "global_scenes", [])],
            "asset_library_name": getattr(project, "asset_library_name", "") or "",
            "skill_pack": getattr(project, "skill_pack", "") or "",
            "story_pack": getattr(project, "story_pack", "") or "",
            "production_technique": getattr(project, "production_technique", "") or "",
            "video_mode": getattr(project, "video_mode", "") or "",
            "custom_style_suffix": getattr(project, "custom_style_suffix", "") or "",
            "parsed_files": getattr(project, "parsed_files", []) or [],
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        }
        self._write_json(d / "project.json", meta)

    def _save_characters(self, project: Project):
        """保存角色：始终保存到项目目录 characters.json；有资产库则同步到库。"""
        d = self._project_dir(project.name)
        chars_data = [c.to_dict() for c in getattr(project, "characters", [])]
        self._write_json(d / "characters.json", chars_data)

        lib_name = getattr(project, 'asset_library_name', '') or ''
        if not lib_name:
            return
        from src.services.library_manager import save_library, load_library
        lib = load_library(lib_name)
        old_chars = lib.get("characters", []) if lib else []
        old_props = lib.get("props", []) if lib else []
        old_scenes = lib.get("scenes", []) if lib else []
        
        old_char_map = {c.name: c for c in old_chars}
        for char in project.characters:
            old_char_map[char.name] = char
        merged_chars = list(old_char_map.values())
        
        save_library(lib_name, merged_chars, old_props, old_scenes, merge=False)

    def _save_props(self, project: Project):
        """保存道具：始终保存到项目目录 props.json；有资产库则同步到库。"""
        d = self._project_dir(project.name)
        props_data = [p.to_dict() for p in getattr(project, "props", [])]
        self._write_json(d / "props.json", props_data)

        lib_name = getattr(project, 'asset_library_name', '') or ''
        if not lib_name:
            return
        from src.services.library_manager import save_library, load_library
        lib = load_library(lib_name)
        old_chars = lib.get("characters", []) if lib else []
        old_props = lib.get("props", []) if lib else []
        old_scenes = lib.get("scenes", []) if lib else []
        
        old_prop_map = {p.name: p for p in old_props}
        for prop in getattr(project, "props", []):
            old_prop_map[prop.name] = prop
        merged_props = list(old_prop_map.values())
        
        save_library(lib_name, old_chars, merged_props, old_scenes, merge=False)

    def _save_scenes(self, project: Project):
        """保存场景：始终保存到项目目录 scenes.json；有资产库则同步到库。"""
        d = self._project_dir(project.name)
        scenes_data = [s.to_dict() for s in getattr(project, "scenes", [])]
        self._write_json(d / "scenes.json", scenes_data)

        lib_name = getattr(project, 'asset_library_name', '') or ''
        if not lib_name:
            return
        from src.services.library_manager import save_library, load_library
        lib = load_library(lib_name)
        old_chars = lib.get("characters", []) if lib else []
        old_props = lib.get("props", []) if lib else []
        old_scenes = lib.get("scenes", []) if lib else []
        
        old_scene_map = {s.name: s for s in old_scenes}
        for scene in project.scenes:
            old_scene_map[scene.name] = scene
        merged_scenes = list(old_scene_map.values())
        
        save_library(lib_name, old_chars, old_props, merged_scenes, merge=False)

    def _save_storyboard(self, project: Project):
        d = self._project_dir(project.name)
        data = [sb.to_dict() for sb in project.storyboard]
        self._write_json(d / "storyboard.json", data)

    def _save_audio(self, project: Project):
        d = self._project_dir(project.name)
        data = [a.to_dict() for a in project.audio_tracks]
        self._write_json(d / "audio.json", data)

    def _save_timeline(self, project: Project):
        d = self._project_dir(project.name)
        data = [c.to_dict() for c in project.subtitle_cues]
        self._write_json(d / "timeline.json", data)

    @staticmethod
    def _extract_section_key(title: str) -> str:
        import re
        m = re.match(r'(\d+\.\d+)', title.strip())
        return m.group(1) if m else title.strip()

    def _save_sections_index(self, project: Project):
        d = self._project_dir(project.name)
        episodes = getattr(project, "episodes", []) or []
        sections = []
        for i, ep in enumerate(episodes):
            title = (ep.get("title") or f"\u7b2c{i+1}\u8282").strip()
            key = self._extract_section_key(title)
            sections.append({"key": key, "title": title[:60], "ep_idx": i})
        self._write_json(d / "sections_index.json", {"sections": sections})

    def load_sections_index(self, project: Project) -> list:
        d = self._project_dir(project.name)
        path = d / "sections_index.json"
        data = self._read_json(path)
        if isinstance(data, dict) and isinstance(data.get("sections"), list):
            return data["sections"]
        episodes = getattr(project, "episodes", []) or []
        if episodes:
            sections = []
            for i, ep in enumerate(episodes):
                title = (ep.get("title") or f"\u7b2c{i+1}\u8282").strip()
                key = self._extract_section_key(title)
                sections.append({"key": key, "title": title[:60], "ep_idx": i})
            return sections
        return []

    def append_sections(self, project: Project, new_episodes: list) -> tuple:
        existing = getattr(project, "episodes", []) or []
        existing_keys = set()
        for ep in existing:
            title = (ep.get("title") or "").strip()
            key = self._extract_section_key(title)
            if key:
                existing_keys.add(key)
        conflicts = []
        added = []
        for ep in new_episodes:
            title = (ep.get("title") or "").strip()
            key = self._extract_section_key(title)
            if key and key in existing_keys:
                conflicts.append({"key": key, "title": title[:60]})
            else:
                added.append(ep)
                if key:
                    existing_keys.add(key)
        return conflicts, added

    def _save_episodes(self, project: Project):
        """保存剧集模式数据（整部剧原文、分集列表、当前集、每集资产引用快照）。

        分镜不写入 episodes.json：每集分镜保存到独立分集文件
        （episodes/EPxxxx_标题.json），避免所有集分镜堆在一个大文件里；
        此处只保留本集资产引用（character/scene/prop_names），并确保
        有分镜的集都存在对应分集文件（旧数据/被批量跳过集的自动迁移）。
        """
        d = self._project_dir(project.name)
        episode_data = getattr(project, "episode_data", {}) or {}
        stripped: Dict[str, dict] = {}
        for key, snap in episode_data.items():
            if isinstance(snap, dict) and snap.get("storyboard"):
                # 有分镜的集：确保分集文件存在（文件是分镜的唯一存储）
                try:
                    ep_idx = int(key)
                except (TypeError, ValueError):
                    stripped[key] = snap  # 非法 key，原样保留
                    continue
                episodes = getattr(project, "episodes", []) or []
                title = ""
                if 0 <= ep_idx < len(episodes):
                    title = (episodes[ep_idx].get("title") or f"第{ep_idx + 1}集").strip()
                f = self._episodes_dir(project) / self._episode_filename(ep_idx, title)
                if not f.exists():
                    self.save_episode_file(project, ep_idx)
                stripped[key] = {
                    k: v for k, v in snap.items() if k != "storyboard"
                }
            else:
                stripped[key] = snap
        data = {
            "script_text": getattr(project, "script_text", "") or "",
            "episodes": getattr(project, "episodes", []) or [],
            "current_episode": getattr(project, "current_episode", -1),
            "episode_data": stripped,
        }
        self._write_json(d / "episodes.json", data)
        self._save_sections_index(project)

    # ------------------------------------------------------------------
    # 分集文件存储：每集的「本集角色/场景/道具引用 + 分镜」保存为独立 JSON，
    # 供切换剧集时直接调用（无需再让 AI 生成分镜），也是批量生成的持久化单元。
    # 目录：data/projects/<项目名>/episodes/EP0001_<标题>.json
    # ------------------------------------------------------------------
    def _episodes_dir(self, project: Project) -> Path:
        return self._project_dir(project.name) / "episodes"

    @staticmethod
    def _episode_filename(ep_idx: int, title: str) -> str:
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", (title or "").strip())[:30]
        return f"EP{ep_idx + 1:04d}_{safe}.json"

    def save_episode_file(self, project: Project, ep_idx: int) -> Optional[Path]:
        """把指定集的快照保存为独立分集文件，返回文件路径（无数据返回 None）

        保存前清理同一 EP 编号下标题不匹配的旧文件（防止导入顺序变化导致
        同一编号混入不同节的内容），确保每个 EP 编号只有一份正确数据。
        """
        data = (getattr(project, "episode_data", {}) or {}).get(str(ep_idx))
        if not data:
            return None
        episodes = getattr(project, "episodes", []) or []
        title = ""
        if 0 <= ep_idx < len(episodes):
            title = (episodes[ep_idx].get("title") or f"第{ep_idx + 1}集").strip()
        payload = {
            "index": ep_idx,
            "title": title,
            "character_names": data.get("character_names", []),
            "scene_names": data.get("scene_names", []),
            "prop_names": data.get("prop_names", []),
            "storyboard": data.get("storyboard", []),
        }
        d = self._episodes_dir(project)
        d.mkdir(parents=True, exist_ok=True)
        # 清理同一 EP 编号下标题不匹配的旧文件
        for old_f in sorted(d.glob(f"EP{ep_idx + 1:04d}_*.json")):
            old_title = self._extract_title_from_ep_file(old_f)
            # 仅删除标题不同的旧文件（相同标题的保留以覆盖写入）
            if old_title is not None and old_title != title:
                try:
                    old_f.unlink()
                except OSError:
                    pass
        path = d / self._episode_filename(ep_idx, title)
        self._write_json(path, payload)
        return path

    @staticmethod
    def _extract_title_from_ep_file(f: Path) -> Optional[str]:
        """从分集 JSON 文件中提取 'title' 字段（快速读取，不载入全部分镜）"""
        try:
            with f.open("r", encoding="utf-8") as fh:
                import json
                data = json.load(fh)
                if isinstance(data, dict):
                    return data.get("title") or None
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def load_episode_file(self, project: Project, ep_idx: int) -> Optional[dict]:
        """读取指定集的分集文件（按 EP 编号前缀匹配，标题变化也能找到）

        当同一编号下有多份文件时，优先选用标题与 ``project.episodes`` 匹配的。
        """
        d = self._episodes_dir(project)
        if not d.exists():
            return None
        episodes = getattr(project, "episodes", []) or []
        expected_title = ""
        if 0 <= ep_idx < len(episodes):
            expected_title = (episodes[ep_idx].get("title") or "").strip()

        best: Optional[dict] = None
        for f in sorted(d.glob(f"EP{ep_idx + 1:04d}_*.json")):
            data = self._read_json(f)
            if not isinstance(data, dict) or not data.get("storyboard"):
                continue
            file_title = (data.get("title") or "").strip()
            if expected_title and file_title == expected_title:
                return data  # 标题完全匹配，立即返回
            if best is None:
                best = data  # 暂存第一个有数据的文件
        return best

    def load_all_episode_files(self, project: Project) -> Dict[str, dict]:
        """读取全部分集文件，返回 {集索引字符串: 快照}

        当同一 EP 编号下有多个标题不同的文件（如导入顺序变化导致残留），
        优先选用标题与 ``project.episodes`` 列表中对应条目匹配的文件。
        """
        d = self._episodes_dir(project)
        result: Dict[str, dict] = {}
        if not d.exists():
            return result

        episodes = getattr(project, "episodes", []) or []

        # 先收集同一 EP 编号下的所有文件
        ep_files: Dict[int, list] = {}
        for f in d.glob("EP*.json"):
            m = re.match(r"EP(\d+)_", f.name)
            if not m:
                continue
            ep_num = int(m.group(1))
            ep_files.setdefault(ep_num, []).append(f)

        for ep_num, files in ep_files.items():
            ep_idx = ep_num - 1
            # 当前该索引对应的正确标题
            expected_title = ""
            if 0 <= ep_idx < len(episodes):
                expected_title = (episodes[ep_idx].get("title") or "").strip()

            # 优先找标题匹配的文件
            best: Optional[dict] = None
            for f in sorted(files):
                data = self._read_json(f)
                if not isinstance(data, dict) or not data.get("storyboard"):
                    continue
                file_title = (data.get("title") or "").strip()
                if expected_title and file_title == expected_title:
                    # 标题完全匹配，立即选中
                    best = {
                        "character_names": data.get("character_names", []),
                        "scene_names": data.get("scene_names", []),
                        "prop_names": data.get("prop_names", []),
                        "storyboard": data.get("storyboard", []),
                    }
                    break
                elif best is None:
                    # 暂存第一个有数据的文件
                    best = {
                        "character_names": data.get("character_names", []),
                        "scene_names": data.get("scene_names", []),
                        "prop_names": data.get("prop_names", []),
                        "storyboard": data.get("storyboard", []),
                    }
            if best is not None:
                result[str(ep_idx)] = best
        return result

    def save_timeline(self, project: Project):
        self._save_timeline(project)

    def save_characters(self, project: Project):
        self._save_characters(project)

    def save_props(self, project: Project):
        self._save_props(project)

    def save_scenes(self, project: Project):
        self._save_scenes(project)

    def save_storyboard(self, project: Project):
        self._save_storyboard(project)

    def save_audio(self, project: Project):
        self._save_audio(project)

    def save_meta(self, project: Project):
        project.updated_at = datetime.now().isoformat()
        self._save_meta(project)
        self._save_episodes(project)

    def load_project(self, name: str) -> Optional[Project]:
        d = self._project_dir(name)
        meta = self._read_json(d / "project.json")
        if not meta:
            return None

        project = Project(
            name=meta.get("name", name),
            age_group=meta.get("age_group", "5-6\u5c81"),
            style=meta.get("style", "\u5361\u901a"),
            story=meta.get("story", ""),
            topic=meta.get("topic", ""),
            length=meta.get("length", "long"),
            video_path=meta.get("video_path"),
            exported_files=meta.get("exported_files", []),
            preview_with_audio=meta.get("preview_with_audio", False),
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
        )
        project.assets_generated = meta.get("assets_generated", False)
        project.images_generated = meta.get("images_generated", False)
        project.skill_pack = meta.get("skill_pack", "") or ""
        project.story_pack = meta.get("story_pack", "") or ""
        project.production_technique = meta.get("production_technique", "") or ""
        project.video_mode = meta.get("video_mode", "") or ""
        project.custom_style_suffix = meta.get("custom_style_suffix", "") or ""
        project.parsed_files = meta.get("parsed_files", []) or []
        project.asset_library_name = meta.get("asset_library_name", "") or ""

        # 角色/道具/场景：只从项目目录的 JSON 文件加载（唯一数据源）
        chars_file = self._read_json(d / "characters.json")
        project.characters = [Character.from_dict(c) for c in chars_file] if chars_file and isinstance(chars_file, list) else []
        props_file = self._read_json(d / "props.json")
        project.props = [Prop.from_dict(p) for p in props_file] if props_file and isinstance(props_file, list) else []
        scenes_file = self._read_json(d / "scenes.json")
        project.scenes = [Scene.from_dict(s) for s in scenes_file] if scenes_file and isinstance(scenes_file, list) else []

        # global_* 始终同步为 characters/props/scenes 的副本
        project.global_characters = list(project.characters)
        project.global_props = list(project.props)
        project.global_scenes = list(project.scenes)
        
        sb_data = self._read_json(d / "storyboard.json")
        if sb_data and isinstance(sb_data, list):
            project.storyboard = [Storyboard.from_dict(sb) for sb in sb_data]

        audio_data = self._read_json(d / "audio.json")
        if audio_data and isinstance(audio_data, list):
            project.audio_tracks = [AudioTrack.from_dict(a) for a in audio_data]

        tl_data = self._read_json(d / "timeline.json")
        if tl_data and isinstance(tl_data, list):
            project.subtitle_cues = [TextCue.from_dict(c) for c in tl_data]

        ep_data = self._read_json(d / "episodes.json")
        if ep_data and isinstance(ep_data, dict):
            project.script_text = ep_data.get("script_text", "")
            project.episodes = ep_data.get("episodes", [])
            project.current_episode = ep_data.get("current_episode", -1)
            project.episode_data = ep_data.get("episode_data", {})

        # 分集文件合并恢复：episodes/EP*.json 是每集分镜的唯一存储（episodes.json
        # 不再保存分镜），此处把分镜回填到内存 episode_data：
        # - 该集无快照 → 整份采用分集文件；
        # - 该集有快照但无分镜 → 保留快照中的资产引用（可能比文件更新），仅回填分镜。
        if getattr(project, "episodes", None):
            for key, snapshot in self.load_all_episode_files(project).items():
                cur = (project.episode_data or {}).get(key)
                if not cur:
                    project.episode_data[key] = snapshot
                elif not cur.get("storyboard") and snapshot.get("storyboard"):
                    merged = dict(cur)
                    merged["storyboard"] = snapshot["storyboard"]
                    project.episode_data[key] = merged

        # 兼容迁移：旧项目已有角色/场景/道具但 global_characters/global_props/global_scenes 为空
        if not project.global_characters and project.characters:
            project.global_characters = list(project.characters)
        if not project.global_props and project.props:
            project.global_props = list(project.props)
        if not project.global_scenes and project.scenes:
            project.global_scenes = list(project.scenes)

        # 清理"从分镜恢复的角色"残留数据（旧版 _supplement_characters_from_storyboard 生成）
        _stale_prefix = "从分镜恢复的角色:"
        _cleaned_chars = False
        for char_list_name in ("characters", "global_characters"):
            char_list = getattr(project, char_list_name, None)
            if char_list:
                cleaned = [c for c in char_list if not (c.description or "").startswith(_stale_prefix)]
                if len(cleaned) < len(char_list):
                    setattr(project, char_list_name, cleaned)
                    _cleaned_chars = True
                    if char_list_name == "global_characters":
                        project.characters = list(cleaned)
        # 清理后持久化角色数据
        if _cleaned_chars:
            self._write_json(d / "characters.json", [c.to_dict() for c in project.characters])

        # 帧图回填：分镜 frame_path 为空但 frames/ 目录下存在对应文件时自动关联
        frames_dir = d / "frames"
        if frames_dir.exists() and project.storyboard:
            restored = 0
            for sb in project.storyboard:
                if sb.frame_path:
                    continue
                idx = sb.scene_number - 1
                frame_file = frames_dir / f"frame_{idx + 1:03d}.png"
                if frame_file.exists():
                    sb.frame_path = str(frame_file)
                    if not sb.status or sb.status == "pending":
                        sb.status = "done"
                    restored += 1
            # 也尝试 .jpg 格式
            if restored == 0:
                for sb in project.storyboard:
                    if sb.frame_path:
                        continue
                    idx = sb.scene_number - 1
                    frame_file = frames_dir / f"frame_{idx + 1:03d}.jpg"
                    if frame_file.exists():
                        sb.frame_path = str(frame_file)
                        if not sb.status or sb.status == "pending":
                            sb.status = "done"
                        restored += 1
            # 回填后立即持久化，避免下次加载重复回填
            if restored > 0:
                self._write_json(d / "storyboard.json", [sb.to_dict() for sb in project.storyboard])

        # 兼容迁移：旧项目已有角色/场景数据但标记未设置
        if not project.assets_generated and (project.characters or project.scenes):
            project.assets_generated = True
        # 兼容迁移：旧项目已有角色图/场景图但标记未设置
        if not project.images_generated:
            has_char_images = any(
                getattr(c, 'image_path', None) and Path(c.image_path).exists()
                for c in project.characters
            )
            has_scene_images = any(
                getattr(s, 'image_path', None) and Path(s.image_path).exists()
                for s in project.scenes
            )
            if has_char_images or has_scene_images:
                project.images_generated = True

        # 恢复角色/造型/道具/场景图片路径：JSON 里 image_path 为 null 但磁盘上
        # 已存在对应文件时自动回填（生成后漏存 / 库 JSON 未回写等场景的自愈）。
        # 必须在「兼容迁移」把 global_* 列表填充之后执行，才能覆盖旧项目。
        self._restore_asset_image_paths(project, d, meta.get("asset_library_name", "") or "")

        self._fix_migrated_paths(project, d)

        return project

    def _restore_asset_image_paths(self, project: Project, d: Path, lib_name: str) -> None:
        """JSON 里 image_path 为 null 但磁盘上已存在对应图片文件时，自动回填。

        触发场景：
        - 生成角色/造型/道具/场景图后漏存（程序中途退出、保存失败等）；
        - 绑定了共享库的项目：图片写入库目录，但库 JSON 的 image_path 没回写
          （旧版 save_library(merge=False) 会把旧条目的 image_path 丢掉），
          重启后 load_project 读到 null，「媒体」页与「👗 管理造型」都显示不出图。

        规则：
        - 只回填 image_path 为空（null/""）的资产，已有路径不动；
        - 目录选择：绑定库时优先库内 characters/ props/ scenes/（跨项目共享），
          否则用项目目录下同名子目录；
        - 文件名规则与 producer._safe_filename 保持一致（底模 <角色名>.png，
          造型 <角色名>_<造型名>.png，道具/场景 <名称>.png）；
        - 回填成功后：写回项目 JSON（meta/characters/props/scenes），并同步到
          库 JSON（merge 模式，不丢旧数据），使下次启动无需再靠磁盘扫描。
        """
        from src.core.producer import _safe_filename

        def resolve_dir(kind: str) -> Optional[Path]:
            p = d / kind
            if lib_name:
                from src.services.library_manager import library_images_dir
                lib_dir = library_images_dir(lib_name, kind)
                if lib_dir is not None:
                    p = lib_dir
            return p if p.exists() else None

        char_dir = resolve_dir("characters")
        prop_dir = resolve_dir("props")
        scene_dir = resolve_dir("scenes")

        restored = 0

        # 角色 + 造型（global_characters 优先；剧集模式下 characters 与其共享实例，
        # 单独扫描 characters 兜底旧数据）
        for char in (list(project.global_characters) +
                     ([c for c in project.characters] if char_dir else [])):
            if char_dir:
                if not char.image_path:
                    p = char_dir / f"{_safe_filename(char.name)}.png"
                    if p.exists():
                        char.image_path = str(p)
                        restored += 1
                for costume in (char.costumes or []):
                    if not costume.image_path:
                        p = char_dir / f"{_safe_filename(char.name)}_{_safe_filename(costume.name)}.png"
                        if p.exists():
                            costume.image_path = str(p)
                            restored += 1

        if prop_dir:
            for prop in (list(project.global_props) +
                         ([p for p in project.props] if not project.global_props else [])):
                if not prop.image_path:
                    p = prop_dir / f"{_safe_filename(prop.name)}.png"
                    if p.exists():
                        prop.image_path = str(p)
                        restored += 1

        if scene_dir:
            for scene in (list(project.global_scenes) +
                          ([s for s in project.scenes] if not project.global_scenes else [])):
                if not scene.image_path:
                    p = scene_dir / f"{_safe_filename(scene.name)}.png"
                    if p.exists():
                        scene.image_path = str(p)
                        restored += 1

        if not restored:
            return

        # 持久化：写回项目 JSON（含 global_* 元数据 + 各资产文件）
        try:
            self.save_project(project)
        except Exception:
            pass  # 回填是尽力而为，不因持久化失败影响加载

        # 同步库 JSON（merge 模式，不丢旧条目的已生成字段）
        if lib_name:
            try:
                from src.services.library_manager import _sync_image_fields_to_library_json
                _sync_image_fields_to_library_json(lib_name, project)
            except Exception:
                pass

    def _fix_migrated_paths(self, project: Project, project_dir: Path):
        """修正因项目目录迁移导致的绝对路径失效问题。

        当用户将整个项目文件夹从旧位置搬到新位置后，JSON 中保存的
        image_path / frame_path / video_path 仍指向旧目录，导致图片无法加载。
        此方法检测路径失效并自动修正为新目录下的对应路径。
        """
        new_root = str(project_dir)
        fixed = False

        def repair(p: Optional[str]) -> Optional[str]:
            nonlocal fixed
            if not p:
                return p
            if Path(p).exists():
                return p
            try:
                parts = Path(p).parts
                proj_idx = None
                for i, part in enumerate(parts):
                    if part == 'projects' and i > 0 and parts[i - 1] == 'data':
                        proj_idx = i
                        break
                if proj_idx is None:
                    return p
                relative = Path(*parts[proj_idx + 1:])
                candidate = project_dir / relative
                if candidate.exists():
                    fixed = True
                    return str(candidate)
            except Exception:
                pass
            return p

        for c in project.global_characters:
            c.image_path = repair(c.image_path)
            for costume in (c.costumes or []):
                costume.image_path = repair(costume.image_path)
        for c in project.characters:
            c.image_path = repair(c.image_path)
            for costume in (c.costumes or []):
                costume.image_path = repair(costume.image_path)
        for p in project.global_props:
            p.image_path = repair(p.image_path)
        for p in project.props:
            p.image_path = repair(p.image_path)
        for s in project.global_scenes:
            s.image_path = repair(s.image_path)
        for s in project.scenes:
            s.image_path = repair(s.image_path)
        for sb in project.storyboard:
            sb.frame_path = repair(sb.frame_path)
            sb.video_path = repair(sb.video_path)
        for key, ep_data in (project.episode_data or {}).items():
            for sb_dict in (ep_data.get('storyboard') or []):
                for path_key in ('frame_path', 'video_path'):
                    old_val = sb_dict.get(path_key)
                    new_val = repair(old_val)
                    if new_val != old_val:
                        sb_dict[path_key] = new_val

        if fixed:
            self.save_project(project)

    def get_progress(self, name: str) -> Dict[str, bool]:
        d = self._project_dir(name)
        progress = {}
        progress["project"] = (d / "project.json").exists()

        meta = self._read_json(d / "project.json") or {}
        progress["story"] = bool(meta.get("story", ""))

        chars = self._read_json(d / "characters.json")
        progress["characters"] = bool(chars and isinstance(chars, list) and len(chars) > 0)

        scenes = self._read_json(d / "scenes.json")
        progress["scenes"] = bool(scenes and isinstance(scenes, list) and len(scenes) > 0)

        sb = self._read_json(d / "storyboard.json")
        progress["storyboard"] = bool(sb and isinstance(sb, list) and len(sb) > 0)

        audio = self._read_json(d / "audio.json")
        progress["audio"] = bool(audio and isinstance(audio, list) and len(audio) > 0)

        progress["video"] = bool(meta.get("video_path", ""))

        return progress

    def list_projects(self) -> List[str]:
        if not self.projects_dir.exists():
            return []
        return [
            d.name for d in self.projects_dir.iterdir()
            if d.is_dir() and (d / "project.json").exists()
        ]
    
    def get_project_info(self, name: str) -> Optional[Dict]:
        d = self._project_dir(name)
        meta_file = d / "project.json"
        if not meta_file.exists():
            return None
        data = self._read_json(meta_file)
        if not data:
            return None
        return {
            "name": data.get("name", name),
            "topic": data.get("topic", ""),
            "story": data.get("story", ""),
            "age_group": data.get("age_group", ""),
            "style": data.get("style", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "has_story": bool(data.get("story", "")),
            "length": data.get("length", "long"),
            "has_characters": (d / "characters.json").exists(),
            "has_scenes": (d / "scenes.json").exists(),
            "has_storyboard": (d / "storyboard.json").exists(),
            "has_audio": (d / "audio.json").exists(),
        }

    def delete_project(self, name: str):
        import time
        import sys
        
        # 先加载项目，获取导出文件列表
        project = self.load_project(name)
        exported_files = project.exported_files if project else []
        
        # 删除项目目录
        project_dir = self._project_dir(name)
        if project_dir.exists():
            # Windows 上 shutil.rmtree 可能因文件被占用而失败，使用重试机制
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    shutil.rmtree(project_dir)
                    break
                except OSError as e:
                    if e.winerror == 32 and attempt < max_retries - 1:  # WinError 32: 文件被占用
                        time.sleep(1)  # 等待1秒后重试
                        continue
                    elif attempt == max_retries - 1:
                        # 最后一次尝试失败，使用强制删除
                        import subprocess
                        if sys.platform == 'win32':
                            try:
                                subprocess.run(
                                    ['cmd', '/c', 'rmdir', '/s', '/q', str(project_dir)],
                                    capture_output=True, timeout=10
                                )
                            except Exception:
                                pass
                        else:
                            raise
                    else:
                        raise
        
        # 删除项目目录外的导出文件（如用户指定到其他位置的 final.mp4）
        for file_path in exported_files:
            try:
                p = Path(file_path)
                if p.exists():
                    p.unlink()
                    # 如果父目录是空的，也一并删除（仅限非系统目录）
                    parent = p.parent
                    if parent.exists() and not any(parent.iterdir()) and str(parent) != str(self.projects_dir):
                        parent.rmdir()
            except OSError:
                pass  # 文件被占用或权限不足时跳过，不中断删除流程

    def migrate_legacy_project(self, name: str) -> bool:
        d = self._project_dir(name)
        old_file = d / "project.json"
        if not old_file.exists():
            return False

        data = self._read_json(old_file)
        if not data:
            return False

        has_sub_files = any(
            (d / sub).exists() for sub in self.SUB_FILES.values()
        )
        if has_sub_files:
            return False

        project = Project(
            name=data.get("name", name),
            age_group=data.get("age_group", "5-6\u5c81"),
            style=data.get("style", "\u5361\u901a"),
            story=data.get("story", ""),
            topic=data.get("topic", ""),
            length=data.get("length", "long"),
            video_path=data.get("video_path"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
        project.characters = [Character.from_dict(c) for c in data.get("characters", [])]
        project.scenes = [Scene.from_dict(s) for s in data.get("scenes", [])]
        project.storyboard = [Storyboard.from_dict(sb) for sb in data.get("storyboard", [])]
        project.audio_tracks = [AudioTrack.from_dict(a) for a in data.get("audio_tracks", [])]

        self.save_project(project)
        return True