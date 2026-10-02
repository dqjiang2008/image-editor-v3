"""
视频生成器 - 核心业务逻辑
"""
import asyncio
import json
from pathlib import Path
from typing import Optional, Callable, List, Dict
from src.config.settings import Settings
from src.services.agnes_client import (
    AgnesAIClient,
    NETWORK_ERRORS,
    is_real_cancellation,
    raise_if_real_cancel,
)
from src.services import asset_matcher as _am
from src.models.project import Project, Character, Scene, Storyboard, Prop
from src.core import skills as _skills

# 故事长度档位 → 每集目标视频时长（秒）
LENGTH_DURATION_MAP = {"very_short": 180, "short": 300, "medium": 600, "long": 1200}

# 每个分镜描述末尾的全局媒体约束说明（随描述进入视频/图像生成 prompt）
DESCRIPTION_MEDIA_SUFFIX = (
    "视频全程不要字幕、不要屏幕文字；"
    "必须保留协调统一的全局BGM和必要环境音，禁止静音段。"
)

VIDEO_MOTION_SUFFIX = (
    "视频必须有明显的镜头运动和角色动作变化，"
    "禁止静态画面或角色原地不动，首帧仅作为起始构图参考，"
    "角色应在画面中自然移动、转身、走动或做出动作，"
    "镜头应推拉摇移产生景别变化"
)


import re
from dataclasses import asdict

_STORYBOARD_HEADER_RE = re.compile(r'^分\s*镜\s*(\d+)', re.IGNORECASE)
_FIELD_RE = re.compile(r'^(时长|场景|镜头|角色|动作|声音|画面|对白|旁白)\s*[：:]\s*(.*)')
_DURATION_RE = re.compile(r'时长[：:]\s*(\d+(?:\.\d+)?)\s*分钟')
_SCENES_RE = re.compile(r'场景[：:]\s*(.+)')
_SECTION_BLOCK_RE = re.compile(
    r'^###\s*(角色描述|场景描述|关键动作|对话)\s*\n', re.MULTILINE
)

# 服装/造型表格列名别名（表头模糊匹配，覆盖 逆天布衣_详细剧情与角色设计 等 md 的表头变体）
_COSTUME_COL_ALIASES = {
    "角色": ("角色", "人物"),
    "造型": ("造型", "服装", "换装"),
    "集数": ("集数", "适用集数", "出场集数", "段落", "阶段", "集"),
    "颜色": ("颜色", "配色"),
    "特征": ("关键特征", "特征", "造型变化", "造型描述", "描述", "细节", "状态标记", "状态"),
}

# 自由文本解析时排除的元数据字段名（角色参考卡中的字段名，不是角色名）
_FREETEXT_CHAR_EXCLUDE = {
    "年龄", "身高", "体重", "发型", "头发", "发色", "瞳色", "眼色", "外貌", "特征",
    "基础特征", "外貌特征", "性格", "性格特征", "背景", "背景故事", "剧情", "剧情发展",
    "道具", "场景", "角色", "武器", "能力", "境界", "服装",
    "本节目标时长", "本节镜头数", "分镜编码",
    "人物", "描述", "关键剧情", "成长线", "变化",
    "项目", "内容", "备注", "说明",
    "集", "节", "场", "段",
    # 道具表/场景表字段
    "持有者", "功能", "视觉特征", "出场集数", "适用集数",
    "主场景", "分场景", "场名",
    # 角色参考卡副字段
    "原名", "别称", "其他特征", "其他", "登场阶段", "关联",
    "基础", "细节",
    # 段标题（非角色名）
    "服装变化", "外貌特征", "基础特征", "性格特征", "关键道具",
}


def _norm_character_name(name: str) -> str:
    """角色名归一化：去掉括号注释和 * / 等装饰，用于模糊匹配。
    「老叫花子（九指神丐）」→「老叫花子」「赵无极（九指剑圣）」→「赵无极」
    """
    s = re.sub(r"[（(][^（）()]*[）)]", "", (name or "").strip())
    s = s.replace("*", "").replace("-", "").replace(" ", "")
    return s


# Windows 文件名非法字符 → 全角替代（保持可读性）；控制字符 → 下划线
_FILENAME_ILLEGAL_MAP = str.maketrans({
    "\\": "＼", "/": "／", ":": "：", "*": "＊",
    "?": "？", '"': "＂", "<": "＜", ">": "＞", "|": "｜",
})


def _safe_filename(name: str) -> str:
    """把角色/造型/道具/场景名转成合法的 Windows 文件名。

    「老叫花子（九指神丐/师父）」→「老叫花子（九指神丐／师父）」：
    半角斜杠等非法字符若不处理，Path 会把它当成目录分隔符，写文件时报
    [Errno 2] No such file or directory（父目录「老叫花子（九指神丐」不存在）。
    """
    s = (name or "").strip().translate(_FILENAME_ILLEGAL_MAP)
    s = re.sub(r"[\x00-\x1f]", "_", s)
    s = s.rstrip(". ")  # Windows 文件名不能以点或空格结尾
    return s or "unnamed"


# —— 角色/造型图的「单人单套服装」约束 ——
# 角色描述经 AI 扩写后常包含「早期穿X、中期换Y、后期着Z」的多套服装叙事，
# 绘图模型会把不同时期的形象画进同一张图（同框多人/换装对比图），导致
# 底模图与造型图里出现 3 个人。处理方式（项目数据不动，只在出图提示词里净化）：
# ① 剔除多套服装/多阶段叙事句；② 追加单人硬约束。
_FEMALE_NAMES = {"母亲", "陈瑶", "林婉儿", "紫瑶"}

SINGLE_PERSON_RULE = (
    "单人构图，画面中只能出现一个人物，同一角色只穿一套服装，"
    "禁止同框出现同一角色的多个形象、多个时期或换装对比，"
    "彩色画面，禁止黑白或灰度"
)

_SENT_SPLIT = "。！？.!?；;"  # 句子边界（含分号，避免误删分号后的无关句子）

_MULTI_OUTFIT_PATTERNS = [
    # 「服装随剧情进展多次变化：早期穿…，中期换…，后期着…。」整句剔除
    re.compile(
        rf"[^{_SENT_SPLIT}]*服装[^{_SENT_SPLIT}]*变化[：:]?[^{_SENT_SPLIT}]*[。！？.!?]?"
    ),
    # 「早期/初期/初时…，中期/后期/晚期…。」多阶段叙事句剔除
    re.compile(
        rf"[^{_SENT_SPLIT}]*(?:早期|初期|初时)[^{_SENT_SPLIT}]*(?:中期|后期|晚期)[^{_SENT_SPLIT}]*[。！？.!?]?"
    ),
]


def _single_subject_desc(desc: str) -> str:
    """把角色描述净化为「单人单套服装」的出图描述（仅用于提示词，不回写项目数据）。"""
    text = (desc or "").strip()
    for pat in _MULTI_OUTFIT_PATTERNS:
        text = pat.sub("", text)
    # 清理剔除后残留的悬挂标点
    text = re.sub(r"\s+", " ", text)
    text = re.sub(rf"[，,、；;]+(?=[，,、；;。！？.!?])", "", text)
    text = re.sub(rf"(?<=[。！？.!?])[，,、；;]+", "", text)  # 句末后紧跟的悬挂逗号/分号
    text = re.sub(r"^[\s，,、；;。]+", "", text)
    text = re.sub(rf"[，,、；;]+$", "", text)
    return text.strip()


_SINGLE_PERSON_PHRASE_PATTERNS = [
    re.compile(r"单人角色设定图[^，,、；;]*"),
    re.compile(r"单人构图[^，,、；;]*"),
    re.compile(r"画面中只能出现一个人物[^，,、；;]*"),
    re.compile(r"只描述一个人物[^，,、；;]*"),
    re.compile(r"同一角色只穿一套服装[^，,、；;]*"),
    re.compile(r"禁止同框出现[^，,、；;]*"),
    re.compile(r"禁止出现多人[^，,、；;]*"),
    re.compile(r"禁止出现[^，,、；;]*多人[^，,、；;]*"),
    re.compile(r"禁止出现[^，,、；;]*群体[^，,、；;]*"),
    re.compile(r"禁止[^，,、；;]*对比图[^，,、；;]*"),
    re.compile(r"只能出现一个人物[^，,、；;]*"),
    re.compile(r"禁止任何偏移或随意变化[^，,、；;]*"),
    re.compile(r"[、，]群体[^，,、；;]*"),
    re.compile(r"[、，]对比图[^，,、；;]*"),
    re.compile(r"等描述"),
]


def _infer_length_from_duration(total_seconds: float) -> str:
    """根据实际总时长反推最接近的长度档位"""
    if total_seconds <= 240:
        return "very_short"
    elif total_seconds <= 420:
        return "short"
    elif total_seconds <= 900:
        return "medium"
    else:
        return "long"


def _strip_single_person_phrases(desc: str) -> str:
    """从角色描述中剥离「单人构图」相关短语，用于分镜图 prompt（分镜可含多角色）。

    角色描述在扩写时被指示写成「单人角色设定图」风格（含"只描述一个人物"、
    "禁止出现多人"等约束），这对角色设定图生成是正确的，但注入分镜图 prompt 时
    会导致 AI 绘图模型只画一个人——即使该分镜有多个角色。此函数在分镜图 prompt
    构建路径上净化这些短语，保留外观描述部分。
    """
    text = (desc or "").strip()
    for pat in _SINGLE_PERSON_PHRASE_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(rf"[，,、；;]+(?=[，,、；;。！？.!?])", "", text)
    text = re.sub(rf"(?<=[。！？.!?])[，,、；;]+", "", text)
    text = re.sub(r"^[\s，,、；;。]+", "", text)
    text = re.sub(rf"[，,、；;]+$", "", text)
    return text.strip()


def _is_md_sep_row(line: str) -> bool:
    """判断是否为 Markdown 表格分隔行（|---|---| 形式）"""
    if "|" not in line:
        return False
    cells = [c.strip() for c in line.split("|") if c.strip()]
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c) for c in cells)


def _strip_character_suffix(header: str) -> str:
    """从造型列表头提取角色名：「陈炎服装」→「陈炎」「林婉儿造型」→「林婉儿」"""
    m = re.match(r"^(?P<char>.+?)(造型|服装|换装)$", header.strip())
    return m.group("char") if m else ""


def _split_cells(line: str) -> list:
    """把表格行切成单元格列表；含 | 按 md 规则，含 tab 按列对齐切分"""
    if "|" in line:
        parts = [c.strip() for c in line.split("|")]
        # 去掉首尾空单元格（| a | b | 形式）
        if parts and parts[0] == "":
            parts = parts[1:]
        if parts and parts[-1] == "":
            parts = parts[:-1]
        return parts
    return line.split("\t")


def parse_costume_table(text: str) -> List[dict]:
    """解析服装/造型表格文本，返回造型条目列表。

    支持两种输入格式：
    1. Markdown 表格：`| 集数 | 角色 | 造型 | 颜色 | 关键特征 | 状态标记 |`
       或普通空格表格（表头行含「角色」「造型」等列名）
    2. 自由文本：每行「角色名：造型名，特征描述」或「角色名/造型名 描述」

    表头行通过「角色列 + 造型列」双列命中 + 下一行是 Markdown 分隔行（|---|---|）
    确认；找不到表头时，按「角色名 造型名 描述...」（空格/制表符分隔 ≥2 段）逐行解析。
    支持文中出现多张造型表，每张独立解析并合并结果。
    若「造型」列名含角色名前缀（如「陈炎服装」），自动提取角色名，可解析无角色列表。

    Returns:
        [{"character": 角色名, "name": 造型名, "description": 特征描述(含适用集数),
          "episodes": 适用集数字符串(可为空)}, ...]
        解析失败或无有效条目时返回 []。
    """
    raw_lines = [ln.strip() for ln in (text or "").splitlines()]
    raw_lines = [ln for ln in raw_lines if ln]

    # 定位所有表头行：在 raw_lines 中找到所有「角色列+造型列」双命中且下一行是分隔行的行
    header_indices = []
    for i, ln in enumerate(raw_lines):
        if "|" not in ln:
            continue
        has_char = any(a in ln for a in _COSTUME_COL_ALIASES["角色"])
        has_cost = any(a in ln for a in _COSTUME_COL_ALIASES["造型"])
        if not (has_char and has_cost):
            continue
        if i + 1 < len(raw_lines) and _is_md_sep_row(raw_lines[i + 1]):
            header_indices.append(i)

    lines = [ln for ln in raw_lines if not set(ln) <= set("-|: ")]
    entries: List[dict] = []

    if header_indices:
        # 逐个解析每张表
        for ti, header_idx in enumerate(header_indices):
            header_line = raw_lines[header_idx]
            # 计算本表在 raw_lines 中的行范围（到下一个表头或文件末尾）
            raw_end = header_indices[ti + 1] if ti + 1 < len(header_indices) else len(raw_lines)
            # 提取本表数据行（跳过分隔行和空行），映射到 lines 中的位置
            table_data_raw = [ln for ln in raw_lines[header_idx + 1:raw_end]
                              if not set(ln) <= set("-|: ") and ln]
            if not table_data_raw:
                continue
            try:
                filtered_header_idx = lines.index(header_line)
                data_start = lines.index(table_data_raw[0], filtered_header_idx + 1)
                data_end = data_start + sum(1 for ln in raw_lines[header_idx + 1:raw_end]
                                            if ln in lines)
            except ValueError:
                continue

            cols = _split_cells(header_line)
            # 列映射：每个别名→匹配到的所有列索引（支持多列合并，如关键特征+状态标记）
            col_map: dict = {}
            for alias, keys in _COSTUME_COL_ALIASES.items():
                indices = [j for j, c in enumerate(cols) if any(k in c for k in keys)]
                if indices:
                    col_map[alias] = indices
            # 无「角色」列时：从造型列表头提取角色名
            fixed_char = ""
            if "角色" not in col_map and "造型" in col_map:
                fixed_char = _strip_character_suffix(cols[col_map["造型"][0]])
            for ln in lines[data_start:data_end]:
                ln = ln.strip()
                if not ln or ln.startswith("#") or _is_md_sep_row(ln):
                    continue
                cells = _split_cells(ln)
                if len(cells) < 2:
                    continue

                def _get(alias, _cells=cells):
                    indices = col_map.get(alias, [])
                    parts = [_cells[i] for i in indices if i < len(_cells)]
                    return "，".join(parts)

                char_name = _get("角色") or fixed_char or (
                    "" if "角色" in col_map or fixed_char else cells[0])
                cost_name = _get("造型") or (
                    "" if "造型" in col_map else (cells[1] if len(cells) > 1 else ""))
                if not char_name:
                    continue
                desc_parts = ([cost_name] if cost_name else []) + [p for p in (_get("颜色"), _get("特征")) if p]
                ep = _get("集数")
                entries.append({
                    "character": char_name,
                    "name": cost_name or "",
                    "description": "；".join(desc_parts),
                    "episodes": ep,
                })
    else:
        # 自由文本：每行「角色名：造型名，描述」或「角色名/造型名 描述」
        # 先统一将半角冒号转全角，后续统一按全角冒号拆分
        _normalized_lines = []
        for ln in lines:
            # 找第一个冒号位置（全角或半角），将半角替换为全角
            idx_half = ln.find(":")
            idx_full = ln.find("：")
            if idx_half >= 0 and (idx_full < 0 or idx_half < idx_full):
                ln = ln.replace(":", "：", 1)
            _normalized_lines.append(ln)
        for ln in _normalized_lines:
            if "：" in ln:
                head, tail = ln.split("：", 1)
                char_name = head.strip().strip("*")
                # 跳过元数据字段名（如「年龄：17岁」「服装：粗布」不是角色：造型）
                if char_name in _FREETEXT_CHAR_EXCLUDE:
                    continue
                if "/" in tail:
                    cost_name, desc = [p.strip() for p in tail.split("/", 1)]
                else:
                    parts = [p.strip() for p in tail.split("，", 1)]
                    cost_name = parts[0] if parts else ""
                    desc = parts[1] if len(parts) > 1 else ""
            elif "/" in ln:
                char_name, rest = [p.strip() for p in ln.split("/", 1)]
                if char_name in _FREETEXT_CHAR_EXCLUDE:
                    continue
                parts = [p.strip() for p in rest.split("，", 1)]
                cost_name = parts[0] if parts else ""
                desc = parts[1] if len(parts) > 1 else ""
            else:
                continue
            if not char_name or not cost_name:
                continue
            entries.append({
                "character": char_name,
                "name": cost_name,
                "description": desc,
                "episodes": "",
            })

    # 清洗：把适用集数并入描述（让造型描述自带集数上下文，生成时 LLM 可感知段落）
    for e in entries:
        ep = (e.get("episodes") or "").strip()
        if ep and ep not in e["description"]:
            e["description"] = f"{ep}｜{e['description']}" if e["description"] else ep
    return [e for e in entries if e.get("character") or e.get("name")]


def parse_storyboards_from_text(text: str) -> Optional[List[dict]]:
    """从剧本文本中直接解析已有的分镜结构。

    支持格式：
        分镜 1
        时长：5s
        场景：xxx
        镜头：xxx
        角色：xxx
        动作：xxx
        声音：xxx
        对白：xxx（可选）
        画面：xxx（可选）

    Returns:
        解析成功返回 list[dict]，无法解析返回 None。
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    storyboards = []
    current = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        header_match = _STORYBOARD_HEADER_RE.match(stripped)
        if header_match:
            if current is not None:
                storyboards.append(current)
            current = {"scene_number": int(header_match.group(1))}
            continue

        if current is None:
            continue

        field_match = _FIELD_RE.match(stripped)
        if field_match:
            key, value = field_match.group(1), field_match.group(2).strip()
            if key == "时长":
                dur_match = re.match(r'([\d.]+)\s*s', value)
                current["duration"] = float(dur_match.group(1)) if dur_match else 5.0
            elif key == "场景":
                current["scene_name"] = value
                current["scene"] = value
            elif key == "镜头":
                current["camera"] = value
            elif key == "角色":
                raw_chars = [c.strip().rstrip('。') for c in re.split(r'[、,，]', value) if c.strip()]
                current["characters"] = raw_chars
            elif key == "动作":
                current["action"] = value
            elif key == "声音":
                current["sound"] = value
            elif key == "画面":
                current["visual_note"] = value
            elif key == "对白":
                current["dialogue"] = value
                role_match = re.match(r'(.+?)\s*[：:说]', value)
                if role_match:
                    current["dialogue_role"] = role_match.group(1).strip()
            elif key == "旁白":
                current["dialogue"] = value
                current["dialogue_role"] = "旁白"

    if current is not None:
        storyboards.append(current)

    return storyboards if storyboards else None


def parse_md_storyboard_tables(text: str) -> Optional[List[dict]]:
    """从 AI 剧本 Markdown 文件中解析分镜表格和对话。

    支持格式（1-3.md / 4-7.md / 8-10.md）：
    - 每节有 **角色** 字段列出角色名
    - 分镜脚本为 Markdown 表格：镜号 | 景别 | 运镜 | 画面内容 | 特效 | 音效 | 时长
    - **对话** 部分含完整对话文本
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    storyboards = []
    current_section_chars = []
    current_section_scene = ""
    current_section_name = ""
    in_dialogue = False
    dialogue_lines = []
    in_ai_prompt = False
    shot_index = 0
    all_dialogues = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        section_match = re.match(
            r'##\s*(?:第[一二三四五六七八九十\d]+节|S\d+)\s*[：:]*\s*(.*)', stripped
        )
        if section_match:
            current_section_name = section_match.group(1).strip()
            current_section_chars = []
            current_section_scene = ""
            in_dialogue = False
            in_ai_prompt = False
            continue

        char_match = re.match(r'\*\*角色\*\*\s*[：:]*\s*(.*)', stripped)
        if char_match:
            raw = char_match.group(1).strip()
            current_section_chars = [
                c.strip().rstrip('。')
                for c in re.split(r'[、,，]', raw)
                if c.strip()
            ]
            continue

        scene_match = re.match(r'\*\*场景\*\*\s*[：:]*\s*(.*)', stripped)
        if scene_match:
            current_section_scene = normalize_scene_name(scene_match.group(1).strip())
            continue

        if re.match(r'\*\*对话\*\*\s*[：:]*', stripped):
            in_dialogue = True
            in_ai_prompt = False
            dialogue_lines = []
            continue

        if re.match(r'\*\*AI生成提示词\*\*\s*[：:]*', stripped):
            in_ai_prompt = True
            in_dialogue = False
            continue

        if re.match(r'\*\*情感基调\*\*\s*[：:]*', stripped):
            if in_dialogue and dialogue_lines:
                all_dialogues.append({
                    "section": current_section_name,
                    "text": "\n".join(dialogue_lines),
                    "characters": list(current_section_chars),
                })
            in_dialogue = False
            in_ai_prompt = False
            continue

        if in_dialogue:
            if stripped.startswith('**') or stripped.startswith('#'):
                if dialogue_lines:
                    all_dialogues.append({
                        "section": current_section_name,
                        "text": "\n".join(dialogue_lines),
                        "characters": list(current_section_chars),
                    })
                    dialogue_lines = []
                in_dialogue = False
            else:
                dialogue_lines.append(stripped)
            continue

        if in_ai_prompt:
            continue

        if stripped.startswith('|') and not re.match(r'^\|[\s\-:|]+\|$', stripped):
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            if len(cells) < 4:
                continue

            if '画面内容' in cells or '镜号' in cells:
                continue

            shot_id = cells[0] if len(cells) > 0 else ""
            camera_type = cells[1] if len(cells) > 1 else ""
            camera_move = cells[2] if len(cells) > 2 else ""
            content = cells[3] if len(cells) > 3 else ""
            effect = cells[4] if len(cells) > 4 else ""
            sound = cells[5] if len(cells) > 5 else ""
            duration_str = cells[6] if len(cells) > 6 else "6s"

            if not content and not shot_id:
                continue

            dur_match = re.match(r'([\d.]+)\s*s', duration_str)
            duration = float(dur_match.group(1)) if dur_match else 6.0

            voice_text = ""
            dialogue_role = ""
            frame_chars = list(current_section_chars)

            dialogue_patterns = [
                r'([\u4e00-\u9fff]{1,6})(?:说|道|喊|叫|问|答|低声|沉声)\s*[：:]*\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
                r'([\u4e00-\u9fff]{1,6})\s*[：:]\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
            ]
            for pattern in dialogue_patterns:
                m = re.search(pattern, content)
                if m:
                    role_candidate = m.group(1).strip()
                    voice_candidate = m.group(2).rstrip("。")
                    for cn in current_section_chars:
                        norm_cn = re.sub(r'[（(][^（）()]*[）)]', '', cn).strip()
                        if role_candidate in (cn, norm_cn) or norm_cn.startswith(role_candidate):
                            dialogue_role = cn
                            voice_text = voice_candidate
                            break
                    if not dialogue_role:
                        voice_text = voice_candidate
                    break

            shot_index += 1
            sb = {
                "scene_number": shot_index,
                "scene_name": current_section_name or current_section_scene,
                "scene": current_section_scene,
                "description": content,
                "characters": frame_chars,
                "dialogue": voice_text,
                "dialogue_role": dialogue_role,
                "voice_text": voice_text,
                "action": f"{camera_move}。{effect}" if effect else camera_move,
                "camera": f"{camera_type}，{camera_move}" if camera_move else camera_type,
                "duration": duration,
            }
            storyboards.append(sb)

    if in_dialogue and dialogue_lines:
        all_dialogues.append({
            "section": current_section_name,
            "text": "\n".join(dialogue_lines),
            "characters": list(current_section_chars),
        })

    if all_dialogues and storyboards:
        _match_dialogues_to_shots(storyboards, all_dialogues)

    return storyboards if storyboards else None


