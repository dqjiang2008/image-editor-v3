"""临时脚本：把 load_project 里的内联图片路径回填块替换为 _restore_asset_image_paths 调用。"""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "src" / "models" / "project.py"
text = p.read_text(encoding="utf-8")

start_marker = "        # 恢复角色图片路径：从磁盘扫描已存在的角色图/造型图文件\n"
end_marker = '        sb_data = self._read_json(d / "storyboard.json")\n'

si = text.find(start_marker)
ei = text.find(end_marker)
assert si != -1 and ei != -1, f"markers not found: si={si} ei={ei}"
assert si < ei

new_block = (
    "        # 恢复角色/造型/道具/场景图片路径：JSON 里 image_path 为 null 但磁盘上\n"
    "        # 已存在对应文件时自动回填（生成后漏存 / 库 JSON 未回写等场景的自愈）\n"
    '        self._restore_asset_image_paths(project, d, meta.get("asset_library_name", "") or "")\n\n'
)

text = text[:si] + new_block + text[ei:]
p.write_text(text, encoding="utf-8")
print(f"done: replaced {ei - si} chars at offset {si}")
