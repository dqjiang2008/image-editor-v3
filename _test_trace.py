"""Test: trace the parsing + stats flow end-to-end."""
import sys
sys.path.insert(0, "g:\\harness_project\\image-editor-v2")

from pathlib import Path
from src.services.library_manager import extract_library_assets, save_library, load_library

# Read a sample file
test_file = r"g:\harness_project\image-editor-v2\AI_txt\# 《逆天布衣》AI剧本格式（专业版） - 1-10集-总结.md"
content = Path(test_file).read_text(encoding="utf-8-sig")

extracted = extract_library_assets(content)
print(f"Characters: {len(extracted.get('characters', []))}")
for c in extracted.get("characters", []):
    print(f"  - {c.name} ({c.char_type})")
print(f"Props: {len(extracted.get('props', []))}")
for p in extracted.get("props", []):
    print(f"  - {p.name}")
print(f"Scenes: {len(extracted.get('scenes', []))}")
for s in extracted.get("scenes", []):
    print(f"  - {s.name}")