def parse_md_section_shots(text: str) -> Optional[List[dict]]:
    """解析单节 Markdown 文本中的分镜表格（逆天布衣_剧本.md 格式）。

    支持两种节标题格式：
    - ``## 第一节：铁匠铺的黄昏``（第1-3集「故事+分镜」格式）
    - ``## E04-S01 第一关·幻境``（第4-10集「场景优化版」格式，E{集}-S{节} 编码）

    分镜表列：镜号 | 场景/区域 | 景别 | 运镜 | 画面内容 | 特效 | 音效 | 时长。
    场景/区域列（如「A 青阳镇全景」）与「**分场景**」行解析为
    分场景码→场景名映射（按码去重，每码一条），每镜挂在自身条目上。
    主场景含「→」（场景转换）时取箭头前段作为基准。
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    storyboards: List[dict] = []
    section_chars: List[str] = []
    section_scene = ""
    section_title = ""
    in_dialogue = False
    dialogue_lines: List[str] = []
    all_dialogues: List[dict] = []
    sub_scene_map: Dict[str, str] = {}

    _SHOT_CODE_RE = re.compile(r'^(E\d+-S\d+)-C\d+\s*$')
    _SUB_CODE_RE = re.compile(r'^([A-Z])\s+(\S.*)$')

    def _sub_scene_name(code: str, area_name: str) -> str:
        base = section_scene.split('·')[-1] if section_scene else ""
        raw = f"{base}·{area_name}" if base else area_name
        return normalize_scene_name(raw)

    for line in lines:
        stripped = line.strip()

        m_sec = re.match(r'#{1,3}\s*第[一二三四五六七八九十\d]+节[：:]\s*(.*)', stripped)
        m_ecode = re.match(r'#{1,3}\s*E\d+-S\d+\s*[：:]?\s*(.*)', stripped)
        if m_sec or m_ecode:
            section_title = (m_sec.group(1) if m_sec else m_ecode.group(1)).strip()
            section_chars = []
            section_scene = ""
            sub_scene_map.clear()
            in_dialogue = False
            continue

        m_char = re.match(r'\*\*角色\*\*\s*[：:]\s*(.+)', stripped)
        if m_char:
            section_chars = [
                c.strip().rstrip('。')
                for c in re.split(r'[、,，]', m_char.group(1).strip())
                if c.strip()
            ]
            continue

        m_main = re.match(r'\*\*主场景\*\*\s*[：:]\s*(.+)', stripped)
        if m_main:
            section_scene = normalize_scene_name(m_main.group(1).strip())
            sub_scene_map.clear()
            continue

        m_sc = re.match(r'\*\*场景\*\*\s*[：:]\s*(.+)', stripped)
        if m_sc:
            section_scene = normalize_scene_name(m_sc.group(1).strip())
            sub_scene_map.clear()
            continue

        m_subs = re.match(r'\*\*分场景\*\*\s*[：:]\s*(.+)', stripped)
        if m_subs:
            for item in re.split(r'\s*/\s*', m_subs.group(1).strip()):
                sm = _SUB_CODE_RE.match(item.strip())
                if sm:
                    sub_scene_map[sm.group(1)] = _sub_scene_name(sm.group(1), sm.group(2))
            continue

        if re.match(r'\*\*(时间|时长)\*\*\s*[：:]', stripped):
            continue

        if re.match(r'\*\*对话\*\*\s*[：:]', stripped):
            in_dialogue = True
            dialogue_lines = []
            continue

        if in_dialogue:
            if stripped.startswith('**') or stripped.startswith('#'):
                if dialogue_lines:
                    all_dialogues.append({
                        "section": section_title,
                        "text": "\n".join(dialogue_lines),
                        "characters": list(section_chars),
                    })
                    dialogue_lines = []
                in_dialogue = False
            else:
                dialogue_lines.append(stripped)
            continue

        if not stripped.startswith('|'):
            continue
        cells = [c.strip() for c in stripped.split('|')[1:-1]]
        if len(cells) < 5 or _is_md_sep_row(cells):
            continue
        shot_m = _SHOT_CODE_RE.match(cells[0])
        if not shot_m:
            continue

        area_str = cells[1] if len(cells) > 1 else ""
        camera_type = cells[2] if len(cells) > 2 else ""
        camera_move = cells[3] if len(cells) > 3 else ""
        content = cells[4] if len(cells) > 4 else ""
        effect = cells[5] if len(cells) > 5 else ""
        sound = cells[6] if len(cells) > 6 else ""
        duration_str = cells[7] if len(cells) > 7 else "6s"

        dur_m = re.match(r'([\d.]+)\s*s', duration_str)
        duration = float(dur_m.group(1)) if dur_m else 6.0

        area_m = _SUB_CODE_RE.match(area_str)
        if area_m and area_m.group(1) in sub_scene_map:
            scene = sub_scene_map[area_m.group(1)]
        elif area_m:
            scene = _sub_scene_name(area_m.group(1), area_m.group(2))
        else:
            scene = section_scene or area_str
        # 区域名与主场景名重复（「光茧·光茧」「第一关石室·第一关石室」）时直接用主场景
        if _scene_scene_redundant(scene, section_scene):
            scene = section_scene

        storyboards.append({
            "scene_number": len([s for s in storyboards if not s.get("_is_sub_scene")]) + 1,
            "scene_name": scene,
            "scene": scene,
            "shot_code": shot_m.group(1),
            "shot_seq": cells[0].split('-')[-1].lstrip('C'),
            "description": content,
            "characters": list(section_chars),
            "dialogue": "",
            "dialogue_role": "",
            "voice_text": "",
            "action": f"{camera_move}。{effect}" if effect else camera_move,
            "camera": f"{camera_type}，{camera_move}" if camera_move else camera_type,
            "sound": sound,
            "duration": duration,
        })

    if in_dialogue and dialogue_lines:
        all_dialogues.append({
            "section": section_title,
            "text": "\n".join(dialogue_lines),
            "characters": list(section_chars),
        })

    if all_dialogues and storyboards:
        _match_dialogues_to_shots(storyboards, all_dialogues)

    return storyboards if storyboards else None


def _scene_scene_redundant(sub_scene: str, main_scene: str) -> bool:
    """判断子场景名是否只是主场景名的冗余拼接（避免「光茧·光茧」式重名）。"""
    if not sub_scene or not main_scene:
        return False
    a = normalize_scene_name(sub_scene).split('·')
    b = normalize_scene_name(main_scene).split('·')
    # 末段相同（「光茧·光茧」）或子场景末段被主场景全名包含
    return bool(a and b and a[-1] == b[-1] or sub_scene == main_scene)


def _match_dialogues_to_shots(storyboards: List[dict], all_dialogues: List[dict]):
    """将对话段落中的角色对话匹配到对应的分镜。"""
    dialogue_queue = []

    for d in all_dialogues:
        text = d["text"]
        chars = d.get("characters", [])
        for m in re.finditer(
            r'([\u4e00-\u9fff]{1,6})(?:说|道|喊|叫|问|答|低声|沉声)\s*[：:]*\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
            text
        ):
            role = m.group(1).strip()
            voice = m.group(2).rstrip("。")
            matched_role = ""
            for cn in chars:
                norm_cn = re.sub(r'[（(][^（）()]*[）)]', '', cn).strip()
                if role in (cn, norm_cn) or norm_cn.startswith(role):
                    matched_role = cn
                    break
            if not matched_role:
                matched_role = role
            dialogue_queue.append((matched_role, voice))

        for m in re.finditer(
            r'([\u4e00-\u9fff]{1,6})\s*[：:]\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
            text
        ):
            role = m.group(1).strip()
            voice = m.group(2).rstrip("。")
            matched_role = ""
            for cn in chars:
                norm_cn = re.sub(r'[（(][^（）()]*[）)]', '', cn).strip()
                if role in (cn, norm_cn) or norm_cn.startswith(role):
                    matched_role = cn
                    break
            if not matched_role:
                matched_role = role
            is_dup = any(
                existing_role == matched_role and existing_voice == voice
                for existing_role, existing_voice in dialogue_queue
            )
            if not is_dup:
                dialogue_queue.append((matched_role, voice))

    qi = 0
    for sb in storyboards:
        if qi >= len(dialogue_queue):
            break
        if sb.get("voice_text"):
            continue
        desc = sb.get("description", "")
        has_quote = bool(re.search(r'[\u201c\u300c"]', desc))
        if has_quote:
            role, voice = dialogue_queue[qi]
            sb["voice_text"] = voice
            sb["dialogue_role"] = role
            sb["dialogue"] = voice
            qi += 1


def normalize_scene_name(raw_scene: str) -> str:
    """将剧集文本中的场景名规范化，与 scenes.json 保持一致。

    规则：
    - 去掉时间后缀（·白天 / ·夜晚 / ·清晨 / ·黄昏 / ·黎明 / ·深夜 / ·傍晚 / ·黎明前 / ·次日白天 / ·暴雨中）
    - 去掉箭头转换描述（→xxx）
    - "凡俗界·" 前缀去掉
    - "陈炎家" → "青阳镇·铁匠铺·里屋"
    - 多余的层级简化
    """
    if not raw_scene:
        return raw_scene
    s = raw_scene.strip()
    s = re.sub(r'[·]\s*(?:白天|夜晚|清晨|黄昏|黎明|深夜|傍晚|黎明前|次日白天|暴雨中|中午)\s*$', '', s)
    s = re.sub(r'→[^·]+', '', s)
    s = re.sub(r'^凡俗界·', '', s)
    s = re.sub(r'^陈炎家·', '青阳镇·铁匠铺·', s)
    s = s.strip().rstrip('·')
    return s


def compose_sub_scene_name(main_scene: str, area_name: str) -> str:
    """合成子场景名：主场景名末段 + 「·」+ 区域名（与分镜解析规则一致）。

    - 与 :func:`parse_md_section_shots` 内部逻辑保持一致，
      使场景库可预先入库子场景（供预生成参考图）；
    - 主场景/区域名重复（如「光茧·光茧」式冗余）时直接用主场景名；
    - 结果经 normalize_scene_name 归一。
    """
    base = (main_scene or "").split("·")[-1] if main_scene else ""
    raw = f"{base}·{area_name}" if base else (area_name or "")
    scene = normalize_scene_name(raw)
    main_norm = normalize_scene_name(main_scene)
    if main_norm and _scene_scene_redundant(scene, main_norm):
        return main_norm
    return scene


def extract_scenes_from_episodes(episodes: list) -> list:
    """从 episodes 列表中提取所有唯一场景名（已规范化）。

    返回 [{"name": "天断山脉·山脚", "description": ""}, ...]
    """
    scene_names = []
    seen = set()
    for ep in episodes:
        text = ep.get("text", "")
        if not text:
            continue
        for m in re.finditer(r'场景\s*[：:]\s*(.+)', text):
            raw = m.group(1).strip()
            norm = normalize_scene_name(raw)
            if norm and norm not in seen:
                seen.add(norm)
                scene_names.append({"name": norm, "description": ""})
    return scene_names


def parse_episode_text_to_storyboards(text: str) -> Optional[List[dict]]:
    """从剧集文本格式（episodes.json 中的 text 字段）解析分镜。

    支持格式：
    - 角色描述：xxx
    - 场景描述：xxx
    - 关键动作：xxx（含对话行）
    - 对话：xxx
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    storyboards = []
    section_chars = []
    section_scene = ""
    section_name = ""
    key_actions = []
    dialogue_text = ""
    in_key_actions = False
    in_dialogue = False
    in_char_desc = False
    in_scene_desc = False
    char_descriptions = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        section_match = re.match(
            r'(?:第[一二三四五六七八九十\d]+节|S\d+)\s*[：:]*\s*(.*)', stripped
        )
        if section_match:
            section_name = section_match.group(1).strip()
            continue

        # 解析 "场景：天断山脉·山脚·白天" 行（在节标题后面）
        scene_line_match = re.match(r'场景\s*[：:]\s*(.*)', stripped)
        if scene_line_match and not in_char_desc and not in_scene_desc and not in_key_actions and not in_dialogue:
            section_scene = normalize_scene_name(scene_line_match.group(1).strip())
            continue

        if re.match(r'角色描述\s*[：:]*', stripped):
            in_char_desc = True
            in_key_actions = False
            in_dialogue = False
            in_scene_desc = False
            continue

        if re.match(r'场景描述\s*[：:]*', stripped):
            in_scene_desc = True
            in_char_desc = False
            in_key_actions = False
            in_dialogue = False
            continue

        if re.match(r'关键动作\s*[：:]*', stripped):
            in_key_actions = True
            in_char_desc = False
            in_dialogue = False
            in_scene_desc = False
            key_actions = []
            continue

        if re.match(r'对话\s*[：:]*', stripped):
            in_dialogue = True
            in_key_actions = False
            in_char_desc = False
            in_scene_desc = False
            dialogue_text = ""
            continue

        if re.match(r'情感基调\s*[：:]*', stripped):
            in_dialogue = False
            in_key_actions = False
            in_char_desc = False
            in_scene_desc = False
            continue

        if in_char_desc:
            char_match = re.match(r'([\u4e00-\u9fff]{1,8}(?:[（(][^）)]*[）)])?)\s*[：:]*\s*(.*)', stripped)
            if char_match:
                cname = char_match.group(1).strip()
                cdesc = char_match.group(2).strip()
                char_descriptions[cname] = cdesc
                section_chars.append(cname)
            continue

        if in_scene_desc:
            if not section_scene:
                section_scene = stripped
            continue

        if in_key_actions:
            if re.match(r'(?:角色描述|场景描述|对话|情感基调)\s*[：:]*', stripped):
                in_key_actions = False
                continue
            key_actions.append(stripped)
            continue

        if in_dialogue:
            if re.match(r'(?:角色描述|场景描述|关键动作|情感基调)\s*[：:]*', stripped):
                in_dialogue = False
                continue
            dialogue_text += stripped + "\n"
            continue

    if key_actions:
        shot_index = 0
        for action_line in key_actions:
            shot_index += 1

            voice_text = ""
            dialogue_role = ""
            frame_chars = list(section_chars)

            m = re.search(
                r'([\u4e00-\u9fff]{1,6})(?:说|道|喊|叫|问|答|低声|沉声)\s*[：:]*\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
                action_line
            )
            if m:
                role_candidate = m.group(1).strip()
                voice_text = m.group(2).rstrip("。")
                for cn in section_chars:
                    norm_cn = re.sub(r'[（(][^（）()]*[）)]', '', cn).strip()
                    if role_candidate in (cn, norm_cn) or norm_cn.startswith(role_candidate):
                        dialogue_role = cn
                        break

            if not voice_text:
                m = re.search(r'[\u201c\u300c"]([^"\u201d\u300d]{4,}?)["\u201d\u300d]', action_line)
                if m:
                    voice_text = m.group(1).rstrip("。")
                    for cn in section_chars:
                        norm_cn = re.sub(r'[（(][^（）()]*[）)]', '', cn).strip()
                        if norm_cn in action_line:
                            dialogue_role = cn
                            break

            sb = {
                "scene_number": shot_index,
                "scene_name": section_name or section_scene,
                "scene": section_scene,
                "description": action_line,
                "characters": frame_chars,
                "dialogue": voice_text,
                "dialogue_role": dialogue_role,
                "voice_text": voice_text,
                "action": action_line,
                "camera": "中景",
                "duration": 6.0,
            }
            storyboards.append(sb)

    # 从对话段落提取所有对话，匹配到已有分镜或追加新分镜
    if dialogue_text:
        dialogue_pairs = []
        for m in re.finditer(
            r'([\u4e00-\u9fff]{1,6})(?:说|道|喊|叫|问|答|低声|沉声)\s*[：:]*\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
            dialogue_text
        ):
            role = m.group(1).strip()
            voice = m.group(2).rstrip("。")
            matched_role = ""
            for cn in section_chars:
                norm_cn = re.sub(r'[（(][^（）()]*[）)]', '', cn).strip()
                if role in (cn, norm_cn) or norm_cn.startswith(role):
                    matched_role = cn
                    break
            if not matched_role:
                matched_role = role
            dialogue_pairs.append((matched_role, voice))

        # 也匹配无动词的纯对话格式："xxx"角色名 + "对话内容"
        for m in re.finditer(
            r'[\u201c\u300c"]([^"\u201d\u300d]{2,}?)["\u201d\u300d]',
            dialogue_text
        ):
            voice = m.group(1).rstrip("。")
            if len(voice) < 4:
                continue
            already = any(v == voice for _, v in dialogue_pairs)
            if not already:
                dialogue_pairs.append(("", voice))

        if dialogue_pairs:
            # 将对话分配到已有分镜（优先匹配无voice_text的分镜）
            assigned = set()
            for di, (role, voice) in enumerate(dialogue_pairs):
                for si, sb in enumerate(storyboards):
                    if si in assigned:
                        continue
                    if not sb.get("voice_text"):
                        sb["voice_text"] = voice
                        sb["dialogue_role"] = role
                        sb["dialogue"] = voice
                        assigned.add(si)
                        break

            # 未分配的对话追加为新分镜
            shot_index = len(storyboards)
            for di, (role, voice) in enumerate(dialogue_pairs):
                if di in assigned:
                    continue
                shot_index += 1
                storyboards.append({
                    "scene_number": shot_index,
                    "scene_name": section_name or section_scene,
                    "scene": section_scene,
                    "description": f"{role}：\u201c{voice}\u201d" if role else f"\u201c{voice}\u201d",
                    "characters": list(section_chars),
                    "dialogue": voice,
                    "dialogue_role": role,
                    "voice_text": voice,
                    "action": "",
                    "camera": "中景",
                    "duration": 6.0,
                })

    return storyboards if storyboards else None


