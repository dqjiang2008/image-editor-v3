"""剧集文本分集工具。

支持识别常见剧集标记：
- 第X集 / 第X话 / 第X回（中文数字或阿拉伯数字）
- 【第X集 标题】
- EP01 / EP.1 / Episode 1
标题行判定：行首匹配标记且整行长度 <= 60 字符。

按节拆集模式（split_script_into_episodes_by_section）：
- 先按「第X集」拆分大集
- 再在每个大集内部按「第X节」拆分小节
- 每节成为独立的一集，标题格式如 "1.1 铁匠铺的黄昏"
- 这样每节拥有完整的 3 分钟时间，7 节 × 3 分钟 = 21 分钟/集
"""

import re
from typing import List, Optional


_EPISODE_TITLE_RE = re.compile(
    r'^\s*(?:#{1,6}\s*)?(?:'
    r'【\s*第[0-9一二三四五六七八九十百千零〇两]{1,6}[集话回][^】]{0,40}】'
    r'|第[0-9一二三四五六七八九十百千零〇两]{1,6}[集话回]'
    r'|EP\.?\s*[0-9]{1,3}'
    r'|Episode\s*[0-9]{1,3}'
    r')',
    re.IGNORECASE,
)

_CN_DIGIT_MAP = {
    '零': 0, '〇': 0, '两': 2,
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '百': 100, '千': 1000,
}


def _cn_to_int(s: str) -> int:
    """中文数字转整数，如 '三' → 3, '十二' → 12, '二十一' → 21"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if not s:
        return 0
    result = 0
    current = 0
    for ch in s:
        v = _CN_DIGIT_MAP.get(ch)
        if v is None:
            continue
        if v >= 10:
            if current == 0:
                current = 1
            result += current * v
            current = 0
        else:
            current = v
    result += current
    return result


_EPISODE_NUM_RE = re.compile(
    r'第([0-9一二三四五六七八九十百千零〇两]{1,6})[集话回]',
    re.IGNORECASE,
)

_SECTION_TITLE_RE = re.compile(
    r'^\s*(?:#{1,6}\s+)?第([0-9一二三四五六七八九十百千零〇两]{1,6})节\s*[：:]?\s*(.*)',
)

_SECTION_INLINE_RE = re.compile(
    r'^\s*(?:#{1,6}\s+)?第([0-9一二三四五六七八九十百千零〇两]{1,6})节[：:]?\s*(.+)',
)


def split_script_into_episodes(text: str) -> Optional[List[dict]]:
    """把整部剧本文本按剧集标记拆分为分集列表。

    仅识别「第X集/话/回」标准标题行。像「第一集人物详细描述」「第一集节数划分」
    这类扩展后缀说明行不作为剧集标题（正则限定 第X集 后必须紧跟 话/回 或结束，
    此处后缀含「人物/节数」等词会被排除），避免把附录章节误切成独立「集」。

    Returns:
        识别到 >= 2 个标题时返回 [{"title": ..., "text": ...}, ...]；
        否则返回 None（无法分集）。
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    headers = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 60:
            continue
        if _EPISODE_TITLE_RE.match(stripped):
            suffix = _EPISODE_TITLE_RE.match(stripped)
            tail = stripped[suffix.end():]
            if any(k in tail for k in ("人物", "节数", "描写", "总结", "要点", "结束", "完")):
                continue
            if any(k in stripped for k in ("结束", "完）", "（完）")):
                continue
            headers.append(i)

    if len(headers) < 2:
        return None

    episodes = []
    for idx, start in enumerate(headers):
        end = headers[idx + 1] if idx + 1 < len(headers) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        title = lines[start].strip()
        episodes.append({"title": title, "text": body})
    return episodes


def _extract_episode_number(title: str) -> int:
    """从集标题中提取集号，如 '#第一集：天降血书' → 1"""
    m = _EPISODE_NUM_RE.search(title)
    if m:
        return _cn_to_int(m.group(1))
    return 0


def _find_sections_in_text(lines: List[str], start_offset: int = 0) -> List[tuple]:
    """在文本行列表中找出所有「第X节」标题行。

    Returns:
        [(line_index, section_number, section_subtitle), ...]
        line_index 是在 lines 中的行号
    """
    sections = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        m = _SECTION_INLINE_RE.match(stripped)
        if not m:
            m = _SECTION_TITLE_RE.match(stripped)
        if m:
            sec_num = _cn_to_int(m.group(1))
            subtitle = m.group(2).strip() if m.group(2) else ""
            sections.append((i, sec_num, subtitle))
    return sections


