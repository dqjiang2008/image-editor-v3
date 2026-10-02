"""共享资产库（asset_library）管理。

共享库保存于 data/asset_library/<库名>/，与其他项目目录同级：

    data/
    ├── projects/逆天布衣/...
    └── asset_library/逆天布衣_剧本/
        ├── characters.json   # 角色（含造型）
        ├── characters/       # 角色图/造型图（跨项目共享）
        ├── props.json        # 道具
        ├── props/            # 道具图
        ├── scenes.json       # 场景
        └── scenes/           # 场景图

用途：首次导入「已含分镜表」的整部剧本（逆天布衣_剧本.md 一类）时，
把文件里的角色/造型/道具/场景提取为全局资产并持久化到库中；
之后其他项目/剧本可用 import_to_project() 调入复用（同名角色沿用已有图片）。

绑定了库的项目（project.asset_library_name）生成图片时直接写入库内图片目录，
并用 sync_library_images() 把已生成的图片同步回库 JSON 的 image_path，
保证其他项目导入库时可直接复用图片，无需重新生成。
"""
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from src.config.settings import Settings
from src.core.producer import (
    _norm_character_name,
    _safe_filename,
    compose_sub_scene_name,
    normalize_scene_name,
    parse_costume_table,
)
from src.models.project import Character, Costume, Prop, Scene
from src.services.episode_splitter import _cn_to_int


def _norm_prop_name(name: str) -> str:
    """归一道具名（用于合并判定）：去空白。"""
    return re.sub(r"\s+", "", name or "")


_LIB_ROOT: Optional[Path] = None


def _is_md_sep_row(line: str) -> bool:
    """判断 markdown 表格分隔行「|------|------|」。"""
    s = line.strip()
    return bool(re.fullmatch(r"\|?[\s\-:|]+\|?", s)) and "-" in s


def _library_root() -> Path:
    global _LIB_ROOT
    if _LIB_ROOT is None:
        _LIB_ROOT = Settings.DATA_DIR / "asset_library"
    return _LIB_ROOT


def list_libraries() -> List[str]:
    """列出所有共享资产库名称"""
    root = _library_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def load_library(name: str) -> Optional[dict]:
    """读取库，返回 {"characters", "costumes", "props", "scenes"}（模型对象）。
    
    加载时自动扫描磁盘 PNG 文件，修复 JSON 中 image_path 为空但磁盘有文件的条目。
    """
    root = _library_root() / name
    if not root.exists():
        return None

    def _read(fname: str) -> list:
        f = root / fname
        if not f.exists():
            return []
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    characters_raw = _read("characters.json")
    props_raw = _read("props.json")
    scenes_raw = _read("scenes.json")
    
    # 加载时自动扫描磁盘文件，修复 image_path 为空的条目
    _repair_image_paths_from_disk(root, "characters", characters_raw)
    _repair_image_paths_from_disk(root, "props", props_raw)
    _repair_image_paths_from_disk(root, "scenes", scenes_raw)
    
    # 如果有修复，回写到 JSON
    # 注意：这里不直接回写，避免加载时意外修改文件
    # 修复只在内存中生效，下次 save_library 时会持久化
    
    costumes: List[dict] = []
    for c in characters_raw:
        costumes.extend(c.get("costumes", []) or [])
    return {
        "characters": [Character.from_dict(c) for c in characters_raw],
        "costumes": [Costume.from_dict(c) for c in costumes],
        "props": [Prop.from_dict(p) for p in props_raw],
        "scenes": [Scene.from_dict(s) for s in scenes_raw],
    }


def _repair_image_paths_from_disk(root: Path, kind: str, assets_raw: list) -> int:
    """扫描 kind 目录下的 PNG 文件，修复 assets_raw 中 image_path 为空的条目。
    
    返回修复的数量。
    """
    img_dir = root / kind
    if not img_dir.exists():
        return 0
    
    png_files = {f.name: f for f in img_dir.glob("*.png")}
    if not png_files:
        return 0
    
    repaired = 0
    for asset in assets_raw:
        if not asset.get("name"):
            continue
        # 已有 image_path 的跳过
        if asset.get("image_path"):
            continue
        
        safe_name = _safe_filename(asset["name"])
        target_filename = f"{safe_name}.png"
        if target_filename in png_files:
            asset["image_path"] = str(png_files[target_filename])
            repaired += 1
        
        # 修复造型图
        for costume in asset.get("costumes", []) or []:
            if not costume.get("name"):
                continue
            if costume.get("image_path"):
                continue
            
            costume_safe = _safe_filename(costume["name"])
            costume_filename = f"{safe_name}_{costume_safe}.png"
            if costume_filename in png_files:
                costume["image_path"] = str(png_files[costume_filename])
                repaired += 1
    
    return repaired


