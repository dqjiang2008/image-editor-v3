"""
Agnes AI 客户端
"""
import asyncio
import aiohttp
import json
import re
import sys
from pathlib import Path
from typing import Optional, List, Callable
from src.config.settings import Settings

__all__ = [
    "AgnesAIClient",
    "json_loads_lenient",
    "NETWORK_ERRORS",
    "RETRYABLE_ERRORS",
    "is_real_cancellation",
]

# 网络/连接类异常集合。
# 注意：aiohttp 在 DNS 解析时用 asyncio.shield 包裹解析任务，解析被中断会泄漏
# asyncio.CancelledError（BaseException 子类，不属于 aiohttp.ClientError）。
# 旧代码只捕获 aiohttp.ClientError，这类异常会穿透重试循环并穿透上层
# `except Exception`，直接崩掉整个生成任务（真实日志：
#   connector._resolve_host → return await asyncio.shield(resolved_host_task)）。
NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, asyncio.CancelledError)

# 可重试异常集合：普通业务异常 + 网络类（CancelledError 不是 Exception 子类，必须单列）
RETRYABLE_ERRORS = (Exception,) + NETWORK_ERRORS


def is_real_cancellation() -> bool:
    """区分「上层真正取消任务」与「网络层 shield 泄漏的 CancelledError」。

    任务被 cancel() 时 current_task().cancelling() > 0；而网络层泄漏的
    CancelledError 不含任何未决取消请求（== 0）。只有前者需要尊重取消
    立即抛出，后者应按网络错误重试。
    """
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return False
    if task is None:
        return False
    cancelling = getattr(task, "cancelling", None)
    if cancelling is None:  # Python < 3.11 无此 API
        return False
    try:
        return cancelling() > 0
    except Exception:
        return False


def raise_if_real_cancel(error: BaseException) -> None:
    """重试前调用：真取消立即抛出；shield 泄漏的 CancelledError 按网络错误处理。"""
    if isinstance(error, asyncio.CancelledError) and is_real_cancellation():
        raise error


def json_loads_lenient(text: str, _depth: int = 0):
    """宽容版 json.loads：容忍 LLM 输出中常见的非法转义/结构问题。

    典型故障：description 字段中文文本里混进裸 " 或未转义控制字符，
    标准 json.loads 报 "Expecting ',' delimiter"。
    修复策略（渐进）：直接解析 → 补全裸反斜杠 → 修复值内裸引号 →
    截断补全括号 → 逐对象提取。任何一步成功且解析出 list 即返回，
    全部失败返回 None。

    参数 _depth: 递归深度计数器，防止无限递归。
    """
    if not text:
        return None
    if _depth > 3:
        return None

    def _try(s: str):
        try:
            v = json.loads(s, strict=False)
            return v if isinstance(v, list) else None
        except (json.JSONDecodeError, ValueError):
            return None

    v = _try(text)
    if v is not None:
        return v

    # 修复裸反斜杠：\ 后跟非法转义字符时补全为 \\
    fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)
    v = _try(fixed)
    if v is not None:
        return v

    v = _try(_fix_inner_quotes(fixed))
    if v is not None:
        return v

    completed = _complete_truncated(_fix_inner_quotes(fixed))
    v = _try(completed)
    if v is not None:
        return v

    objs = _extract_objects(text)
    items = []
    for obj in objs:
        v = json_loads_lenient("[" + obj + "]", _depth=_depth + 1)
        if v:
            items.extend(v)
    if items:
        seen = set()
        uniq = []
        for it in items:
            key = (it.get("name", ""), it.get("description", ""))
            if key not in seen:
                seen.add(key)
                uniq.append(it)
        return uniq
    return None

def _complete_truncated(s: str) -> str:
    """输出被 max_tokens 截断时：截到最后一个完整对象，补全缺失的引号/括号。"""

    def _scan(trunc: str):
        in_str = False
        esc = False
        stack = []
        for c in trunc:
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                stack.append(c)
            elif c in "}]":
                if stack and ((c == "}" and stack[-1] == "{") or (c == "]" and stack[-1] == "[")):
                    stack.pop()
        return in_str, stack

    closers = {"{": "}", "[": "]"}
    in_str, _ = _scan(s)
    if not in_str:
        # 所有字符串已闭合，直接补剩余括号
        _, stack = _scan(s)
        return s + "".join(closers[o] for o in reversed(stack))
    # 在字符串内部被截断：回退到最近的闭合引号位置
    last_q = -1
    in_str2 = False
    esc2 = False
    for idx, c in enumerate(s):
        if in_str2:
            if esc2:
                esc2 = False
            elif c == "\\":
                esc2 = True
            elif c == '"':
                in_str2 = False
                last_q = idx
            continue
        if c == '"':
            in_str2 = True
    if last_q <= 0:
        return s
    head = s[:last_q + 1]
    while head.endswith(","):
        head = head[:-1].rstrip()
    _, stack = _scan(head)
    return head + "".join(closers[o] for o in reversed(stack))


def _extract_objects(text: str, max_scan: int = 100000) -> List[str]:
    """兜底：从文本中逐个提取顶层 {...} 对象（容忍内部裸引号/截断）。"""
    objs = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        start = i
        in_str = False
        esc = False
        depth = 0
        end = -1
        for idx in range(start, min(n, start + max_scan)):
            c = text[idx]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        if end >= 0:
            objs.append(text[start:end + 1])
            i = end + 1
        else:
            # 对象未闭合（截断）：截取到下一个对象的 { 之前
            tail = text[start:]
            nxt = tail.find('{"name"', 1)
            if nxt > 0:
                tail = tail[:nxt]
            last_q = tail.rfind('"')
            if last_q > 0:
                tail = tail[:last_q + 1]
            tail = tail.rstrip().rstrip(",")
            if not tail.endswith('"'):
                tail += '"'
            objs.append(tail + "}")
            break
    return objs



def _fix_inner_quotes(src: str) -> str:
    """修复 JSON 字符串值内部的裸双引号：交替替换为「」。

    只处理「值开引号」之后的引号（引号后紧跟 , } ] : 或结尾才视为值
    真正结束，中间的 " 一律视为裸引号）。
    """
    out = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch != '"':
            out.append(ch)
            i += 1
            continue
        j = i - 1
        prev = ""
        while j >= 0 and src[j] in " \t\r\n":
            j -= 1
        if j >= 0:
            prev = src[j]
        if not (prev in ':,([{ ' or i == 0):
            out.append(ch)
            i += 1
            continue
        k = i + 1
        found = -1
        while k < n:
            ck = src[k]
            if ck == "\\" and k + 1 < n:
                k += 2
                continue
            if ck == '"':
                m = k + 1
                while m < n and src[m] in " \t\r\n":
                    m += 1
                if m >= n or src[m] in ',}]:':
                    found = k
                    break
            k += 1
        if found < 0:
            out.append(src[i:])
            break
        inner = src[i + 1:found]
        rep = []
        alt = 0
        for c in inner:
            if c == '"':
                rep.append('「' if alt % 2 == 0 else '」')
                alt += 1
            else:
                rep.append(c)
        out.append('"' + "".join(rep) + '"')
        i = found + 1
    return "".join(out)


