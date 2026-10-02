import json

files = [
    ("EP0001_1.1_铁匠铺的黄昏.json", "EP0001 1.1"),
    ("EP0023_4.1_第一关·幻境.json", "EP0023 4.1"),
    ("EP0029_5.1_传送阵.json", "EP0029 5.1"),
    ("EP0015_2.8_狼群.json", "EP0015 2.8"),
]

for fname, label in files:
    path = f"g:/harness_project/image-editor-v2/data/projects/逆天布衣/episodes/{fname}"
    d = json.load(open(path, "r", encoding="utf-8"))
    sb = d["storyboard"][0]
    action = sb.get("action", "") or "(empty)"
    camera = sb.get("camera", "") or "(empty)"
    print(f"{label}: action={action[:50]}  camera={camera}")