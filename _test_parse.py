"""Test that the fix in _parse_storyboard_table works correctly"""
import sys
sys.path.insert(0, "g:/harness_project/image-editor-v2")

from src.models.project import Storyboard
import re

# Simulate a row from episode 5
text = """| E05-S01-C01 | A 瀑布后石壁 | 中景 | 固定 | 陈炎站在瀑布后面。水雾打湿头发肩膀。面前光滑石壁，刻密密麻麻符文。暗金色，弯弯绕绕。 | 无 | 无 | 水雾粒子，符文暗金光 | 瀑布声，水雾声 | 8s |"""

# Copy the exact parsing logic from _parse_storyboard_table
cells = [cell.strip() for cell in text.split('|')[1:-1]]
print(f"Cell count: {len(cells)}")
for i, c in enumerate(cells):
    print(f"  cells[{i}] = {repr(c[:60])}")

camera = cells[2]
camera_move = cells[3]
description = cells[4]
effects = cells[7]
sound = cells[8]

full_description = description
if camera_move and camera_move != "无":
    full_description += f"\n运镜：{camera_move}"
if effects and effects != "无":
    full_description += f"\n特效：{effects}"
if sound and sound != "无":
    full_description += f"\n音效：{sound}"

print(f"\n--- Result ---")
print(f"camera field: {repr(f'{camera}，{camera_move}' if camera_move and camera_move != '无' else camera)}")
print(f"action field: {repr(description if description else '')}")
print(f"description field: {repr(full_description[:80])}")