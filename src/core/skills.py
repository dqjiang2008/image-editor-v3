"""
技能包加载器 - 从 resources/skills/ 读取美术风格包 / 叙事包 / 制作技法包

双轨设计:
- 普通用户「无感」: 在项目信息页选定一个风格包后，producer 的所有 prompt
  构建路径自动注入该包的风格锚定词、必守/严禁规则、视频风格标签，
  用户不需要了解 skill 内部结构。
- 高级用户「手动」: 可在分镜页高级选项里追加/覆盖风格锚定词、导演技法
  注入开关、叙事包约束，或在自由注入框里直接写自定义 prompt 片段。

所有函数对文件缺失/解析失败均静默降级（返回空值），不影响主流程。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path
import re

from src.config.settings import Settings

SKILLS_DIR = Settings.RESOURCES_DIR / "skills"

# 美术风格包（art_skills/*）解析目标文件
_ART_FILES = {
    "character": "art_character.md",
    "character_derivative": "art_character_derivative.md",
    "scene": "art_scene.md",
    "scene_derivative": "art_scene_derivative.md",
    "prop": "art_prop.md",
    "prop_derivative": "art_prop_derivative.md",
    "video": "art_storyboard_video.md",
}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _first_heading(text: str) -> str:
    """取 markdown 第一个一级标题（# 开头）作为展示名，跳过重复标题"""
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+?)\s*(?:风格说明|说明)?$", line.strip())
        if m:
            return m.group(1).strip()
    return ""


def _style_gene(text: str) -> str:
    """从 prefix.md 的「风格基因」表提取一级风格，作为 prompt 风格锚定词"""
    m = re.search(r"\|\s*\*?\*?一级风格\*?\*?\s*\|\s*([^|\n]+?)\s*\|", text)
    if m:
        return m.group(1).strip().strip("*").strip()
    return ""


def _template_section(text: str) -> str:
    """提取「提示词模板」章节正文：跳过其中的表格行，保留纯文本段落/列表

    章节可能以「### xxx（四视图）」等子标题组织，也可能混有约束表格，
    表格行（以 | 开头）不是可注入 prompt 的内容，需要剔除。
    """
    lines = text.splitlines()
    out: List[str] = []
    capturing = False
    level = 0
    for line in lines:
        m = re.match(r"^(#{1,6})\s*(.+)$", line.strip())
        if m:
            heading = m.group(2)
            cur_level = len(m.group(1))
            if "提示词模板" in heading:
                capturing = True
                level = cur_level
                continue
            if capturing and cur_level <= level:
                break
        elif capturing and line.strip():
            stripped = line.strip()
            if stripped.startswith("|"):
                # 模板章节内的表格（输出格式约束/视图定义等）不入 prompt
                continue
            out.append(stripped)
    return "\n".join(out).strip()


def _must_sections(text: str) -> List[str]:
    """提取「必守」「严禁」章节里的规则行（表格行或列表项）"""
    lines = text.splitlines()
    items: List[str] = []
    in_section = False
    level = 0
    for line in lines:
        m = re.match(r"^(#{1,6})\s*(.+)$", line.strip())
        if m:
            heading = m.group(2)
            cur_level = len(m.group(1))
            if "必守" in heading or "严禁" in heading:
                in_section = True
                level = cur_level
                continue
            if in_section and cur_level <= level:
                break
            elif in_section:
                # 同级其他小节（如「通用要求」）不算必守/严禁正文，停止
                if cur_level == level and not ("必守" in heading or "严禁" in heading):
                    in_section = False
                continue
        elif in_section:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "---":
                continue
            # 表格行：取第二列内容（规则正文）；列表项：去掉 -
            if stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if len(cells) >= 2 and cells[1] not in ("", "编号", "---"):
                    items.append(cells[1])
            else:
                items.append(re.sub(r"^[-*]\s*", "", stripped))
    return items