def split_script_into_episodes_by_section(text: str) -> Optional[List[dict]]:
    """按节拆集：先按「第X集」拆大集，再按「第X节」拆小节，每节成为独立集。

    标题格式：
      - "1.1 铁匠铺的黄昏"  （大集号.节号 节名）
      - "1.2 四指"
      - "2.1 黄土新坟"

    如果某大集内没有「第X节」标记，则该大集整体作为一个集保留。

    Returns:
        拆分后的 [{"title": ..., "text": ...}, ...]；
        无法拆分时返回 None。
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()

    ep_headers = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        if _EPISODE_TITLE_RE.match(stripped):
            suffix = _EPISODE_TITLE_RE.match(stripped)
            tail = stripped[suffix.end():]
            if any(k in tail for k in ("人物", "节数", "描写", "总结", "要点", "结束", "完")):
                continue
            if any(k in stripped for k in ("结束", "完）", "（完）")):
                continue
            ep_headers.append(i)

    if not ep_headers:
        return None

    big_episodes = []
    for idx, start in enumerate(ep_headers):
        end = ep_headers[idx + 1] if idx + 1 < len(ep_headers) else len(lines)
        title = lines[start].strip()
        body_lines = lines[start:end]
        big_episodes.append({
            "title": title,
            "lines": body_lines,
            "start": start,
            "end": end,
        })

    result = []
    for big_ep in big_episodes:
        ep_num = _extract_episode_number(big_ep["title"])
        ep_title_stripped = big_ep["title"].lstrip('#').strip()
        body_lines = big_ep["lines"]

        sections = _find_sections_in_text(body_lines)

        if not sections:
            result.append({
                "title": f"{ep_num}.0 {ep_title_stripped}" if ep_num > 0 else ep_title_stripped,
                "text": "\n".join(body_lines).strip(),
            })
            continue

        for sec_idx, (sec_line, sec_num, sec_subtitle) in enumerate(sections):
            sec_start = sec_line
            sec_end = sections[sec_idx + 1][0] if sec_idx + 1 < len(sections) else len(body_lines)
            sec_body = "\n".join(body_lines[sec_start:sec_end]).strip()

            if sec_subtitle:
                display_title = f"{ep_num}.{sec_num} {sec_subtitle}"
            else:
                display_title = f"{ep_num}.{sec_num}"

            if len(sec_body) < 50:
                continue

            result.append({
                "title": display_title,
                "text": sec_body,
            })

    if len(result) < 1:
        return None
    return result


# 「第X集：标题」H1 标题中紧跟的说明后缀（如「第一集人物详细描述」）不作为剧集标题
_EP_SUFFIX_SKIP = ("人物", "节数", "描写", "总结", "要点", "结束", "完")

# E04-S01 风格的 H2 节标题（场景优化版剧本第4集起采用）
_ECODE_H2_RE = re.compile(
    r'^\s*(?:#{1,6}\s+)?E(\d+)-S(\d+)\s*[：:]?\s*(.*)'
)

# 「第X集」H1 标题（含说明后缀，如「（7节×3分钟）」；兼容「五、第一集：」带节序前缀）
_EP_H1_NUM_RE = re.compile(
    r'^\s*#{1,4}\s*(?:[一二三四五六七八九十百千零〇两\d]+[、.．]\s*)?'
    r'第([0-9一二三四五六七八九十百千零〇两]{1,6})集[：:]\s*(.*)'
)


def is_storyboard_script(text: str) -> bool:
    """判断文本是否为「已含分镜表」的剧本（逆天布衣_剧本.md 一类）。

    判据：① 含「第X集」标题（H1/H2/H3 均可，兼容整部剧本与分集正文两种文件）；
    ② 含 E0X-S0Y-C0Z 镜号编码行。
    两者同时满足时走 :func:`split_script_by_storyboard_code` 拆集，
    每节的分镜走 producer.parse_md_section_shots 解析（跳过 AI 生成）。
    """
    if not text or not text.strip():
        return False
    has_ep = bool(re.search(r'^\s*#{1,4}\s*第[0-9一二三四五六七八九十百千零〇两]{1,6}集[：:]', text, re.M))
    has_shots = bool(re.search(r'\|\s*E\d+-S\d+-C\d+\s*\|', text))
    return has_ep and has_shots


def is_episode_body(text: str) -> bool:
    """判断是否为「单集分镜正文」文件（含 Ecode 节标题与镜号编码，但无分集「第X集」附录总表）。

    分集正文（如「第2集.md」）不含角色参考卡/服装总表/道具表等附录，
    只含 ``### E0X-S0Y`` 节标题与分镜表，导入时应「增量补场景」而非重抽全量资产。
    """
    if not text or not text.strip():
        return False
    has_ecode = bool(re.search(r'^\s*#{1,4}\s*E\d+-S\d+\s*[：:]?\s*\S', text, re.M))
    has_shots = bool(re.search(r'\|\s*E\d+-S\d+-C\d+\s*\|', text))
    has_appendix = bool(re.search(
        r'^\s*#{1,4}\s*(角色参考卡|关键道具|服装变化表|角色服装变化表|分集场景总表)',
        text, re.M,
    ))
    return has_ecode and has_shots and not has_appendix


def is_scene_library(text: str) -> bool:
    """判断是否为「场景资产库」文件（如《逆天布衣》场景资产库 - 主场景整合表）。

    判据：含「| SC01 | 场景名 | …」唯一主场景清单行，或
    「| S001 | 主场景 | 第X集 | …」场景归属总表行，且非分集正文。
    """
    if not text or not text.strip():
        return False
    if is_episode_body(text):
        return False
    has_sc = bool(re.search(r"^\|\s*SC\d+\s*\|", text, re.M))
    has_s = bool(re.search(r"^\|\s*S\d{3}\s*\|", text, re.M))
    return has_sc or (has_s and "主场景" in text)


def parse_section_target_duration(text: str) -> float:
    """从节文本中解析「**本节目标时长**：180秒」，返回秒数（无则 0.0）。"""
    m = re.search(r'\*\*本节目标时长\*\*\s*[：:]\s*([\d.]+)\s*秒?', text)
    return float(m.group(1)) if m else 0.0


def parse_section_shot_count(text: str) -> int:
    """从节文本中解析「**本节镜头数**：25个」，返回镜头数（无则 0）。"""
    m = re.search(r'\*\*本节镜头数\*\*\s*[：:]\s*(\d+)', text)
    return int(m.group(1)) if m else 0


def parse_sections(text: str) -> List[dict]:
    """从剧集文本中解析分节信息。
    
    识别格式：
    - 「**本节目标时长**：180秒」
    - 「**本节镜头数**：25个」
    - 「### E01-S01 第一节：xxx」
    
    Returns:
        [{"name": "第一节：xxx", "target_duration": 180, "shot_count": 25}, ...]
    """
    if not text:
        return []
    
    sections = []
    
    # 识别 Ecode 节标题（### E01-S01 第一节：xxx）
    ecode_pattern = re.compile(r'^#{1,4}\s*E(\d+)-S(\d+)\s*(.*)$', re.M)
    
    for m in ecode_pattern.finditer(text):
        ep_num = int(m.group(1))
        sec_num = int(m.group(2))
        subtitle = m.group(3).strip()
        
        # 提取该节的文本块（从当前节标题到下一节标题之间）
        start_pos = m.start()
        next_match = list(ecode_pattern.finditer(text))
        next_pos = None
        for nm in next_match:
            if nm.start() > start_pos:
                next_pos = nm.start()
                break
        
        section_text = text[start_pos:next_pos] if next_pos else text[start_pos:]
        
        target_duration = parse_section_target_duration(section_text)
        shot_count = parse_section_shot_count(section_text)
        
        sections.append({
            "name": subtitle or f"第{sec_num}节",
            "target_duration": target_duration if target_duration > 0 else 180,  # 默认3分钟
            "shot_count": shot_count if shot_count > 0 else 15,  # 默认15个分镜
        })
    
    # 如果没有找到Ecode节标题，但文本中有目标时长/镜头数信息，返回默认一节
    if not sections:
        target_duration = parse_section_target_duration(text)
        shot_count = parse_section_shot_count(text)
        if target_duration > 0 or shot_count > 0:
            sections.append({
                "name": "全集（默认一节）",
                "target_duration": target_duration if target_duration > 0 else 180,
                "shot_count": shot_count if shot_count > 0 else 15,
            })
    
    return sections


def split_script_by_storyboard_code(text: str) -> Optional[List[dict]]:
    """按「第X集」+「第X节」两级结构拆集，节标题兼容 ``E0X-S0Y`` 编码格式。

    与 :func:`split_script_into_episodes_by_section` 的区别：
    - 节标题除「第X节：名称」外，还识别 ``## E04-S01 第一关·幻境`` 形式
      （场景优化版剧本第4-10集采用），此时集号/节号直接取自 E{集}-S{节} 编码；
    - 附录章节（角色参考卡 / 关键道具 / 服装变化表）不会被误切成集。

    输出的标题格式与 :func:`split_script_into_episodes_by_section` 保持一致：
    ``"1.1 铁匠铺的黄昏"``。如果整个文本中没有任何可识别的节，返回 None。
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    n = len(lines)

    # 1) 定位「第X集」标题行（H1~H4，附录章节不会误伤）
    ep_headers = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        m = _EP_H1_NUM_RE.match(stripped)
        if not m:
            continue
        tail = m.group(2).strip()
        if any(k in tail for k in _EP_SUFFIX_SKIP):
            continue
        ep_headers.append(i)

    if not ep_headers:
        # 2') 无「第X集」标题：按 Ecode（E0X-S0Y）节标题自报集号分组
        #     适用于分集正文文件（第2集.md：仅 ## 第二集 + ### E02-S0x，无 H1）
        ecode_secs: List[tuple] = []  # (line_idx, ep_num, sec_num, subtitle)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or not stripped.startswith("#"):
                continue
            m_ecode = _ECODE_H2_RE.match(stripped)
            if not m_ecode:
                continue
            ep_num = int(m_ecode.group(1))
            sec_num = int(m_ecode.group(2))
            subtitle = m_ecode.group(3).strip()
            ecode_secs.append((i, ep_num, sec_num, subtitle))
        if not ecode_secs:
            return None
        # 按 ep_num 排序后分组
        result: List[dict] = []
        cur_ep: int = -1
        sec_idx_in_ep = 0
        for idx, (line_idx, ep_num, sec_num, subtitle) in enumerate(ecode_secs):
            sec_end = ecode_secs[idx + 1][0] if idx + 1 < len(ecode_secs) else n
            sec_body = "\n".join(lines[line_idx:sec_end]).strip()
            if len(sec_body) < 50:
                continue
            if ep_num != cur_ep:
                cur_ep = ep_num
                sec_idx_in_ep = 0
            display_title = (
                f"{ep_num}.{sec_num} {subtitle}" if subtitle else f"{ep_num}.{sec_num}"
            )
            result.append({"title": display_title, "text": sec_body})
            sec_idx_in_ep += 1
        return result or None

    # 2) 逐集分块，块内识别「第X节：」或「E0X-S0Y」H2 标题
    result: List[dict] = []
    any_section = False
    for idx, start in enumerate(ep_headers):
        end = ep_headers[idx + 1] if idx + 1 < len(ep_headers) else n
        ep_num = _cn_to_int(_EP_H1_NUM_RE.match(lines[start].strip()).group(1))
        body_lines = lines[start:end]

        sections = []  # (line_idx, sec_num, subtitle)
        for i, line in enumerate(body_lines):
            stripped = line.strip()
            if not stripped or not stripped.startswith("#"):
                continue
            m_ecode = _ECODE_H2_RE.match(stripped)
            if m_ecode:
                sec_num = int(m_ecode.group(2))
                subtitle = m_ecode.group(3).strip()
                sections.append((i, sec_num, subtitle))
                continue
            m_sec = _SECTION_TITLE_RE.match(stripped)
            if m_sec:
                sec_num = _cn_to_int(m_sec.group(1))
                subtitle = m_sec.group(2).strip() if m_sec.group(2) else ""
                sections.append((i, sec_num, subtitle))

        if not sections:
            # 无节标记：整块作为一集保留
            title_stripped = lines[start].lstrip('#').strip()
            result.append({
                "title": f"{ep_num}.0 {title_stripped}" if ep_num > 0 else title_stripped,
                "text": "\n".join(body_lines).strip(),
            })
            continue

        any_section = True
        for sec_idx, (sec_line, sec_num, sec_subtitle) in enumerate(sections):
            sec_start = sec_line
            sec_end = sections[sec_idx + 1][0] if sec_idx + 1 < len(sections) else len(body_lines)
            sec_body = "\n".join(body_lines[sec_start:sec_end]).strip()
            if len(sec_body) < 50:
                continue
            display_title = (
                f"{ep_num}.{sec_num} {sec_subtitle}"
                if sec_subtitle else f"{ep_num}.{sec_num}"
            )
            result.append({"title": display_title, "text": sec_body})

    if not any_section:
        return None
    return result or None