class AnimationProducer:
    """视频生成器"""

    def __init__(self, client: AgnesAIClient, project: Project, log_callback=None, project_manager=None):
        self.client = client
        self.project = project
        self.log_callback = log_callback or (lambda msg: print(msg))
        self.project_manager = project_manager
        # 本集「场景未匹配」清单：只累计、每集末尾汇总一条告警（避免逐条刷屏）
        self._sb_scene_unmatched = []

    # ------------------------------------------------------------------
    # 技能包上下文（resources/skills/ 接入点）
    # 普通用户「无感」：project.skill_pack / project.story_pack 设定后，
    # 所有 prompt 构建自动注入对应风格/叙事约束；未设定时各方法返回空，
    # 主流程与现状完全一致。
    # 高级用户「手动」：GUI 分镜页高级选项可再叠加自定义注入文本。
    # ------------------------------------------------------------------

    def _style_pack(self):
        """当前项目启用的美术风格包（未启用返回 None）"""
        return _skills.active_style_pack(self.project)

    def _story_pack(self):
        """当前项目启用的叙事包（未启用返回 None）"""
        return _skills.active_story_pack(self.project)

    def _style_tag(self) -> str:
        """风格锚定词：有风格包时返回风格包锚定词，否则回退 project.style"""
        pack = self._style_pack()
        if pack:
            return pack.prompt_style()
        return self.project.style

    def _custom_style_suffix(self) -> str:
        """高级用户手动注入的自定义风格短语（分镜页高级选项）"""
        return (getattr(self.project, "custom_style_suffix", "") or "").strip()

    def _video_style_suffix(self) -> str:
        """视频 prompt 末尾追加的风格包视频风格标签（未启用风格包返回空）"""
        pack = self._style_pack()
        return pack.video_style_suffix() if pack else ""

    def _frame_extra_constraints(self) -> str:
        """分镜帧图/视频 prompt 末尾追加的风格一致性约束"""
        pack = self._style_pack()
        parts = []
        if pack:
            parts.append(pack.storyboard_image_constraint())
        extra = self._custom_style_suffix()
        if extra:
            parts.append(extra)
        return "，".join(parts)

    def _storyboard_script_constraints(self) -> str:
        """分镜脚本生成（client.generate_script）的技法约束上下文

        组合来源（均未启用时返回空字符串，不影响现状）：
        - 叙事包 story_pack 的「分镜表叙事手法」（高级/无感自动注入，截断至 4000 字）
        - 项目选定的制作技法 production_technique（分镜表/分镜提示词通用技法）
        - 风格包 director_storyboard 的情绪/光影词库（截断至 3000 字，供分镜措辞参考）
        """
        blocks = []
        sp = self._story_pack()
        if sp:
            text = sp.guidance("storyboard")
            if text:
                blocks.append(f"【叙事手法参考（{sp.display_name}）】\n{text[:4000]}")
        tech = (getattr(self.project, "production_technique", "") or "").strip()
        if tech:
            text = _skills.production_technique_by_display(tech)
            if text:
                blocks.append(f"【制作技法参考（{tech}）】\n{text[:4000]}")
        pack = self._style_pack()
        if pack and pack.director:
            blocks.append(f"【风格分镜技法参考（{pack.display_name}）】\n{pack.director[:3000]}")
        if not blocks:
            return ""
        return "\n\n以下技法参考仅供分镜设计措辞参考，请勿输出参考内容本身，只输出 JSON 分镜列表：\n" + "\n\n".join(blocks)
    
    def import_section_storyboard(self, ep_idx: int):
        """按当前集文本中的 Markdown 分镜表导入分镜（E0X-S0Y-C0Z 格式，跳过 AI 生成）。

        解析结果里的场景名对齐全局场景列表（模糊匹配不到时新建场景条目），
        与 parse_episode_storyboard 保持同一契约。无法解析时抛 ValueError。
        """
        from src.models.project import Storyboard, Scene
        from src.core.producer import parse_md_section_shots

        episodes = getattr(self.project, "episodes", []) or []
        if not episodes or not (0 <= ep_idx < len(episodes)):
            raise ValueError("项目不是剧集模式或未选择剧集")
        text = (episodes[ep_idx].get("text") or "").strip()
        if not text:
            raise ValueError("当前集文本为空，无法解析分镜")

        parsed = parse_md_section_shots(text)
        if not parsed:
            raise ValueError(
                "当前集文本中未找到「E0X-S0Y-C0Z」分镜表，无法按文件导入分镜"
            )

        global_scenes = getattr(self.project, "global_scenes", []) or []
        existing = {s.name for s in global_scenes} | {s.name for s in self.project.scenes}

        storyboards: List[Storyboard] = []
        for i, row in enumerate(parsed):
            name = row.get("scene", "") or row.get("scene_name", "")
            aligned = None
            if name in existing:
                aligned = name
            else:
                best, score = _am.best_scene_match(name, list(existing))
                if best and score >= _am.SCENE_MATCH_MIN_SCORE:
                    aligned = best
                else:
                    aligned = name
                    new_scene = Scene(
                        name=name,
                        description=row.get("description", "")[:80],
                        episodes=[(episodes[ep_idx].get("title") or f"第{ep_idx + 1}集")],
                    )
                    global_scenes.append(new_scene)
                    self.project.scenes.append(new_scene)
                    existing.add(name)
                    self._log(f"  🆕 按分镜导入自动新建场景：{name}")

            sb = Storyboard(
                scene_number=i + 1,
                scene_name=name,
                scene=aligned,
                description=row.get("description", ""),
                characters=list(row.get("characters", [])),
                dialogue=row.get("dialogue", ""),
                dialogue_role=row.get("dialogue_role", ""),
                voice_text=row.get("voice_text", row.get("dialogue", "")),
                action=row.get("action", ""),
                camera=row.get("camera", "中景"),
                duration=row.get("duration", 6.0),
                status="pending",
            )
            storyboards.append(sb)

        # 本集角色引用 = 分镜中出现的角色名（与全局角色库对齐）
        all_chars = getattr(self.project, "global_characters", []) or self.project.characters
        char_pool = {_norm_character_name(c.name): c for c in all_chars}
        ep_chars = []
        for row in parsed:
            for cn in row.get("characters", []):
                hit = char_pool.get(_norm_character_name(cn))
                if hit and hit.name not in ep_chars:
                    ep_chars.append(hit.name)
                elif cn not in ep_chars:
                    ep_chars.append(cn)

        self.project.characters = [
            c for c in (all_chars or self.project.characters)
            if c.name in ep_chars
        ] if all_chars else self.project.characters

        ep_title = (episodes[ep_idx].get("title") or f"第{ep_idx + 1}集").strip()
        self._log(
            f"📋 [{ep_title}] 按文件分镜表导入 {len(storyboards)} 个分镜"
            f"（角色 {len(ep_chars)} 个，新建/复用场景 {len(existing)} 个）"
        )
        return storyboards

    def _log(self, message: str):
        self.log_callback(message)
    
    def _save_assets_to_library(self):
        """将当前项目的角色/道具/场景保存到共用资产库，并绑定到项目。

        无论故事模式还是剧集模式，提取的资产都应保存到共用库目录
        (data/asset_library/)，以便其他项目也能导入使用。
        """
        chars = self.project.characters or []
        props = getattr(self.project, "props", []) or []
        scenes = self.project.scenes or []
        if not chars and not props and not scenes:
            return
        try:
            from src.services.library_manager import save_library
            lib_name = getattr(self.project, "asset_library_name", "") or self.project.name
            lib_path, _ = save_library(
                lib_name, chars, props, scenes, merge=True
            )
            self.project.asset_library_name = lib_name
            self._log(f"📦 资产已保存到共用库: {lib_name} ({lib_path})")
        except Exception as e:
            self._log(f"⚠️ 保存资产到共用库失败: {e}")

    def _auto_save(self, what: str = "all"):
        if not self.project_manager:
            return
        if what == "all":
            self.project_manager.save_project(self.project)
        elif what == "meta":
            self.project_manager.save_meta(self.project)
        elif what == "characters":
            self.project_manager.save_characters(self.project)
        elif what == "props":
            self.project_manager.save_props(self.project)
        elif what == "scenes":
            self.project_manager.save_scenes(self.project)
        elif what == "storyboard":
            self.project_manager.save_storyboard(self.project)
            # 剧集模式下：分镜的唯一存储是分集文件，必须同步保存
            ep_idx = getattr(self.project, 'current_episode', -1)
            if 0 <= ep_idx < len(getattr(self.project, 'episodes', [])):
                # 先把当前内存中的 storyboard（含最新 frame_path/video_path）
                # 回填到 episode_data，否则 save_episode_file 会从过时的
                # episode_data 读取分镜，把旧数据写回磁盘覆盖掉正确数据
                ep_data = (getattr(self.project, 'episode_data', None) or {})
                cur = ep_data.get(str(ep_idx), {})
                cur["storyboard"] = [sb.to_dict() for sb in self.project.storyboard]
                if "character_names" not in cur:
                    cur["character_names"] = [c.name for c in getattr(self.project, 'characters', [])]
                if "scene_names" not in cur:
                    cur["scene_names"] = [s.name for s in getattr(self.project, 'scenes', [])]
                if "prop_names" not in cur:
                    cur["prop_names"] = [p.name for p in getattr(self.project, 'props', [])]
                ep_data[str(ep_idx)] = cur
                self.project.episode_data = ep_data
                if hasattr(self.project_manager, 'save_episode_file'):
                    self.project_manager.save_episode_file(self.project, ep_idx)
        elif what == "audio":
            self.project_manager.save_audio(self.project)
    
    async def generate_story(
        self,
        topic: str,
        character_name: str = "主角",
        length: str = "medium"
    ) -> str:
        """生成故事（启用叙事包时自动注入叙事手法指导）"""
        self._log("开始生成故事...")
        pack = self._story_pack()
        style_guidance = pack.guidance("planning")[:6000] if pack else ""
        if pack:
            self._log(f"启用叙事包「{pack.display_name}」指导故事生成")
        story = await self.client.generate_story(
            topic=topic,
            age_group=self.project.age_group,
            character_name=character_name,
            length=length,
            style_guidance=style_guidance,
        )
        self.project.story = story
        self._log("故事生成完成！")
        self._auto_save("meta")
        return story
    
    async def analyze_story(self, story: str, target_minutes: int = 3) -> dict:
        """AI 分析故事内容，返回拆分建议"""
        self._log("正在分析故事内容...")
        result = await self.client.analyze_story_for_split(
            story=story,
            target_duration_minutes=target_minutes,
        )
        self._log(f"分析完成：{result.get('summary', '')[:100]}...")
        return result
    
    async def split_story_to_files(
        self,
        story: str,
        split_plan: list,
        story_file_path: str = None,
    ) -> list:
        """将故事拆分为多个文件，保存在原故事同目录中"""
        self._log(f"正在将故事拆分为 {len(split_plan)} 个文件...")
        result = await self.client.split_story(
            story=story,
            split_plan=split_plan,
        )
        
        # 确定保存目录
        if story_file_path:
            save_dir = Path(story_file_path).parent
            base_name = Path(story_file_path).stem
        else:
            project_dir = Settings.get_project_dir(self.project.name)
            save_dir = project_dir / "stories"
            save_dir.mkdir(parents=True, exist_ok=True)
            base_name = self.project.name
        
        saved_files = []
        for part in result:
            part_num = part.get("part_number", 1)
            title = part.get("title", f"第{part_num}部分")
            content = part.get("content", "")
            
            # 保存文件
            file_name = f"{base_name}_part{part_num}_{title}.txt"
            # 清理文件名中的非法字符
            for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
                file_name = file_name.replace(ch, '_')
            file_path = save_dir / file_name
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            saved_files.append({
                "path": str(file_path),
                "title": title,
                "part_number": part_num,
            })
            self._log(f"  ✅ 已保存: {file_name}")
        
        self._log(f"拆分完成！共保存 {len(saved_files)} 个文件到: {save_dir}")
        return saved_files
    
    async def _extract_episode_flow(self, story: str, topic: str, length: str = None) -> dict:
        """剧集模式处理流程：全局资产（角色/道具/场景）一次提取，分镜按集生成"""
        episodes = getattr(self.project, 'episodes', [])
        cur = getattr(self.project, 'current_episode', -1)
        ep_title = (
            episodes[cur].get("title", f"第{cur+1}集") if 0 <= cur < len(episodes) else "未选择剧集"
        )
        self._log("📺 剧集模式：角色/道具/场景为全剧共享资产（只提取一次），分镜按集生成")

        # ① 全局资产：缺失的部分从整部剧原文一次性提取（全剧共享）
        force_reextract = getattr(self.project, '_force_reextract_assets', True)
        missing = []
        if force_reextract or not self.project.characters:
            missing.append("角色")
        if force_reextract or not getattr(self.project, 'props', []):
            missing.append("道具")
        if force_reextract or not self.project.scenes:
            missing.append("场景")
        if missing:
            script_text = getattr(self.project, 'script_text', '') or story
            if force_reextract:
                self._log(f"正在为整部剧重新提取全局资产（用户确认重新提取）...")
            else:
                self._log(f"正在为整部剧一次性提取全局资产（缺: {'、'.join(missing)}）...")
            await self._extract_series_assets(script_text, topic, length)
            if hasattr(self.project, '_force_reextract_assets'):
                del self.project._force_reextract_assets
        else:
            self._log("✅ 全局资产（角色/道具/场景）已存在，跳过重复提取")
            self._log("💡 如需重新提取全局资产，请点击提取按钮并选择「重新提取」")

        # ② 当前集分镜：用该集文本 + 全剧场景，按场景拆分为多个镜头
        if cur < 0:
            self._log("⚠️ 尚未选择剧集：全局资产已就绪，请在「当前集」下拉框选择要制作的剧集后再生成分镜")
            return {
                "project_name": self.project.name,
                "characters": self.project.characters,
                "props": getattr(self.project, 'props', []),
                "scenes": self.project.scenes,
                "storyboard": [],
                "storyboard_count": 0,
            }

        self._log(f"正在为 {ep_title} 生成分镜（按场景拆分为多个镜头）...")
        # 确保使用当前集文本（直接取剧集数据，与界面状态解耦）
        ep_text = episodes[cur].get("text", "") if 0 <= cur < len(episodes) else ""
        if ep_text:
            self.project.story = ep_text

        # 剧集模式：从全局资产中筛选当前集相关的子集
        # 优先级：① episodes 字段标注（兼容 ["1.2"]/["第1集"]/["1-10"]/["全剧"] 等格式）
        #        → ② 本集正文增强名匹配（去括号别名/归一化分隔符/场景名主段）
        #        → ③ 兜底使用全部
        # 旧版仅按 episodes 字段精确相等（ep_title in c.episodes）筛选，
        # 字段为空时等于全选，导致分镜拿到了全剧 24 个角色/25 个场景。
        global_characters = getattr(self.project, 'global_characters', [])
        if global_characters:
            matched_chars = self._filter_assets_for_episode(global_characters, ep_title, ep_text)
            self.project.characters = matched_chars
            if len(matched_chars) < len(global_characters):
                self._log(f"  从全剧 {len(global_characters)} 个角色中筛选出当前集 {len(matched_chars)} 个角色")
                self._log(f"  当前集角色: {', '.join(c.name for c in matched_chars)}")
            else:
                self._log(f"  使用全剧 {len(global_characters)} 个角色（未筛选出更小子集）")

        # 剧集模式：从全局场景中筛选当前集相关的场景子集（筛选策略同上）
        global_scenes = getattr(self.project, 'global_scenes', [])
        if global_scenes:
            matched_scenes = self._filter_assets_for_episode(
                global_scenes, ep_title, ep_text, use_coverage=True
            )
            self.project.scenes = matched_scenes
            if len(matched_scenes) < len(global_scenes):
                self._log(f"  从全剧 {len(global_scenes)} 个场景中筛选出当前集 {len(matched_scenes)} 个场景")
                self._log(f"  当前集场景: {', '.join(s.name for s in matched_scenes)}")
            else:
                self._log(f"  使用全剧 {len(global_scenes)} 个场景（未筛选出更小子集）")

        self.project.storyboard.clear()
        storyboards = await self.generate_storyboard_script()
        self._log(f"✅ {ep_title} 分镜生成完成：共 {len(storyboards)} 个")

        # ③ 快照本集数据（本集资产引用 + 分镜）并保存分集文件：
        # 之后切换到该集时直接调用已保存数据，无需再让 AI 生成分镜
        if not getattr(self.project, 'episode_data', None):
            self.project.episode_data = {}
        self.project.episode_data[str(cur)] = {
            "character_names": [c.name for c in self.project.characters],
            "scene_names": [s.name for s in self.project.scenes],
            "prop_names": [p.name for p in getattr(self.project, 'props', [])],
            "storyboard": [sb.to_dict() for sb in self.project.storyboard],
        }
        self._auto_save("meta")
        if self.project_manager and hasattr(self.project_manager, "save_episode_file"):
            self.project_manager.save_episode_file(self.project, cur)

        return {
            "project_name": self.project.name,
            "characters": self.project.characters,
            "props": getattr(self.project, 'props', []),
            "scenes": self.project.scenes,
            "storyboard": self.project.storyboard,
            "storyboard_count": len(storyboards),
        }

    def _save_episode_snapshot(self, ep_idx: int):
        """把当前集的资产引用与分镜快照写入 episode_data 并持久化（含分集文件）"""
        if not getattr(self.project, 'episode_data', None):
            self.project.episode_data = {}
        self.project.episode_data[str(ep_idx)] = {
            "character_names": [c.name for c in self.project.characters],
            "scene_names": [s.name for s in self.project.scenes],
            "prop_names": [p.name for p in getattr(self.project, 'props', [])],
            "storyboard": [sb.to_dict() for sb in self.project.storyboard],
        }
        self._auto_save("meta")
        if self.project_manager and hasattr(self.project_manager, "save_episode_file"):
            self.project_manager.save_episode_file(self.project, ep_idx)

    async def generate_episode_storyboard(
        self,
        ep_idx: int,
        progress_callback: Callable = None,
    ) -> int:
        """生成本节（当前集）分镜。

        按本节正文筛选全局资产子集（角色/场景，场景含覆盖率匹配）→
        AI 生成本节分镜（所属场景名对齐全局场景列表）→
        快照保存到 episode_data + 分集文件（episodes/EPxxxx_标题.json）。
        """
        episodes = getattr(self.project, 'episodes', [])
        if not episodes or not (0 <= ep_idx < len(episodes)):
            raise ValueError("项目不是剧集模式或未选择剧集，无法生成本节分镜")
        if not (getattr(self.project, 'global_characters', []) or self.project.characters):
            raise ValueError("尚未提取全局角色，请先生成全局资产（角色/道具/场景）")
        if not (getattr(self.project, 'global_scenes', []) or self.project.scenes):
            raise ValueError("尚未提取全局场景，请先生成全局资产（角色/道具/场景）")

        ep = episodes[ep_idx]
        ep_title = (ep.get("title") or f"第{ep_idx + 1}集").strip()
        ep_text = ep.get("text", "") or ""
        self._log(f"🎬 正在为 {ep_title} 生成分镜...")

        # 切换到该集，并按本节正文从全局资产筛选子集（保持场景名与全局资产一致）
        self.project.current_episode = ep_idx
        self.project.story = ep_text
        global_characters = getattr(self.project, 'global_characters', [])
        global_scenes = getattr(self.project, 'global_scenes', [])
        self.project.characters = self._filter_assets_for_episode(
            global_characters or self.project.characters, ep_title, ep_text
        )
        self.project.scenes = self._filter_assets_for_episode(
            global_scenes or self.project.scenes, ep_title, ep_text, use_coverage=True
        )
        self.project.props = list(
            getattr(self.project, 'global_props', []) or self.project.props
        )

        try:
            storyboards = await self.generate_storyboard_script()
        except NETWORK_ERRORS as e:
            # 网络层泄漏的 CancelledError 属 BaseException，需按可重试网络错误处理
            raise_if_real_cancel(e)
            self._log(f"❌ {ep_title} 分镜生成失败: {type(e).__name__}: {e}")
            raise
        self._log(f"✅ {ep_title} 分镜生成完成（{len(storyboards)} 个），已保存")
        # 持久化到 episode_data + 分集文件，切换剧集时直接调用
        self._save_episode_snapshot(ep_idx)
        return len(storyboards)

    def parse_episode_storyboard(self, ep_idx: int):
        """从当前集的 md 文本解析预设分镜（场景来自文件定义，跳过 AI 生成）。

        解析规则：
        - 「场景：」行按 → 拆分为多个场景，每个场景生成一个分镜条目
        - 「时长：」行决定单集总时长，均摊到每个场景
        - 「### 角色描述/场景描述/关键动作/对话」按段落依次分配给各场景
        - 场景名会与全局场景列表对齐（匹配不到则新建场景条目）
        """
        from src.models.project import Storyboard, Scene

        episodes = getattr(self.project, "episodes", []) or []
        if not episodes or not (0 <= ep_idx < len(episodes)):
            raise ValueError("项目不是剧集模式或未选择剧集")
        ep = episodes[ep_idx]
        text = ep.get("text", "") or ""
        if not text.strip():
            raise ValueError("当前集文本为空，无法解析分镜")

        m = _DURATION_RE.search(text)
        total_seconds = int(m.group(1)) * 60 if m else 180

        sm = _SCENES_RE.search(text)
        if not sm:
            raise ValueError("当前集文本中没有找到「场景：」行，无法解析分镜")
        raw_names = re.split(r"[→,，;；]", sm.group(1))
        scene_names = [n.strip() for n in raw_names if n.strip()]
        if not scene_names:
            raise ValueError("「场景：」行没有解析到有效场景名")

        per_scene = max(2.0, round(total_seconds / len(scene_names), 1))

        # 按 ### 标题拆分正文块
        blocks = {}
        parts = _SECTION_BLOCK_RE.split(text)
        # parts: [前缀, 标题1, 正文1, 标题2, 正文2, ...]
        for i in range(1, len(parts), 2):
            heading = parts[i].strip()
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            blocks[heading] = body

        def _paragraphs(key: str) -> list:
            body = blocks.get(key, "")
            if not body:
                return []
            return [p.strip() for p in re.split(r"\n\n+", body) if p.strip()]

        char_lines = _paragraphs("角色描述")
        char_names = []
        known_bank = {
            _norm_character_name(c.name)
            for c in getattr(self.project, "global_characters", []) or []
        } | {
            _norm_character_name(c.name)
            for c in self.project.characters
        }
        for line in char_lines:
            mm = re.match(r"^([^\n：:]{1,30})[：:]", line.strip())
            if mm:
                name = mm.group(1).strip()
                if not name or name in char_names:
                    continue
                # 仅保留已知角色库中的角色；库为空时回退保留全部
                if not known_bank or _norm_character_name(name) in known_bank:
                    char_names.append(name)

        desc_paras = _paragraphs("场景描述")
        action_paras = _paragraphs("关键动作")
        dialog_paras = _paragraphs("对话")

        global_scenes = getattr(self.project, "global_scenes", []) or []
        existing_scene_names = {s.name for s in global_scenes} | {
            s.name for s in self.project.scenes
        }

        storyboards = []
        ep_title = (ep.get("title") or f"第{ep_idx + 1}集").strip()
        for i, name in enumerate(scene_names):
            aligned = None
            if name in existing_scene_names:
                aligned = name
            else:
                best, score = _am.best_scene_match(name, list(existing_scene_names))
                if best and score >= _am.SCENE_MATCH_MIN_SCORE:
                    aligned = best
                else:
                    new_scene = Scene(
                        name=name,
                        description=desc_paras[i] if i < len(desc_paras) else "",
                        episodes=[ep_title],
                    )
                    global_scenes.append(new_scene)
                    self.project.scenes.append(new_scene)
                    existing_scene_names.add(name)
                    aligned = name
                    self._log(f"  🆕 按分镜导入自动新建场景：{name}")

            sb = Storyboard(
                scene_number=i + 1,
                scene_name=name,
                scene=aligned,
                description=desc_paras[i] if i < len(desc_paras) else "",
                characters=char_names,
                dialogue=dialog_paras[i] if i < len(dialog_paras) else "",
                action=action_paras[i] if i < len(action_paras) else "",
                camera="中景",
                duration=per_scene,
                status="pending",
            )
            storyboards.append(sb)

        self._log(
            f"📋 已按文件场景导入分镜（{ep_idx + 1}集）："
            + ", ".join(f"{sb.scene_name}({sb.duration}s)" for sb in storyboards)
        )
        return storyboards

    def _filter_assets_for_episode(
        self, assets: list, ep_title: str, ep_text: str, use_coverage: bool = False
    ) -> list:
        """从全局资产中筛选当前集相关子集（分镜生成用）。

        优先级：
        ① episodes 字段能区分时按标注筛选（兼容 ["1.2"]/["第1集"]/["1-10"]/["全剧"] 格式）；
        ② 按本集正文做增强名匹配（去括号别名/归一化「·」分隔符/场景名主段）；
           use_coverage=True（场景专用）时额外用「场景名片段覆盖率」判定，
           避免剧本用词与场景名不同形时匹配失败退化成「全量场景」；
        ③ 兜底返回全部。
        """
        if not assets:
            return []
        # ① LLM 标注的适用集数（存在标注且能命中时优先）
        if any(getattr(a, 'episodes', None) for a in assets):
            hits = [
                a for a in assets
                if _am.episodes_field_hits(getattr(a, 'episodes', None), ep_title) is True
            ]
            if hits:
                return hits
        # ② 按本集正文做增强名匹配
        text = _am.normalize_text(ep_text or '')
        if text:
            matched = [
                a for a in assets
                if getattr(a, 'name', '') and _am.match_asset_in_text(a.name, text)
            ]
            if use_coverage:
                # 场景额外用「场景名片段覆盖率」判定：剧本/正文用词与场景名
                # 不同形时（如 1.6 正文「天断山脉·峡谷」→ 场景「天断山脉峡谷狼战」）
                # 名匹配会失败，旧版直接兜底成全部 25 个场景 → 生成 25 个分镜。
                # 覆盖率 ≥50% 且至少命中 2 个片段即认定该场景属于本集。
                seen = {id(a) for a in matched}
                for a in assets:
                    if id(a) in seen:
                        continue
                    name = getattr(a, 'name', '')
                    hits_cnt, _total = _am.scene_text_hits(name, text)
                    if (hits_cnt >= _am.SCENE_COVERAGE_MIN_HITS
                            and _am.scene_text_coverage(name, text)
                            >= _am.SCENE_COVERAGE_MIN_RATIO):
                        matched.append(a)
                        seen.add(id(a))
            if matched:
                return matched
        # ③ 兜底：全部
        return list(assets)

    async def _extract_series_assets(self, script_text: str, topic: str, length: str = None):
        """剧集模式：从整部剧原文一次性提取全局资产（角色/道具/场景，全剧共享）"""
        ep_count = len(getattr(self.project, 'episodes', [])) or 1
        length_key = length or self.project.length or "very_short"
        per_ep_seconds = LENGTH_DURATION_MAP.get(length_key, 180)
        total_seconds = per_ep_seconds * ep_count
        # 全剧场景数：每集约2个主要场景，封顶30个
        series_scene_count = max(5, min(ep_count * 2, 30))
        video_context = (
            f"这是一部共{ep_count}集的连续短剧，每集约{per_ep_seconds // 60}分钟"
            f"（全剧总时长约{total_seconds // 60}分钟）。"
            f"角色/道具/场景是全剧共享资产，请覆盖全剧出现的关键内容；"
            f"计划全剧提取约{series_scene_count}个主要场景。"
        )

        # 全剧文本较长，提取时使用更大的输入窗口，保证后段剧集的角色/道具/场景不被遗漏
        self._log("正在提取全剧角色...")
        characters_data = await self.client.extract_characters(
            script_text, video_context=video_context, max_chars=60000,
            style_hint=self.project.style or ""
        )
        known_characters = self._collect_known_characters()
        self.project.characters = [
            Character(
                name=c.get("name", ""),
                description=c.get("description", ""),
                style=self.project.style,
                episodes=c.get("episodes", []),
            )
            for c in characters_data
        ]
        reused = 0
        for character in self.project.characters:
            known = known_characters.get(character.name)
            if not known:
                continue
            character.image_path = known.image_path or character.image_path
            reused += 1
        if reused:
            self._log(f"🔁 已沿用 {reused} 个同名角色设定（图片）")
        char_names = [c.name for c in self.project.characters if c.name]
        self._log(f"全剧角色提取完成：{len(self.project.characters)} 个 | 角色名称: {', '.join(char_names)}")

        self._log("正在提取全剧道具...")
        props_data = await self.client.extract_props(
            script_text, video_context=video_context, max_chars=60000,
            style_hint=self.project.style or ""
        )
        known_props = self._collect_known_props()
        self.project.props = [
            Prop(name=p.get("name", ""), description=p.get("description", ""), style=self.project.style, episodes=p.get("episodes", []))
            for p in props_data
        ]
        reused_p = 0
        for prop in self.project.props:
            known = known_props.get(prop.name)
            if not known:
                continue
            prop.image_path = known.image_path or prop.image_path
            reused_p += 1
        if reused_p:
            self._log(f"🔁 已沿用 {reused_p} 个同名道具设定（图片）")
        prop_names = [p.name for p in self.project.props if p.name]
        self._log(f"全剧道具提取完成：{len(self.project.props)} 个 | 道具名称: {', '.join(prop_names)}")

        self._log("正在提取全剧场景...")
        scenes_data = await self.client.extract_scenes(
            script_text,
            min_scenes=series_scene_count,
            max_scenes=series_scene_count,
            video_context=video_context,
            max_chars=60000,
            style_hint=self.project.style or ""
        )
        self.project.scenes = [
            Scene(
                name=s.get("name", f"场景{i+1}"),
                description=s.get("description", ""),
                style=self.project.style,
                time_of_day=s.get("time_of_day", "day"),
                episodes=s.get("episodes", []),
            )
            for i, s in enumerate(scenes_data)
        ]
        scene_names = [s.name for s in self.project.scenes if s.name]
        self._log(f"全剧场景提取完成：{len(self.project.scenes)} 个 | 场景名称: {', '.join(scene_names)}")

        self.project.global_characters = list(self.project.characters)
        self.project.global_props = list(self.project.props)
        self.project.global_scenes = list(self.project.scenes)

        self._save_assets_to_library()

        self._auto_save("characters")
        self._auto_save("props")
        self._auto_save("scenes")

    async def extract_from_story(self, story: str, topic: str, length: str = None) -> dict:
        """从故事中提取角色、场景、分镜

        剧集模式（project.episodes 非空）下自动优化：
        - 全局资产（角色/道具/场景）只从整部剧原文提取一次，全剧共享
        - 每集只按场景重新生成该集的分镜
        """
        if getattr(self.project, 'episodes', None):
            return await self._extract_episode_flow(story, topic, length)
        self._log("开始从故事中提取信息...")
        
        # 在清空前收集已知角色/道具（本集已有 + 其他剧集快照），用于跨集一致性
        known_characters = self._collect_known_characters()
        known_props = self._collect_known_props()
        
        # 保存旧分镜的已生成图/视频，清空后按序号回填
        old_sb_media = {}
        for sb in self.project.storyboard:
            idx = sb.scene_number - 1
            old_sb_media[idx] = {
                "frame_path": sb.frame_path,
                "video_path": sb.video_path,
                "status": sb.status,
            }
        
        # 清空旧数据，避免重复添加
        self.project.characters.clear()
        self.project.props.clear()
        self.project.scenes.clear()
        self.project.storyboard.clear()
        
        # 提取项目名称（仅用于日志显示，不修改用户创建的项目名称）
        self._log("正在提取项目名称...")
        project_name = await self.client.extract_project_name(story, topic)
        project_name = project_name.replace('\n', '').replace('\r', '').strip()
        self._log(f"AI 建议的项目名称: {project_name}（保持原名称: {self.project.name}）")
        
        # 按故事长度档位计算参考分镜数与总时长上限；AI将自行决定实际数量
        if length:
            target_scene_count = Settings.LENGTH_SCENE_COUNT.get(length, Settings.DEFAULT_SCENE_COUNT)
        else:
            target_scene_count = Settings.DEFAULT_SCENE_COUNT
        target_duration = (
            LENGTH_DURATION_MAP.get(length, target_scene_count * Settings.DEFAULT_DURATION)
            if length else target_scene_count * Settings.DEFAULT_DURATION
        )
        # 场景要少：每场景约 3 个镜头 → 场景数 ≈ 分镜数 / 3
        target_scene_image_count = max(3, round(target_scene_count / 3))
        avg_scene_seconds = target_duration / max(target_scene_image_count, 1)
        avg_shot_seconds = target_duration / max(target_scene_count, 1)
        video_context = (
            f"该故事将制作成视频，参考时长上限约{target_duration // 60}分钟（{target_duration}秒），"
            f"参考场景数约{target_scene_image_count}个，参考分镜数约{target_scene_count}个。"
            f"分镜数量和时长由你根据故事内容自行决定，以上数值仅作参考上限，"
            f"确保完整讲述故事即可，不必凑满参考数量。"
            f"平均每个场景约{avg_scene_seconds:.1f}秒、每个镜头约{avg_shot_seconds:.1f}秒。"
        )
        self._log(
            f"时长档位: {length or '默认'}, 参考分镜上限: {target_scene_count}, "
            f"参考场景上限: {target_scene_image_count}, 参考总时长上限: {target_duration}秒, "
            f"平均每镜: {avg_shot_seconds:.1f}秒（AI将自行决定实际数量）"
        )
        
        # 提取角色（按时长上下文生成详细的关键角色描述）
        self._log("正在提取角色...")
        style_hint = self.project.style or ""
        characters_data = await self.client.extract_characters(story, video_context=video_context, style_hint=style_hint)
        
        # 已有角色名集合（用于去重）
        existing_char_names = {c.name for c in self.project.characters}
        
        for char_data in characters_data:
            char_name = char_data.get("name", "")
            # 跳过已有同名角色（保留1.1节的描述和图片）
            if char_name in existing_char_names:
                self._log(f"跳过已有角色: {char_name}")
                continue
            character = Character(
                name=char_name,
                description=char_data.get("description", ""),
                style=self.project.style
            )
            self.project.characters.append(character)
            self._log(f"新增角色: {char_name}")
        self._log(f"提取了 {len(characters_data)} 个角色（新增 {len(self.project.characters) - len(existing_char_names)} 个）")
        # 故事模式约定：如果AI提取了0个角色，清空项目角色列表（故事可能无角色，如教学题）
        if len(characters_data) == 0 and len(self.project.characters) == len(existing_char_names):
            if self.project.characters:
                self._log(f"📖 故事模式：AI未提取到角色，清空项目角色列表（{len(self.project.characters)}个旧角色）")
                self.project.characters.clear()
                self.project.global_characters.clear()
        # 跨集角色一致性：若其他集已生成同名角色，沿用其图片/语音设置（描述以当前风格为准，不沿用旧描述）
        reused = 0
        for character in self.project.characters:
            known = known_characters.get(character.name)
            if not known:
                continue
            character.image_path = known.image_path or character.image_path
            reused += 1
        if reused:
            self._log(f"🔁 已沿用 {reused} 个同名角色设定（图片），描述以当前风格为准")
        
        # 提取道具（产品摄影设定；跨集复用已知道具）
        self._log("正在提取道具...")
        props_data = await self.client.extract_props(story, video_context=video_context, style_hint=style_hint)
        for prop_data in props_data:
            prop = Prop(
                name=prop_data.get("name", ""),
                description=prop_data.get("description", ""),
                style=self.project.style
            )
            self.project.props.append(prop)
        reused_p = 0
        for prop in self.project.props:
            known = known_props.get(prop.name)
            if not known:
                continue
            prop.image_path = known.image_path or prop.image_path
            reused_p += 1
        if reused_p:
            self._log(f"🔁 已沿用 {reused_p} 个同名道具设定（图片），描述以当前风格为准")
        self._log(f"提取了 {len(props_data)} 个道具")
        
        # 提取场景（场景数更少，一景多镜；告知 LLM 时长节奏，避免过度拆分）
        self._log("正在提取场景...")
        try:
            scenes_data = await self.client.extract_scenes(
                story, 
                min_scenes=target_scene_image_count,
                max_scenes=target_scene_image_count,
                video_context=video_context,
                style_hint=style_hint
            )
            self._log(f"场景提取返回: {type(scenes_data)}, 数量: {len(scenes_data) if scenes_data else 0}")
            
            # 已有场景名集合（用于去重）
            existing_scene_names = {s.name for s in self.project.scenes}
            
            if scenes_data:
                for i, scene_data in enumerate(scenes_data):
                    try:
                        if isinstance(scene_data, dict):
                            scene_name = scene_data.get("name", f"场景{i+1}")
                            # 跳过已有同名场景（保留1.1节的描述和图片）
                            if scene_name in existing_scene_names:
                                self._log(f"跳过已有场景: {scene_name}")
                                continue
                            scene = Scene(
                                name=scene_name,
                                description=scene_data.get("description", ""),
                                style=self.project.style,
                                time_of_day=scene_data.get("time_of_day", "day")
                            )
                            self.project.scenes.append(scene)
                            self._log(f"  新增场景: {scene.name}")
                        else:
                            self._log(f"  警告: 场景{i+1}数据类型异常: {type(scene_data)}")
                    except Exception as e:
                        self._log(f"  警告: 场景{i+1}处理失败: {e}")
            
            self._log(f"提取了 {len(self.project.scenes)} 个场景")
        except Exception as e:
            self._log(f"场景提取失败: {e}")
            import traceback
            self._log(f"堆栈: {traceback.format_exc()}")
            raise
        
        # 生成分镜脚本
        self._log("正在生成分镜脚本（AI将根据故事内容自行决定分镜数量和时长）...")
        extracted_scene_count = len(scenes_data) if scenes_data else 0
        
        # 分镜数和时长作为参考上限传给 AI，AI 自行决定实际数量
        if extracted_scene_count == 0:
            scene_count = max(target_scene_count // 2, 15)
            self._log(f"⚠️ 场景提取返回0个场景，使用保守分镜参考上限 {scene_count}")
        else:
            scene_count = target_scene_count
            self._log(
                f"参考分镜上限: {scene_count}（已提取 {extracted_scene_count} 个场景，"
                f"AI将自行决定实际分镜数）"
            )
        
        # 总时长已按档位计算（target_duration），此处补充已提取的场景作为分镜生成的上下文
        scene_context = "\n".join(
            f"{index}. {scene.name}: {scene.description}"
            for index, scene in enumerate(self.project.scenes, start=1)
        ) if self.project.scenes else ""
        self._log(
            f"参考分镜上限: {scene_count}, 参考总时长上限: {target_duration}秒 ({target_duration/60:.1f}分钟) "
            f"(length={length}, extract_scenes 返回 {extracted_scene_count} 个场景, AI自行决定实际数量)"
        )
        storyboard_data = await self.client.generate_script(
            story, 
            scene_count, 
            total_duration=target_duration,
            scene_context=scene_context,
            characters=[{"name": c.name} for c in self.project.characters],
            auto_count=True,
        )
        
        # auto_count 模式：AI 自行决定分镜数和时长，不再强制缩放
        # 仅当实际时长超出上限 20% 时才缩放（防止失控）
        total_ai_duration = sum(sb_data.get("duration", 5) for sb_data in storyboard_data)
        actual_sb_count = len(storyboard_data)
        self._log(f"AI自行决定: {actual_sb_count} 个分镜, 总时长 {total_ai_duration:.1f}秒 ({total_ai_duration/60:.1f}分钟)")
        
        if total_ai_duration > target_duration * 1.2:
            self._log(f"⚠️ AI生成的时长超出上限20%，将按比例缩放分镜时长")
            scale_factor = target_duration / total_ai_duration
            for sb_data in storyboard_data:
                original_duration = sb_data.get("duration", 5)
                sb_data["duration"] = max(4.0, original_duration * scale_factor)
            total_ai_duration = sum(sb_data.get("duration", 5) for sb_data in storyboard_data)
            self._log(f"✅ 缩放后分镜总时长: {total_ai_duration:.1f}秒 ({total_ai_duration/60:.1f}分钟)")

        # 根据实际分镜数和时长更新项目长度档位
        actual_length = _infer_length_from_duration(total_ai_duration)
        if actual_length != (length or self.project.length):
            self._log(f"📐 根据AI实际生成时长 {total_ai_duration:.0f}秒，更新项目长度: {length or self.project.length} → {actual_length}")
            self.project.length = actual_length
        
        for sb_data in storyboard_data:
            dialogue_role_name = sb_data.get("dialogue_role", "")
            
            # 所属场景名：与剧集模式共用同一契约函数（唯一真相见 asset_matcher）
            matched_scene_name, raw_scene_value = self._resolve_storyboard_scene(
                sb_data, sb_data.get("scene_number", 1)
            )
            sb_scene = matched_scene_name or raw_scene_value
            sb_title = (sb_data.get("scene_name") or "").strip()

            # 后处理：AI 常不填 characters/voice_text/dialogue_role
            sb_desc = (sb_data.get("description", "") or "").rstrip() + DESCRIPTION_MEDIA_SUFFIX
            sb_action = sb_data.get("action", "")
            sb_characters = sb_data.get("characters", []) or []
            sb_dialogue_role = dialogue_role_name or sb_data.get("dialogue_role", "")
            sb_voice_text = sb_data.get("voice_text", sb_data.get("dialogue", ""))

            if not sb_characters:
                tmp_sb = Storyboard(
                    scene_number=sb_data.get("scene_number", 1),
                    scene_name=sb_title or sb_scene,
                    scene=sb_scene,
                    description=sb_desc,
                    characters=[],
                    dialogue=sb_data.get("dialogue", ""),
                    dialogue_role=sb_dialogue_role,
                    voice_text=sb_voice_text,
                    action=sb_action,
                    camera=sb_data.get("camera", "中景"),
                    duration=5.0,
                )
                sb_characters = self._extract_characters_from_text(tmp_sb)

            if not sb_voice_text:
                sb_voice_text, sb_dialogue_role = self._extract_dialogue_from_text(
                    sb_desc, sb_action, sb_dialogue_role, sb_characters
                )

            storyboard = Storyboard(
                scene_number=sb_data.get("scene_number", 1),
                scene_name=sb_title or sb_scene,
                scene=sb_scene,
                description=sb_desc,
                characters=sb_characters,
                dialogue=sb_data.get("dialogue", ""),
                dialogue_role=sb_dialogue_role,
                voice_text=sb_voice_text,
                action=sb_action,
                camera=sb_data.get("camera", "中景"),
                duration=Settings.normalize_video_duration(
                    sb_data.get("duration", Settings.DEFAULT_DURATION)
                ),
            )
            # 回填旧分镜已生成的图/视频
            sb_idx = sb_data.get("scene_number", 1) - 1
            old_media = old_sb_media.get(sb_idx)
            if old_media:
                if old_media.get("frame_path"):
                    storyboard.frame_path = old_media["frame_path"]
                if old_media.get("video_path"):
                    storyboard.video_path = old_media["video_path"]
                if old_media.get("status") in ("done", "completed"):
                    storyboard.status = old_media["status"]
            self.project.storyboard.append(storyboard)
        # 按帧文件名回填：若按序号未匹配到，尝试按 frames/frame_NNN.png 文件存在性回填
        frames_dir = self.project_manager._project_dir(self.project.name) / "frames" if self.project_manager else None
        if frames_dir and frames_dir.exists():
            restored = 0
            for sb in self.project.storyboard:
                if sb.frame_path:
                    continue
                idx = sb.scene_number - 1
                frame_file = frames_dir / f"frame_{idx + 1:03d}.png"
                if frame_file.exists():
                    sb.frame_path = str(frame_file)
                    sb.status = "done"
                    restored += 1
            if restored:
                self._log(f"🖼️ 从帧目录回填了 {restored} 个分镜图")
        self._log(f"生成了 {len(storyboard_data)} 个分镜")
        total_storyboard_duration = sum(sb.duration for sb in self.project.storyboard)
        self._log(
            f"分镜时长合计: {total_storyboard_duration:.1f} 秒"
            f" ({total_storyboard_duration / 60:.2f} 分钟)"
        )
        
        self._report_scene_match_summary()

        self.project.global_characters = list(self.project.characters)
        self.project.global_props = list(self.project.props)
        self.project.global_scenes = list(self.project.scenes)

        self._save_assets_to_library()

        self._log("分镜提取完成！")
        self._auto_save("all")
        
        return {
            "project_name": project_name,
            "characters": self.project.characters,
            "scenes": self.project.scenes,
            "storyboard": self.project.storyboard,  # 返回分镜列表
            "storyboard_count": len(storyboard_data),
        }
    
    async def _expand_character_description(self, base_desc: str, char_name: str) -> str:
        """扩写角色描述为完整绘图描述"""
        if not base_desc or len(base_desc) < 30:
            # 描述太简单，调用AI扩写
            try:
                # 如果描述为空，给出默认提示而不是直接传给AI空内容
                if not base_desc or not base_desc.strip():
                    self._log(f"角色 {char_name} 描述为空，使用默认描述")
                    return f"{char_name}，写实风格角色设定图，面部特征清晰，表情自然"
                
                expand_prompt = f"""请将以下角色描述扩写为专业角色设定图级别的详细描述（200-300字），用于AI绘图和视频生成，按"从上到下"的结构撰写：
- 开头：角色名 + 核心身份设定（物种/身份/年代背景）
- 整体质感：主体呈现XX写实质感（如写实电影质感、写实国风神话质感、末世废土质感）
- 发型发色：发型样式、发色、发亮度、发质、发丝走向与发量、发饰位置与样式
- 脸型面孔：脸型、眼睛形状与眼色、眉形、鼻型、嘴型、胡须/无胡须状态、疤痕/胎记/斑点精确位置与形状
- 服装着装：顶部、外套/上衣、下装、鞋子，颜色分布，材质质感，徽标或装饰位置
- 服装破损细节：补丁/磨损/破洞/烧焦痕迹的精确位置（如"左肘补丁""右膝磨损泛白""袖口烧焦"）、形状与大小，无破损则写"完好无损"
- 手持/动作：手部动作或持握物品
- 整体色调：主要色、点缀色

重要规则（必须遵守）：
- 这是"单人角色设定图"的描述：只描述一个人物的一套固定服装，禁止出现"早期穿/中期换/后期着"、"随剧情变化"、"服装多次变化"等多套服装或多阶段叙事（服装差异请用"造型"功能单独管理）
- 禁止出现多人、群体、对比图等描述
- 服装破损/补丁的位置必须精确到身体部位（如左肘、右膝、下摆左侧），不可模糊描述，因为AI绘图需要精确锚定位置才能跨帧一致

角色描述：{base_desc}

扩写后的完整描述："""
                expanded = await self.client._call_text_api(expand_prompt)
                # 验证返回是否有效
                if expanded and len(expanded) > 30 and "忘记附上" not in expanded and "似乎" not in expanded:
                    self._log(f"角色 {char_name} 描述由AI扩写: {expanded[:50]}...")
                    return expanded.strip()
                else:
                    self._log(f"角色 {char_name} AI扩写返回无效，使用原描述")
                    return base_desc.strip()
            except Exception as e:
                self._log(f"角色 {char_name} AI扩写失败 ({e})，使用原描述")
                return base_desc.strip()
        else:
            # 描述已较完整，直接返回
            return base_desc.strip()
    
    async def generate_characters(
        self,
        character_configs: List[dict] = None,
        progress_callback: Callable = None,
        selected_characters: List[str] = None,
        skip_existing_costumes: bool = True,
    ) -> List[Character]:
        """生成角色图像

        selected_characters: 只生成这些角色（按角色名），None/空表示全部角色。
            对应角色页勾选框的「选择性生成」，与分镜的 selected_indices 行为一致。
        skip_existing_costumes: 顺带处理造型时是否跳过已有造型图。默认 True —— 
            只想重出底模时不会连带把该角色所有造型图重刷一遍（造型可单独重出）。
        """
        self._log("开始生成角色图像...")
        characters = character_configs or self.project.characters

        if selected_characters:
            only = {str(n).strip() for n in selected_characters if str(n).strip()}
            # 剧集模式下 project.characters 可能只是本集快照，故并上 global_characters，
            # 保证在「🌐 全部」视图里勾选的角色也能出图
            pool, seen_names = [], set()
            for c in (list(characters or [])
                      + list(getattr(self.project, 'global_characters', []) or [])):
                if c.name in only and c.name not in seen_names:
                    seen_names.add(c.name)
                    pool.append(c)
            characters = pool
            if not characters:
                self._log("⚠️ 勾选的角色在当前项目中未找到，未生成任何图片")
                return characters
            self._log(
                f"选择性生成：勾选 {len(characters)} 个角色 → "
                f"{'、'.join(c.name for c in characters)}"
            )

        skipped_costumes = 0
        skipped_chars = 0
        char_dir = self._asset_images_dir("characters")
        for i, character in enumerate(characters):
            # 所有角色均生成底图（含能量体、动物/妖兽、群体等非人物类型）

            # 已有角色图：跳过，避免重复生成（节省API费用，保持跨节一致性）
            if character.image_path and Path(character.image_path).exists():
                skipped_chars += 1
                # 若旧图在项目目录而项目已绑定共享库：把图搬进库目录并改指向，
                # 保证库内 image_path 可跨项目复用（_asset_images_dir 已完成复制，这里改指向）
                lib_name = getattr(self.project, "asset_library_name", "") or ""
                if lib_name:
                    target_img = char_dir / f"{_safe_filename(character.name)}.png"
                    if target_img.exists() and Path(character.image_path).resolve() != target_img.resolve():
                        character.image_path = str(target_img)
                self._log(f"跳过已有角色图: {character.name}")
                # 造型图仍需处理
                for costume in (getattr(character, 'costumes', None) or []):
                    if (skip_existing_costumes and costume.image_path
                            and Path(costume.image_path).exists()):
                        skipped_costumes += 1
                        # 旧造型图在项目目录而项目已绑定库：_asset_images_dir 已复制入库，改指向
                        lib_name = getattr(self.project, "asset_library_name", "") or ""
                        if lib_name:
                            c_target = char_dir / f"{_safe_filename(character.name)}_{_safe_filename(costume.name)}.png"
                            if c_target.exists() and Path(costume.image_path).resolve() != c_target.resolve():
                                costume.image_path = str(c_target)
                        self._log(f"跳过已有造型图: {character.name}/{costume.name}")
                        continue
                    try:
                        import hashlib
                        c_seed = int(hashlib.md5(f"{character.name}::{costume.name}".encode()).hexdigest(), 16) % 1000
                        base_desc = _single_subject_desc((character.description or "").strip())
                        cost_desc = (costume.description or "").strip()
                        pack = self._style_pack()
                        if pack:
                            if cost_desc:
                                c_prompt = pack.costume_prompt(base_desc, cost_desc, character.name in _FEMALE_NAMES, is_nonhuman=getattr(character, 'char_type', '') in ('能量体', '动物/妖兽', '群体'))
                            else:
                                c_prompt = pack.costume_prompt(base_desc, "保持基础形象不变", character.name in _FEMALE_NAMES, is_nonhuman=getattr(character, 'char_type', '') in ('能量体', '动物/妖兽', '群体'))
                            c_prompt += "，彩色画面，禁止黑白或灰度"
                        else:
                            c_prompt = (
                                f"全身摄影，纯白色背景，无背景阴影，无手持道具，"
                                f"{base_desc}，{cost_desc}，"
                                f"{self.project.style}风格，{SINGLE_PERSON_RULE}"
                            )
                        c_data = await self.client.generate_image(c_prompt, seed=c_seed, size="1024x1024")
                        c_path = char_dir / f"{_safe_filename(character.name)}_{_safe_filename(costume.name)}.png"
                        with open(c_path, "wb") as f:
                            f.write(c_data)
                        costume.image_path = str(c_path)
                        self._log(f"造型 {character.name}/{costume.name} 生成完成")
                    except Exception as e:
                        self._log(f"造型 {character.name}/{costume.name} 生成失败: {e}")
                continue
            
            self._log(f"正在生成角色: {character.name} ({i+1}/{len(characters)})")
            if progress_callback:
                progress_callback(f"生成角色: {character.name}", i+1, len(characters))
            
            # 角色描述为空时，尝试从造型描述中提取信息
            char_desc = (character.description or "").strip()
            if not char_desc:
                # 从造型描述中提取线索
                costumes = getattr(character, 'costumes', None) or []
                costume_descs = [c.description for c in costumes if c.description]
                if costume_descs:
                    # 使用第一个造型的描述作为基础
                    char_desc = costume_descs[0]
                    self._log(f"角色 {character.name} 描述为空，使用造型描述作为基础")
                else:
                    self._log(f"角色 {character.name} 描述为空，使用默认描述")
            
            # 扩写角色描述为完整绘图描述并写回到角色对象
            expanded_desc = await self._expand_character_description(char_desc, character.name)
            character.description = expanded_desc
            self._log(f"角色 {character.name} 描述已扩写并保存")
            
            # 为每个角色使用固定 seed 保持一致性（使用 MD5 确保跨进程稳定，seed 范围 -1 到 999）
            import hashlib
            seed = int(hashlib.md5(character.name.encode()).hexdigest(), 16) % 1000
            # 角色设定图：默认全身摄影前缀（纯白背景、无阴影、无道具）；
            # 启用风格包后改用该包的角色提示词模板（含风格锚定词/必守规则）
            pack = self._style_pack()
            # 非人物类型（能量体/动物/群体）：跳过人体模板，直接用描述出图
            char_type = getattr(character, 'char_type', '') or ''
            is_nonhuman = char_type in ('能量体', '动物/妖兽', '群体')
            if is_nonhuman:
                prompt = f"{_single_subject_desc(character.description)}，{self.project.style}风格"
                self._log(f"角色 {character.name} 为非人物类型「{char_type}」，使用直接描述模板")
            else:
                gender_tag = "" if character.name in _FEMALE_NAMES else "男性，"
                if pack:
                    prompt = pack.character_prompt(_single_subject_desc(character.description))
                    prompt = f"{gender_tag}{prompt}，{SINGLE_PERSON_RULE}"
                    self._log(f"角色 {character.name} 使用风格包「{pack.display_name}」角色模板")
                else:
                    prompt = (
                        f"全身摄影，纯白色背景，无背景阴影，无手持道具，"
                        f"{gender_tag}{_single_subject_desc(character.description)}，"
                        f"{self.project.style}风格，{SINGLE_PERSON_RULE}"
                    )
            try:
                image_data = await self.client.generate_image(prompt, seed=seed, size="1024x1024")
                # 保存图像（绑定共享库时直接写入库目录，跨项目复用）
                char_dir = self._asset_images_dir("characters")
                image_path = char_dir / f"{_safe_filename(character.name)}.png"
                with open(image_path, "wb") as f:
                    f.write(image_data)
                character.image_path = str(image_path)
                self._log(f"角色 {character.name} 生成完成")
            except Exception as e:
                self._log(f"角色 {character.name} 生成失败: {e}")
            
            for costume in (getattr(character, 'costumes', None) or []):
                # 已有造型图：默认跳过（造型可单独重出），避免重出底模时连带重刷全部造型
                if (skip_existing_costumes and costume.image_path
                        and Path(costume.image_path).exists()):
                    skipped_costumes += 1
                    self._log(f"跳过已有造型图: {character.name}/{costume.name}")
                    continue
                try:
                    c_seed = int(hashlib.md5(f"{character.name}::{costume.name}".encode()).hexdigest(), 16) % 1000
                    base_desc = _single_subject_desc((character.description or "").strip())
                    cost_desc = (costume.description or "").strip()
                    if pack:
                        if cost_desc:
                            c_prompt = pack.costume_prompt(base_desc, cost_desc, character.name in _FEMALE_NAMES, is_nonhuman=getattr(character, 'char_type', '') in ('能量体', '动物/妖兽', '群体'))
                        else:
                            c_prompt = pack.costume_prompt(base_desc, "保持基础形象不变", character.name in _FEMALE_NAMES, is_nonhuman=getattr(character, 'char_type', '') in ('能量体', '动物/妖兽', '群体'))
                        c_prompt += "，彩色画面，禁止黑白或灰度"
                        c_image_data = await self.client.generate_image(c_prompt, seed=c_seed, size="1024x1024")
                    else:
                        c_prompt = (
                            f"全身摄影，纯白色背景，无背景阴影，无手持道具，"
                            f"{base_desc}，造型变化：{cost_desc}，"
                            f"{self.project.style}风格，{SINGLE_PERSON_RULE}"
                            if cost_desc else
                            f"全身摄影，纯白色背景，无背景阴影，无手持道具，"
                            f"{base_desc}，{self.project.style}风格，{SINGLE_PERSON_RULE}"
                        )
                        c_image_data = await self.client.generate_image(c_prompt, seed=c_seed, size="1024x1024")
                    c_image_path = char_dir / f"{_safe_filename(character.name)}_{_safe_filename(costume.name)}.png"
                    with open(c_image_path, "wb") as f:
                        f.write(c_image_data)
                    costume.image_path = str(c_image_path)
                    costume.seed = c_seed
                    self._log(f"角色 {character.name} 造型「{costume.name}」生成完成")
                except Exception as e:
                    self._log(f"角色 {character.name} 造型「{costume.name}」生成失败: {e}")
        self._log(f"角色图像生成完成！（本次生成 {len(characters) - skipped_chars} 个，跳过 {skipped_chars} 个已有，造型跳过 {skipped_costumes} 个）")
        if skipped_costumes:
            self._log(
                f"造型图共跳过 {skipped_costumes} 张（已有图，如需重出请在「👗 管理造型」里"
                f"对单个造型点「🎨 生成该造型图」）"
            )
        # 造型挂在 global_characters（剧集模式）/characters（单集模式）上，
        # 必须同时保存 meta（project.json 的 global_characters）和 characters.json，
        # 否则重启后造型 image_path 丢失，「生成造型图像」时没有反映
        self._auto_save("meta")
        self._auto_save("characters")
        return characters

    async def generate_costumes_only(
        self,
        progress_callback: Callable = None,
        selected_characters: List[str] = None,
        selected_costumes: dict = None,
        force: bool = False,
    ) -> List[dict]:
        """只为尚未出图（或出图失败）的造型单独出图，不重新生成角色底模。

        跳过规则：costume.image_path 已设置且文件存在时直接跳过（避免重复计费）；
        force=True 时忽略该规则，强制重出（供「🎨 生成该造型图」单张重出使用）。
        与 generate_characters 共用同一套 prompt 逻辑（风格包 img2img 锚定底模），
        因此已生成的角色图可作为参考图使用。

        selected_characters: 只处理这些角色的造型（按角色名），None/空 = 全部角色。
        selected_costumes: {角色名: [造型名, ...]}，只处理这些具体造型（优先级高于
            selected_characters），供「管理造型」弹窗内对单个/若干造型出图。

        Returns:
            [{"character": 角色名, "costume": 造型名, "status": "skipped"|"generated"|"failed",
              "error": 错误信息(仅 failed)}]，供 GUI 汇总展示。
        """
        import hashlib
        import base64

        results: List[dict] = []
        total = 0
        current = 0

        # 选择性生成范围（角色页勾选框 / 管理造型弹窗）
        only_chars = {str(n).strip() for n in (selected_characters or []) if str(n).strip()}
        only_costumes = {
            str(k): {str(x).strip() for x in (v or []) if str(x).strip()}
            for k, v in (selected_costumes or {}).items()
        }

        def _has_image(costume) -> bool:
            return bool(costume.image_path and Path(costume.image_path).exists())

        def _skip(costume) -> bool:
            """已有图则跳过（force=True 时强制重出）"""
            return (not force) and _has_image(costume)

        def _is_nonhuman_char(c) -> bool:
            """非人物类型（能量体/动物/群体）不需要造型图"""
            return getattr(c, 'char_type', '') in ('能量体', '动物/妖兽', '群体')

        # 处理范围：剧集模式下 global_characters 与 project.characters 共享实例，
        # 只取「名称不重复」的并集，避免同一角色重复出图。
        seen = set()
        plan = []   # [(character, [costume, ...])]：本次实际要处理的造型
        for c in (list(getattr(self.project, "global_characters", []) or [])
                  + list(self.project.characters or [])):
            if not c.name or c.name in seen:
                continue
            seen.add(c.name)
            costumes = list(getattr(c, "costumes", None) or [])
            if not costumes:
                continue
            if only_costumes:
                allow = only_costumes.get(c.name)
                if not allow:
                    continue
                costumes = [x for x in costumes if x.name in allow]
            elif only_chars and c.name not in only_chars:
                continue
            if costumes:
                plan.append((c, costumes))

        pack = self._style_pack()
        char_dir = self._asset_images_dir("characters")

        # 预统计总数（含跳过项，进度条按全部造型计）
        total = sum(len(costumes) for _, costumes in plan)
        nonhuman_count = sum(
            len(costumes) for _char, costumes in plan if _is_nonhuman_char(_char)
        )
        pending_count = sum(
            1 for _char, costumes in plan for x in costumes
            if not _skip(x) and not _is_nonhuman_char(_char)
        )

        if total == 0:
            if only_chars or only_costumes:
                self._log(
                    "造型批量生成：勾选范围内没有可生成的造型"
                    "（该角色可能还没有造型，可在「👗 管理造型」里添加）。"
                )
            else:
                self._log("造型批量生成：没有找到任何造型，请先在角色页为角色添加造型或使用「批量导入造型」")
            return results
        if pending_count == 0:
            self._log(
                "造型批量生成：勾选范围内所有造型均已有图，无需生成"
                "（如需重出，可在「👗 管理造型」里对单个造型点「🎨 生成该造型图」）"
            )
            for character, costumes in plan:
                for costume in costumes:
                    results.append({"character": character.name, "costume": costume.name, "status": "skipped"})
            return results

        self._log(
            f"开始批量生成造型：共 {total} 个造型，需生成 {pending_count} 个"
            f"（跳过 {total - pending_count - nonhuman_count} 个已有图"
            f" + {nonhuman_count} 个非人物类型）..."
            + ("（强制重出）" if force else "")
        )
        if progress_callback:
            progress_callback(f"批量生成造型 0/{total}", 0, total)

        for character, costumes in plan:
            for costume in costumes:
                current += 1
                # 已有图：跳过（force=True 时强制重出）
                if _skip(costume):
                    results.append({"character": character.name, "costume": costume.name, "status": "skipped"})
                    if progress_callback:
                        progress_callback(f"跳过已有造型: {character.name}/{costume.name}", current, total)
                    continue

                # 非人物类型不需要造型图
                if _is_nonhuman_char(character):
                    self._log(f"跳过非人物类型造型: {character.name}/{costume.name}")
                    results.append({"character": character.name, "costume": costume.name, "status": "skipped"})
                    if progress_callback:
                        progress_callback(f"跳过非人物类型造型: {character.name}/{costume.name}", current, total)
                    continue

                try:
                    c_seed = int(hashlib.md5(f"{character.name}::{costume.name}".encode()).hexdigest(), 16) % 1000
                    base_desc = _single_subject_desc((character.description or "").strip())
                    cost_desc = (costume.description or "").strip()
                    if pack:
                        if cost_desc:
                            c_prompt = pack.costume_prompt(base_desc, cost_desc, character.name in _FEMALE_NAMES, is_nonhuman=getattr(character, 'char_type', '') in ('能量体', '动物/妖兽', '群体'))
                        else:
                            c_prompt = pack.costume_prompt(base_desc, "保持基础形象不变", character.name in _FEMALE_NAMES, is_nonhuman=getattr(character, 'char_type', '') in ('能量体', '动物/妖兽', '群体'))
                        c_prompt += "，彩色画面，禁止黑白或灰度"
                        c_image_data = await self.client.generate_image(c_prompt, seed=c_seed, size="1024x1024")
                    else:
                        c_prompt = (
                            f"全身摄影，纯白色背景，无背景阴影，无手持道具，"
                            f"{base_desc}，造型变化：{cost_desc}，"
                            f"{self.project.style}风格，{SINGLE_PERSON_RULE}"
                            if cost_desc else
                            f"全身摄影，纯白色背景，无背景阴影，无手持道具，"
                            f"{base_desc}，{self.project.style}风格，{SINGLE_PERSON_RULE}"
                        )
                        c_image_data = await self.client.generate_image(c_prompt, seed=c_seed, size="1024x1024")
                    c_image_path = char_dir / f"{_safe_filename(character.name)}_{_safe_filename(costume.name)}.png"
                    with open(c_image_path, "wb") as f:
                        f.write(c_image_data)
                    costume.image_path = str(c_image_path)
                    costume.seed = c_seed
                    results.append({"character": character.name, "costume": costume.name, "status": "generated"})
                    self._log(f"造型 {character.name}/{costume.name} 生成完成")
                except Exception as e:
                    results.append({
                        "character": character.name,
                        "costume": costume.name,
                        "status": "failed",
                        "error": str(e),
                    })
                    self._log(f"造型 {character.name}/{costume.name} 生成失败: {e}")

                if progress_callback:
                    progress_callback(f"生成造型: {character.name}/{costume.name}", current, total)

        generated = sum(1 for r in results if r["status"] == "generated")
        failed = sum(1 for r in results if r["status"] == "failed")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        self._log(f"造型批量生成完成：成功 {generated}，失败 {failed}，跳过 {skipped}")
        # 造型挂在 global_characters（剧集模式）/characters（单集模式）上，
        # 必须同时保存 meta（project.json 的 global_characters）和 characters.json，
        # 否则重启后造型数据丢失（"解析并生成造型时没有反映"的根因）
        self._auto_save("meta")
        self._auto_save("characters")
        return results
    
    async def generate_props(
        self,
        prop_configs: List[dict] = None,
        progress_callback: Callable = None
    ) -> List[Prop]:
        """生成道具图像（独立道具产品摄影：纯白背景、无人物环境、完整展示本体）"""
        self._log("开始生成道具图像...")
        props = prop_configs or getattr(self.project, 'props', [])
        
        for i, prop in enumerate(props):
            # 已有道具图：跳过，避免重复生成（节省API费用）
            if prop.image_path and Path(prop.image_path).exists():
                # 若旧图在项目目录而项目已绑定共享库：_asset_images_dir("props")
                # 已把旧图复制入库，这里改指向库内图片
                lib_name = getattr(self.project, "asset_library_name", "") or ""
                if lib_name:
                    prop_dir = self._asset_images_dir("props")
                    target_img = prop_dir / f"{_safe_filename(prop.name)}.png"
                    if target_img.exists() and Path(prop.image_path).resolve() != target_img.resolve():
                        prop.image_path = str(target_img)
                        for gp in getattr(self.project, "global_props", []) or []:
                            if gp.name == prop.name:
                                gp.image_path = str(target_img)
                                break
                self._log(f"跳过已有道具图: {prop.name}")
                continue
            self._log(f"正在生成道具: {prop.name} ({i+1}/{len(props)})")
            if progress_callback:
                progress_callback(f"生成道具: {prop.name}", i+1, len(props))
            
            pack = self._style_pack()
            if pack:
                prompt = pack.prop_prompt(prop.description)
            else:
                prompt = (
                    f"独立道具产品摄影，纯白色背景（色号 #fff），无人物、无环境。"
                    f"以略微俯视的正面角度完整呈现，主体居中占据画面主要区域，"
                    f"完整轮廓与所有主要组成部分清晰可见，画面四周保留充足空间。"
                    f"完整展示道具本体，不裁切，不单独展示局部组件。"
                    f"{prop.description}，{self.project.style}风格"
                )
            try:
                image_data = await self.client.generate_image(prompt, size="1024x1024")
                # 保存图像（绑定共享库时直接写入库目录，跨项目复用）
                prop_dir = self._asset_images_dir("props")
                image_path = prop_dir / f"{_safe_filename(prop.name)}.png"
                with open(image_path, "wb") as f:
                    f.write(image_data)
                prop.image_path = str(image_path)
                self._log(f"道具 {prop.name} 生成完成")
            except Exception as e:
                self._log(f"道具 {prop.name} 生成失败: {e}")
        
        self._log("道具图像生成完成！")
        self._auto_save("props")
        return props
    
    async def generate_scenes(
        self,
        scene_configs: List[dict] = None,
        progress_callback: Callable = None,
        selected_scenes: List[str] = None,
    ) -> List[Scene]:
        """生成场景图像。

        selected_scenes: 场景名列表；提供时只生成这些场景（选择性生成）。
        """
        self._log("开始生成场景图像...")
        scenes = scene_configs or self.project.scenes
        if selected_scenes:
            wanted = set(selected_scenes)
            scenes = [s for s in scenes if s.name in wanted]

        if not scenes:
            self._log("没有需要生成的场景（勾选为空或未匹配到场景）")
            self._auto_save("scenes")
            return []

        generated = 0
        skipped = 0
        sub_scene_generated = 0
        for i, scene in enumerate(scenes):
            # 已有场景图：跳过，避免重复生成（节省API费用，保持跨节一致性）
            if scene.image_path and Path(scene.image_path).exists():
                skipped += 1
                # 若旧图在项目目录而项目已绑定共享库：把图搬进库目录并改指向
                lib_name = getattr(self.project, "asset_library_name", "") or ""
                if lib_name:
                    scene_dir = self._asset_images_dir("scenes")
                    target_img = scene_dir / f"{_safe_filename(scene.name)}.png"
                    if target_img.exists() and Path(scene.image_path).resolve() != target_img.resolve():
                        scene.image_path = str(target_img)
                        # 同步更新 global_scenes 中的对应场景
                        for gs in getattr(self.project, "global_scenes", []) or []:
                            if gs.name == scene.name:
                                gs.image_path = str(target_img)
                                break
                self._log(f"跳过已有场景图: {scene.name}")
                continue
            
            # 分场景处理策略：
            # - 如果分场景的 description 中提到不同角度/光线/时间（如"外景"、"内景"、"黄昏"、"夜晚"），需要生成独立图
            # - 生成时以主场景图为参考（img2img），保持风格一致
            main_scene_name = getattr(scene, 'main_scene', '')
            main_scene_ref = None
            if main_scene_name:
                for s in scenes:
                    if s.name == main_scene_name:
                        main_scene_ref = s
                        break
            
            self._log(f"正在生成场景: {scene.name} ({i+1}/{len(scenes)})")
            if main_scene_name and main_scene_ref:
                self._log(f"  → 分场景，将参考主场景「{main_scene_name}」的图保持风格一致")
            if progress_callback:
                progress_callback(f"生成场景: {scene.name}", i+1, len(scenes))

            # 构建包含角色描述的场景 prompt
            prompt = self._build_scene_prompt(scene)
            try:
                # 分场景生成策略：如果有主场景参考图，使用 img2img 模式保持风格一致
                if main_scene_ref and main_scene_ref.image_path and Path(main_scene_ref.image_path).exists():
                    import base64
                    with open(main_scene_ref.image_path, "rb") as f:
                        main_scene_b64 = base64.b64encode(f.read()).decode()
                    
                    # 构建参考图提示词
                    ref_text = f"\n\n参考图说明：\n- 以下图片是主场景「{main_scene_name}」的基础外观图\n- 请保持相同的建筑风格、色调和世界观，但根据当前场景描述调整视角/光线/细节\n"
                    full_prompt = prompt + ref_text
                    
                    self._log(f"  → 使用 img2img 模式，参考主场景图生成分场景")
                    image_data = await self.client.generate_image(
                        full_prompt, 
                        size="1024x1024", 
                        image=[main_scene_b64], 
                        max_retries=12
                    )
                    sub_scene_generated += 1
                else:
                    # 主场景或无主场景参考：正常生成
                    image_data = await self.client.generate_image(prompt, size="1024x1024")
                
                # 保存图像（绑定共享库时直接写入库目录，跨项目复用）
                scene_dir = self._asset_images_dir("scenes")
                image_path = scene_dir / f"{_safe_filename(scene.name)}.png"
                with open(image_path, "wb") as f:
                    f.write(image_data)
                scene.image_path = str(image_path)
                # 同步更新 global_scenes 中的对应场景（保证持久化后重启能恢复）
                global_scenes = getattr(self.project, 'global_scenes', [])
                for gs in global_scenes:
                    if gs.name == scene.name:
                        gs.image_path = str(image_path)
                        break
                generated += 1
                mode_msg = "（参考主场景图生成）" if (main_scene_ref and main_scene_ref.image_path) else ""
                self._log(f"场景 {scene.name} 生成完成{mode_msg}")
            except Exception as e:
                self._log(f"场景 {scene.name} 生成失败: {e}")

        sub_scene_msg = f"，其中 {sub_scene_generated} 个分场景参考主场景图生成" if sub_scene_generated else ""
        self._log(f"场景图像生成完成（本次生成 {generated} 个，跳过 {skipped} 个已有{sub_scene_msg}）！")
        self._auto_save("scenes")
        return scenes
    
    def _extract_characters_from_text(self, sb) -> list:
        """当 sb.characters 为空时，从 action/description/dialogue 文本中自动提取已知角色名。

        遍历项目角色库（含 global_characters），按名字长度降序匹配，
        避免短名字被长名字的子串误匹配。返回匹配到的角色名列表。
        """
        text_parts = [
            getattr(sb, 'action', '') or '',
            getattr(sb, 'description', '') or '',
            getattr(sb, 'dialogue', '') or '',
            getattr(sb, 'voice_text', '') or '',
        ]
        text = " ".join(text_parts)
        if not text.strip():
            return []

        bank = self._collect_known_characters()
        if not bank:
            return []

        sorted_names = sorted(bank.keys(), key=lambda n: len(_norm_character_name(n)), reverse=True)
        found = []
        matched_spans = []
        for name in sorted_names:
            norm = _norm_character_name(name)
            base = norm.split("/")[0].strip()
            if not base:
                continue
            for candidate in [name, norm, base]:
                if candidate in text:
                    start = text.index(candidate)
                    end = start + len(candidate)
                    overlap = any(s < end and e > start for s, e in matched_spans)
                    if not overlap:
                        found.append(name)
                        matched_spans.append((start, end))
                    break
        return found

    def _extract_dialogue_from_text(
        self, description: str, action: str, dialogue_role: str, characters: list
    ) -> tuple:
        """从 description/action 文本中提取对话内容和说话角色。

        支持多种格式：
        - 角色名说/道/喊："对话内容"
        - 角色名："对话内容"（中文冒号+中文引号，无动词）
        - "对话内容"（纯引号，需从 characters 推断说话者）

        Returns:
            (voice_text, dialogue_role)
        """
        all_text = f"{description} {action}"
        voice_text = ""
        role = dialogue_role

        # 模式1：角色名 + 说/道/喊/叫/问/答/低声/沉声 + [冒号] + 引号内容
        m = re.search(
            r'([\u4e00-\u9fff]{1,6})(?:说|道|喊|叫|问|答|低声|沉声)\s*[：:]*\s*["\u201c\u300c]([^"\u201d\u300d]+?)["\u201d\u300d]',
            all_text
        )
        if m:
            role = role or m.group(1).strip()
            voice_text = m.group(2).rstrip("。")
            return voice_text, role

        # 模式2：角色名 + 中文冒号 + 中文引号内容（如：老叫花子："到了。入口在那后面。"）
        m = re.search(
            r'([\u4e00-\u9fff]{1,6})\s*[：:]\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
            all_text
        )
        if m:
            role = role or m.group(1).strip()
            voice_text = m.group(2).rstrip("。")
            return voice_text, role

        # 模式3：纯中文引号内容（无角色名前缀），从 characters 推断
        m = re.search(r'[\u201c\u300c"]([^"\u201d\u300d]{2,}?)["\u201d\u300d]', all_text)
        if m:
            voice_text = m.group(1).rstrip("。")
            if not role and characters:
                for c_name in characters:
                    norm_c = _norm_character_name(c_name)
                    if norm_c in all_text:
                        role = c_name
                        break

        return voice_text, role

    def _build_prompt_with_characters(self, sb) -> str:
        """构建包含角色描述的 prompt"""
        # 清洗 description：去除重复的短语
        raw_desc = sb.description
        if raw_desc:
            parts = [p.strip() for p in raw_desc.split("，")]
            unique_parts = []
            seen = set()
            for part in parts:
                if part and part not in seen:
                    unique_parts.append(part)
                    seen.add(part)
            clean_desc = "，".join(unique_parts)
        else:
            clean_desc = ""
        
        # 清洗 action：去除重复的短语
        raw_action = sb.action
        if raw_action:
            parts = [p.strip() for p in raw_action.split("，")]
            unique_parts = []
            seen = set()
            for part in parts:
                if part and part not in seen:
                    unique_parts.append(part)
                    seen.add(part)
            clean_action = "，".join(unique_parts)
        else:
            clean_action = ""
        
        base_prompt = f"{clean_desc}，{clean_action}，{sb.camera}，{self._style_tag()}风格"
        # 全局媒体约束：新分镜的描述已内含说明文本；旧数据在此自动补齐，避免重复
        if "不要字幕" not in base_prompt:
            base_prompt += "，" + DESCRIPTION_MEDIA_SUFFIX
        
                # 添加角色描述以保持一致性（支持 "角色名" 或 "角色名/造型名" 写法）
        effective_characters = list(sb.characters) if sb.characters else []
        if not effective_characters:
            effective_characters = self._extract_characters_from_text(sb)
        if effective_characters and (self.project.characters or getattr(self.project, 'global_characters', [])):
            char_descriptions = []
            char_display_names = []
            for char_name in effective_characters:
                # 通过统一解析器取 锚定文字（主描述 + 造型变化描述）
                _, anchor_text = self._resolve_character_reference(char_name)
                if anchor_text:
                    anchor_text = _strip_single_person_phrases(anchor_text)
                if anchor_text:
                    char_descriptions.append(anchor_text)
                    # 用于约束句的显示名：造型引用保留完整写法，普通角色用角色名
                    base = char_name.split("/")[0].strip()
                    char_display_names.append(char_name if "/" in char_name else base)
            
            if char_descriptions:
                # 去重：避免添加相同的描述
                unique_descs = []
                seen_descs = set()
                for desc in char_descriptions:
                    if desc not in seen_descs:
                        unique_descs.append(desc)
                        seen_descs.add(desc)
                
                if unique_descs:
                    chars_text = "；".join(unique_descs)
                    # 关键：对全部出场角色强调一致性约束（此前只约束第一个角色），
                    # 防止AI随意改变外观
                    names_clause = "、".join(f"「{n}」" for n in char_display_names)
                    # 多角色时追加显式指令，防止 AI 只画一个人
                    multi_char_clause = ""
                    if len(char_display_names) >= 2:
                        multi_char_clause = f"画面中必须同时出现{names_clause}共{len(char_display_names)}个角色，禁止只画一个人，"
                    base_prompt = f"<最高优先级：{multi_char_clause}角色{names_clause}的外观必须严格固定，以参考图为唯一标准：面部五官比例与特征（含胡须/无胡须状态、疤痕/胎记位置）、发型发丝走向与发量、服装款式与颜色与材质、服装破损/补丁/磨损的精确位置与形状，均须与角色参考图完全一致，禁止任何偏移或随意变化，禁止更改服装颜色>，{chars_text}，{base_prompt}"
        
        # 加入台词内容：让视频 AI 表现角色说话的口型与动作（台词不会显示为屏幕文字）
        voice_text = (getattr(sb, 'voice_text', '') or '').strip()
        if voice_text and voice_text not in base_prompt:
            speaker = (getattr(sb, 'dialogue_role', '') or '').strip() or "旁白"
            base_prompt += f"，{speaker}正在说：“{voice_text}”，角色口型与情绪与台词一致"
        
        # 启用风格包 / 高级自定义注入时，末尾追加风格锁定与一致性约束
        extra = self._frame_extra_constraints()
        if extra:
            base_prompt += extra
        
        return base_prompt
    def _asset_images_dir(self, kind: str):
        """资产图保存目录：绑定共享库时写 data/asset_library/<库名>/<kind>/，
        否则写项目目录 data/projects/<项目>/<kind>/。

        同时负责把「项目目录中已存在的旧图」复制进库目录（若绑定库且目标缺失），
        让 image_path 可以安全指向库内；返回最终图片目录。
        """
        lib_name = getattr(self.project, "asset_library_name", "") or ""
        project_dir = Settings.get_project_dir(self.project.name)
        proj_dir = project_dir / kind
        proj_dir.mkdir(parents=True, exist_ok=True)
        if not lib_name:
            return proj_dir
        from src.services.library_manager import library_images_dir
        lib_dir = library_images_dir(lib_name, kind)
        if lib_dir is None:
            return proj_dir
        # 旧图在项目目录时搬一份进库：只迁移「资产表里有记录、且 image_path
        # 指向项目目录」的图，避免把改名/删除后的孤儿 png 也塞进库。
        # （库 JSON 的同步由 GUI 的 sync_library_images 钩子在生成完成后回写）
        if kind == "characters":
            targets = list(getattr(self.project, "global_characters", []) or [])
            src_assets = targets
            for c in targets:
                for cost in getattr(c, "costumes", []) or []:
                    src_assets.append(cost)
        elif kind == "props":
            src_assets = list(getattr(self.project, "global_props", []) or [])
        elif kind == "scenes":
            src_assets = list(getattr(self.project, "global_scenes", []) or [])
        else:
            src_assets = []
        import shutil as _sh
        for a in src_assets:
            ip = getattr(a, "image_path", "") or ""
            if not ip:
                continue
            p = Path(ip)
            if not p.exists():
                continue
            # 已在库目录（或本目录之外）的跳过；只迁移落在项目目录里的旧图
            try:
                if p.resolve().parent == proj_dir.resolve():
                    target = lib_dir / p.name
                    if not target.exists():
                        _sh.copy2(p, target)
            except Exception:
                pass
        return lib_dir

    def _collect_known_characters(self) -> dict:
        """收集已知角色库（当前项目角色 + global_characters + 各剧集快照角色），名→Character，用于跨集角色一致"""
        bank = {}
        # 优先从 project.characters 获取
        for c in self.project.characters:
            if c.name and c.name not in bank:
                bank[c.name] = c
        # 补充 global_characters 中的角色（确保剧集模式下也能匹配到）
        for c in (getattr(self.project, 'global_characters', []) or []):
            if c.name and c.name not in bank:
                bank[c.name] = c
        data = getattr(self.project, 'episode_data', {}) or {}
        for snap in data.values():
            for c in snap.get("characters", []) or []:
                name = c.get("name", "") if isinstance(c, dict) else getattr(c, "name", "")
                if name and name not in bank:
                    try:
                        bank[name] = Character.from_dict(c) if isinstance(c, dict) else c
                    except Exception:
                        pass
        return bank

    def _collect_known_props(self) -> dict:
        """收集已知道具库（当前项目道具 + 各剧集快照），名→Prop，用于跨集道具一致"""
        bank = {}
        for p in getattr(self.project, 'props', []):
            if p.name and p.name not in bank:
                bank[p.name] = p
        data = getattr(self.project, 'episode_data', {}) or {}
        for snap in data.values():
            for p in snap.get("props", []) or []:
                name = p.get("name", "") if isinstance(p, dict) else getattr(p, "name", "")
                if name and name not in bank:
                    try:
                        bank[name] = Prop.from_dict(p) if isinstance(p, dict) else p
                    except Exception:
                        pass
        return bank

    def _resolve_character_reference(self, char_name: str):
        """解析角色引用名，返回 (参考图路径, 锚定文字)。

        支持 "角色名" 或 "角色名/造型名" 两种写法：
        - "陈炎"：取角色主图 + 主描述
        - "陈炎/血衣"：取该角色 costumes 中名为"血衣"的造型图 + 主描述 + 造型变化描述
        - 造型不存在或无图时，回退到角色主图 + 主描述（不报错）

        Returns:
            (image_path_or_None, anchor_text)
        """
        raw = (char_name or "").strip()
        if not raw:
            return None, ""

        # 拆分 角色名/造型名
        slash_pos = raw.find("/")

        # 收集候选角色（本集 + 全局），与参考图构建保持同一搜索范围
        candidates = []
        seen_names = set()
        for c in ((self.project.characters or []) if self.project else []):
            if c.name and c.name not in seen_names:
                candidates.append(c)
                seen_names.add(c.name)
        for c in (getattr(self.project, 'global_characters', []) or []):
            if c.name and c.name not in seen_names:
                candidates.append(c)
                seen_names.add(c.name)

        # 先按完整名字精确匹配（无造型引用时走原逻辑，行为与之前完全一致）
        target = None
        for c in candidates:
            if c.name == raw:
                target = c
                break
        # 精确匹配失败时，用归一化名字模糊匹配（去括号等装饰）
        if target is None and slash_pos < 0:
            norm_raw = _norm_character_name(raw)
            for c in candidates:
                if _norm_character_name(c.name) == norm_raw:
                    target = c
                    break
        if target is not None and slash_pos < 0:
            return target.image_path, (target.description or "").strip()

        # 拆分 角色名/造型名
        base_name = raw[:slash_pos].strip() if slash_pos >= 0 else raw
        costume_name = raw[slash_pos + 1:].strip() if slash_pos >= 0 else ""

        if target is None:
            # 先按 base_name 精确匹配
            for c in candidates:
                if c.name == base_name:
                    target = c
                    break
            # 精确匹配失败时，用归一化名字模糊匹配
            if target is None:
                norm_base = _norm_character_name(base_name)
                for c in candidates:
                    if _norm_character_name(c.name) == norm_base:
                        target = c
                        break
            if target is None:
                return None, ""

        anchor_parts = []
        if target.description:
            anchor_parts.append(target.description)

        image_path = target.image_path
        if costume_name:
            for cost in (getattr(target, 'costumes', None) or []):
                if cost.name == costume_name:
                    if cost.image_path:
                        image_path = cost.image_path
                    if cost.description:
                        anchor_parts.append(f"造型「{cost.name}」：{cost.description}")
                    break
            # 造型名未匹配到也回退主图，但把造型名作为文字提示保留
            else:
                anchor_parts.append(f"造型「{costume_name}」（未生成独立图，参照主图同一人）")

        return image_path, "；".join(anchor_parts)

    def _find_scene_for_storyboard(self, sb):
        """按分镜的「所属场景名」找到关联场景对象。

        与分镜生成/加载修复共用同一契约函数
        （asset_matcher.resolve_scene_assignment），确保「界面显示的所属场景名」
        与「分镜图关联的场景参考图」永远一致，不会各算各的。
        
        场景策略：
        - 优先返回分镜关联的场景对象（无论是主场景还是分场景）
        - 如果分场景没有独立图片，回退到使用主场景的图片
        - 分镜图生成时使用场景的 image_path 作为参考图
        """
        scenes = getattr(self.project, 'scenes', None) or []
        if not scenes:
            return None
        scene_names = [s.name for s in scenes if getattr(s, 'name', '')]
        shot_text = " ".join([
            getattr(sb, 'description', '') or '',
            getattr(sb, 'action', '') or '',
        ])
        matched, _score, _reason = _am.resolve_scene_assignment(
            getattr(sb, 'scene', '') or '',
            scene_names,
            shot_text=shot_text,
            raw_title=getattr(sb, 'scene_name', '') or '',
        )
        if not matched:
            return None
        
        # 找到匹配的场景对象
        matched_scene = None
        for s in scenes:
            if s.name == matched:
                matched_scene = s
                break
        
        if not matched_scene:
            return None
        
        # 如果分场景没有独立图片，回退到主场景
        if not getattr(matched_scene, 'image_path', None):
            main_scene_name = getattr(matched_scene, 'main_scene', '')
            if main_scene_name:
                for s in scenes:
                    if s.name == main_scene_name and getattr(s, 'image_path', None):
                        return s  # 返回主场景对象（有图片）
        
        return matched_scene

    def _resolve_storyboard_scene(self, sb_data: dict, index: int):
        """【统一入口】解析一个分镜的「所属场景名」。

        唯一真相：``asset_matcher.resolve_scene_assignment``（契约见该模块）。
        - 成功 → 返回标准场景名（∈ self.project.scenes 的名称）；
        - 失败 → 返回 None，**保留 AI 原值**（绝不写脏数据），
          仅把 (序号, 原值) 累计到 _sb_scene_unmatched，供每集末尾汇总告警。

        返回 ``(标准场景名 | None, 用于展示/回退的原始值)``。
        """
        scene_names = [s.name for s in getattr(self.project, 'scenes', []) if getattr(s, 'name', '')]
        raw_scene = (sb_data.get("scene") or "").strip()
        raw_title = (sb_data.get("scene_name") or "").strip()
        raw_value = raw_scene or raw_title
        if not scene_names:
            return None, raw_value
        shot_text = " ".join(
            str(sb_data.get(k) or "")
            for k in ("description", "action", "voice_text", "dialogue")
        )
        matched, _score, reason = _am.resolve_scene_assignment(
            raw_scene, scene_names, shot_text=shot_text, raw_title=raw_title
        )
        if matched:
            if reason == "index" and matched != raw_scene:
                self._log(f"  分镜 {index} 场景编号 '{raw_scene}' -> '{matched}'")
            elif matched != raw_value:
                self._log(
                    f"  分镜 {index} 场景名匹配({reason}): '{raw_value}' -> '{matched}'"
                )
            return matched, raw_value
        # 未匹配：保留原值，只累计（每集末尾汇总一条告警）
        self._sb_scene_unmatched.append((index, raw_value or "(空)"))
        return None, raw_value

    def _report_scene_match_summary(self):
        """输出本集场景匹配汇总（未匹配的只报一条，避免 24 条刷屏）"""
        unmatched = getattr(self, '_sb_scene_unmatched', None) or []
        if unmatched:
            preview = "、".join(f"#{idx}「{val}」" for idx, val in unmatched[:6])
            more = f" 等 {len(unmatched)} 个" if len(unmatched) > 6 else ""
            self._log(
                f"⚠️ 有 {len(unmatched)} 个分镜的场景名不在场景列表中"
                f"（已保留原值）：{preview}{more}"
                f"\n  提示：场景名不一致时，分镜图无法关联场景参考图；"
                f"可在分镜页第2列直接改为场景列表中的名称。"
            )
        self._sb_scene_unmatched = []

    def _build_frame_prompt(self, sb) -> str:
        """构建分镜帧图 prompt：在分镜 prompt 基础上并入关联场景的详细描述，保持场景一致"""
        prompt = self._build_prompt_with_characters(sb)
        scene = self._find_scene_for_storyboard(sb)
        if scene and getattr(scene, 'description', None):
            prompt = f"{scene.description}，{prompt}"
        # 多角色时在 prompt 末尾再次强调所有角色必须同框
        char_names = list(getattr(sb, 'characters', None) or [])
        if not char_names:
            char_names = self._extract_characters_from_text(sb)
        if len(char_names) >= 2:
            names_str = "、".join(f"「{n}」" for n in char_names)
            multi_hint = f"画面中必须同时呈现{names_str}共{len(char_names)}个角色，每个人物都要完整可见，禁止只画一个人或遗漏角色"
            if multi_hint not in prompt:
                prompt += f"，{multi_hint}"
        return prompt

    def _build_frame_reference_urls(self, sb, prev_sb=None) -> tuple:
        """构造分镜帧图生成的参考图 data URI 列表与标签（img2img 模式）

        Returns:
            (image_uris, labels): image_uris 为 data URI 列表；labels 为对应标签列表
        """
        import base64
        image_uris = []
        labels = []

        def _to_data_uri(path, max_size=1280, quality=95):
            if not path or not Path(path).exists():
                return None
            try:
                from PIL import Image
                import io
                img = Image.open(path)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_size:
                    ratio = max_size / max(w, h)
                    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                b64 = base64.b64encode(buf.getvalue()).decode()
                return f"data:image/jpeg;base64,{b64}"
            except ImportError:
                try:
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    ext = Path(path).suffix.lower().lstrip(".")
                    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
                    return f"data:{mime};base64,{b64}"
                except Exception:
                    return None
            except Exception:
                return None

        # 角色参考图优先（多角色时每人都需要参考图），上一帧图后补
        frame_char_names = list(getattr(sb, 'characters', None) or [])
        if not frame_char_names:
            frame_char_names = self._extract_characters_from_text(sb)
        for char_name in frame_char_names:
            if len(image_uris) >= 4:
                break
            ref_path, _ = self._resolve_character_reference(char_name)
            if ref_path:
                uri = _to_data_uri(ref_path, max_size=768, quality=90)
                if uri:
                    image_uris.append(uri)
                    labels.append(f"角色「{char_name}」的外观形象")

        scene = self._find_scene_for_storyboard(sb)
        if scene and len(image_uris) < 5:
            uri = _to_data_uri(getattr(scene, 'image_path', None), max_size=768, quality=90)
            if uri:
                image_uris.append(uri)
                labels.append(f"场景「{scene.name}」的环境背景")

        # 上一帧图放最后（构图/机位参考），避免其"单人"画面主导生成结果
        if prev_sb and len(image_uris) < 5:
            prev_frame_path = getattr(prev_sb, 'frame_path', None)
            if prev_frame_path and Path(prev_frame_path).exists():
                uri = _to_data_uri(prev_frame_path, max_size=768, quality=90)
                if uri:
                    image_uris.append(uri)
                    labels.append("上一帧构图与机位参考")

        sb_text = " ".join([
            getattr(sb, 'description', '') or '',
            getattr(sb, 'action', '') or '',
            getattr(sb, 'dialogue', '') or '',
            getattr(sb, 'voice_text', '') or '',
        ])
        for prop in (getattr(self.project, 'props', []) if self.project else []):
            if len(image_uris) >= 5:
                break
            if prop.name and prop.name in sb_text:
                uri = _to_data_uri(getattr(prop, 'image_path', None), max_size=768, quality=90)
                if uri:
                    image_uris.append(uri)
                    labels.append(f"道具「{prop.name}」的外观")

        return image_uris, labels

    def _build_reference_images(self, sb, first_frame: str):
        """构造视频参考图列表与标签（base64）：首帧 + 场景图 + 角色图 + 道具图，最多5张

        Returns:
            (images, labels)：images 为 base64 列表；labels 与之对齐，
            如 ["分镜构图与机位", "场景「xx」的环境背景", "角色「壳」的外观形象", "道具「xx」的外观"]
        """
        import base64
        images = []
        labels = []

        def _add(path, label):
            if not path or len(images) >= 5:
                return
            try:
                if Path(path).exists():
                    from PIL import Image as _Img
                    import io as _io
                    img = _Img.open(path)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    w, h = img.size
                    if max(w, h) > 768:
                        ratio = 768 / max(w, h)
                        img = img.resize((int(w * ratio), int(h * ratio)), _Img.LANCZOS)
                    buf = _io.BytesIO()
                    img.save(buf, format="JPEG", quality=90)
                    images.append(base64.b64encode(buf.getvalue()).decode())
                    labels.append(label)
            except Exception:
                try:
                    if Path(path).exists():
                        with open(path, "rb") as f:
                            images.append(base64.b64encode(f.read()).decode())
                            labels.append(label)
                except Exception:
                    pass

        # 1) 分镜首帧图：构图与机位参考
        if first_frame:
            images.append(first_frame)
            labels.append("分镜构图与机位")

        # 2) 角色图：本分镜出场角色的外观参考（按剧本 sb.characters 或自动提取）— 优先于场景图
        # 搜索范围：project.characters + global_characters，确保剧集模式下也能找到角色
        all_characters = list(self.project.characters or [])
        global_chars = getattr(self.project, 'global_characters', []) or []
        all_names = set(c.name for c in all_characters)
        for gc in global_chars:
            if gc.name not in all_names:
                all_characters.append(gc)
                all_names.add(gc.name)
        
        frame_effective_chars = list(getattr(sb, 'characters', None) or [])
        if not frame_effective_chars:
            frame_effective_chars = self._extract_characters_from_text(sb)
        for char_name in frame_effective_chars:
            if len(images) >= 4:
                break
            ref_path, _ = self._resolve_character_reference(char_name)
            _add(ref_path,
                 f"角色「{char_name}」的外观形象")

        # 3) 场景图：环境背景参考（按剧本 sb.scene 关联）
        scene = self._find_scene_for_storyboard(sb)
        if scene:
            _add(getattr(scene, 'image_path', None),
                 f"场景「{scene.name}」的环境背景")

        # 4) 道具图：分镜描述/动作/对白中提到的道具（补满剩余图位）
        sb_text = " ".join([
            getattr(sb, 'description', '') or '',
            getattr(sb, 'action', '') or '',
            getattr(sb, 'dialogue', '') or '',
            getattr(sb, 'voice_text', '') or '',
        ])
        for prop in (getattr(self.project, 'props', []) if self.project else []):
            if len(images) >= 5:
                break
            if prop.name and prop.name in sb_text:
                _add(getattr(prop, 'image_path', None),
                     f"道具「{prop.name}」的外观")

        return images, labels

    def _build_image_reference_prompt(self, images: list, labels: list = None) -> str:
        """按文档示例生成多图引用提示，每个 <Picture N> 精确对应其参考内容"""
        n = len(images)
        if n <= 1:
            return ""
        labels = labels or []
        ref_parts = []
        for idx in range(1, n + 1):
            label = labels[idx - 1] if idx - 1 < len(labels) else "视觉特征"
            ref_parts.append(f"以 <Picture {idx}> 的{label}为参考")
        return "，" + "，".join(ref_parts) + "，画面风格与所有参考图保持一致"

    def _build_scene_prompt(self, scene) -> str:
        """构建包含角色描述的场景 prompt（启用风格包时注入该包场景模板）"""
        pack = self._style_pack()
        if pack:
            base_prompt = pack.scene_prompt(scene.description, getattr(scene, "time_of_day", "day"))
        else:
            base_prompt = f"{scene.description}，{self.project.style}风格，{scene.time_of_day}"
        
        # 尝试从场景描述中提取角色名并添加描述
        if self.project.characters:
            char_descriptions = []
            for char in self.project.characters:
                # 如果角色名出现在场景描述中，添加角色描述
                if char.name in scene.description:
                    char_descriptions.append(char.description)
            
            if char_descriptions:
                chars_text = "，".join(char_descriptions)
                base_prompt = f"{chars_text}，{base_prompt}"
        
        return base_prompt
    
    async def generate_storyboard(
        self,
        scene_count: int = None,
        progress_callback: Callable = None,
        selected_indices: list = None,
    ) -> List[Storyboard]:
        """生成分镜帧（img2img 模式：优先使用角色/场景/道具参考图保持一致性）

        selected_indices: 指定要生成的分镜索引列表，为 None 时生成全部。
        """
        self._log("开始生成分镜帧...")
        all_storyboards = self.project.storyboard[:scene_count] if scene_count else self.project.storyboard

        if selected_indices is not None:
            indices_to_generate = [idx for idx in selected_indices if 0 <= idx < len(all_storyboards)]
        else:
            indices_to_generate = list(range(len(all_storyboards)))

        total = len(indices_to_generate)
        for seq, idx in enumerate(indices_to_generate):
            sb = all_storyboards[idx]
            self._log(f"正在生成分镜 {sb.scene_number}: {sb.scene_name} ({seq+1}/{total})")
            if progress_callback:
                progress_callback(f"生成分镜: {sb.scene_name}", seq+1, total)
            
            sb.status = "generating"
            prompt = self._build_frame_prompt(sb)
            prev_sb = all_storyboards[idx - 1] if idx > 0 else None
            ref_urls, ref_labels = self._build_frame_reference_urls(sb, prev_sb=prev_sb)
            try:
                if ref_urls:
                    ref_text = self._build_image_reference_prompt(ref_urls, ref_labels)
                    if ref_text and ref_text not in prompt:
                        prompt += ref_text
                    self._log(f"分镜 {sb.scene_number} 使用 {len(ref_urls)} 张参考图（img2img）")
                    image_data = await self.client.generate_image(
                        prompt, size="1280x720", image=ref_urls, max_retries=12
                    )
                else:
                    image_data = await self.client.generate_image(prompt, size="1280x720", max_retries=12)
                frame_dir = self._get_episode_frames_dir()
                frame_path = frame_dir / f"frame_{sb.scene_number:03d}.png"
                with open(frame_path, "wb") as f:
                    f.write(image_data)
                sb.frame_path = str(frame_path)
                sb.status = "done"
                self._log(f"分镜 {sb.scene_number} 生成完成")
            except NETWORK_ERRORS as e:
                sb.status = "failed"
                if isinstance(e, asyncio.CancelledError) and is_real_cancellation():
                    self._log(f"️ 生成被取消，已保存已完成的 {seq} 个分镜")
                    self._auto_save("storyboard")
                    raise
                self._log(f"分镜 {sb.scene_number} 生成失败: {type(e).__name__}: {e}")
            except Exception as e:
                self._log(f"分镜 {sb.scene_number} 生成失败: {e}")
                sb.status = "failed"
        
        self._log("分镜帧生成完成！")
        self._auto_save("storyboard")
        return all_storyboards

    async def generate_single_storyboard_image(
        self,
        scene_index: int,
        progress_callback: Callable = None,
    ) -> Optional[str]:
        """只生成指定分镜的图片（img2img 模式：优先使用角色/场景/道具参考图）。"""
        if scene_index < 0 or scene_index >= len(self.project.storyboard):
            raise IndexError(f"分镜序号超出范围: {scene_index + 1}")
        storyboard = self.project.storyboard[scene_index]
        storyboard.status = "generating"
        if progress_callback:
            progress_callback(f"生成分镜: {storyboard.scene_name}", 1, 1)
        try:
            prompt = self._build_frame_prompt(storyboard)
            prev_sb = self.project.storyboard[scene_index - 1] if scene_index > 0 else None
            ref_urls, ref_labels = self._build_frame_reference_urls(storyboard, prev_sb=prev_sb)
            if ref_urls:
                ref_text = self._build_image_reference_prompt(ref_urls, ref_labels)
                if ref_text and ref_text not in prompt:
                    prompt += ref_text
                self._log(f"分镜 {storyboard.scene_number} 使用 {len(ref_urls)} 张参考图（img2img）")
                image_data = await self.client.generate_image(
                    prompt, size="1280x720", image=ref_urls, max_retries=12
                )
            else:
                image_data = await self.client.generate_image(prompt, size="1280x720", max_retries=12)
            frame_dir = self._get_episode_frames_dir()
            frame_path = frame_dir / f"frame_{storyboard.scene_number:03d}.png"
            with open(frame_path, "wb") as file:
                file.write(image_data)
            storyboard.frame_path = str(frame_path)
            storyboard.status = "done"
            self._log(f"分镜 {storyboard.scene_number} 生成完成")
        except NETWORK_ERRORS:
            storyboard.status = "failed"
            if is_real_cancellation():
                raise
            self._log(f"分镜 {storyboard.scene_number} 生成失败: 网络异常（可重试）")
            return None
        except Exception:
            storyboard.status = "failed"
            raise
        self._auto_save("storyboard")
        return storyboard.frame_path

    def _get_episode_video_dir(self) -> Path:
        """获取当前集的视频目录：videos/第N集_标题/

        非剧集模式返回 videos/
        """
        project_dir = Settings.get_project_dir(self.project.name)
        video_dir = project_dir / "videos"
        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        if episodes and len(episodes) > 1 and 0 <= cur_ep < len(episodes):
            ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()
            ep_dir_name = f"第{cur_ep+1}集_{ep_title}"
            video_dir = video_dir / ep_dir_name
        video_dir.mkdir(parents=True, exist_ok=True)
        return video_dir

    def _get_episode_frames_dir(self) -> Path:
        """获取当前集的帧图目录：frames/第N集_标题/

        非剧集模式返回 frames/
        """
        project_dir = Settings.get_project_dir(self.project.name)
        frames_dir = project_dir / "frames"
        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        if episodes and len(episodes) > 1 and 0 <= cur_ep < len(episodes):
            ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()
            ep_dir_name = f"第{cur_ep+1}集_{ep_title}"
            frames_dir = frames_dir / ep_dir_name
        frames_dir.mkdir(parents=True, exist_ok=True)
        return frames_dir

    async def generate_storyboard_script(
        self,
        progress_callback: Callable = None
    ) -> List[Storyboard]:
        """根据当前集的故事文本和场景生成分镜脚本。

        剧集模式下：
        - 分镜数量按当前集的时长确定（每集独立计算）
        - 只使用当前集引用的场景和角色
        - 全局场景/角色是所有集共用的，但分镜只按当前集引用的子集生成
        """
        scene_count = len(self.project.scenes)
        self._sb_scene_unmatched = []  # 重置本集场景未匹配清单

        # 优先尝试从剧本文本中直接解析已有分镜结构（无需场景也可解析）
        story_text = self.project.story
        parsed_storyboards = parse_storyboards_from_text(story_text)

        # 尝试从 Markdown 分镜表格格式解析（1-3.md / 4-7.md / 8-10.md）
        if not parsed_storyboards:
            parsed_storyboards = parse_md_storyboard_tables(story_text)
            if parsed_storyboards:
                self._log(f"📋 从 Markdown 分镜表格中解析到 {len(parsed_storyboards)} 个分镜")

        # 尝试从「场景优化版」单节 Markdown 解析（逆天布衣_剧本.md：
        # 「**主场景**/**分场景** + 镜号 E0X-S0Y-C0Z 分镜表」格式）
        if not parsed_storyboards:
            parsed_storyboards = parse_md_section_shots(story_text)
            if parsed_storyboards:
                self._log(
                    f"📋 从单节分镜表格中解析到 {len(parsed_storyboards)} 个分镜"
                    f"（E0X-S0Y-C0Z 格式，场景按分场景码映射）"
                )

        # 尝试从剧集文本格式解析（角色描述/关键动作/对话）
        if not parsed_storyboards:
            parsed_storyboards = parse_episode_text_to_storyboards(story_text)
            if parsed_storyboards:
                self._log(f"📋 从剧集文本格式中解析到 {len(parsed_storyboards)} 个分镜（含角色和对话）")

        if scene_count == 0 and not parsed_storyboards:
            raise ValueError("当前项目没有场景，无法生成分镜脚本")

        # 剧集模式下，记录当前集信息并按集计算时长
        episodes = getattr(self.project, 'episodes', [])
        cur_ep = getattr(self.project, 'current_episode', -1)
        ep_info = ""
        ep_scene_names = []
        is_episode_mode = episodes and len(episodes) > 1 and 0 <= cur_ep < len(episodes)

        if is_episode_mode:
            ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()
            ep_info = f"（当前集: {ep_title}）"
            ep_data = (getattr(self.project, 'episode_data', {}) or {}).get(str(cur_ep), {})
            ep_scene_names = ep_data.get("scene_names", [])

            # 每集时长直接取 LENGTH_DURATION_MAP（已是每集时长）
            total_duration = LENGTH_DURATION_MAP.get(self.project.length, 180)
            # 每集分镜数：按每集时长和场景数计算
            per_ep_sb_count = Settings.LENGTH_SCENE_COUNT.get(self.project.length, 15)
            storyboard_count = max(per_ep_sb_count, scene_count, 3)
        else:
            total_duration = LENGTH_DURATION_MAP.get(
                self.project.length, scene_count * Settings.DEFAULT_DURATION
            )
            storyboard_count = max(
                Settings.LENGTH_SCENE_COUNT.get(self.project.length, scene_count * 3),
                scene_count,
            )

        # 日志：明确标注使用的是当前集的场景和时长
        if ep_scene_names:
            self._log(
                f"按当前集时长({total_duration:.0f}s)和 {scene_count} 个场景生成 {storyboard_count} 个分镜{ep_info}"
                f"\n  本集场景: {', '.join(ep_scene_names)}"
                f"\n  平均每场景约 {storyboard_count / scene_count:.1f} 个镜头..."
            )
        else:
            self._log(
                f"按时长({total_duration:.0f}s)和 {scene_count} 个场景生成 {storyboard_count} 个分镜{ep_info}"
                f"（平均每场景约 {storyboard_count / scene_count:.1f} 个镜头）..."
            )

        # 使用当前集的故事文本（project.story 已在选集时被设置为该集文本）
        # parsed_storyboards 已在函数开头解析

        if parsed_storyboards:
            self._log(
                f"📋 从剧本文本中解析到 {len(parsed_storyboards)} 个已有分镜，直接使用"
            )
            # 自动补充场景：将解析出的场景名添加到项目场景列表（如果不存在）
            parsed_scene_names = set()
            for sb in parsed_storyboards:
                sc = (sb.get("scene") or "").strip()
                if sc:
                    parsed_scene_names.add(sc)
            existing_scene_names = {s.name for s in self.project.scenes}
            new_scenes = parsed_scene_names - existing_scene_names
            if new_scenes:
                for sn in sorted(new_scenes):
                    scene = Scene(
                        name=sn,
                        description="",
                        style=self.project.style,
                        time_of_day="day"
                    )
                    self.project.scenes.append(scene)
                    self._log(f"  🏞️ 自动补充场景: {sn}")
                self._auto_save("scenes")
            storyboard_data = parsed_storyboards
        else:
            # 构建场景上下文：只使用当前集引用的场景（project.scenes 已在选集时筛选）
            scene_context = "\n".join(
                f"S{index}. {scene.name}: {scene.description}"
                for index, scene in enumerate(self.project.scenes, start=1)
            ) + "\n\n提示：每个分镜的 description/action/camera 按专业分镜脚本风格详细撰写；与已有角色相关的描述不要重复堆砌，如果有多个角色，每个角色的设定只描述一次。"

            # 高级/无感：叙事包分镜表手法 + 制作技法文档 作为分镜脚本约束注入
            technique_context = self._storyboard_script_constraints()
            if technique_context:
                scene_context += f"\n{technique_context}"

            # 构建角色列表：只使用当前集引用的角色（project.characters 已在选集时筛选）
            characters_list = [{"name": c.name} for c in self.project.characters]

            self._log(
                f"🤖 剧本文本中未找到分镜结构，调用AI生成分镜脚本..."
            )
            storyboard_data = await self.client.generate_script(
                story_text,
                scene_count=storyboard_count,
                total_duration=total_duration,
                scene_context=scene_context,
                characters=characters_list,
                auto_count=True,
            )
        if not storyboard_data:
            raise ValueError(f"分镜脚本生成结果为空（当前场景数: {scene_count}）")
        self.project.storyboard.clear()
        for index, sb_data in enumerate(storyboard_data, start=1):
            dialogue_role_name = sb_data.get("dialogue_role", "")
            
            # 对 action 字段进行去重处理
            raw_action = sb_data.get("action", "")
            if raw_action:
                parts = [p.strip() for p in raw_action.split("，")]
                unique_parts = []
                seen = set()
                for part in parts:
                    if part and part not in seen:
                        unique_parts.append(part)
                        seen.add(part)
                action = "，".join(unique_parts)
            else:
                action = ""

            # 从文本解析的分镜：从"声音"字段中提取对白
            dialogue = sb_data.get("dialogue", "")
            voice_text = sb_data.get("voice_text", "")
            if not dialogue:
                sound = sb_data.get("sound", "")
                if sound:
                    quote_matches = re.findall(
                        r'([\u4e00-\u9fff]{1,3})(?:低频)?(?:低声|说|喊|叫|吼|道)\s*[：:]\s*["\u201c]([^"\u201d]+?)["\u201d]',
                        sound
                    )
                    if quote_matches:
                        dialogue = quote_matches[0][1].rstrip("。")
                        if not dialogue_role_name:
                            dialogue_role_name = quote_matches[0][0].strip()
            if not voice_text:
                voice_text = dialogue

            # 构建 description：从文本解析时分镜没有 description 字段，
            # 需要把动作、镜头、声音等组合成描述
            description = sb_data.get("description", "")
            if not description:
                desc_parts = []
                camera_val = sb_data.get("camera", "")
                if camera_val:
                    desc_parts.append(camera_val)
                if action:
                    desc_parts.append(action)
                sound = sb_data.get("sound", "")
                if sound:
                    desc_parts.append(f"声音：{sound}")
                visual_note = sb_data.get("visual_note", "")
                if visual_note:
                    desc_parts.append(f"画面：{visual_note}")
                description = "；".join(desc_parts)
            description = description.rstrip() + DESCRIPTION_MEDIA_SUFFIX
            
            # 所属场景名：统一由 _resolve_storyboard_scene 解析（契约见 asset_matcher）。
            # Storyboard.scene = 所属场景名（∈ 场景列表名），scene_name = 镜头标题（仅展示）。
            # 旧版优先取 scene_name（AI 常把镜头小标题写进该字段）并用它覆盖销毁 scene，
            # 导致 1.2/2.1 等集出现「黄土新冢」「长跪坟前」这类非场景名的值。
            matched_scene_name, raw_scene_value = self._resolve_storyboard_scene(sb_data, index)
            sb_scene = matched_scene_name or raw_scene_value
            sb_title = (sb_data.get("scene_name") or "").strip()

            # 后处理：AI 常不填 characters/voice_text/dialogue_role，
            # 需从 description/action 文本中自动提取补全
            sb_characters = sb_data.get("characters", []) or []
            sb_dialogue_role = dialogue_role_name or sb_data.get("dialogue_role", "")
            sb_voice_text = voice_text

            # 如果 characters 为空，先构建临时 Storyboard 以利用 _extract_characters_from_text
            if not sb_characters:
                tmp_sb = Storyboard(
                    scene_number=sb_data.get("scene_number", index),
                    scene_name=sb_title or sb_scene,
                    scene=sb_scene,
                    description=description,
                    characters=[],
                    dialogue=dialogue,
                    dialogue_role=sb_dialogue_role,
                    voice_text=sb_voice_text,
                    action=action,
                    camera=sb_data.get("camera", "中景"),
                    duration=5.0,
                )
                sb_characters = self._extract_characters_from_text(tmp_sb)
                if sb_characters:
                    self._log(f"  分镜{index}: 自动提取角色 {sb_characters}")

            # 如果 voice_text 为空，尝试从 description/action 中提取对话
            if not sb_voice_text:
                sb_voice_text, sb_dialogue_role = self._extract_dialogue_from_text(
                    description, action, sb_dialogue_role, sb_characters
                )

            self.project.storyboard.append(Storyboard(
                scene_number=sb_data.get("scene_number", index),
                scene_name=sb_title or sb_scene,
                scene=sb_scene,
                description=description,
                characters=sb_characters,
                dialogue=dialogue,
                dialogue_role=sb_dialogue_role,
                voice_text=sb_voice_text,
                action=action,
                camera=sb_data.get("camera", "中景"),
                duration=Settings.normalize_video_duration(
                    sb_data.get("duration", Settings.DEFAULT_DURATION)
                ),
            ))
        self._report_scene_match_summary()
        self._auto_save("storyboard")
        if parsed_storyboards:
            self._log(f"✅ 从剧本文本解析了 {len(self.project.storyboard)} 个分镜")
        elif len(self.project.storyboard) != storyboard_count:
            self._log(
                f"警告：参考分镜上限为 {storyboard_count}，AI 实际返回 "
                f"{len(self.project.storyboard)} 个分镜"
            )
        else:
            self._log(f"根据 {scene_count} 个场景生成了 {len(self.project.storyboard)} 个分镜")
        return self.project.storyboard
    
    async def generate_scene_video(
        self,
        scene_index: int,
        progress_callback: Callable = None,
        max_retries_per_scene: int = 2
    ) -> Optional[str]:
        """生成单个分镜的视频"""
        # 确保使用生成开始时的 debug_mode，不受UI切换影响
        self.client.set_debug_mode(Settings.AGNES_DEBUG_MODE)
        
        if scene_index >= len(self.project.storyboard):
            self._log(f"分镜索引 {scene_index} 超出范围")
            return None
        
        sb = self.project.storyboard[scene_index]
        if not sb.frame_path:
            self._log(f"分镜 {scene_index} 没有帧图像，无法生成视频")
            return None
        
        for retry in range(max_retries_per_scene + 1):
            if retry > 0:
                self._log(f"分镜 {scene_index} 第 {retry} 次重试...")
            
            self._log(f"正在生成分镜 {scene_index} 的视频...")
            if progress_callback:
                progress_callback(f"生成视频: {sb.scene_name}", scene_index + 1, len(self.project.storyboard))
            
            # auto 模式：故事模式（无episodes）→text，剧集模式（有episodes）→reference
            import base64
            user_mode = Settings.AGNES_VIDEO_MODE
            if user_mode == "auto":
                is_episode_mode = bool(getattr(self.project, 'episodes', None))
                user_mode = "reference" if is_episode_mode else "text"
                self._log(f"auto 模式 → 项目为{'剧集模式' if is_episode_mode else '故事模式'}，使用 {user_mode} 模式")
            first_frame = None
            reference_images = []
            reference_labels = []
            image_ref_text = ""
            if user_mode != "text":
                try:
                    from PIL import Image as _Img
                    import io as _io
                    _ff_img = _Img.open(sb.frame_path)
                    if _ff_img.mode != "RGB":
                        _ff_img = _ff_img.convert("RGB")
                    _ff_buf = _io.BytesIO()
                    _ff_img.save(_ff_buf, format="JPEG", quality=90)
                    first_frame = base64.b64encode(_ff_buf.getvalue()).decode()
                except Exception:
                    with open(sb.frame_path, "rb") as f:
                        first_frame = base64.b64encode(f.read()).decode()
                reference_images, reference_labels = self._build_reference_images(sb, first_frame)
                image_ref_text = self._build_image_reference_prompt(reference_images, reference_labels)
            
            # 使用包含角色描述的 prompt 保持主角一致性
            prompt = self._build_prompt_with_characters(sb)
            # 视频模式追加风格包的视频风格标签（与 art_storyboard_video.md 一致）
            style_suffix = self._video_style_suffix()
            if style_suffix and style_suffix not in prompt:
                prompt += style_suffix
            # 视频提示词模式：如果项目选择了视频模式，将模式规则注入 prompt 约束
            video_mode = getattr(self.project, "video_mode", "") or ""
            if video_mode:
                mode_text = _skills.video_prompt_mode_text(video_mode)
                if mode_text:
                    prompt = f"{mode_text.strip()}\n\n---\n\n{prompt}"
                    self._log(f"分镜 {scene_index} 注入视频模式「{video_mode}」规则（{len(mode_text)}字）")
                else:
                    self._log(f"分镜 {scene_index} 使用视频模式「{video_mode}」但未找到规则文件")
            # 写实风格强化：检测风格包是否为真人写实类，追加反3D/反动画硬约束
            pack = self._style_pack()
            if pack and pack.style_gene and any(
                kw in pack.style_gene for kw in ("真人", "写实", "Photorealism", "photorealistic", "realpeople")
            ):
                anti_3d_clause = "，禁止3D动画渲染风格，禁止卡通/二次元/插画风格，必须是真人实拍质感，皮肤真实、光影自然、材质写实"
                if anti_3d_clause not in prompt:
                    prompt += anti_3d_clause
            # 视频动态约束：打破首帧图静态锚定，要求镜头运动和角色动作变化
            if VIDEO_MOTION_SUFFIX not in prompt:
                prompt += "，" + VIDEO_MOTION_SUFFIX
            
            audios = None  # 语音由 AI 依据对白自动生成，不再上传音频文件
            try:
                if user_mode != "text":
                    self._log(f"分镜 {scene_index} 正在上传首帧图像和参考图...")
                else:
                    self._log(f"分镜 {scene_index} 使用 text 模式，跳过图片上传")
                
                # 确定 mode 和 audios 参数
                # 优先使用用户在UI中选择的模式
                self._log(f"分镜 {scene_index} 读取到的 Settings.AGNES_VIDEO_MODE = '{user_mode}'")
                
                if user_mode == "reference":
                    # 用户选择了 reference 模式：使用参考图生成
                    mode = "reference"
                    if image_ref_text and audios:
                        prompt_with_audio = f"{image_ref_text}，以 <Audio 1> 的节奏和环境氛围作为参考，{prompt}"
                    elif audios:
                        prompt_with_audio = f"以 <Audio 1> 的节奏和环境氛围作为参考，{prompt}"
                    else:
                        prompt_with_audio = prompt
                    video_data = await self.client.generate_video(
                        prompt=prompt_with_audio,
                        duration=int(sb.duration),
                        first_frame=first_frame,
                        mode=mode,
                        size="720P",
                        progress_callback=lambda msg: self._log(f"分镜 {scene_index} {msg}"),
                        audios=audios if audios else None,
                        extra_images=reference_images[1:]
                    )
                    self._log(f"分镜 {scene_index} 使用 reference 模式生成视频（参考图 {len(reference_images)} 张）")
                elif user_mode == "text":
                    # 用户选择了 text 模式：纯文本生成，不带音频
                    mode = "text"
                    video_data = await self.client.generate_video(
                        prompt=prompt,
                        duration=int(sb.duration),
                        first_frame=first_frame,
                        mode=mode,
                        size="720P",
                        progress_callback=lambda msg: self._log(f"分镜 {scene_index} {msg}"),
                        audios=None
                    )
                    self._log(f"分镜 {scene_index} 使用 text 模式生成视频")
                elif use_agnes and audios:
                    # 用户未选择模式，AGNES开启且有音频：使用 reference
                    mode = "reference"
                    if image_ref_text:
                        prompt_with_audio = f"{image_ref_text}，以 <Audio 1> 的节奏和环境氛围作为参考，{prompt}"
                    else:
                        prompt_with_audio = f"以 <Audio 1> 的节奏和环境氛围作为参考，{prompt}"
                    video_data = await self.client.generate_video(
                        prompt=prompt_with_audio,
                        duration=int(sb.duration),
                        first_frame=first_frame,
                        mode=mode,
                        size="720P",
                        progress_callback=lambda msg: self._log(f"分镜 {scene_index} {msg}"),
                        audios=audios,
                        extra_images=reference_images[1:]
                    )
                    self._log(f"分镜 {scene_index} 使用 reference 模式（自动，参考图 {len(reference_images)} 张）")
                else:
                    # 默认 text 模式
                    mode = "text"
                    video_data = await self.client.generate_video(
                        prompt=prompt,
                        duration=int(sb.duration),
                        first_frame=first_frame,
                        mode=mode,
                        size="720P",
                        progress_callback=lambda msg: self._log(f"分镜 {scene_index} {msg}"),
                        audios=None
                    )
                    self._log(f"分镜 {scene_index} 使用 text 模式生成视频（默认）")
                self._log(f"分镜 {scene_index} 正在下载视频...")
                # 保存视频到集数目录
                video_dir = self._get_episode_video_dir()
                video_path = video_dir / f"scene_{scene_index+1:02d}.mp4"
                with open(video_path, "wb") as f:
                    f.write(video_data)
                sb.video_path = str(video_path)
                self._log(f"✅ 分镜 {scene_index} 视频生成完成: {video_path}")
                self._auto_save("storyboard")
                                
                return str(video_path)
            except NETWORK_ERRORS as e:
                # 与图片路径同因：网络层泄漏的 CancelledError 必须按可重试网络错误处理，
                # 否则会穿透本重试循环并崩掉整个「生成所有分镜视频」任务。
                raise_if_real_cancel(e)
                error_msg = f"{type(e).__name__}: {e}"
                self._log(f"❌ 分镜 {scene_index} 视频生成失败（网络异常）: {error_msg}")
                if retry < max_retries_per_scene:
                    self._log(f"分镜 {scene_index} 将自动重试...")
                    continue
                self._log(f"分镜 {scene_index} 已达到最大重试次数，跳过")
                break
            except Exception as e:
                error_msg = str(e)
                self._log(f"❌ 分镜 {scene_index} 视频生成失败: {error_msg}")
                
                # 如果是 400 错误，可能是参数问题，询问用户是否重试
                if "400" in error_msg and retry < max_retries_per_scene:
                    self._log(f"分镜 {scene_index} 生成失败，将跳过该分镜继续生成其他分镜")
                    break  # 400 错误通常是参数问题，不重试
                elif retry < max_retries_per_scene:
                    self._log(f"分镜 {scene_index} 将自动重试...")
                    continue
                else:
                    self._log(f"分镜 {scene_index} 已达到最大重试次数，跳过")
                    break
        
        return None
    
    async def generate_video(self, progress_callback: Callable = None, selected_indices: list = None) -> dict:
        """生成所有分镜视频（逐个生成，失败后跳过继续）

        selected_indices: 指定要生成的分镜索引列表，为 None 时生成全部。
        """
        self._log("开始生成所有分镜视频...")

        if selected_indices is not None:
            indices_to_generate = [idx for idx in selected_indices if 0 <= idx < len(self.project.storyboard)]
        else:
            indices_to_generate = list(range(len(self.project.storyboard)))

        success_count = 0
        failed_indices = []

        for i in indices_to_generate:
            result = await self.generate_scene_video(i, progress_callback)
            if result:
                success_count += 1
            else:
                failed_indices.append(i)
        
        self._log(f"分镜视频生成完成！成功: {success_count}/{len(indices_to_generate)}")
        if failed_indices:
            self._log(f"失败的分镜索引: {failed_indices}")
        
        self._auto_save("storyboard")
        return {
            "success_count": success_count,
            "total_count": len(self.project.storyboard),
            "failed_indices": failed_indices
        }
    
    async def merge_videos(self, progress_callback: Callable = None) -> Optional[str]:
        """合并所有分镜视频"""
        import sys
        print("\n" + "="*60, file=sys.stderr)
        print("🎬 开始执行视频合并", file=sys.stderr)
        print("="*60, file=sys.stderr)
        
        self._log("开始合并视频...")
        self.last_error = ""  # 供 GUI 显示具体失败原因
        
        # 打印 storyboard 信息
        print(f"📋 storyboard 数量: {len(self.project.storyboard)}", file=sys.stderr)
        for i, sb in enumerate(self.project.storyboard):
            print(f"   [{i}] name={sb.scene_name}, video_path={sb.video_path}", file=sys.stderr)
        
        video_paths = [sb.video_path for sb in self.project.storyboard if sb.video_path]
        if not video_paths:
            self.last_error = "没有可合并的视频（分镜列表里没有已生成的视频）"
            self._log("❌ 没有可合并的视频（storyboard 中没有 video_path）")
            print("❌ 没有 video_path", file=sys.stderr)
            return None
        
        print(f"✅ 找到 {len(video_paths)} 个视频路径", file=sys.stderr)
        for p in video_paths:
            print(f"   - {p}", file=sys.stderr)
        
        # 验证所有视频文件是否存在
        valid_paths = []
        for path in video_paths:
            if Path(path).exists():
                valid_paths.append(path)
                print(f"   ✅ 存在: {path}", file=sys.stderr)
            else:
                self._log(f"⚠️ 视频文件不存在，跳过: {path}")
                print(f"   ❌ 不存在: {path}", file=sys.stderr)
        
        if not valid_paths:
            self._log("❌ 没有找到有效的视频文件（所有文件都不存在）")
            print("❌ 没有有效文件", file=sys.stderr)
            return None
        
        self._log(f"✅ 找到 {len(valid_paths)} 个有效视频文件")
        print(f"✅ 有效文件: {len(valid_paths)}", file=sys.stderr)
        
        # 使用 ffmpeg 合并视频（更稳定）
        try:
            import subprocess
            import tempfile
            
            # 创建临时文件列表
            project_dir = Settings.get_project_dir(self.project.name)
            videos_root = project_dir / "videos"
            videos_root.mkdir(parents=True, exist_ok=True)

            # 剧集模式下，合并后的总视频保存在集数目录之上（videos/）
            # 文件列表放在集数目录内
            ep_video_dir = self._get_episode_video_dir()

            # 创建文件列表
            file_list_path = ep_video_dir / "file_list.txt"
            with open(file_list_path, 'w', encoding='utf-8') as f:
                for path in valid_paths:
                    abs_path = str(Path(path).absolute()).replace('\\', '/')
                    f.write(f"file '{abs_path}'\n")

            self._log(f"已创建文件列表: {file_list_path}")

            # 输出路径：剧集模式下按集命名，保存在 videos/ 目录下
            episodes = getattr(self.project, 'episodes', [])
            cur_ep = getattr(self.project, 'current_episode', -1)
            if episodes and len(episodes) > 1 and 0 <= cur_ep < len(episodes):
                ep_title = (episodes[cur_ep].get("title") or f"第{cur_ep+1}集").strip()
                final_path = videos_root / f"第{cur_ep+1}集_{ep_title}_final.mp4"
            else:
                final_path = videos_root / "final.mp4"
            
            # 使用 ffmpeg 合并
            self._log("开始合并视频...")
            self._log("这可能需要一些时间，请耐心等待...")
            
            # 查找 ffmpeg 可执行文件（统一走 media_tools：PATH/tools 目录/
            # imageio-ffmpeg/常见安装位置；缺失时给出可执行的安装指引）
            from src.services import media_tools
            ffmpeg_path = media_tools.find_ffmpeg(refresh=True)
            if not ffmpeg_path:
                self.last_error = "未检测到 FFmpeg（合并视频需要它）"
                self._log("❌ 未检测到 FFmpeg，无法合并视频")
                self._log(media_tools.install_hint())
                return None
            
            cmd = [
                ffmpeg_path,
                '-y',  # 覆盖输出文件
                '-f', 'concat',
                '-safe', '0',
                '-i', str(file_list_path),
                '-c', 'copy',  # 直接复制流，不重新编码（快速）
                '-movflags', '+faststart',
                str(final_path)
            ]
            
            self._log(f"执行命令: {' '.join(cmd)}")
            
            # 使用 Popen 而不是 run，避免阻塞
            import sys
            if sys.platform == 'win32':
                # Windows 下隐藏控制台
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    startupinfo=startupinfo
                )
            else:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8'
                )
            
            # 等待完成，带超时
            try:
                stdout, stderr = process.communicate(timeout=300)
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                self.last_error = "视频合并超时（超过 5 分钟）"
                self._log("视频合并超时（超过 5 分钟）")
                return None
            
            # 检查执行结果
            if returncode != 0:
                self._log(f"ffmpeg 返回码: {returncode}")
                if stderr:
                    self._log(f"错误输出: {stderr[:500]}")
                
                # 如果直接复制失败，尝试重新编码
                self._log("直接复制失败，尝试重新编码...")
                cmd_reencode = [
                    ffmpeg_path,
                    '-y',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', str(file_list_path),
                    '-c:v', 'libx264',
                    '-preset', 'slow',
                    '-crf', '18',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    '-movflags', '+faststart',
                    str(final_path)
                ]
                
                self._log(f"执行命令: {' '.join(cmd_reencode)}")
                
                # 使用 Popen 重新编码
                if sys.platform == 'win32':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                    
                    process = subprocess.Popen(
                        cmd_reencode,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8',
                        startupinfo=startupinfo
                    )
                else:
                    process = subprocess.Popen(
                        cmd_reencode,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8'
                    )
                
                try:
                    stdout, stderr = process.communicate(timeout=600)
                    returncode = process.returncode
                except subprocess.TimeoutExpired:
                    process.kill()
                    self.last_error = "视频重新编码超时（超过 10 分钟）"
                    self._log("重新编码超时（超过 10 分钟）")
                    return None
                
                if returncode != 0:
                    self.last_error = "FFmpeg 合并与重新编码均失败（编码不兼容）"
                    self._log(f"重新编码也失败了，返回码: {returncode}")
                    if stderr:
                        self._log(f"错误输出: {stderr[:500]}")
                    return None
            
            # 清理临时文件
            if file_list_path.exists():
                file_list_path.unlink()
            
            # 验证输出文件
            if final_path.exists():
                file_size = final_path.stat().st_size
                self._log(f"✅ 视频合并完成: {final_path}")
                self._log(f"   文件大小: {file_size / 1024 / 1024:.2f} MB")
            else:
                self.last_error = f"输出文件未生成: {final_path}"
                self._log(f"❌ 视频文件未生成: {final_path}")
                return None
            
            self.project.video_path = str(final_path)
            self.last_error = ""
            # 注意：不在此处（工作线程）保存项目文件，避免与主线程的自动保存竞态；
            # meta 由主线程回调 _on_merge_video_finished 统一保存
            return str(final_path)
        except FileNotFoundError:
            from src.services import media_tools
            self.last_error = "FFmpeg 不可用"
            self._log("❌ ffmpeg 未安装或不可执行")
            self._log(media_tools.install_hint())
            return None
        except subprocess.TimeoutExpired:
            self.last_error = "视频合并超时"
            self._log("视频合并超时（超过 10 分钟）")
            return None
        except Exception as e:
            import traceback
            self.last_error = f"{type(e).__name__}: {e}"
            self._log(f"视频合并失败: {e}")
            self._log(f"详细错误: {traceback.format_exc()}")
            return None