def save_library(name: str, characters: List[Character],
                 props: List[Prop], scenes: List[Scene],
                 merge: bool = False) -> tuple:
    """把资产写入共享库，返回 (库目录路径, 重复资产统计)。

    merge=True（upsert）：先读旧库，按名称合并——旧条目保留 image_path/desc/episodes
    等已生成字段，新条目追加；同名角色造型按名称去重。
    merge=False（默认）：同名库直接覆盖（首次建库用）。
    
    返回: (Path, {"duplicate_chars": [...], "duplicate_props": [...], "duplicate_scenes": [...]})
    """
    root = _library_root() / name
    root.mkdir(parents=True, exist_ok=True)

    duplicates = {"duplicate_chars": [], "duplicate_props": [], "duplicate_scenes": []}
    
    if merge:
        old = load_library(name) or {"characters": [], "props": [], "scenes": []}
        
        # 检测重复
        old_char_names = {_norm_character_name(c.name) for c in old.get("characters", [])}
        old_prop_names = {_norm_prop_name(p.name) for p in old.get("props", [])}
        from src.core.producer import normalize_scene_name
        old_scene_names = {normalize_scene_name(s.name) for s in old.get("scenes", [])}
        
        for c in characters:
            if _norm_character_name(c.name) in old_char_names:
                duplicates["duplicate_chars"].append(c.name)
        for p in props:
            if _norm_prop_name(p.name) in old_prop_names:
                duplicates["duplicate_props"].append(p.name)
        for s in scenes:
            if normalize_scene_name(s.name) in old_scene_names:
                duplicates["duplicate_scenes"].append(s.name)
        
        characters = _merge_characters(old.get("characters", []), characters)
        props = _merge_items(old.get("props", []), props, _norm_prop_name)
        scenes = _merge_scenes(old.get("scenes", []), scenes)

    def _write(fname: str, data: list):
        f = root / fname
        tmp = f.with_name(f".tmp_{fname}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(f)

    _write("characters.json", [c.to_dict() for c in characters])
    _write("props.json", [p.to_dict() for p in props])
    _write("scenes.json", [s.to_dict() for s in scenes])
    return root, duplicates


def _merge_characters(old: List[Character], new: List[Character]) -> List[Character]:
    """角色 upsert：旧角色保留（含已生成图），新角色追加；同名造型按名称去重合并。"""
    merged: List[Character] = []
    old_map = {_norm_character_name(c.name): c for c in old}
    for c in new:
        key = _norm_character_name(c.name)
        existing = old_map.get(key)
        if existing is not None:
            # 补 description（若旧为空而新有）
            if not existing.description and c.description:
                existing.description = c.description
            # 旧条目缺 image_path/seed 而新条目有（如新导入/新生成后回写）：补上，
            # 否则 sync_library_images 的 merge 模式会把库内已生成图的路径丢掉
            if not existing.image_path and c.image_path:
                existing.image_path = c.image_path
            if existing.seed is None and c.seed is not None:
                existing.seed = c.seed
            have = {_norm_character_name(k.name) for k in existing.costumes}
            for k in c.costumes:
                if _norm_character_name(k.name) not in have:
                    existing.costumes.append(k)
            merged.append(existing)
            old_map.pop(key)
        else:
            merged.append(c)
    merged.extend(old_map.values())  # 旧库里有、本次没提到的角色也保留
    return merged


def _sync_image_fields_to_library_json(lib_name: str, project) -> None:
    """把项目 global_* 中已有 image_path 的资产回写进库 JSON（merge 模式，不丢旧数据）。

    与 sync_library_images 的区别：不要求磁盘上存在 PNG 文件，只负责把
    内存模型对象里的 image_path/seed 合并进库 JSON，保证重启后 load_project
    能从库 JSON 直接读到正确路径。
    """
    chars = [c for c in (getattr(project, "global_characters", []) or [])
             if c.image_path or any((cost.image_path for cost in (c.costumes or [])), default=False)]
    props = [p for p in (getattr(project, "global_props", []) or []) if p.image_path]
    scenes = [s for s in (getattr(project, "global_scenes", []) or []) if s.image_path]
    if not chars and not props and not scenes:
        return
    # merge=True：保留旧库条目及其已生成字段，新条目追加，不丢数据
    save_library(lib_name, chars, props, scenes, merge=True)


def _merge_items(old: List[object], new: List[object], norm_key) -> List[object]:
    """道具/通用条目 upsert：按归一名去重，旧条目优先保留（含 image_path）。"""
    merged = list(old)
    have = {norm_key(o.name) for o in old}
    for n in new:
        k = norm_key(n.name)
        if k not in have:
            merged.append(n)
            have.add(k)
    return merged


def _merge_scenes(old: List[Scene], new: List[Scene]) -> List[Scene]:
    """场景 upsert：按名称去重；同名时合并 episodes（并集）并保留旧 image_path。"""
    from src.core.producer import normalize_scene_name

    merged: List[Scene] = []
    old_map = {normalize_scene_name(s.name): s for s in old}
    for s in new:
        key = normalize_scene_name(s.name)
        existing = old_map.get(key)
        if existing is not None:
            e = set(existing.episodes or [])
            e.update(s.episodes or [])
            existing.episodes = sorted(e)
            if not existing.description and s.description:
                existing.description = s.description
            old_map.pop(key)
            merged.append(existing)
        else:
            merged.append(s)
    merged.extend(old_map.values())
    return merged


def library_images_dir(name: str, kind: str) -> Optional[Path]:
    """返回库内图片目录：characters（角色图+造型图）/ props / scenes。

    库不存在或 kind 非法时返回 None（调用方回退到项目目录）。
    """
    if kind not in ("characters", "props", "scenes"):
        return None
    root = _library_root() / name
    if not root.exists():
        return None
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_library_assets(md_text: str) -> Dict[str, List]:
    """从整部剧本文本中确定性提取 角色/造型/道具/场景（不用 AI）。

    提取来源（兼容三种格式）：
    - 整部剧本（逆天布衣_剧本.md）：附录参考卡 + 服装变化表 + 关键道具表 + 分镜「**主场景**」行
    - 资产总表（1-10集-总结.md 一类）：
      - 角色：「### 2.x 陈炎（主角）」参考卡（支持「| 项目 | 内容 |」表格与「- 」列表两种写法）
        +「六、全30集角色出场总表」表格补角色
      - 造型：「七、角色服装变化表（精确版）」表格（parse_costume_table）
      - 道具：「## 三、关键道具表」表格
      - 场景：「## 五、分集场景总表」表格（主场景列按 normalize_scene_name 归一）
    - 场景资产库（主场景整合表 一类）：
      - 场景：「| SC01 | 主场景名 | 首次出现 | 涉及集数 | …」唯一主场景清单 +
        「| S001 | 主场景 | 第X集 | …」归属总表补充 episodes
      - 角色：「六、场景与角色对应表」主要出场角色列（去括号后缀）
      - 造型/道具：无（纯场景库文件不含服装总表/道具表）

    返回 {"characters": [Character], "props": [Prop], "scenes": [Scene]}
    """
    from src.core.producer import normalize_scene_name

    characters: List[Character] = []
    props: List[Prop] = []
    scenes: List[Scene] = []
    seen_chars: set = set()
    char_meta: Dict[str, dict] = {}

    # 过滤非角色条目（场景数据、优先级等）
    _CHAR_EXCLUDE_KEYWORDS = {"主场景", "场景归属", "优先级", "分场景", "场景清单", "场景总表"}

    def _add_char(raw_name: str, desc: str):
        norm = _norm_character_name(raw_name)
        if not norm or norm in seen_chars or len(norm) < 2:
            return
        # 过滤非角色条目
        if any(kw in raw_name for kw in _CHAR_EXCLUDE_KEYWORDS):
            return
        seen_chars.add(norm)
        char_meta[norm] = {"name": raw_name, "desc": desc}

    # ---------- 角色参考卡：「### 2.x 陈炎（主角）」或「### 3.14 银色电网（第9集）」 ----------
    # 兼容两种正文：① 「| 项目 | 内容 |」表格行；② 「- 」列表行
    # 前缀格式：2.x / 3.14 / 4.2.1 等任意数字编号
    card_pat = re.compile(
        r"^###\s*(?:\d+(?:\.\d+)+\s+)?([^#\n]+?)（[^）\n]*）\s*$\n(.*?)(?=^#{1,3}\s|\n---|\Z)",
        re.M | re.S,
    )
    for m in card_pat.finditer(md_text):
        raw_name = m.group(1).strip()
        body = m.group(2)
        bullets = [ln for ln in body.splitlines()
                   if ln.strip().startswith(("-", "·")) and not ln.strip().startswith("|")]
        table_rows = [
            ln for ln in body.splitlines()
            if ln.strip().startswith("|")
            and ln.strip() != "|"
            and not _is_md_sep_row(ln)
            and "项目" not in ln.strip()[:6]
        ]
        has_content = ("基础特征" in body) or len(bullets) >= 2 or len(table_rows) >= 2
        if not has_content:
            continue
        desc_part = re.split(r"服装变化", body)[0]
        parts: List[str] = []
        for ln in desc_part.splitlines():
            s = ln.strip()
            if not s:
                continue
            if s.startswith("|"):
                if _is_md_sep_row(s):
                    continue
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) >= 2:
                    key = cells[0]
                    val = " ".join(cells[1:])
                    if key in ("项目", ""):
                        continue
                    parts.append(f"{key} {val}" if key not in ("年龄", "发型", "头发", "外貌", "特征") else val)
            elif s.startswith(("-", "·")):
                parts.append(re.sub(r"^\s*[-·*]\s*", "", s))
            elif s.startswith("**") or s.startswith("#"):
                continue
        desc = "；".join(p for p in parts if p)[:500]
        _add_char(raw_name, desc)

    # ---------- 角色出场总表「| 角色 | 出场集数 |」补充 ----------
    cast_block = re.search(
        r"^\s*#{1,4}\s*(?:\d+[、.．]\s*)?全\d+集角色[^#\n]*出场总表\s*$\n(.*?)(?=^#{1,2}\s|\n---|\Z)",
        md_text, re.M | re.S,
    )
    if cast_block:
        for line in cast_block.group(1).splitlines():
            s = line.strip()
            if not s.startswith("|") or _is_md_sep_row(s):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 2 or cells[0] in ("角色", ""):
                continue
            _add_char(cells[0], f"出场集数 {cells[1]}" if len(cells) > 1 and cells[1] else "")

    # ---------- 角色：分镜表「**角色**」行兜底 ----------
    for m in re.finditer(r"^\*\*角色\*\*\s*[：:]\s*(.+)$", md_text, re.M):
        for name in re.split(r"[、,，]", m.group(1).strip()):
            n = name.strip().rstrip("。")
            if not n:
                continue
            _add_char(n, "")

    # ---------- 场景资产库「六、场景与角色对应表」出场角色兜底 ----------
    # 场景库文件没有角色参考卡/出场总表，角色仅出现在该表的「主要出场角色」列
    scene_cast_block = re.search(
        r"^\s*#{1,4}\s*(?:六[、.．]?\s*)?场景与角色对应表.*?\n(.*?)(?=^#{1,2}\s|\n---|\Z)",
        md_text, re.M | re.S,
    )
    if scene_cast_block:
        for line in scene_cast_block.group(1).splitlines():
            s = line.strip()
            if not s.startswith("|") or _is_md_sep_row(s):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 3:
                continue
            if not re.match(r"^(SC|S)\d+$", cells[0]) and cells[0] != "":
                continue
            for raw in re.split(r"[、,，]", cells[2]):
                name = re.sub(r"[（(].*?[）)]", "", raw.strip())
                if name:
                    _add_char(name, "")

    # 根据描述自动判断角色类型
    def _detect_char_type(desc: str) -> str:
        """从角色描述中推断 char_type。

        只认结构化标记「种类：XXX」，不全文搜关键词，
        没有明确标记一律默认「人类」。
        """
        if not desc:
            return "人类"
        m = re.search(r"种类[：:]?\s*(\S+)", desc)
        if m:
            raw = m.group(1)
            if "妖兽" in raw or "动物" in raw:
                return "动物/妖兽"
            if "能量" in raw or "灵体" in raw:
                return "能量体"
            if "群体" in raw:
                return "群体"
        return "人类"

    for norm, info in char_meta.items():
        char_type = _detect_char_type(info.get("desc", ""))
        characters.append(Character(
            name=info["name"],
            description=info["desc"],
            char_type=char_type
        ))

    # ---------- 造型：「角色服装变化表」→ 挂到对应角色 ----------
    costume_block = re.search(
        r"^\s*#{1,4}\s*(?:\d+[、.．]?\s*)?角色服装变化表.*?(?=^#{1,2}\s|\n---|\Z)",
        md_text, re.M | re.S,
    )
    if costume_block:
        for e in parse_costume_table(costume_block.group(0)):
            cnorm = _norm_character_name(e["character"])
            target = next(
                (c for c in characters if _norm_character_name(c.name) == cnorm),
                None,
            )
            if target is None:
                target = Character(name=e["character"])
                characters.append(target)
            target.costumes.append(
                Costume(name=e.get("name", ""), description=e.get("description", ""))
            )

    # ---------- 道具：含「道具」列且表头含 视觉特征/功能/持有者 的表格 ----------
    seen_props: set = set()
    tbl_starts = [
        m.start() for m in re.finditer(
            r"\|\s*道具\s*\|[^|\n]*\|[^|\n]*(?:视觉特征|功能|持有者)[^|\n]*\|",
            md_text, re.M,
        )
    ]
    for ts in tbl_starts:
        # ts 指向表头行首「|」，向下扫描连续表格行直到空行或非表格行
        lines = []
        pos = ts
        while pos < len(md_text):
            nl = md_text.find("\n", pos)
            line = md_text[pos:nl if nl != -1 else len(md_text)]
            stripped = line.lstrip()
            if not stripped.startswith("|"):
                break
            lines.append(line)
            if nl == -1:
                break
            pos = nl + 1
        for line in lines:
            if not line.lstrip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) < 2:
                continue
            name = cells[0]
            if name in ("道具", "") or name in seen_props or set(name) <= set("|-: "):
                continue
            seen_props.add(name)
            desc = "；".join(c for c in cells[1:] if c)[:300]
            props.append(Prop(name=name, description=desc))

    # ---------- 场景：只提取主场景（55个），不提取分场景 ----------
    # 根据文档说明：资源库只需建立55个主场景参考图，分场景为同一主场景内的不同机位/区域变化
    ep_starts = [
        (m.start(), _cn_to_int(m.group(1)))
        for m in re.finditer(r"^\s*#{1,4}\s*(?:[一二三四五六七八九十百千零〇两\d]+[、.．]\s*)?第([0-9一二三四五六七八九十百千零〇两]+)集[：:]", md_text, re.M)
    ]
    # Ecode 节标题（### E0X-S0Y）直接自报集号，比「第X集」标题更可靠
    ecode_starts = [
        (m.start(), int(m.group(1)))
        for m in re.finditer(r"^\s*#{1,4}\s*E(\d+)-S\d+", md_text, re.M)
    ]
    seen_scenes: set = set()
    
    # ① 每节「**主场景**」行 → 只提取主场景名
    for m in re.finditer(r"^\*\*主场景\*\*\s*[：:]\s*(.+)$", md_text, re.M):
        ep = 0
        for pos, num in ep_starts + ecode_starts:
            if pos < m.start():
                ep = num
        name = normalize_scene_name(m.group(1).strip().split("→")[0])
        if not name or name in seen_scenes:
            continue
        seen_scenes.add(name)
        episodes_field = [f"{ep}"] if ep else []
        scenes.append(Scene(name=name, description="", episodes=episodes_field))

    # ② 资产总表「## 五、分集场景总表」：只提取「主场景」列，忽略「分场景」列
    scene_map_block = re.search(
        r"^\s*#{1,4}\s*(?:五[、.．]\s*)?分集场景总表.*?\n(.*?)(?=^#{1,2}\s|\n---|\Z)",
        md_text, re.M | re.S,
    )
    if scene_map_block:
        cur_ep = ""
        ep_rows: Dict[str, List[str]] = {}  # ep_code -> [主场景...]
        for line in scene_map_block.group(1).splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                m_num = re.search(r"第([0-9一二三四五六七八九十百千零〇两]+)", s)
                cur_ep = m_num.group(1) if m_num else ""
                continue
            if s.startswith("|") and not _is_md_sep_row(s):
                cells = [c.strip() for c in s.strip("|").split("|")]
                # 列布局：「| 节数 | 节名 | 主场景 | 分场景 | 角色 |」→ 只取主场景=cells[2]
                if len(cells) < 3 or cells[0] in ("集数", "E码", "节数", ""):
                    continue
                ecode = cells[0]  # E01-S01
                if not re.match(r"^E\d+-S\d+$", ecode):
                    continue
                main_scene = cells[2] if len(cells) > 2 else ""
                if not main_scene:
                    continue
                ep_code_num = ecode.split("-")[0]
                ep_rows.setdefault(ep_code_num, []).append(main_scene)
        for ep_code_num, scene_cells in sorted(ep_rows.items()):
            ep_num = ep_code_num.lstrip("E")
            ep_int = int(ep_num) if ep_num.isdigit() else 0
            for cell in scene_cells:
                for raw in re.split(r"[、,，/]", cell):
                    name = normalize_scene_name(raw.strip())
                    if not name or name in seen_scenes:
                        continue
                    seen_scenes.add(name)
                    scenes.append(Scene(
                        name=name, description="",
                        episodes=[f"{ep_int}"] if ep_int else [],
                    ))

    # ---------- 场景：「场景资产库 - 主场景整合表」类文件 ----------
    # 唯一主场景清单行：「| SC01 | 主场景名 | 首次出现 | 涉及集数 | …」
    # 兼容 5 列（| SC01 | …）与 6 列（| | SC01 | …，首列空）两种布局；
    # S 行同理：「| S001 | 主场景 | 第1集 | N | 分场景列表 |」或 6 列变体。
    sc_cells: List[List[str]] = []
    s_cells: List[List[str]] = []
    for _line in md_text.splitlines():
        _s = _line.strip()
        _cells = [c.strip() for c in _s.strip("|").split("|")] if _s.startswith("|") else []
        if not _cells:
            continue
        # 6 列变体：首列空，实际数据从第二列开始
        if _cells and _cells[0] == "":
            _cells = _cells[1:]
        if re.match(r"^SC\d+$", _cells[0]):
            # [编号, 主场景名, 首次出现, 涉及集数, …]
            sc_cells.append(_cells)
        elif re.match(r"^S\d{3}$", _cells[0]):
            # [编号, 主场景, 所属集数, 分场景数量, 分场景列表]
            s_cells.append(_cells)
    if sc_cells or s_cells:
        def _eps_from_text(text: str) -> list:
            """「第1-2集」/「第1/3/4集」→ ["1", "2"]（去重保持顺序）。"""
            out: List[str] = []
            for m2 in re.finditer(r"第([0-9一二三四五六七八九十百千零〇两\-/、，,\s]+)集", text):
                raws = re.split(r"[/、,，]", m2.group(1).strip())
                for raw in raws:
                    raw = raw.strip()
                    if not raw:
                        continue
                    m3 = re.match(r"^(\d+)\s*-\s*(\d+)$", raw)
                    if m3:
                        for v in range(int(m3.group(1)), int(m3.group(2)) + 1):
                            out.append(str(v))
                    else:
                        val = _cn_to_int(raw) if not raw.isdigit() else int(raw)
                        out.append(str(val))
            seen_ep: set = set()
            return [e for e in out if not (e in seen_ep or seen_ep.add(e))]

        def _add(name_raw: str, eps: list, desc: str):
            name = normalize_scene_name(name_raw.strip().split("→")[0])
            if not name:
                return
            # 如果已存在（从**主场景**或分集场景总表先添加的），且旧描述为空，则更新描述
            if name in seen_scenes:
                if desc:
                    for s in scenes:
                        if s.name == name and not s.description:
                            s.description = desc[:300]
                            break
                return
            seen_scenes.add(name)
            episodes_field = []
            for e in eps:
                if e not in episodes_field:
                    episodes_field.append(e)
            scenes.append(Scene(name=name, description=desc[:300], episodes=episodes_field))

        # SC 行列布局：
        #   「| SC01 | 主场景名 | 首次出现 | 涉及集数 | …」
        #   新版 8 列：「| SC01 | 主场景名 | 首次出现 | 涉及集数 | 视觉描述 | 关键元素 | 光线/色调 | 参考图生成提示 |」
        #   cells[2]=首次出现, cells[3]=涉及集数, cells[4]=视觉描述, cells[5]=关键元素, cells[6]=光线/色调
        for cells in sc_cells:
            if len(cells) < 4:
                continue
            name = cells[1]
            eps = _eps_from_text(cells[3]) or _eps_from_text(cells[2])
            # 提取场景描述：视觉描述 + 关键元素 + 光线/色调（如有）
            desc_parts = []
            if len(cells) >= 5 and cells[4]:
                desc_parts.append(cells[4])
            if len(cells) >= 6 and cells[5]:
                desc_parts.append(f"关键元素：{cells[5]}")
            if len(cells) >= 7 and cells[6]:
                desc_parts.append(f"光线/色调：{cells[6]}")
            desc = "，".join(desc_parts) if desc_parts else ""
            _add(name, eps, desc)
        # S 行补充 episodes（「| S001 | 主场景 | 第1集 | N | 分场景列表 |」）
        s_ep_map: Dict[str, list] = {}
        for cells in s_cells:
            if len(cells) < 3:
                continue
            s_ep_map.setdefault(cells[1].strip().split("→")[0], []).extend(
                _eps_from_text(cells[2])
            )
        for sc in scenes:
            if not sc.episodes:
                key = normalize_scene_name(sc.name)
                for sname, eps in s_ep_map.items():
                    if normalize_scene_name(sname) == key:
                        sc.episodes = eps
                        break

    return {"characters": characters, "creatures": [], "props": props, "scenes": scenes}


