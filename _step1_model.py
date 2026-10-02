"""Step 1: Modify project.py - add creature_count to Character, remove Creature"""
filepath = r'g:\harness_project\image-editor-v2\src\models\project.py'

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")

# 1. Add creature_count field after char_type line
for i, line in enumerate(lines):
    if 'char_type: str = "人类"  # 角色类型' in line:
        indent = '    '
        lines.insert(i+1, f'{indent}creature_count: str = ""  # 数量（非人类角色使用，如：7匹、一头、数百头）\n')
        print(f"Added creature_count field after line {i+1}")
        break

# 2. Add creature_count to to_dict
for i, line in enumerate(lines):
    if '"char_type": getattr(self, "char_type", "人类"),' in line:
        indent = '            '
        lines.insert(i+1, f'{indent}"creature_count": getattr(self, "creature_count", ""),\n')
        print(f"Added creature_count to to_dict after line {i+1}")
        break

# 3. Add creature_count to from_dict
for i, line in enumerate(lines):
    if 'char_type=char_type,' in line and 'costumes' in lines[i-2] if i >= 2 else False:
        indent = '            '
        lines.insert(i+1, f'{indent}creature_count=data.get("creature_count", ""),\n')
        print(f"Added creature_count to from_dict after line {i+1}")
        break

# Alternative: find the return cls block
for i, line in enumerate(lines):
    if 'char_type=char_type,' in line and i > 0 and 'episodes=' in lines[i-1]:
        indent = '            '
        lines.insert(i+1, f'{indent}creature_count=data.get("creature_count", ""),\n')
        print(f"Added creature_count to from_dict (alt) after line {i+1}")
        break

# 4. Remove Creature class - find the @dataclass before Creature
creature_start = None
creature_end = None
for i, line in enumerate(lines):
    if line.strip() == '@dataclass' and i+1 < len(lines) and 'class Creature:' in lines[i+1]:
        creature_start = i
    if creature_start is not None and line.strip() == '' and i > creature_start + 5:
        # Check if the next non-empty line starts a new class or import
        next_lines = ''.join(lines[i:min(i+5, len(lines))])
        if 'class ' in next_lines or 'def ' in next_lines:
            creature_end = i
            break

# If we didn't find end via class boundary, find by the closing of from_dict
if creature_start is None:
    for i, line in enumerate(lines):
        if 'class Creature:' in line:
            creature_start = i
            # Find the end - a blank line followed by @dataclass or class
            for j in range(i+1, len(lines)):
                if lines[j].strip() == '' and j+1 < len(lines) and ('@dataclass' in lines[j+1] or 'class ' in lines[j+1]):
                    creature_end = j
                    break
            if creature_end is None:
                # Just find the next class
                for j in range(i+1, len(lines)):
                    if 'class ' in lines[j] and 'Creature' not in lines[j]:
                        creature_end = j-1
                        while creature_end > i and lines[creature_end].strip() == '':
                            creature_end -= 1
                        creature_end += 1
                        break
            break

if creature_start is not None and creature_end is not None:
    removed = creature_end - creature_start
    del lines[creature_start:creature_end]
    print(f"Removed Creature class: lines {creature_start+1} to {creature_end} ({removed} lines)")

# 5. Remove creatures field from Project
for i, line in enumerate(lines):
    if 'creatures: List[Creature]' in line:
        del lines[i]
        print(f"Removed creatures field at line {i+1}")
        break

# 6. Remove Creature from import if imported here
for i, line in enumerate(lines):
    if 'from ' in line and 'import' in line and 'Creature' in line:
        lines[i] = line.replace(', Creature', '').replace('Creature, ', '')
        print(f"Cleaned Creature import at line {i+1}")

# Remove unused List import if it's the only usage
# Actually List is used elsewhere, so leave it

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("\nFile saved. Running syntax check...")

import py_compile
try:
    py_compile.compile(filepath, doraise=True)
    print("Syntax check: PASSED")
except py_compile.PyCompileError as e:
    print(f"Syntax error: {e}")