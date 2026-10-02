# -*- coding: utf-8 -*-
import json

data = json.load(open(r'g:\harness_project\image-editor-v2\data\projects\逆天布衣\episodes.json', 'r', encoding='utf-8'))
print(f'Type: {type(data)}')
print(f'Keys: {list(data.keys())}')
for k, v in data.items():
    print(f'\nKey: {k}')
    if isinstance(v, list):
        print(f'  List length: {len(v)}')
        for i, item in enumerate(v):
            if isinstance(item, dict):
                print(f'  [{i}]: {item.get("title", "no title")}')
            elif isinstance(item, str):
                print(f'  [{i}]: {item[:60]}...')
            else:
                print(f'  [{i}]: {type(item)}')
            if i >= 40:
                print(f'  ... ({len(v)} total)')
                break
    elif isinstance(v, dict):
        print(f'  Dict keys: {list(v.keys())[:10]}')
    else:
        print(f'  {v}')

print('\n=== EP0007 ===')
ep7 = json.load(open(r'g:\harness_project\image-editor-v2\data\projects\逆天布衣\episodes\EP0007_1.7_入口.json', 'r', encoding='utf-8'))
print(f'Title: {ep7.get("title")}')
print(f'First storyboard scene: {ep7["storyboard"][0]["scene"]}')
print(f'First storyboard desc: {ep7["storyboard"][0]["description"][:80]}...')
print(f'Storyboard count: {len(ep7.get("storyboard", []))}')