def import_to_project(library_name: str, project) -> dict:
    """从资产库读取资产数据，返回资产统计。

    注意：资产只保存在 asset_library/ 下，不写入项目 global_* 列表。
    项目通过 asset_library_name 绑定资产库，使用时直接从库读取。
    返回统计：{"characters": n, "costumes": n, "props": n, "scenes": n}
    """
    data = load_library(library_name)
    if data is None:
        raise FileNotFoundError(f"共享资产库「{library_name}」不存在")

    # 记录项目与共享库的绑定关系
    try:
        project.asset_library_name = library_name
    except Exception:
        pass

    return {
        "characters": len(data.get("characters", [])),
        "costumes": sum(len(c.costumes) for c in data.get("characters", [])),
        "props": len(data.get("props", [])),
        "scenes": len(data.get("scenes", [])),
    }


def sync_library_images(project, log=None) -> dict:
    """把项目已生成的角色/造型/道具/场景图同步进共享库（复制图片 + 回写库 JSON）。

    适用场景：
    - 项目绑定了库（asset_library_name），但旧图片仍保存在项目目录
      （历史数据 / 生成时库尚未绑定），需要把现有图片搬进库目录，
      并更新库 JSON 的 image_path 指向库内，保证其他项目导入可复用。
    - 项目新生成的图片已经写入库目录，此函数只需回写库 JSON。
    - 磁盘上已有 PNG 文件但 JSON 中 image_path 为空：自动扫描并修复。

    规则：
    - 按资产名（角色/造型/道具/场景）匹配库内资产，缺失则跳过；
    - 图片目标位置为库内 characters/ props/ scenes/ 子目录（与生成逻辑同一套
      文件名规则 _safe_filename，见 producer.generate_*）；
    - 目标已存在则跳过复制，只更新 JSON；
    - 只更新指向共享目录的资产（项目里 image_path 为空或指向库目录，
      或指向项目目录的旧图——复制后指向库目录）。
    - 扫描磁盘 PNG 文件：若角色/道具/场景的 image_path 为空但磁盘有对应文件，
      自动补全 image_path 指向库内文件。

    返回 {"characters": n, "costumes": n, "props": n, "scenes": n}。
    """
    lib_name = getattr(project, "asset_library_name", "") or ""
    if not lib_name:
        return {"characters": 0, "costumes": 0, "props": 0, "scenes": 0}
    root = _library_root() / lib_name
    if not root.exists():
        return {"characters": 0, "costumes": 0, "props": 0, "scenes": 0}

    stats = {"characters": 0, "costumes": 0, "props": 0, "scenes": 0}

    def _ensure_img_dir(kind: str) -> Path:
        d = root / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _copy_and_fix(src: Optional[str], dst_dir: Path, fname: str,
                      target, stats_key: str) -> bool:
        """复制 src 图片到 dst_dir/fname（已存在则跳过），更新 target.image_path。"""
        dst = dst_dir / fname
        if src and Path(src).exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                if log:
                    log(f"⚠️ 同步图片到库失败（{dst.name}）: {e}")
                return False
        if dst.exists():
            target.image_path = str(dst)
            target.seed = getattr(target, "seed", None)
            stats[stats_key] += 1
            return True
        return False

    chars_dir = _ensure_img_dir("characters")
    for c in getattr(project, "global_characters", []) or []:
        if not c.name:
            continue
        if c.image_path:
            _copy_and_fix(c.image_path, chars_dir,
                          f"{_safe_filename(c.name)}.png", c, "characters")
        for cost in getattr(c, "costumes", []) or []:
            if not cost.name:
                continue
            if getattr(cost, "image_path", None):
                _copy_and_fix(cost.image_path, chars_dir,
                              f"{_safe_filename(c.name)}_{_safe_filename(cost.name)}.png",
                              cost, "costumes")

    props_dir = _ensure_img_dir("props")
    for p in getattr(project, "global_props", []) or []:
        if p.name and getattr(p, "image_path", None):
            _copy_and_fix(p.image_path, props_dir,
                          f"{_safe_filename(p.name)}.png", p, "props")

    scenes_dir = _ensure_img_dir("scenes")
    for s in getattr(project, "global_scenes", []) or []:
        if s.name and getattr(s, "image_path", None):
            _copy_and_fix(s.image_path, scenes_dir,
                          f"{_safe_filename(s.name)}.png", s, "scenes")

    # 扫描磁盘 PNG 文件，修复 JSON 中 image_path 为空的条目
    def _scan_and_fix_from_disk(kind: str, assets, stats_key: str,
                                is_costume: bool = False):
        """扫描 kind 目录下的 PNG 文件，补全 assets 中 image_path 为空的条目。"""
        img_dir = root / kind
        if not img_dir.exists():
            return
        png_files = {f.name: f for f in img_dir.glob("*.png")}
        if not png_files:
            return
        
        for asset in assets:
            if not getattr(asset, "name", ""):
                continue
            # 已有 image_path 的跳过
            if getattr(asset, "image_path", None):
                continue
            
            safe_name = _safe_filename(asset.name)
            if is_costume:
                # 造型图需要父角色名
                continue  # 造型图在上面的循环中已处理
            
            # 查找匹配的 PNG 文件
            target_filename = f"{safe_name}.png"
            if target_filename in png_files:
                asset.image_path = str(png_files[target_filename])
                stats[stats_key] += 1
                if log:
                    log(f"🔍 从磁盘扫描修复 [{asset.name}] 的 image_path")

    # 扫描角色底模图
    _scan_and_fix_from_disk("characters", 
                           getattr(project, "global_characters", []) or [],
                           "characters")
    
    # 扫描道具图
    _scan_and_fix_from_disk("props",
                           getattr(project, "global_props", []) or [],
                           "props")
    
    # 扫描场景图
    _scan_and_fix_from_disk("scenes",
                           getattr(project, "global_scenes", []) or [],
                           "scenes")

    if any(stats.values()):
        # 回写库 JSON（用同步后的模型对象；merge=True 保留库内已有条目及其
        # 已生成字段，避免项目角色数少于库角色数时把其他角色的 image_path 覆盖丢失）
        lib_chars = getattr(project, "global_characters", []) or []
        lib_props = getattr(project, "global_props", []) or []
        lib_scenes = getattr(project, "global_scenes", []) or []
        save_library(lib_name, lib_chars, lib_props, lib_scenes, merge=True)
        if log:
            log(
                f"📦 已同步资产图到共享库「{lib_name}」："
                f"角色 {stats['characters']}，造型 {stats['costumes']}，"
                f"道具 {stats['props']}，场景 {stats['scenes']}"
            )
    return stats