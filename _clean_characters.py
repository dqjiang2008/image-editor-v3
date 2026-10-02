import json

PROJECT_PATH = r"g:\harness_project\image-editor-v2\data\projects\逆天布衣\project.json"

# 真实角色名单（来自源文件第二节 2.1-2.27 + 第三节 3.1-3.16）
REAL_CHARACTERS = {
    # 人类角色（27个）
    "陈炎", "陈老栓", "老叫花子", "林婉儿", "赵无极", "赵无咎",
    "林震天", "血衣侯", "雷震天", "陈天霸", "陈天行", "母亲",
    "陈瑶", "炎伯", "始祖", "药老", "火尊", "冰尊", "土尊",
    "监察使", "周小山", "赵管事", "王屠户", "李寡妇", "村长赵伯",
    "周管事", "黑衣人首领",
    # 非人物类型（16个）
    "狼群", "黑熊", "白狼", "兽潮",
    "血衣楼黑衣人", "赵家弟子", "雷家弟子", "炎家弟子", "炎家长老",
    "围观人群", "噬灵族始祖", "灵压具象化", "九指剑阵", "银色电网",
    "九指石像", "始祖碎片",
}

with open(PROJECT_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

chars = data.get("global_characters", [])
before = len(chars)

# 筛选：只保留真实角色
kept = [c for c in chars if c.get("name") in REAL_CHARACTERS]
removed = [c.get("name") for c in chars if c.get("name") not in REAL_CHARACTERS]

data["global_characters"] = kept

with open(PROJECT_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

# 统计
total_costumes = sum(len(c.get("costumes") or []) for c in kept)
print(f"移除前: {before} 个角色")
print(f"移除后: {len(kept)} 个角色（真实角色）")
print(f"移除 {before - len(kept)} 个: {', '.join(removed)}")
print(f"实际造型数: {total_costumes} 个")