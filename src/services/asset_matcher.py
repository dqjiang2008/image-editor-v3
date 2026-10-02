"""资产名称匹配工具：剧集模式下把全局资产（角色/道具/场景）按名称匹配到某一集的正文。

背景（2026-09 修复「切集后本集角色/场景数量错误」问题）：
- 全局角色名常带括号别名（如「陈老栓（爷爷）」「老叫花子（九指神丐）」），
  而剧集正文使用短名（「陈老栓」「老叫花子」），旧版的
  ``char.name in story_text`` 全名子串匹配会大量漏配；
- 场景名常用「·」连接主段与描述（如「铁匠铺里屋·爷爷临终」），
  而正文场景行是「青阳镇·铁匠铺·里屋·夜晚」，精确子串同样对不上；
- LLM 提取资产时输出的 episodes 字段（适用集数）格式多变
  （["1.2"] / ["第1集"] / ["1-10"] / ["全剧"]），与按节拆集后的标题
  （「1.2 四指」）做精确相等匹配（``ep_title in c.episodes``）也几乎必然失配。

本模块提供：
- :func:`normalize_text`：剔除空白与「·」等装饰分隔符，用于匹配；
- :func:`match_asset_in_text`：多形态（全名/去括号主干/主干分段）匹配；
- :func:`episodes_field_hits`：解析 episodes 字段与分集标题的覆盖关系。
"""

import re
from typing import Optional

# 匹配时视为装饰分隔符、从文本/名称中剔除的字符（含全角空格、中点等）
_STRIP_RE = re.compile(r"[\s\u3000·・•·⋅・\-—―–～~]+")

# 括号别名（全角/半角括号），如「陈老栓（爷爷）」→「陈老栓」
_PAREN_RE = re.compile(r"[（(][^（）()]*[）)]")

# 集数编号（含小节号），如 1 / 12 / 1.2 / 1.10
_NUM_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")

# 集数区间，如 1-10 / 1~10 / 第1-10集 / 1至10
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～至到]\s*(\d+(?:\.\d+)?)")


def normalize_text(text: str) -> str:
    """归一化文本用于名称匹配：剔除空白与「·」等装饰分隔符。"""
    if not text:
        return ""
    return _STRIP_RE.sub("", text)


def name_base(name: str) -> str:
    """去掉括号别名后的名称主干：「陈老栓（爷爷）」→「陈老栓」。"""
    if not name:
        return ""
    return _PAREN_RE.sub("", name).strip()


def name_variants(name: str) -> list:
    """生成资产名的候选匹配形态：全名、去括号主干、各自的归一化形式。"""
    if not name:
        return []
    name = name.strip()
    candidates = [name]
    base = name_base(name)
    if base and base != name:
        candidates.append(base)
    variants = []
    for cand in candidates:
        for form in (cand, normalize_text(cand)):
            if form and form not in variants:
                variants.append(form)
    return variants


def match_asset_in_text(name: str, normalized_text: str, segment_min_len: int = 3) -> bool:
    """判断资产名能否匹配到（已归一化的）正文文本。

    匹配顺序：
    1. 全名 / 去括号主干（及归一化形式）完整出现；
    2. 仅当名称含「·」时（场景名常见），允许主干分段中的任一段
      （长度 ≥ segment_min_len）出现——如「铁匠铺里屋·爷爷临终」
      可凭主段「铁匠铺里屋」命中「青阳镇·铁匠铺·里屋·夜晚」。
    """
    if not name or not normalized_text:
        return False
    for v in name_variants(name):
        if len(v) >= 2 and v in normalized_text:
            return True
    raw = name.strip()
    if ("·" in raw) or ("・" in raw):
        base = name_base(raw)
        for part in re.split(r"[·・•·]", base):
            npart = normalize_text(part)
            if len(npart) >= segment_min_len and npart in normalized_text:
                return True
    return False


def scene_name_match_score(ai_name: str, scene_name: str) -> int:
    """计算 AI 生成的分镜场景名与项目场景名的匹配得分（越大越可信，0=不匹配）。

    分数设计：
    - 100：归一化后完全相同；
    - 80+：AI 名是场景名的子串（公共部分越长越可信）；
    - 60+：场景名是 AI 名的子串；
    - 40+：AI 名按「·」分段的某段（≥2字）出现在场景名中；
    - 30+：场景名按「·」分段的某段（≥2字）出现在 AI 名中。
    """
    if not ai_name or not scene_name:
        return 0
    a = normalize_text(ai_name)
    s = normalize_text(scene_name)
    if not a or not s:
        return 0
    if a == s:
        return 100
    if a in s:
        return 80 + len(a)
    if s in a:
        return 60 + len(s)
    score = 0
    for part in re.split(r"[·・•·]", ai_name):
        npart = normalize_text(part)
        if len(npart) >= 2 and npart in s:
            score = max(score, 40 + len(npart))
    for part in re.split(r"[·・•·]", scene_name):
        npart = normalize_text(part)
        if len(npart) >= 2 and npart in a:
            score = max(score, 30 + len(npart))
    # 多段全含：AI 名可拆成两段（各≥2字）且都出现在场景名中，
    # 处理中间被插入内容的情况（如 "洞府幻境" vs "传承洞府第一关幻境"）
    if score == 0 and len(a) >= 4:
        n = len(a)
        for cut in range(2, n - 1):
            p1, p2 = a[:cut], a[cut:]
            if p1 in s and p2 in s:
                score = max(score, 45)
                break
    return score