def _video_tags(text: str) -> str:
    """从 art_storyboard_video.md 提取视频风格标签（中文 + 英文均提取，用逗号拼接）"""
    cn_tag = ""
    en_tag = ""
    for line in text.splitlines():
        m = re.search(r"`([^`]+)`", line)
        if not m:
            continue
        tag = m.group(1).strip()
        if "中文" in line or "Seedance" in line:
            if not cn_tag and re.search(r"[\u4e00-\u9fff]", tag):
                cn_tag = tag
        elif "英文" in line or "English" in line:
            if not en_tag:
                en_tag = tag
    if cn_tag and en_tag:
        return f"{cn_tag}，{en_tag}"
    return cn_tag or en_tag


@dataclass
class StylePack:
    """一个美术风格包（art_skills/<id>）的机读视图"""
    id: str
    display_name: str = ""
    style_gene: str = ""          # 一级风格 → 通用风格锚定词
    prefix_rules: List[str] = field(default_factory=list)   # prefix.md 必守/严禁
    character: str = ""           # 角色基础模板
    character_derivative: str = ""  # 造型衍生模板
    scene: str = ""
    scene_derivative: str = ""
    prop: str = ""
    prop_derivative: str = ""
    video_tags: str = ""          # 视频风格标签（中文）
    director: str = ""            # director_storyboard.md 全文（情绪/光影词库）

    def prompt_style(self) -> str:
        """用于替换 project.style 的风格锚定词（如「国风二次元新国潮」）"""
        return self.style_gene or self.display_name

    def _fill(self, template: str, replacements: Dict[str, str]) -> str:
        text = template
        for k, v in replacements.items():
            text = text.replace("{" + k + "}", v)
        text = re.sub(r"\{[^}]*\}", "", text)  # 去掉剩余占位符
        lines = [l.strip() for l in text.splitlines() if l.strip() and l.strip() != "---"]
        # 去掉 markdown 代码块围栏残留
        lines = [l for l in lines if l != "```"]
        text = "，".join(lines)
        # 连续标点压缩：「：；」「, ,」「，，」等 → 单个「，」（保留有意义处不强行合并）
        text = re.sub(r"[:：][;；,，]+\s*", "，", text)
        text = re.sub(r"[，,;；、]+\s*(?=[，,;；、])", "", text)
        text = re.sub(r"([，,])\s*([，,])+", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        # 尾部悬挂的标点
        text = re.sub(r"[，,;；、]+$", "", text)
        return text

    def character_prompt(self, description: str) -> str:
        """角色基础图 prompt：风格模板 + 角色描述 + 必守规则"""
        if not self.character:
            return f"{description}，{self.prompt_style()}风格"
        text = self._fill(self.character, {"角色描述": description})
        must = "，".join(self.prefix_rules[:8])
        return f"{text}，角色描述：{description}，{self.prompt_style()}风格。{must}"

    def costume_prompt(self, base_desc: str, costume_desc: str, is_female: bool = False, is_nonhuman: bool = False) -> str:
        """造型 prompt

        Args:
            base_desc: 底模外观描述
            costume_desc: 造型变化描述
            is_female: 是否为女性角色（仅人物类型生效）
            is_nonhuman: 是否为非人物类型（能量体/动物/群体—跳过人体模板）
        """
        if is_nonhuman:
            return f"{base_desc}，{costume_desc}，{self.prompt_style()}风格"
        gender_tag = "女性角色" if is_female else "男性角色"
        if not self.character_derivative:
            return f"{gender_tag}，{base_desc}，造型变化：{costume_desc}，{self.prompt_style()}风格"
        p = (
            f"{gender_tag}，古风角色四视图设定图，真人写实摄影，古风写实纪实，"
            f"强对比度，极致细节，8K，超保真，character design sheet，character turnaround，"
            f"保持基础形象面容不变，"
            f"底模外观：{base_desc}，造型变化：{costume_desc}，"
            f"【L1·妆容】皮肤细腻，水光奶油瓷肌，妆容贴合角色设定，"
            f"【L2·发型】发丝根根分明，发型与服饰配套，"
            f"【L3+L4·服饰】衣服质感清晰，纹理超清晰，层次分明，"
            f"【L5·配饰】配饰精致，与整体风格统一，"
            f"同一画面左至右并排：人像特写+正视图+侧视图+后视图，"
            f"自然站立，纯净中性灰背景，均匀柔光，无硬阴影，"
            f"四视图一致性，面容细腻渲染，发丝细腻渲染，纹理细节超清晰，"
            f"图中不要有任何文字"
        )
        return f"{p}，{self.prompt_style()}风格"

    def scene_prompt(self, description: str, time_of_day: str = "day") -> str:
        """场景图 prompt：场景模板 + 场景描述 + 时段"""
        if not self.scene:
            return f"{description}，{self.prompt_style()}风格，{time_of_day}"
        text = self._fill(self.scene, {
            "场景类型": description,
            "室内/室外": "室内" if "内" in description else "室外",
            "季节+时间": time_of_day, "时间": time_of_day,
        })
        return f"{text}，场景：{description}，{self.prompt_style()}风格，{time_of_day}"

    def prop_prompt(self, description: str) -> str:
        """道具图 prompt：道具模板 + 道具描述"""
        if not self.prop:
            return f"{description}，{self.prompt_style()}风格"
        text = self._fill(self.prop, {"道具类型": description, "道具描述": description})
        return f"{text}，道具：{description}，{self.prompt_style()}风格"

    def frame_style_suffix(self) -> str:
        """分镜帧/视频 prompt 末尾追加的风格锁定短语"""
        parts = [self.prompt_style() + "风格"]
        if self.video_tags:
            parts.append(self.video_tags)
        return "，".join(parts)

    def storyboard_image_constraint(self) -> str:
        """分镜帧 img2img 时的追加约束（风格一致性）"""
        return "，画面风格与参考图保持一致，" + self.frame_style_suffix()

    def video_style_suffix(self) -> str:
        """视频 prompt 末尾追加的风格标签 + 严禁项约束（防止写实风格滑向3D动画）"""
        parts = [self.frame_style_suffix()]
        anti_3d = []
        for rule in self.prefix_rules:
            rl = rule.lower()
            if any(kw in rl for kw in ("严禁", "禁止", "不得")) and any(kw in rl for kw in ("3d", "三维", "卡通", "动漫", "动画", "二次元", "插画", "渲染")):
                anti_3d.append(rule)
        if anti_3d:
            parts.append("；".join(anti_3d))
        return "，".join(parts)


_PACKS: Dict[str, StylePack] = {}
_LOADED = False


def _load_packs():
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    art_dir = SKILLS_DIR / "art_skills"
    if not art_dir.exists():
        return
    for d in sorted(art_dir.iterdir()):
        if not d.is_dir():
            continue
        pack = StylePack(id=d.name)
        readme = _read_text(d / "README.md")
        pack.display_name = _first_heading(readme) or d.name
        prefix_text = _read_text(d / "prefix.md")
        pack.style_gene = _style_gene(prefix_text)
        pack.prefix_rules = _must_sections(prefix_text)
        if not pack.display_name:
            pack.display_name = pack.style_gene or d.name
        art_prompt_dir = d / "art_prompt"
        for key, fname in _ART_FILES.items():
            text = _read_text(art_prompt_dir / fname) if art_prompt_dir.exists() else ""
            if key == "video":
                pack.video_tags = _video_tags(text)
            else:
                setattr(pack, key, _template_section(text))
        director_dir = d / "driector_skills"
        if director_dir.exists():
            pack.director = _read_text(director_dir / "director_storyboard.md")
        _PACKS[pack.id] = pack


def list_style_packs() -> List[StylePack]:
    """列出全部可用美术风格包"""
    _load_packs()
    return list(_PACKS.values())


def get_style_pack_by_display(display: str) -> Optional[StylePack]:
    """按 GUI 下拉框展示名反查风格包（用于项目信息页/分镜页下拉框）"""
    if not display:
        return None
    for p in list_style_packs():
        if p.display_name == display or p.style_gene == display or p.id == display:
            return p
    return None


def get_style_pack(pack_id: str) -> Optional[StylePack]:
    """按 id 获取风格包；空 id 或未找到返回 None"""
    _load_packs()
    if not pack_id:
        return None
    return _PACKS.get(pack_id)


def style_pack_options() -> List[str]:
    """GUI 下拉框选项：展示名列表（调用方自行加「默认」项）"""
    return [p.display_name for p in list_style_packs()]


# 风格 → 美术风格包 ID 映射
_STYLE_TO_PACK_IDS: Dict[str, List[str]] = {
    "写实": [
        "realpeople_ancient_chinese",
        "realpeople_modern_city",
        "realpeople_urban_modern",
    ],
    "卡通": [
        "2D_90s_japanese_anime",
        "2D_chinese_guofeng",
        "2D_mature_urban_romance",
        "2D_flat_design",
    ],
    "3D": [
        "3D_anime_render",
        "3D_chinese_traditional",
        "3D_clay_stopmotion",
        "3D_guofeng_cyber",
    ],
    "水彩": [
        "watercolor_painting",
    ],
    "手绘": [
        "hand_drawn_sketch",
    ],
    "像素": [
        "pixel_art",
    ],
    "油画": [
        "oil_painting",
    ],
}


def style_pack_options_for_style(style_name: str) -> List[str]:
    """按项目风格筛选对应的美术风格包展示名列表"""
    pack_ids = _STYLE_TO_PACK_IDS.get(style_name, [])
    if not pack_ids:
        return []
    result = []
    for p in list_style_packs():
        if p.id in pack_ids:
            result.append(p.display_name)
    return result


# ============================================================
# 叙事包（story_skills/*）
# ============================================================

@dataclass
class StoryPack:
    """一个叙事类型技能包（story_skills/<id>）的机读视图"""
    id: str
    display_name: str = ""
    planning: str = ""        # 叙事规划手法（注入故事生成）
    storyboard_table: str = ""  # 分镜表叙事手法（注入分镜脚本生成）

    def guidance(self, stage: str = "planning") -> str:
        """按阶段返回叙事手法指导文本"""
        if stage == "storyboard":
            return self.storyboard_table
        return self.planning


_story_packs: Dict[str, StoryPack] = {}
_story_loaded = False


def _load_story_packs():
    global _story_loaded
    if _story_loaded:
        return
    _story_loaded = True
    base = SKILLS_DIR / "story_skills"
    if not base.exists():
        return
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        pack = StoryPack(id=d.name)
        readme = _read_text(d / "README.md")
        pack.display_name = _first_heading(readme) or d.name
        director_dir = d / "driector_skills"
        if director_dir.exists():
            pack.planning = _read_text(director_dir / "director_planning_narrative.md")
            pack.storyboard_table = _read_text(
                director_dir / "director_storyboard_table_narrative.md"
            )
        _story_packs[pack.id] = pack


def list_story_packs() -> List[StoryPack]:
    """列出全部叙事包"""
    _load_story_packs()
    return list(_story_packs.values())


def get_story_pack_by_display(display: str) -> Optional[StoryPack]:
    """按 GUI 下拉框展示名反查叙事包"""
    if not display:
        return None
    for p in list_story_packs():
        if p.display_name == display or p.id == display:
            return p
    return None


def get_story_pack(pack_id: str) -> Optional[StoryPack]:
    if not pack_id:
        return None
    _load_story_packs()
    return _story_packs.get(pack_id)


def story_pack_options() -> List[str]:
    """GUI 下拉框选项：展示名列表"""
    return [p.display_name for p in list_story_packs()]


# ============================================================
# 制作技法包（production_skills/*.md）
# ============================================================

_PRODUCTION_DOCS = {
    "storyboard_table": "storyboard_table_techniques.md",
    "storyboard_prompt": "storyboard_prompt_techniques.md",
}


def production_technique_text(kind: str = "storyboard_table") -> str:
    """返回制作技法文档原文（注入到分镜脚本生成的约束上下文）"""
    fname = _PRODUCTION_DOCS.get(kind, kind if kind.endswith(".md") else None)
    if not fname:
        return ""
    return _read_text(SKILLS_DIR / "production_skills" / fname)


def production_technique_options() -> List[str]:
    """GUI 下拉框选项：可用技法包展示名"""
    labels = {
        "storyboard_table": "分镜表通用技法",
        "storyboard_prompt": "分镜提示词通用技法",
    }
    opts = []
    for kind in _PRODUCTION_DOCS:
        if (SKILLS_DIR / "production_skills" / _PRODUCTION_DOCS[kind]).exists():
            opts.append(labels.get(kind, kind))
    return opts


def production_technique_by_display(display: str) -> str:
    """按 GUI 展示名取技法原文（高级用户手动使用）"""
    labels = {
        "分镜表通用技法": "storyboard_table",
        "分镜提示词通用技法": "storyboard_prompt",
    }
    kind = labels.get(display, "")
    return production_technique_text(kind) if kind else ""


# ============================================================
# 视频提示词模式（resources/modelPrompt/video/*）
# ============================================================

_VIDEO_PROMPT_DIR = Settings.RESOURCES_DIR / "modelPrompt" / "video"

_VIDEO_MODE_DISPLAY_NAMES = {
    "seedance2Multi-parameterMode.md": "Seedance 2.0 多参模式",
    "universalFirstAndLastFrameMode.md": "通用首尾帧模式",
    "universalMulti-parameterMode.md": "通用多参模式",
    "wan2.6Single-imageFirstFrameMode.md": "Wan 2.6 单图首帧模式",
}


def video_prompt_mode_options() -> List[str]:
    """GUI 下拉框选项：可用视频提示词模式展示名"""
    opts = []
    if not _VIDEO_PROMPT_DIR.exists():
        return opts
    for f in sorted(_VIDEO_PROMPT_DIR.iterdir()):
        if f.suffix == ".md":
            display = _VIDEO_MODE_DISPLAY_NAMES.get(f.name, f.stem)
            opts.append(display)
    return opts


def video_prompt_mode_file(display: str) -> Optional[Path]:
    """按 GUI 展示名反查视频提示词模式文件路径"""
    if not display:
        return None
    for fname, dname in _VIDEO_MODE_DISPLAY_NAMES.items():
        if dname == display:
            p = _VIDEO_PROMPT_DIR / fname
            return p if p.exists() else None
    for f in sorted(_VIDEO_PROMPT_DIR.iterdir()):
        if f.suffix == ".md" and f.stem == display:
            return f
    return None


def video_prompt_mode_text(display: str) -> str:
    """按 GUI 展示名取视频提示词模式原文（注入到视频 prompt 生成的约束上下文）"""
    p = video_prompt_mode_file(display)
    return _read_text(p) if p else ""


# ============================================================
# Project 级便捷入口（producer 各 prompt 构建点统一调用）
# ============================================================

def active_style_pack(project) -> Optional[StylePack]:
    """取项目当前启用的风格包；未启用返回 None"""
    return get_style_pack_by_display(getattr(project, "skill_pack", "") or "")


def active_story_pack(project) -> Optional[StoryPack]:
    """取项目当前启用的叙事包；未启用返回 None"""
    return get_story_pack_by_display(getattr(project, "story_pack", "") or "")