class AgnesAIClient:
    """Agnes AI API 客户端"""
    
    def __init__(self, api_key: str = "", base_url: str = "", log_callback=None, debug_mode: bool = False, debug_dir: str = None):
        self.api_key = api_key or Settings.API_KEY
        self.base_url = base_url or Settings.API_BASE_URL
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_loop_id: Optional[int] = None
        self.log_callback = log_callback or (lambda msg: None)
        self.debug_mode = debug_mode  # 调试模式：不实际发送请求，只保存JSON
        self.debug_dir = debug_dir or "data/debug"  # 调试文件保存目录
    
    def set_debug_mode(self, mode: bool):
        """设置调试模式（用于生成任务开始时显式设置）"""
        self.debug_mode = mode
        print(f"[DEBUG] 调试模式已设置为: {mode}", file=sys.stderr, flush=True)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        current_loop_id = id(loop)
        
        if self._session is None or self._session.closed or self._session_loop_id != current_loop_id:
            if self._session and not self._session.closed:
                try:
                    await self._session.close()
                except Exception:
                    pass
            timeout = aiohttp.ClientTimeout(total=720, connect=60, sock_read=600)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._session_loop_id = current_loop_id
        return self._session
    
    async def close(self):
        """关闭会话，避免资源泄漏"""
        if self._session and not self._session.closed:
            await self._session.close()
            print(f"[API] 客户端连接已关闭", file=sys.stderr)
        self._session = None
        self._session_loop_id = None
    
    async def generate_story(
        self,
        topic: str,
        age_group: str = "短视频",
        character_name: str = "主角",
        length: str = "medium",
        character_descriptions: str = "",
        scene_context: str = "",
        style_guidance: str = "",
    ) -> str:
        """生成故事"""
        length_map = {"very_short": "3分钟", "short": "5分钟", "medium": "10分钟", "long": "20分钟"}
        age_style_map = {
            "短视频": "节奏明快，内容精炼，适合快速传播",
            "宣传片": "主题突出，视觉冲击力强，具有品牌或活动宣传效果",
            "微电影": "情节完整，情感丰富，叙事流畅有深度",
            "纪录片": "真实客观，信息丰富，具有知识性和观赏性"
        }

        guidance_block = (
            f"\n\n叙事手法参考（请遵循其核心理念与结构，但只输出故事正文，不要输出本指导内容）：\n{style_guidance}\n"
            if style_guidance else ""
        )

        prompt = f"""你是一个专业的视频内容创作者。请创作一个关于"{topic}"的视频脚本。

要求：
1. 风格：{age_style_map.get(age_group, "")}
2. 视频长度：约{length_map.get(length, "20分钟")}的脚本（包含丰富的场景描写和对话）
3. 内容积极正面，引人入胜
4. 包含大量有趣的对话和情节
5. 结局精彩，令人印象深刻
6. 内容需要足够长，包含多个场景转换和情节发展，适合制作{length_map.get(length, "20分钟")}视频
{guidance_block}
请直接输出内容，不要加标题。"""

        return await self._call_text_api(prompt)
    
    async def analyze_story_for_split(
        self,
        story: str,
        target_duration_minutes: int = 3,
    ) -> dict:
        """分析故事内容，返回拆分建议。

        Returns:
            {
                "summary": "故事汇总说明",
                "suggested_split_count": int,
                "reason": "拆分理由"
            }
        """
        prompt = f"""你是一个专业的视频内容策划师。请分析以下故事内容，并给出拆分建议。

目标：将这个故事拆分为多个约{target_duration_minutes}分钟的视频分文件。

请分析故事的章节结构、情节转折点、场景转换等，给出以下信息：
1. 故事汇总说明（简要概括故事主题、主要角色、核心情节）
2. 建议拆分成几个独立的{target_duration_minutes}分钟视频文件
3. 每个分文件应该包含哪些情节（简要说明每个分文件的起止点）

请以以下 JSON 格式输出（不要输出其他内容）：
{{
    "summary": "故事汇总说明（100-200字）",
    "total_word_count": 故事总字数,
    "estimated_total_duration_minutes": 估算总时长（分钟）,
    "suggested_split_count": 建议拆分数量（整数）,
    "split_plan": [
        {{
            "part_number": 1,
            "title": "第一部分标题",
            "description": "这部分包含的情节简述",
            "start_hint": "这部分从故事的哪里开始（关键词或句子片段）",
            "end_hint": "这部分到哪里结束（关键词或句子片段）"
        }}
    ],
    "reason": "为什么建议这样拆分（50-100字）"
}}

故事内容：
{story}"""

        result = await self._call_text_api(prompt, max_tokens=8000)
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if match:
            raw_json = match.group()
            try:
                return json.loads(raw_json, strict=False)
            except json.JSONDecodeError as e:
                print(f"[API] 分析JSON首次解析失败: {e}", file=sys.stderr, flush=True)
                print(f"[API] 原始JSON前500字: {raw_json[:500]}", file=sys.stderr, flush=True)
                raise
        raise ValueError(f"AI 未返回有效的 JSON 格式，原始响应: {result[:500]}")

    async def split_story(
        self,
        story: str,
        split_plan: list,
    ) -> list:
        """根据拆分计划将故事拆分为多个独立文件。

        Args:
            story: 完整故事内容
            split_plan: 拆分计划列表，每个元素包含 part_number, title, start_hint, end_hint

        Returns:
            拆分后的故事列表，每个元素为 {"title": str, "content": str}
        """
        plan_json = json.dumps(split_plan, ensure_ascii=False, indent=2)
        prompt = f"""你是一个专业的文本编辑师。请根据以下拆分计划，将完整故事拆分为多个独立的故事文件。

拆分计划：
{plan_json}

要求：
1. 每个拆分文件必须是一个完整独立的故事，有开头、发展、结尾
2. 保留原文的写作风格和语言特色
3. 如果原文的结尾不够完整，请适当补充过渡使其自成一体
4. 每个拆分文件应包含足够的细节，能够独立用于视频制作
5. 不要遗漏任何重要情节和角色

请以以下 JSON 格式输出（不要输出其他内容）：
[
    {{
        "part_number": 1,
        "title": "第一部分标题",
        "content": "拆分后的完整故事内容"
    }}
]

完整故事内容：
{story}"""

        result = await self._call_text_api(prompt, max_tokens=30000)
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            raw_json = match.group()
            try:
                return json.loads(raw_json, strict=False)
            except json.JSONDecodeError as e:
                print(f"[API] 拆分JSON首次解析失败: {e}", file=sys.stderr, flush=True)
                print(f"[API] 原始JSON前500字: {raw_json[:500]}", file=sys.stderr, flush=True)
                raise
        raise ValueError(f"AI 未返回有效的 JSON 格式，原始响应: {result[:500]}")
    
    async def generate_script(
        self,
        story: str,
        scene_count: int = 40,
        total_duration: int = None,
        scene_context: str = "",
        characters: List[dict] = None,
        auto_count: bool = False,
    ) -> List[dict]:
        """生成分镜脚本

        Args:
            auto_count: 若为 True，让 AI 根据故事内容自行决定分镜数量和总时长，
                        scene_count/total_duration 仅作为参考上限
        """
        if total_duration is None:
            total_duration = scene_count * 5
        
        # 限制分镜数量：5分钟最多50个
        max_scenes_for_duration = (total_duration // 60) * 10  # 每分钟最多10个分镜
        scene_count = min(scene_count, max(max_scenes_for_duration, 10))
        
        scene_context_text = (
            f"\n已提取的场景（每个场景至少对应一个分镜；S1/S2/... 是场景编号，"
            f"分镜的 scene 字段必须回填这个编号）：\n{scene_context}\n"
            if scene_context else ""
        )
        
        # 构建角色列表上下文
        if characters:
            character_names = [f"- {c['name']}" for c in characters]
            character_context = "\n".join(character_names)
        else:
            character_context = ""
        character_context_text = (
            f"\n已提取的角色（分镜中只能使用这些角色，不能添加新角色）：\n{character_context}\n"
            if character_context else ""
        )
        
        if auto_count:
            count_instruction = (
                f"请根据故事内容的丰富程度，自行决定分镜数量和总时长。\n"
                f"参考上限：最多{scene_count}个分镜，总时长不超过{total_duration}秒（约{total_duration // 60}分钟）。\n"
                f"如果故事内容丰富、情节复杂，可以多用分镜充分讲述；如果故事简短，可以减少分镜数。\n"
                f"原则：**必须完整讲述整个故事，不能省略任何情节**。宁可多几个分镜，也不要压缩故事内容。\n"
                f"每个分镜时长4-12秒，请合理分配节奏。"
            )
            duration_rule = (
                f"3. 所有分镜的 duration 总和应与你自行决定的总时长一致，"
                f"不超过{total_duration}秒（约{total_duration // 60}分钟）"
            )
            avg_hint = f"平均每个分镜约{total_duration // max(scene_count, 1)}秒，可根据情节轻重调整"
        else:
            count_instruction = f"请将以下故事拆分成{scene_count}个分镜场景，总时长严格控制在{total_duration}秒（约{total_duration // 60}分钟）。"
            duration_rule = f"3. 所有分镜的 duration 总和必须**严格等于** {total_duration} 秒（约{total_duration // 60}分钟），这是硬性要求！"
            avg_hint = f"{scene_count}个分镜，平均每个分镜约{total_duration // scene_count}秒，请合理分配"
        
        prompt = f"""{count_instruction}

故事：
{story}
{character_context_text}
{scene_context_text}

请按以下 JSON 格式输出分镜列表（不要输出其他内容）：
[
    {{
        "scene_number": 1,
        "scene_name": "镜头标题（6-10字，如：爷爷唤孙）",
        "scene": "S1",
        "description": "光线氛围+环境细节（40-90字）；对白分镜用中文引号写明台词，纯画面分镜只写环境氛围",
        "characters": ["角色1", "角色2"],
        "dialogue_role": "",
        "voice_text": "",
        "action": "镜头运动+角色动作+情绪细节描述（60-150字）",
        "camera": "景别，机位。焦段，质感（如：中景，平视机位。50mm焦段，写实电影质感）",
        "duration": 5.0
    }}
]

重要规则（必须遵守）：
1. 分镜中的 characters 字段和 dialogue_role 字段**只能使用上面列出的已提取角色**，绝对不能添加新角色
2. 每个分镜时长必须为 4-12 秒，绝不能超过 12 秒
{duration_rule}
4. {avg_hint}
5. **characters 字段是最高优先级硬约束**：每个分镜的 characters 必须列出该镜头中所有在场角色（包括说话者和旁听者），绝不能留空数组[]。如果画面中有2个及以上角色同框，必须全部列出，例如两人对话写 ["角色A", "角色B"]，三人场景写 ["角色A", "角色B", "角色C"]。characters 决定出图时画几个人，漏写角色会导致该角色不出现在画面中
6. **voice_text 字段是硬约束**：角色对话分镜必须将台词内容填入 voice_text 字段（不要留空），同时 dialogue_role 填写说话的角色名。旁白分镜的 voice_text 填写旁白内容、dialogue_role 留空。纯画面分镜（无对话无旁白）voice_text 留空
7. 开头1-2个分镜可以是旁白（用于介绍场景和背景）：dialogue_role 填空字符串，voice_text 填写旁白解说内容；也可以是纯画面分镜（无对话无旁白，voice_text 留空），由剧情需要决定
8. 结尾1-2个分镜可以是旁白（用于总结和结局）：dialogue_role 填空字符串，voice_text 填写旁白解说内容；也可以是纯画面分镜（无对话无旁白，voice_text 留空），由剧情需要决定
9. 中间分镜可以是角色对话（dialogue_role 填写说话的角色名，voice_text 填写对话内容），也可以是纯画面分镜（无对话无旁白，voice_text 留空）。纯画面分镜只描述镜头运动、角色动作和环境氛围，不生成语音
10. 如果同一场景有多个角色对话，可以分成多个分镜（每个分镜侧重一个角色的对话），但 characters 字段必须列出该镜头中所有在场角色（包括说话者和旁听者），绝不能只写说话者
11. description 和 action 字段必须按下面的详细风格要求撰写（专业分镜水准），同时确保完整输出所有分镜，不要省略或截断 JSON
12. 分镜之间要有连贯性，保证故事流畅，相邻分镜的动作和镜头要承接（如"承接前一镜中角色被困的落点"）
13. 重要情节用特写或近景，场景转换用全景或远景
14. 对白分镜的 description 用中文引号写出该角色的一句台词，旁白分镜写出旁白解说词；纯画面分镜的 description 只写光线氛围、环境细节与画面质感，不写说话内容
15. 对白不要写进 action（台词由 description 和 voice_text 承载），action 专注于镜头运动、动作细节与环境反馈
16. 场景要少、镜头要多：同一场景必须拆分成 2-4 个分镜镜头，用不同机位/景别/角度表现同一环境下的连续情节；只有当该场景情节确实很短时才允许只拆 1 个镜头
17. 每个分镜的 scene 字段**必须回填上面场景上下文列表里的编号**（如 "S1"、"S2"），绝不能写场景名、绝不能创造新编号；scene 字段与 characters 字段一样是硬性约束
18. scene_name 是镜头小标题（6-10字，如「长跪坟前」「村民低语」），仅用于界面展示，**不是场景名**，不要与 scene 字段混淆

各字段的详细风格要求（专业分镜脚本水准）：
- description：本镜头的光线、氛围、环境细节与画面质感（40-90字）。对白分镜需用中文引号写明角色台词（如：悟空说"俺老孙来也！"），旁白分镜写明旁白解说词；纯画面分镜只写环境氛围，不写说话内容
- action：镜头运动与角色动作的详细描述（60-150字）。包含：镜头如何运动（固定观察/横移/推近/拉远）、环境动态反馈（碎石跳动、云层合拢、蒸汽渗出）、角色的具体动作与微表情（试探后退、关节摩擦、眼神狡黠）、情绪转折；对白不要写进 action（已由 voice_text 承载）。
- camera：专业镜头语言，格式为"景别，机位。焦段，质感"。如："中景，平视机位。50mm焦段，写实电影质感"、"全景，低角度机位。广角，史诗感"、"特写，俯视机位。85mm焦段，浅景深"。
- characters：该镜头中所有在场角色的名字列表。如果画面中有2个及以上角色同框，必须全部列出，例如两人对话写 ["角色A", "角色B"]，三人场景写 ["角色A", "角色B", "角色C"]。绝不能只写一个人——characters 决定出图时画几个人，漏写角色会导致该角色不出现在画面中

完整示例（仅供参考风格，不要照抄内容）：
[
    {{
        "scene_number": 3,
        "scene_name": "残余部众的拉锯",
        "scene": "S1",
        "description": "云层遮蔽后仅剩的冷灰余光与核心红光交织，压抑闷重的末世氛围，粗粝写实电影质感。壳说"不能再等，逻辑偏移？那就用偏移的逻辑活下去。"",
        "characters": ["壳"],
        "dialogue_role": "壳",
        "voice_text": "不能再等。逻辑偏移？那就用偏移的逻辑活下去。",
        "action": "镜头固定观察残骸堆中央，八条步足压满锈蚀地面；云层从破碎合金穹顶上方快速合拢，冷灰色月光被吞没，周围报废躯壳与线缆沉入黑暗。壳试着向后挪动，一条步足卡在断裂钢梁之间，关节发出刺耳摩擦，核心红光加速闪烁，缝隙渗出细密热蒸汽，镜头落在被卡住、红光摇曳的困境上。",
        "camera": "中景，平视机位。50mm焦段，写实电影质感",
        "duration": 3.0
    }},
    {{
        "scene_number": 4,
        "scene_name": "师徒河边对话",
        "scene": "S2",
        "description": "山脚河畔，巨石嶙峋，水雾弥漫，冷灰写实质感。老叫花子指着石壁说"到了，入口在那后面。"",
        "characters": ["老叫花子", "陈炎"],
        "dialogue_role": "老叫花子",
        "voice_text": "到了，入口在那后面。",
        "action": "镜头从中景横移，老叫花子抬手指向河对岸石壁，陈炎顺着他手指方向望去，水面倒映两人身影，水花飞溅。",
        "camera": "中景，平视机位。50mm焦段，写实电影质感",
        "duration": 5.0
    }}
]"""

        result = await self._call_text_api(prompt, max_tokens=min(65000, max(20000, scene_count * 700)))
        import json
        import re
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            raw_json = match.group()
            try:
                storyboard = json.loads(raw_json, strict=False)
            except json.JSONDecodeError as e:
                print(f"[API] 分镜JSON首次解析失败: {e}", file=sys.stderr, flush=True)
                print(f"[API] 原始JSON前500字: {raw_json[:500]}", file=sys.stderr, flush=True)
                storyboard = self._repair_storyboard_json(raw_json)
            for item in storyboard:
                if isinstance(item, dict) and "duration" in item:
                    item["duration"] = Settings.normalize_video_duration(item["duration"])
            # 后处理：AI 常不填 characters/voice_text/dialogue_role，
            # 从 description/action 文本中提取补全
            if characters:
                char_names_set = {c["name"] for c in characters}
                for item in storyboard:
                    if not isinstance(item, dict):
                        continue
                    # 补全 characters：从 description/action 中匹配已知角色名
                    if not item.get("characters"):
                        text = " ".join([
                            item.get("description", "") or "",
                            item.get("action", "") or "",
                        ])
                        found = []
                        for cn in sorted(char_names_set, key=len, reverse=True):
                            if cn in text:
                                found.append(cn)
                            else:
                                # 尝试归一化匹配（去括号）
                                norm = re.sub(r"[（(][^（）()]*[）)]", "", cn).strip()
                                if norm and norm in text:
                                    found.append(cn)
                        if found:
                            item["characters"] = found
                    # 补全 voice_text：从 description/action 中提取引号内对话
                    if not item.get("voice_text"):
                        text = " ".join([
                            item.get("description", "") or "",
                            item.get("action", "") or "",
                        ])
                        # 模式：角色名说/道："对话" 或 角色名："对话"
                        m = re.search(
                            r'[\u4e00-\u9fff]{1,6}(?:说|道|喊|叫|问|答)?\s*[：:]*\s*[\u201c\u300c"]([^"\u201d\u300d]+?)[\u201d\u300d"]',
                            text
                        )
                        if m:
                            item["voice_text"] = m.group(1).rstrip("。")
                            # 推断 dialogue_role
                            if not item.get("dialogue_role"):
                                role_m = re.search(
                                    r'([\u4e00-\u9fff]{1,6})(?:说|道|喊|叫|问|答)?\s*[：:]*\s*[\u201c\u300c"]',
                                    text
                                )
                                if role_m:
                                    rn = role_m.group(1).strip()
                                    if rn in char_names_set:
                                        item["dialogue_role"] = rn
            return storyboard
        raise ValueError("分镜生成结果中没有找到 JSON 数组")

    def _repair_storyboard_json(self, raw: str) -> list:
        """修复 AI 返回的常见 JSON 问题：未转义字符、截断、尾随逗号等

        修复策略（逐步升级）：
        1. 中文引号 → 英文引号
        2. 尾随逗号
        3. 逐字符扫描修复字符串值（换行/制表/反斜杠/控制字符）
        4. 尝试解析
        5. 截断修复
        6. 逐对象提取 + 激进修复
        """
        import json
        import re
        import sys

        text = raw

        print(f"[API] 开始修复分镜JSON，原始长度: {len(text)}", file=sys.stderr, flush=True)

        # ── Step 1: 中文引号 → 英文引号 ──
        text = text.replace('\u201c', '"').replace('\u201d', '"')
        text = text.replace('\u2018', "'").replace('\u2019', "'")

        # ── Step 2: 尾随逗号 ──
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)

        # ── Step 3: 逐字符扫描，修复字符串值 ──
        def _fix_string_value(val: str) -> str:
            """修复单个 JSON 字符串值中的问题"""
            val = val.replace('"', '\\"')
            val = val.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')
            val = val.replace('\t', '\\t')
            val = re.sub(r'\\(?![\"\\\\/bfnrtu])', r'\\\\', val)
            val = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', val)
            return val

        def _scan_and_fix_strings(s: str) -> str:
            """逐字符扫描 JSON 文本，识别 key:value 对中的 value 字符串并修复"""
            result = []
            i = 0
            n = len(s)
            while i < n:
                if s[i] == '"':
                    start = i
                    i += 1
                    while i < n:
                        if s[i] == '\\':
                            i += 2
                            continue
                        if s[i] == '"':
                            i += 1
                            break
                        i += 1
                    string_literal = s[start:i]
                    result.append(string_literal)
                    # 检查这个字符串后面是否跟着 : (说明是 key)
                    j = i
                    while j < n and s[j] in ' \t\n\r':
                        j += 1
                    if j < n and s[j] == ':':
                        # 这是 key，不修改
                        pass
                    else:
                        # 这是 value 字符串，修复内容
                        inner = string_literal[1:-1]
                        fixed_inner = _fix_string_value(inner)
                        if fixed_inner != inner:
                            result[-1] = '"' + fixed_inner + '"'
                else:
                    result.append(s[i])
                    i += 1
            return ''.join(result)

        text = _scan_and_fix_strings(text)

        # ── Step 4: 再次清理尾随逗号 ──
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)

        # ── Step 5: 尝试解析 ──
        try:
            result = json.loads(text, strict=False)
            print(f"[API] JSON修复成功（Step 1-4），解析到 {len(result)} 个分镜", file=sys.stderr, flush=True)
            return result
        except json.JSONDecodeError as e:
            print(f"[API] Step 1-4 修复后仍解析失败: {e}", file=sys.stderr, flush=True)

        # ── Step 6: 截断修复 ──
        last_brace = text.rfind('}')
        if last_brace > 0:
            truncated = text[:last_brace + 1] + ']'
            truncated = re.sub(r',\s*]', ']', truncated)
            try:
                result = json.loads(truncated, strict=False)
                print(f"[API] JSON截断修复成功，解析到 {len(result)} 个分镜", file=sys.stderr, flush=True)
                return result
            except json.JSONDecodeError as e2:
                print(f"[API] 截断修复仍失败: {e2}", file=sys.stderr, flush=True)

        # ── Step 7: 逐对象提取 + 激进修复 ──
        print(f"[API] 尝试逐对象提取修复...", file=sys.stderr, flush=True)
        items = []
        obj_pattern = re.compile(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', re.DOTALL)
        for obj_match in obj_pattern.finditer(text):
            obj_str = obj_match.group()
            obj_str = re.sub(r',\s*}', '}', obj_str)
            try:
                item = json.loads(obj_str, strict=False)
                items.append(item)
                continue
            except json.JSONDecodeError:
                pass

            obj_str = _scan_and_fix_strings(obj_str)
            obj_str = re.sub(r',\s*}', '}', obj_str)
            try:
                item = json.loads(obj_str, strict=False)
                items.append(item)
                continue
            except json.JSONDecodeError:
                pass

            obj_str = re.sub(r'[\x00-\x1f]', '', obj_str)
            obj_str = re.sub(r',\s*}', '}', obj_str)
            try:
                item = json.loads(obj_str, strict=False)
                items.append(item)
            except json.JSONDecodeError:
                salvaged = self._salvage_storyboard_object(obj_str)
                if salvaged:
                    print(
                        f"[API] 单对象激进抢救成功: 分镜 "
                        f"{salvaged.get('scene_number', '?')}",
                        file=sys.stderr, flush=True,
                    )
                    items.append(salvaged)
                else:
                    print(f"[API] 单对象修复失败，跳过: {obj_str[:100]}", file=sys.stderr, flush=True)
                continue

        if items:
            for i, item in enumerate(items):
                if isinstance(item, dict) and "scene_number" not in item:
                    item["scene_number"] = i + 1
            print(f"[API] 逐对象提取修复成功，解析: {len(items)} 个分镜", file=sys.stderr, flush=True)
            return items

        # ── Step 8: 最终失败 ──
        print(f"[API] 所有修复策略均失败，原始JSON前500字: {raw[:500]}", file=sys.stderr, flush=True)
        raise ValueError(f"分镜 JSON 修复失败，无法解析 AI 返回内容（前200字: {raw[:200]}）")

    # 分镜对象的已知字段（激进抢救用）
    _SB_STRING_KEYS = (
        "scene_name", "scene", "description", "dialogue_role",
        "voice_text", "action", "camera",
    )
    _SB_NUM_KEYS = ("scene_number", "duration")

    def _salvage_storyboard_object(self, obj_str: str):
        """激进抢救单个分镜对象：JSON 整体无法解析时按字段正则提取可用内容。

        背景：旧版对解析失败的分镜直接跳过，导致「目标 25 个，实际 24 个」的
        分镜号跳号（如 2.1 第 20 镜丢失）。这里尽力保住该镜的关键字段，
        缺失字段留空由上层补默认值，保证分镜数量与编号不断档。
        """
        import re as _re
        item = {}
        for key in self._SB_STRING_KEYS:
            m = _re.search(
                r'"' + key + r'"\s*:\s*"(.*?)"\s*(?:,|\}|$)', obj_str, _re.DOTALL
            )
            if m:
                val = m.group(1).replace('\\n', '\n').replace('\\"', '"').strip()
                if val:
                    item[key] = val
        for key in self._SB_NUM_KEYS:
            m = _re.search(r'"' + key + r'"\s*:\s*([\d.]+)', obj_str)
            if m:
                try:
                    item[key] = (
                        float(m.group(1)) if key == "duration"
                        else int(float(m.group(1)))
                    )
                except ValueError:
                    pass
        m = _re.search(r'"characters"\s*:\s*\[(.*?)\]', obj_str, _re.DOTALL)
        if m:
            chars = [c.strip().strip('"').strip() for c in m.group(1).split(',')]
            item["characters"] = [c for c in chars if c]
        # 至少要保住描述或动作，否则视为抢救失败（避免产生空镜头）
        if not (item.get("description") or item.get("action")):
            return None
        return item
    
    async def generate_image(
        self,
        prompt: str,
        size: str = "512x512",
        seed: Optional[int] = None,
        max_retries: int = 8,
        image: Optional[list] = None,
    ) -> bytes:
        """生成图像（含503重试）

        Args:
            prompt: 提示词
            size: 图像尺寸
            seed: 随机种子
            max_retries: 最大重试次数
            image: 参考图列表（图生图模式），如 [URL1, URL2] 或 [base64_1, base64_2]
                   传入时自动放入 extra_body.image 激活 img2img
        """
        import sys
        
        session = await self._get_session()
        data = {
            "model": Settings.IMAGE_MODEL,
            "prompt": prompt,
            "size": size,
        }
        if seed is not None:
            data["seed"] = seed
        # 官方文档要求 image 和 response_format 都在 extra_body 内
        extra_body = {"response_format": "b64_json"}
        if image:
            # image 是列表，每项为 Data URI 或 URL
            extra_body["image"] = image
        data["extra_body"] = extra_body

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        url = f"{self.base_url}/v1/images/generations"
        print(f"\n[API] 调用图像API", file=sys.stderr)
        print(f"[API] URL: {url}", file=sys.stderr)
        print(f"[API] 模型: {Settings.IMAGE_MODEL}", file=sys.stderr)
        if image:
            print(f"[API] img2img 参考图数: {len(image)}", file=sys.stderr)

        debug_data = dict(data)
        if "image" in debug_data:
            debug_data["image"] = f"<base64 len={len(debug_data['image'])}>"
        print(f"[API] 请求体: {json.dumps(debug_data, ensure_ascii=False, indent=2)}", file=sys.stderr)
        # 也把完整 prompt 写到文件，方便查看（终端太长会截断）
        try:
            with open("_last_prompt.txt", "w", encoding="utf-8") as pf:
                pf.write(f"MODEL: {Settings.IMAGE_MODEL}\n")
                pf.write(f"SIZE: {size}\n")
                pf.write(f"SEED: {seed}\n")
                if image:
                    pf.write(f"IMG2IMG: yes ({len(image)} ref images)\n")
                pf.write("="*60 + "\n")
                pf.write(prompt)
                pf.write("\n" + "="*60 + "\n")
        except Exception:
            pass
        
        for attempt in range(max_retries + 1):
            try:
                async with session.post(
                    url,
                    headers=headers,
                    json=data
                ) as response:
                    status = response.status
                    error_text = await response.text()
                    
                    print(f"[API] 响应状态: {status}", file=sys.stderr)
                    
                    if status == 200:
                        result = await response.json()
                        if "data" in result and len(result["data"]) > 0:
                            image_data = result["data"][0]
                            # 优先返回base64格式，无需二次下载
                            if "b64_json" in image_data:
                                import base64
                                return base64.b64decode(image_data["b64_json"])
                            # 兼容返回url的情况，下载失败单独重试，不用重新生成图像
                            elif "url" in image_data:
                                img_url = image_data["url"]
                                # 下载最多重试3次，避免重复调用生成接口
                                for download_retry in range(3):
                                    try:
                                        async with session.get(img_url) as img_resp:
                                            if img_resp.status == 200:
                                                return await img_resp.read()
                                            else:
                                                print(f"[API] ⏳ 图片下载失败（状态码{img_resp.status}），5秒后重试 ({download_retry+1}/3)", file=sys.stderr)
                                                await asyncio.sleep(5)
                                    except NETWORK_ERRORS as e:
                                        raise_if_real_cancel(e)
                                        print(f"[API] ⏳ 图片下载网络异常，5秒后重试 ({download_retry+1}/3): {str(e)[:100]}", file=sys.stderr)
                                        await asyncio.sleep(5)
                                # 下载全部失败，再重新生成
                                print(f"[API] ❌ 图片下载多次失败，重新调用生成接口", file=sys.stderr)
                                continue
                        raise Exception("图像生成失败：返回数据格式异常")
                    elif status in (429, 503):
                        if attempt < max_retries:
                            # 指数退避+随机抖动，避免重试风暴
                            import random
                            wait = min(120, (2 ** attempt) * 5 + random.randint(0, 5))
                            print(f"[API] ⏳ {status} 服务端限流，自动等待{wait}秒后重试 ({attempt+1}/{max_retries})，无需手动操作", file=sys.stderr)
                            await asyncio.sleep(wait)
                            session = await self._get_session()
                            continue
                        print(f"[API] ❌ 服务不可用 ({status})，已重试{max_retries}次，请稍后重试", file=sys.stderr)
                        raise Exception(f"服务不可用 ({status}): {error_text[:300]}")
                    else:
                        print(f"[API] ❌ API错误 ({status})", file=sys.stderr)
                        print(f"[API] 错误详情: {error_text[:500]}", file=sys.stderr)
                        raise Exception(f"API 错误 ({status}): {error_text[:300]}")
            except NETWORK_ERRORS as e:
                # 含网络层泄漏的 CancelledError：必须在此吞掉重试，
                # 否则会穿透本循环与上层 except Exception，导致整集生成崩溃。
                raise_if_real_cancel(e)
                if attempt < max_retries:
                    wait = 5 * (attempt + 1)
                    print(f"[API] ⏳ 网络错误，{wait}秒后重试 ({attempt+1}/{max_retries})", file=sys.stderr)
                    await asyncio.sleep(wait)
                    session = await self._get_session()
                    continue
                print(f"[API] ❌ 网络错误: {type(e).__name__}: {e}", file=sys.stderr)
                raise Exception(f"网络错误: {type(e).__name__}: {e}")
        
        raise Exception("图像生成失败：超过最大重试次数")
    
    async def generate_video(
        self,
        prompt: str,
        duration: int = 5,
        first_frame: Optional[str] = None,
        last_frame: Optional[str] = None,
        size: str = "720P",
        max_retries: int = 3,
        progress_callback: Optional[Callable] = None,
        audios: Optional[list] = None,
        mode: Optional[str] = None,  # 添加 mode 参数：text, keyframe, reference
        extra_images: Optional[list] = None,  # 额外参考图（场景图/角色图，base64或URL），reference模式使用，最多5张
    ) -> bytes:
        """生成视频（按 Agnes 官方异步任务模式：创建任务 -> 轮询状态 -> 下载）
        
        Args:
            prompt: 提示词
            duration: 视频时长（秒）
            first_frame: 首帧图像（base64）
            last_frame: 尾帧图像（base64）
            size: 视频尺寸
            max_retries: 最大重试次数
            progress_callback: 进度回调
            audios: 音频 URL 列表，如 ["https://example.com/audio.mp3"]
            mode: 生成模式，可选 "text"、"keyframe" 或 "reference"
                  如果不传入，则根据 first_frame/last_frame 自动判断
        """
        import sys
        import base64
        
        # Agnes Video 2.5 Flash 要求 seconds 为字符串 "4"–"12"
        actual_duration = max(
            Settings.VIDEO_MIN_DURATION,
            min(duration, Settings.VIDEO_MAX_DURATION),
        )
        
        session = await self._get_session()
        data = {
            "model": Settings.VIDEO_MODEL,
            "prompt": prompt,
            "seconds": str(actual_duration),
            "size": "720P",
            "aspect_ratio": "16:9",
        }
        
        # 确定 mode
        if mode:
            # 用户显式指定了 mode
            data["mode"] = mode
            if mode == "keyframe":
                if not first_frame or not last_frame:
                    raise ValueError("keyframe 模式需要 first_frame 和 last_frame")
                data["first_frame"] = first_frame
                data["last_frame"] = last_frame
            elif mode == "reference":
                # reference 模式：首帧图 + 场景图/角色图 等参考图（最多5张）
                images = [first_frame] if first_frame else []
                images += [img for img in (extra_images or []) if img]
                if images:
                    data["images"] = images[:5]
                    print(f"[API] 参考图片数: {len(data['images'])}", file=sys.stderr)
                # audios 在 reference 模式下使用
                if audios:
                    data["audios"] = audios
            elif mode == "text":
                # text 模式不带任何媒体参数
                pass
        elif first_frame and last_frame:
            data["mode"] = "keyframe"
            data["first_frame"] = first_frame
            data["last_frame"] = last_frame
        elif first_frame:
            data["mode"] = "reference"
            images = [first_frame] + [img for img in (extra_images or []) if img]
            data["images"] = images[:5]
            print(f"[API] 参考图片数: {len(data['images'])}", file=sys.stderr)
            # 如果提供了 audios，在 reference 模式下使用
            if audios:
                data["audios"] = audios
        else:
            data["mode"] = "text"
        
        # 添加音频参数（如果提供且 mode 支持）
        if audios and data.get("mode") != "text":
            data["audios"] = audios
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        print(f"[API] 调用视频API: {self.base_url}/v1/videos", file=sys.stderr, flush=True)
        print(f"[API] 模型: {Settings.VIDEO_MODEL}", file=sys.stderr, flush=True)
        print(f"[API] 提示词: {prompt[:100]}...", file=sys.stderr, flush=True)
        print(f"[API] 时长: {actual_duration}秒, 尺寸: 720P", file=sys.stderr, flush=True)
        print(f"[API] 首帧: {'有' if first_frame else '无'}", file=sys.stderr, flush=True)
        
        # 打印请求参数（图片数据截断，避免日志过长）
        debug_data = dict(data)
        for key in ("images", "first_frame", "last_frame"):
            if key in debug_data and isinstance(debug_data[key], list):
                debug_data[key] = [f"<base64, {len(img)}字符>" if isinstance(img, str) and len(img) > 100 else img for img in debug_data[key]]
            elif key in debug_data and isinstance(debug_data[key], str) and len(debug_data[key]) > 100:
                debug_data[key] = f"<base64, {len(debug_data[key])}字符>"
        print(f"[API] 请求参数: {json.dumps(debug_data, ensure_ascii=False, indent=2)}", file=sys.stderr, flush=True)
        print(f"--- API 请求结束 ---", file=sys.stderr, flush=True)
        
        # 调试模式：保存JSON到文件，不实际发送请求
        if self.debug_mode:
            import os
            debug_dir = Path(self.debug_dir)
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f"request_{int(asyncio.get_event_loop().time())}.json"
            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[DEBUG] 请求JSON已保存到: {debug_file}", file=sys.stderr, flush=True)
            print(f"[DEBUG] 调试模式启用，跳过实际API调用", file=sys.stderr, flush=True)
            # 返回模拟的响应
            return b"DEBUG_MODE: request saved to " + str(debug_file).encode()
        
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                async with session.post(
                    f"{self.base_url}/v1/videos",
                    headers=headers,
                    json=data
                ) as response:
                    status = response.status
                    error_text = await response.text()
                    
                    print(f"[API] 响应状态: {status}", file=sys.stderr, flush=True)
                    
                    if status == 200:
                        result = await response.json()
                        print(f"[API] 响应JSON: {json.dumps(result, ensure_ascii=False, indent=2)}", file=sys.stderr, flush=True)
                        
                        # Agnes 官方异步任务模式：返回 video_id/task_id
                        video_id = result.get('video_id') or result.get('id') or result.get('task_id')
                        if video_id:
                            print(f"[API] 视频任务已创建: {video_id[:24]}…", file=sys.stderr, flush=True)
                            if progress_callback:
                                progress_callback(f"视频任务已创建，正在生成...")
                            return await self._poll_video_status(session, video_id, progress_callback)
                        
                        # 兼容同步返回模式（直接返回视频）
                        if "data" in result and len(result["data"]) > 0:
                            video_data = result["data"][0]
                            if "url" in video_data:
                                print(f"[API] 视频URL: {video_data['url'][:100]}...", file=sys.stderr, flush=True)
                                async with session.get(video_data["url"]) as vid_resp:
                                    video_bytes = await vid_resp.read()
                                    print(f"[API] 视频大小: {len(video_bytes)} 字节", file=sys.stderr, flush=True)
                                    return video_bytes
                        
                        raise Exception("视频生成失败：响应格式异常")
                    elif status in (429, 503):
                        if attempt < max_retries:
                            wait = 15 * (attempt + 1)
                            print(f"[API] ⏳ {status} 队列繁忙，{wait}秒后重试 ({attempt+1}/{max_retries})", file=sys.stderr, flush=True)
                            await asyncio.sleep(wait)
                            session = await self._get_session()
                            continue
                        print(f"[API] ❌ 服务不可用 ({status})，已重试{max_retries}次", file=sys.stderr, flush=True)
                        last_error = Exception(f"服务不可用 ({status}): {error_text[:300]}")
                    elif status == 400:
                        print(f"[API] ❌ 请求错误 (400): {error_text[:500]}", file=sys.stderr, flush=True)
                        last_error = Exception(f"API 请求错误 (400): {error_text[:300]}")
                        break
                    else:
                        print(f"[API] ❌ API错误 ({status})", file=sys.stderr, flush=True)
                        print(f"[API] 错误详情: {error_text[:500]}", file=sys.stderr, flush=True)
                        last_error = Exception(f"API 错误 ({status}): {error_text[:300]}")
                        if attempt < max_retries:
                            wait = 10 * (attempt + 1)
                            print(f"[API] ⏳ {wait}秒后重试 ({attempt+1}/{max_retries})", file=sys.stderr, flush=True)
                            await asyncio.sleep(wait)
                            session = await self._get_session()
                            continue
                        break
            except NETWORK_ERRORS as e:
                raise_if_real_cancel(e)
                if attempt < max_retries:
                    wait = 5 * (attempt + 1)
                    print(f"[API] ⏳ 网络错误，{wait}秒后重试 ({attempt+1}/{max_retries})", file=sys.stderr, flush=True)
                    await asyncio.sleep(wait)
                    session = await self._get_session()
                    continue
                print(f"[API] ❌ 网络错误: {type(e).__name__}: {e}", file=sys.stderr)
                last_error = Exception(f"网络错误: {type(e).__name__}: {e}")
        
        raise last_error or Exception("视频生成失败：超过最大重试次数")
    
    async def _poll_video_status(self, session, video_id: str, progress_callback: Optional[Callable] = None) -> bytes:
        """轮询视频生成状态（按 Agnes 官方文档：使用 /agnesapi 端点，每 2 秒轮询一次）"""
        import sys
        
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # 构建 Agnes 官方轮询端点
        # 格式: {base_url}/agnesapi?video_id={video_id}&model_name={model_name}
        base = self.base_url.rstrip('/')
        if base.endswith('/v1'):
            base = base[:-3]
        poll_url = f"{base}/agnesapi?video_id={video_id}&model_name={Settings.VIDEO_MODEL}"
        
        print(f"[API] 轮询端点: {poll_url}", file=sys.stderr, flush=True)
        
        max_polls = 300  # 最多轮询 300 次（约 15 分钟）
        poll_interval = 3  # 每 3 秒轮询一次（平衡响应速度与 API 压力）
        
        for i in range(max_polls):
            await asyncio.sleep(poll_interval)
            status_msg = f"轮询视频状态 ({i+1}/{max_polls})... video_id: {video_id[:24]}…"
            print(f"[API] {status_msg}", file=sys.stderr, flush=True)
            if progress_callback:
                progress_callback(status_msg)
            
            try:
                async with session.get(
                    poll_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        continue
                    
                    result = await response.json()
                    status = str(result.get('status', '')).lower()
                    progress = result.get('progress')
                    
                    # 每 5 次轮询打印一次进度
                    if status not in ('completed', 'failed') and i % 5 == 0:
                        pct = f" ({progress}%)" if isinstance(progress, (int, float)) else ""
                        print(f"[API] 视频生成中…已等待 {(i+1)*poll_interval} 秒{pct}", file=sys.stderr, flush=True)
                        if progress_callback:
                            progress_callback(f"视频生成中…{pct}")
                    
                    if status == 'completed':
                        # 任务完成，下载视频
                        metadata = result.get('metadata') or {}
                        download_url = metadata.get('url') if isinstance(metadata, dict) else None
                        if download_url:
                            print(f"[API] 视频生成完成，开始下载: {download_url[:100]}…", file=sys.stderr, flush=True)
                            if progress_callback:
                                progress_callback("视频生成完成，开始下载...")
                            async with session.get(download_url) as vid_resp:
                                video_bytes = await vid_resp.read()
                                print(f"[API] 视频下载完成，大小: {len(video_bytes)} 字节", file=sys.stderr, flush=True)
                                if progress_callback:
                                    progress_callback(f"视频下载完成，大小: {len(video_bytes)} 字节")
                                return video_bytes
                        
                        # 兼容其他返回格式
                        if 'video' in result:
                            import base64
                            return base64.b64decode(result['video'])
                        if 'url' in result:
                            async with session.get(result['url']) as vid_resp:
                                return await vid_resp.read()
                        if 'data' in result and result['data']:
                            item = result['data'][0]
                            if 'video' in item:
                                import base64
                                return base64.b64decode(item['video'])
                            if 'url' in item:
                                async with session.get(item['url']) as vid_resp:
                                    return await vid_resp.read()
                        
                        raise Exception(f"无法解析视频响应: {result}")
                    
                    elif status == 'failed':
                        error_info = result.get('error') or {}
                        error_msg = error_info.get('message') if isinstance(error_info, dict) else str(error_info)
                        raise Exception(f"视频生成失败: {error_msg or result}")
                    
            except RETRYABLE_ERRORS as e:
                # 轮询期间的网络抖动不应中断整个视频任务（保留原状态继续轮询）
                raise_if_real_cancel(e)
                print(f"[API] 轮询异常: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        
        raise Exception("视频生成超时（超过 15 分钟），任务可能仍在后台进行，可稍后重试")
    
    # 文本 API 重试策略（与视频/图像 API 对齐：网络抖动不再直接判死整集）
    TEXT_API_MAX_RETRIES = 3
    TEXT_API_RETRY_DELAYS = (5, 15, 30)

    async def _call_text_api(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 8000,
        max_retries: int = None,
    ) -> str:
        """调用文本 API（带网络重试）。

        重试策略（与视频/图像 API 对齐）：
        - 网络错误（ClientConnectorError / 超时等 aiohttp.ClientError）：退避重试并重建连接；
        - 503 / 429：服务端过载或限流，退避重试；
        - 401 / 400 等认证或参数错误：重试无意义，直接抛出。

        背景：旧版一次网络抖动即抛异常，导致「一键全剧分镜」在 142 集批量中
        大量集失败（日志：Cannot connect to host ... [信号灯超时时间已到]）。
        """
        import sys

        retries = self.TEXT_API_MAX_RETRIES if max_retries is None else max_retries
        delays = self.TEXT_API_RETRY_DELAYS
        last_error = None

        for attempt in range(retries + 1):
            try:
                return await self._call_text_api_once(prompt, system_prompt, max_tokens)
            except RETRYABLE_ERRORS as e:
                # CancelledError 不是 Exception 子类，若只写 except Exception
                # 会连带漏掉网络层泄漏的取消异常（新版已并入 RETRYABLE_ERRORS）
                raise_if_real_cancel(e)
                last_error = e
                msg = str(e)
                # 认证/参数类错误重试无意义
                fatal = ("401" in msg) or ("认证失败" in msg) or ("400" in msg)
                if fatal or attempt >= retries:
                    raise
                delay = delays[min(attempt, len(delays) - 1)]
                print(
                    f"[API] ⏳ 文本API失败({type(e).__name__}: {msg[:120]})，"
                    f"{delay}秒后重试 ({attempt + 1}/{retries})",
                    file=sys.stderr, flush=True,
                )
                try:
                    await self.close()  # 重建连接，规避半开连接/代理抖动
                except Exception:
                    pass
                await asyncio.sleep(delay)

        raise last_error if last_error else Exception("文本API调用失败")

    async def _call_text_api_once(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 8000,
    ) -> str:
        """调用文本 API（单次尝试，不含重试）"""
        import sys
        
        session = await self._get_session()
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        data = {
            "model": Settings.TEXT_MODEL,
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": max_tokens,
        }
        
        # 使用标准认证方式（参考旧工程实现）
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        url = f"{self.base_url}/v1/chat/completions"
        print(f"\n[API] 调用文本API", file=sys.stderr)
        print(f"[API] URL: {url}", file=sys.stderr)
        print(f"[API] 模型: {Settings.TEXT_MODEL}", file=sys.stderr)
        print(f"[API] 请求体: {json.dumps(data, ensure_ascii=False)[:200]}...", file=sys.stderr)
        
        try:
            async with session.post(
                url,
                headers=headers,
                json=data
            ) as response:
                status = response.status
                error_text = await response.text()
                
                print(f"[API] 响应状态: {status}", file=sys.stderr)
                
                if status == 200:
                    try:
                        result = await response.json()
                        if "choices" in result and len(result["choices"]) > 0:
                            print(f"[API] ✅ 文本生成成功", file=sys.stderr)
                            return result["choices"][0]["message"]["content"]
                        print(f"[API] ❌ 响应格式错误: {result}", file=sys.stderr)
                        raise Exception("文本生成失败：响应中没有choices")
                    except json.JSONDecodeError as e:
                        print(f"[API] ❌ JSON解析失败: {e}", file=sys.stderr)
                        raise Exception(f"JSON解析失败: {e}")
                elif status == 503:
                    print(f"[API] ❌ 服务不可用 (503)", file=sys.stderr)
                    print(f"[API] 错误详情: {error_text[:500]}", file=sys.stderr)
                    print(f"[API] 💡 可能原因: 服务器维护、过载或API端点错误", file=sys.stderr)
                    raise Exception(f"服务不可用 (503): {error_text[:300]}")
                elif status == 401:
                    print(f"[API] ❌ 认证失败 (401)", file=sys.stderr)
                    print(f"[API] 错误详情: {error_text[:500]}", file=sys.stderr)
                    raise Exception(f"认证失败 (401): {error_text[:300]}")
                elif status == 429:
                    print(f"[API] ⚠️ 调用频率限制 (429)", file=sys.stderr)
                    raise Exception(f"调用频率限制 (429): 请稍后重试")
                else:
                    print(f"[API] ❌ API错误 ({status})", file=sys.stderr)
                    print(f"[API] 错误详情: {error_text[:500]}", file=sys.stderr)
                    raise Exception(f"API 错误 ({status}): {error_text[:300]}")
        except NETWORK_ERRORS as e:
            raise_if_real_cancel(e)
            print(f"[API] ❌ 网络错误: {type(e).__name__}: {e}", file=sys.stderr)
            raise Exception(f"网络错误: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"[API] ❌ 异常: {type(e).__name__}: {e}", file=sys.stderr)
            raise
    
    async def extract_project_name(self, story: str, topic: str) -> str:
        """从故事中提取项目名称"""
        prompt = f"""请为以下故事提取一个简洁的项目名称（不超过10个字）：

主题：{topic}
故事：{story[:500]}...

请直接输出名称，不要加其他内容。"""
        result = await self._call_text_api(prompt)
        return result.replace('\n', '').replace('\r', '').strip()
    
    async def extract_characters(
        self, story: str, video_context: str = "", max_chars: int = 8000,
        style_hint: str = "",
    ) -> List[dict]:
        """从故事中提取角色

        Args:
            story: 故事文本
            video_context: 视频制作背景说明（总时长、场景数等），用于让 AI 按时长尺度生成角色
            max_chars: 送入 AI 的最大字符数（剧集模式下用更大的窗口覆盖全剧）
            style_hint: 项目视觉风格提示（如"3D"、"卡通"、"写实"），引导 AI 按对应风格撰写角色描述
        """
        video_context_text = f"\n视频制作背景：{video_context}\n" if video_context else ""
        style_hint_text = f'\n视觉风格要求：角色描述必须体现「{style_hint}」风格的视觉质感，严禁出现与「{style_hint}」不一致的风格词（如选了3D风格就不能写"写实"、"手绘"等；选了卡通风格就不能写"写实摄影"等）。\n' if style_hint else ""
        prompt = f"""请从以下故事中提取所有角色，按 JSON 格式输出：

故事：{story[:max_chars]}
{video_context_text}{style_hint_text}
重要提示：
1. 提取故事中**所有**出现的角色，包括：
   - 主要角色（主角、重要配角）
   - 次要角色（短暂出现的角色）
   - 群体角色需要拆分为独立个体（如"七仙女"拆分为"大仙女"、"二仙女"等）
   - 神仙、妖怪、动物等非人类角色
   - 只被提到一次的角色也要提取
2. 不要遗漏任何角色，即使是短暂出现的角色
3. 每个角色需要有明确的名称和描述
4. 描述详细程度按角色重要性分级：关键主角描述最详细（200-250字），重要配角次之（120-180字），仅一两次出场的次要角色可精简（80-120字）

description 字段必须是**专业角色设定图级别的详细描述**（150-250字），用于AI绘图和视频生成，按"从上到下"的结构撰写：
- 开头：角色名 + 核心身份设定（物种/身份/年代背景）
- 整体质感：主体呈现XX风格质感（根据项目视觉风格决定，如3D风格写"3D渲染质感"、卡通风格写"二次元动画质感"、写实风格写"写实电影质感"、水彩风格写"水彩手绘质感"等）
- 头部细节：脸型五官、发型/毛色、眼睛特征、表情神态，包含磨损/光泽/纹理等微观细节
- 躯干细节：体型轮廓、服装款式颜色材质、铠甲/饰品做旧细节（散热孔、刮痕、修补痕迹等）
- 四肢细节：手臂与手部特征、腿足/爪/蹄特征、关节细节
- 色彩方案：整体主色 + 局部点缀色（如"整体为金色与鹅黄色，局部点缀虎皮棕斑与皂黑色"）
- 姿态：自然站姿（如"双手自然垂于身侧"）
- 结尾：气质与情绪氛围（如"整体呈现活泼灵动、自信不羁的气质"）

注意：描述中**不要**包含"纯白色背景、全身摄影、面朝镜头、无背景阴影、无手持道具"等摄影设定词，系统会自动添加这些摄影前缀；也不要写手持道具。

示例（仅供参考风格，不要照抄内容，务必按项目视觉风格调整"主体呈现"的质感描述）：
"壳，一台具有拟人化行为模式的旧世代八足机械人。主体呈现3D渲染末世废土质感，无人类面部，头部为扁平化复合光学传感舱，表面覆盖厚重氧化涂层与细小裂纹；中央主传感器散发冷白色微弱光晕，两侧分布多个辅助光学镜头，表面附着细密灰尘与锈斑。躯干为低重心流线型装甲结构，胸甲布满密集散热孔、刮痕与焊接修补痕迹，内部管线部分外露，关节处可见磨损的液压管线与铜绿锈蚀。双臂为精密机械钳，指缝间残留油污；八条步足由分段式合金骨架构成，关节球体呈暗褐色，足部末端带有防滑纹路，整体姿态因长期磨损略显不对称。机体整体为灰褐色，局部点缀铁锈红与冷金属灰，表面呈现真实的刮擦、氧化与临时维修痕迹，无品牌标识。双手自然垂于身侧。整体呈现疲惫、警觉、被战争磨损的幸存者气质，具有强烈的机械生命感与末世氛围。"

人形角色示例（仅供参考风格，不要照抄内容，务必按项目视觉风格调整"主体呈现"的质感描述）：
"美猴王，一只由仙石孕育、拜师学艺的灵猴。主体呈现3D渲染国风神话质感，头部为猴形轮廓，脸颊覆满金色短毛，一双火眼金睛明亮有神，眼角上挑透着灵气，头顶束起黑色发髻并插一根白玉发簪。躯干精瘦矫健，身穿鹅黄色短衫，衣料呈现粗麻质感与磨损褶皱，腰围虎皮短裙，皮毛斑纹清晰。双臂修长有力，手部覆金色细毛、指节分明，腿部肌肉线条流畅，脚穿皂色布靴，靴面沾有山尘。整体色彩以金色与鹅黄色为主，局部点缀虎皮棕斑与皂黑色。双手自然垂于身侧。整体呈现活泼灵动、自信不羁的美猴王气质，充满野性生命力与神话氛围。"

输出格式：
[
    {{"name": "角色名", "description": "详细的角色设定图描述（150-250字）", "episodes": ["1.1", "1.2"]}}
]

重要要求：
- episodes 字段必须标注该角色出现在哪些集/节，例如：["1.1", "1.2"] 表示该角色出现在第1.1和1.2节
- 如果角色在全剧各集都有出现，episodes 写 ["全剧"]
- 如果角色只在某一集出现，episodes 只写该集编号，如 ["1.2"]
- 请根据故事内容仔细判断每个角色的出现范围

JSON 硬性规则（务必遵守）：
1. 字符串值内部出现的双引号必须转义为 \"（或直接改用中文引号「」），严禁裸双引号
2. 字符串值内部不得出现未转义的英文单引号以外的控制字符或换行符
3. 直接输出 JSON 数组，不要加其他内容。确保提取所有角色，不要遗漏！"""
        result = await self._call_text_api(prompt)
        import json
        import re
        import sys
        
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[角色提取] AI返回内容长度: {len(result)} 字符", file=sys.stderr)
        print(f"[角色提取] 返回内容前500字符:", file=sys.stderr)
        print(result[:500], file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            try:
                characters = json.loads(match.group(), strict=False)
                print(f"[角色提取] ✅ JSON解析成功，提取到 {len(characters)} 个角色", file=sys.stderr)
                return characters
            except json.JSONDecodeError as e:
                print(f"[角色提取] ❌ 标准JSON解析失败({e})，尝试宽容解析...", file=sys.stderr)
        else:
            print(f"[角色提取] ❌ 未找到 JSON 数组，尝试宽容解析...", file=sys.stderr)
        # 宽容解析：容忍描述值内的裸引号/未转义反斜杠/输出截断
        characters = json_loads_lenient(result)
        if characters:
            print(f"[角色提取] ✅ 宽容解析成功，提取到 {len(characters)} 个角色", file=sys.stderr)
            return characters
        print(f"[角色提取] ❌ 宽容解析也失败，返回0个角色", file=sys.stderr)
        return []
    
    async def extract_props(
        self, story: str, video_context: str = "", max_chars: int = 8000,
        style_hint: str = "",
    ) -> List[dict]:
        """从故事中提取关键道具（重要物品/物件）

        Args:
            story: 故事文本
            video_context: 视频制作背景说明，用于控制道具数量与详略
            max_chars: 送入 AI 的最大字符数（剧集模式下用更大的窗口覆盖全剧）
            style_hint: 项目视觉风格提示（如"3D"、"卡通"、"写实"），引导 AI 按对应风格撰写道具描述
        """
        video_context_text = f"\n视频制作背景：{video_context}\n" if video_context else ""
        style_hint_text = f"\n视觉风格要求：道具描述必须体现「{style_hint}」风格的视觉质感，严禁出现与「{style_hint}」不一致的风格词。\n" if style_hint else ""
        prompt = f"""请从以下故事中提取关键**道具**（对剧情有重要作用、需要视觉呈现的物件），按 JSON 格式输出：

故事：{story[:max_chars]}
{video_context_text}{style_hint_text}
重要提示：
1. 提取故事中具有重要视觉呈现价值的关键道具（如：核心护盾、备用能量导管、数据干扰弹、定海神针、金色铁棒等）
2. 数量控制在 4-12 个，选择最重要的；普通背景杂物不需要提取
3. 每个道具需要有明确的名称和描述

description 字段必须是**专业道具产品设定图级别的详细描述**（80-150字），按"从整体到局部"的结构撰写：
- 整体形态：悬浮/条状/晶体/盾形等整体轮廓与占位结构
- 结构组成：主要功能区（如主控广播区、编号标记区、延伸结构）、组成部件
- 材质与颜色：主体材质（半透明数据流/金属/晶体）、主色+点缀色（如冷蓝为底、猩红与暗紫点缀）
- 发光与纹理：高亮标签、边缘发光、数据纹理、脉冲/动态闪烁
- 运行状态：数据流均匀、编号清晰、结构完整等稳定运行表现

注意：描述中**不要**包含"产品摄影、纯白色背景、无人物、无环境、俯视正面角度、完整展示本体、不裁切"等摄影设定词，系统会自动添加这些摄影前缀。

示例（仅供参考风格，不要照抄内容）：
"主控猎杀指令。道具整体为悬浮数据光带形态，呈细长条状结构，包含主控广播文本区、编号标记区与蛛网状脉冲延伸结构。主体采用半透明冷蓝色数据流材质，表面呈现细密数字纹理与轻微折射；编号区为鲜红色高亮标签，带有边缘发光效果；脉冲延伸部分为密集蛛网状线条，末端带有微弱动态闪烁。整体以冷蓝为主，搭配猩红与暗紫；边缘、节点与连接纹路呈现高精度数据界面质感。道具表面具有稳定运行状态，数据流均匀、编号清晰、脉冲结构完整。"

输出格式：
[
    {{"name": "道具名", "description": "详细的道具产品设定描述（80-150字）", "episodes": ["1.1", "1.2"]}}
]

重要要求：
- episodes 字段必须标注该道具出现在哪些集/节，例如：["1.1", "1.2"] 表示该道具出现在第1.1和1.2节
- 如果道具在全剧各集都有出现，episodes 写 ["全剧"]
- 如果道具只在某一集出现，episodes 只写该集编号，如 ["1.2"]
- 请根据故事内容仔细判断每个道具的出现范围

JSON 硬性规则（务必遵守）：
1. 字符串值内部出现的双引号必须转义为 \"（或直接改用中文引号「」），严禁裸双引号
2. 字符串值内部不得出现未转义的控制字符或换行符
3. 直接输出 JSON 数组，不要加其他内容。确保提取最关键的道具，不要遗漏核心物品！"""

        result = await self._call_text_api(prompt)
        import json
        import re
        import sys

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[道具提取] AI返回内容长度: {len(result)} 字符", file=sys.stderr)
        print(result[:500], file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            try:
                props = json.loads(match.group(), strict=False)
                print(f"[道具提取] ✅ JSON解析成功，提取到 {len(props)} 个道具", file=sys.stderr)
                return props
            except json.JSONDecodeError as e:
                print(f"[道具提取] ❌ 标准JSON解析失败({e})，尝试宽容解析...", file=sys.stderr)
        else:
            print(f"[道具提取] ❌ 未找到 JSON 数组，尝试宽容解析...", file=sys.stderr)
        # 宽容解析：容忍描述值内的裸引号/未转义反斜杠/输出截断
        props = json_loads_lenient(result)
        if props:
            print(f"[道具提取] ✅ 宽容解析成功，提取到 {len(props)} 个道具", file=sys.stderr)
            return props
        print(f"[道具提取] ❌ 宽容解析也失败，返回0个道具", file=sys.stderr)
        return []

    async def extract_scenes(
        self, story: str, min_scenes: int = 0, max_scenes: int = 0,
        video_context: str = "", max_chars: int = 8000,
        style_hint: str = "",
    ) -> List[dict]:
        """从故事中提取场景

        Args:
            story: 故事文本
            min_scenes: 最小场景数提示，用于引导 LLM 提取足够多的场景
            max_scenes: 最大场景数限制，避免AI过度提取
            video_context: 视频制作背景说明（总时长、节奏），用于让 AI 按时长尺度提取场景
            max_chars: 送入 AI 的最大字符数（剧集模式下用更大的窗口覆盖全剧）
            style_hint: 项目视觉风格提示（如"3D"、"卡通"、"写实"），引导 AI 按对应风格撰写场景描述
        """
        scene_req = ""
        if min_scenes > 0 and max_scenes > 0:
            scene_req = f"\n要求：请提取 {min_scenes}-{max_scenes} 个场景，确保覆盖故事的主要情节，但不要过度拆分。"
        elif min_scenes > 0:
            scene_req = f"\n要求：请至少提取 {min_scenes} 个场景，确保覆盖故事的主要情节和场景转折。"
        
        video_context_text = f"\n视频制作背景：{video_context}\n" if video_context else ""
        style_hint_text = f"\n视觉风格要求：场景描述必须体现「{style_hint}」风格的视觉质感，严禁出现与「{style_hint}」不一致的风格词。\n" if style_hint else ""
        prompt = f"""请从以下故事中提取场景，按 JSON 格式输出：

故事：{story[:max_chars]}{scene_req}{video_context_text}{style_hint_text}

输出格式：
[
    {{"name": "场景名", "description": "场景描述", "time_of_day": "day/night/dusk/dawn", "episodes": ["1.1", "1.2"]}}
]

重要要求：
- episodes 字段必须标注该场景出现在哪些集/节，例如：["1.1", "1.2"] 表示该场景出现在第1.1和1.2节
- 如果场景在全剧各集都有出现，episodes 写 ["全剧"]
- 如果场景只在某一集出现，episodes 只写该集编号，如 ["1.2"]
- 请根据故事内容仔细判断每个场景的出现范围

要求：
1. 根据故事的情节发展，提取所有重要的场景
2. 每个场景应该有明确的名称和描述
3. 场景数量控制在 {min_scenes}-{max_scenes} 个之间（如果指定了范围）
4. 不要过度拆分场景，每个场景应该是一个完整的情节段落
5. 直接输出 JSON 数组，不要加其他内容
6. 确保 JSON 格式正确，能被 json.loads() 解析
7. 字符串值内部出现的双引号必须转义为 \"（或改用中文引号「」），严禁裸双引号；值内不得出现未转义的换行符

description 字段必须是**详细的环境设定描述**（80-160字），用于AI绘图和视频生成，需要包含：
- 地点与空间布局（如：蟠桃园深处、瑶池宝殿前、云海之上的南天门外）
- 环境元素细节（如：蟠桃树高大繁茂、枝头硕果累累、地上残核断枝、远处云海翻腾）
- 光线描述：光源、方向、色调（如：夕阳余晖斜照、冷灰色月光被云层吞没、红光与冷光交织）
- 氛围与情绪基调（如：压抑闷重的末世氛围、仙气缭绕的祥和、紧张压迫）
- 材质质感与色彩基调（如：锈蚀金属、温润白玉、金红暖色调、冷灰主色，质感须符合项目视觉风格）

示例（仅供参考风格，不要照抄内容，务必按项目视觉风格调整质感描述）：
"蟠桃园深处，高大繁茂的蟠桃树枝干虬结，枝头硕大的蟠桃泛着粉金色光泽，地上散落着残核断枝。夕阳余晖穿过枝叶缝隙斜照下来，光影斑驳，远处云海翻腾与天际相接。氛围祥和却暗藏躁动，色彩以金红暖色调为主，具有3D渲染电影质感。"""
        result = await self._call_text_api(prompt)
        import json
        import re
        import sys
        
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[场景提取] AI返回内容长度: {len(result)} 字符", file=sys.stderr)
        print(f"[场景提取] 返回内容前1000字符:", file=sys.stderr)
        print(result[:1000], file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        
        # 尝试多种方式提取JSON
        # 方式1：直接尝试解析整个返回内容
        try:
            scenes = json.loads(result, strict=False)
            if isinstance(scenes, list):
                print(f"[场景提取] ✅ 直接解析成功，提取到 {len(scenes)} 个场景", file=sys.stderr)
                return scenes
        except json.JSONDecodeError:
            pass
        
        # 方式2：使用正则表达式提取JSON数组
        match = re.search(r'\[.*\]', result, re.DOTALL)
        if match:
            try:
                scenes = json.loads(match.group(), strict=False)
                print(f"[场景提取] ✅ 正则提取后解析成功，提取到 {len(scenes)} 个场景", file=sys.stderr)
                return scenes
            except json.JSONDecodeError as e:
                print(f"[场景提取] ❌ JSON解析失败: {e}", file=sys.stderr)
                print(f"[场景提取] 正则匹配的内容: {match.group()[:500]}...", file=sys.stderr)
        else:
            print(f"[场景提取] ❌ 未找到 JSON 数组（正则匹配失败）", file=sys.stderr)
        
        # 方式3：尝试清理常见的格式问题
        cleaned = result.strip()
        # 移除可能的markdown代码块标记
        cleaned = re.sub(r'^```json\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^```\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'```$', '', cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
        
        try:
            scenes = json.loads(cleaned, strict=False)
            if isinstance(scenes, list):
                print(f"[场景提取] ✅ 清理markdown后解析成功，提取到 {len(scenes)} 个场景", file=sys.stderr)
                return scenes
        except json.JSONDecodeError:
            pass

        # 方式4：宽容解析（容忍描述值内裸引号/未转义反斜杠/输出截断）
        scenes = json_loads_lenient(result)
        if scenes:
            print(f"[场景提取] ✅ 宽容解析成功，提取到 {len(scenes)} 个场景", file=sys.stderr)
            return scenes

        print(f"[场景提取] ❌ 所有解析方式均失败，返回0个场景", file=sys.stderr)
        return []


def fetch_available_models(api_key: str = "", api_base_url: str = "") -> dict:
    """同步获取 API 可用模型列表（用于设置对话框刷新按钮）。
    
    返回格式: {"text": [...], "image": [...], "video": [...]}
    API 不支持或出错时返回空字典，调用方应 fallback 到默认列表。
    """
    import requests as _requests
    
    url = f"{(api_base_url or Settings.API_BASE_URL).rstrip('/')}/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key or Settings.API_KEY}",
    }
    
    try:
        resp = _requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[API] 获取模型列表失败: HTTP {resp.status_code}", file=sys.stderr)
            return {}
        
        data = resp.json()
        raw_models = [m["id"] for m in data.get("data", []) if m.get("id")]
        
        if not raw_models:
            return {}
        
        # 按名称分类
        text_models = []
        image_models = []
        video_models = []
        other_models = []
        
        for m in raw_models:
            lower = m.lower()
            if "image" in lower:
                image_models.append(m)
            elif "video" in lower:
                video_models.append(m)
            elif m.startswith("agnes-"):
                text_models.append(m)
            else:
                other_models.append(m)
        
        # 剩余不能明确归类的放入 text（作为兜底）
        text_models.extend(other_models)
        
        return {
            "text": sorted(text_models),
            "image": sorted(image_models),
            "video": sorted(video_models),
        }
    except Exception as e:
        print(f"[API] 获取模型列表异常: {e}", file=sys.stderr)
        return {}