def best_scene_match(ai_name: str, scene_names: list, min_score: int = 40):
    """从场景名列表中找出与 AI 分镜场景名最匹配的一个。

    返回 (匹配到的场景名, 得分)；低于 min_score 视为无匹配，返回 (None, 0)。
    """
    best_name, best_score = None, 0
    for name in scene_names or []:
        if not name:
            continue
        score = scene_name_match_score(ai_name, name)
        if score > best_score:
            best_name, best_score = name, score
    if best_score < min_score:
        return None, 0
    return best_name, best_score


# ══════════════════════════════════════════════════════════════════════════
# 场景关联契约（唯一真相）
# ──────────────────────────────────────────────────────────────────────────
# 字段语义（全工程统一，不得再分叉）：
#   Storyboard.scene      = 「所属场景名」，值必须 ∈ 项目场景列表的名称，
#                           是分镜图/视频关联场景参考图的唯一依据；
#   Storyboard.scene_name = 「镜头标题」（6-10字，如「爷爷唤孙」），
#                           纯展示用途（UI 悬浮提示 / 日志），不参与匹配；
#   AI 输出约定           = scene 字段回填 "S1"/"S2" 编号；scene_name 填镜头标题。
#
# 唯一匹配入口：resolve_scene_assignment()
#   生成（单集/批量/旧文本解析）、加载修复、维护脚本必须全部只调用它，
#   保证「同一输入 → 同一结果」（确定性、无随机、幂等）。
# ══════════════════════════════════════════════════════════════════════════

# 名称匹配最低可信分（低于此值视为未匹配，保留原值不写脏数据）
SCENE_MATCH_MIN_SCORE = 40
# 正文覆盖率兜底：至少命中片段数
SCENE_COVERAGE_MIN_HITS = 2
# 正文覆盖率兜底：命中片段占比下限
SCENE_COVERAGE_MIN_RATIO = 0.5
# 覆盖率切片长度（2 字滑窗，兼顾中文短场景名）
SCENE_COVERAGE_SEG_LEN = 2

# 场景编号锚点：S1 / s3 / S3. / S3 青阳镇铁匠铺黄昏
_SCENE_INDEX_RE = re.compile(r"^\s*[Ss]\s*(\d{1,3})\s*(?:$|[.\-、,，:：)）\s])")


def parse_scene_index(value) -> Optional[int]:
    """解析 AI 回填的场景编号：``"S3"`` / ``"s3 青阳镇铁匠铺黄昏"`` → 3；否则 None。

    编号是「分镜 ↔ 场景」最可信的锚点：AI 只需回填编号，场景名由程序按下标
    取标准值，从根本上消除名称漂移（旧版让 AI 自己写场景名，必然对不上）。
    """
    if value is None:
        return None
    m = _SCENE_INDEX_RE.match(str(value))
    if not m:
        return None
    idx = int(m.group(1))
    return idx if idx >= 1 else None


def scene_name_segments(scene_name: str, seg_len: int = SCENE_COVERAGE_SEG_LEN) -> list:
    """把场景名切成滑窗片段（默认 2 字），用于正文覆盖率判定。"""
    name = normalize_text(scene_name or "")
    if not name:
        return []
    if len(name) < seg_len:
        return [name]
    return [name[i:i + seg_len] for i in range(len(name) - seg_len + 1)]


def scene_text_hits(scene_name: str, text: str, seg_len: int = SCENE_COVERAGE_SEG_LEN):
    """统计场景名片段在正文中的命中情况，返回 ``(命中数, 片段总数)``。"""
    segs = scene_name_segments(scene_name, seg_len)
    if not segs:
        return 0, 0
    norm = normalize_text(text or "")
    if not norm:
        return 0, len(segs)
    return sum(1 for s in segs if s in norm), len(segs)


def scene_text_coverage(scene_name: str, text: str, seg_len: int = SCENE_COVERAGE_SEG_LEN) -> float:
    """场景名在正文中的覆盖率（0~1）：命中片段数 / 片段总数。

    用于判断「该场景是否属于本集」以及 AI 完全改写场景名时的兜底：
    - 1.6 集正文含「天断山脉」「峡谷」「狼」，场景名「天断山脉峡谷狼战」
      覆盖率 ≈ 0.57 → 判定属于本集（旧版子串匹配失败 → 兜底全量 25 个场景）；
    - 与本集无关的场景（如 1.6 正文中的「青阳镇铁匠铺黄昏」）覆盖率极低。
    """
    hits, total = scene_text_hits(scene_name, text, seg_len)
    if not total:
        return 0.0
    return hits / total


