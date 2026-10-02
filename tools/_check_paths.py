# -*- coding: utf-8 -*-
"""临时诊断脚本：检查路径数据是否一致，验证 GBK/UTF-8 混淆假设。"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LIB_DIR = BASE / "data" / "asset_library" / "# 《逆天布衣》AI剧本格式（专业版） - 1-10集-总结"

# 1. config.json 的 last_project_name
cfg_raw = (BASE / "data" / "config.json").read_text(encoding="utf-8")
cfg = json.loads(cfg_raw)
name = cfg["last_project_name"]
print("config last_project_name repr:", repr(name))
try:
    restored = name.encode("gbk", errors="ignore").decode("utf-8")
    print("  GBK->UTF8 roundtrip:", repr(restored))
except Exception as e:
    print("  roundtrip failed:", e)

# 2. project.json 的关键字段
proj = json.loads((BASE / "data" / "projects" / "逆天布衣" / "project.json").read_text(encoding="utf-8"))
print("project name:", repr(proj.get("name")))
print("asset_library_name:", repr(proj.get("asset_library_name")))
print("lib dir exists:", LIB_DIR.exists())

# 3. 库 JSON 里的 image_path 全空
lib_chars = json.loads((LIB_DIR / "characters.json").read_text(encoding="utf-8"))
print("lib characters.json count:", len(lib_chars),
      "| with image_path:", sum(1 for c in lib_chars if c.get("image_path")))
first = lib_chars[0]["name"]
print("first char name repr:", repr(first))
try:
    print("  fix:", repr(first.encode("gbk").decode("utf-8")))
except Exception as e:
    print("  gbk roundtrip failed:", e)

# 4. 磁盘图片实际存在
pngs = list((LIB_DIR / "characters").glob("*.png"))
print("lib characters/*.png count:", len(pngs))