def resolve_scene_assignment(
    raw_scene,
    scene_names: list,
    shot_text: str = "",
    raw_title: str = "",
    min_score: int = SCENE_MATCH_MIN_SCORE,
):
    """【唯一场景关联入口】把分镜原始场景字段解析为项目场景列表中的标准场景名。

    所有调用方（单集生成 / 批量生成 / 旧文本解析路径 / GUI 加载修复 /
    维护脚本）必须只调用本函数，保证同一输入在任何入口结果恒定。

    解析顺序（命中即返回）：
      ① ``raw_scene`` 是 ``"S{n}"`` 编号 → 按 n 取场景列表下标（零改写风险）；
      ② 名称匹配（归一化相同 > 互相包含 > 「·」分段 > 拆两段）；
      ③ ``raw_title`` 参与同样的名称匹配（AI 把场景名写进标题字段时兜底）；
      ④ 正文覆盖率兜底（AI 完全改写场景名时）；
      ⑤ 全部失败 → 返回 ``(None, 0, "unmatched")``，**调用方必须保留原值**，
         不得写入脏数据（只累计计数并汇总告警）。

    返回 ``(标准场景名 | None, 得分, 命中原因)``；命中原因取值：
    ``index`` / ``name`` / ``title`` / ``coverage`` / ``unmatched`` / ``no_scene_list``。
    """
    names = [n for n in (scene_names or []) if n]
    if not names:
        return None, 0, "no_scene_list"

    raw = (raw_scene or "").strip() if isinstance(raw_scene, str) else ""

    # ① 编号锚点（最可信）
    idx = parse_scene_index(raw)
    if idx is not None and 1 <= idx <= len(names):
        return names[idx - 1], 1000, "index"

    # ②③ 名称匹配：scene 优先，其次 title（小标题）
    title = (raw_title or "").strip() if isinstance(raw_title, str) else ""
    for cand, reason in ((raw, "name"), (title, "title")):
        if not cand:
            continue
        best_name, score = best_scene_match(cand, names, min_score=min_score)
        if best_name:
            return best_name, score, reason

    # ④ 正文覆盖率兜底（AI 完全改写场景名，如「洞府幻境」→「传承洞府第一关幻境」）
    if shot_text:
        best_name, best_ratio, best_hits = None, 0.0, 0
        for name in names:
            hits, total = scene_text_hits(name, shot_text)
            ratio = hits / total if total else 0.0
            if ratio > best_ratio or (ratio == best_ratio and hits > best_hits):
                best_name, best_ratio, best_hits = name, ratio, hits
        if (best_name and best_ratio >= SCENE_COVERAGE_MIN_RATIO
                and best_hits >= SCENE_COVERAGE_MIN_HITS):
            return best_name, int(best_ratio * 90), "coverage"

    return None, 0, "unmatched"


def episodes_field_hits(episodes, ep_title: str):
    """判断资产 episodes 字段是否覆盖分集标题（如「1.2 四指」）。

    返回：
    - True：明确覆盖该集；
    - False：明确不覆盖；
    - None：字段为空（无信息），由调用方决定兼容行为（一般视为全剧可用）。

    兼容 LLM 常见输出格式：
    - ["1.2"]（按节编号，与标题「1.2 四指」对应）
    - ["第1集"] / ["1"]（按大集编号）
    - ["1-10"] / ["第1-10集"]（区间）
    - ["全剧"] / ["全局"]
    """
    if not episodes:
        return None
    vals = [str(v).strip() for v in episodes if str(v) and str(v).strip()]
    if not vals:
        return None
    if any(("全剧" in v) or ("全局" in v) for v in vals):
        return True
    m = re.search(r"(\d+)\.(\d+)", ep_title or "")
    section = f"{m.group(1)}.{m.group(2)}" if m else None  # 如 "1.2"
    major = m.group(1) if m else None                     # 如 "1"
    for v in vals:
        nums = _NUM_TOKEN_RE.findall(v)
        # 精确小节号：token 级比较（避免 "1.10" 误含 "1.1"）
        if section and section in nums:
            return True
        # 区间：按大集号判断（"1-10" 覆盖 1.x 全部小节）
        rng = _RANGE_RE.search(v)
        if rng and major:
            lo, hi = float(rng.group(1)), float(rng.group(2))
            if lo <= float(major) <= hi:
                return True
        # 单一大集编号（"第1集"/"1"）：与大集号相等即命中
        if major and nums and "." not in v:
            if all(float(n) == float(major) for n in nums):
                return True
    return False
