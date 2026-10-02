# 🤝 贡献指南

感谢你考虑为 AI Video Production Studio 做出贡献！

## 📋 目录

- [行为准则](#行为准则)
- [如何贡献](#如何贡献)
- [开发环境设置](#开发环境设置)
- [提交 Pull Request](#提交-pull-request)
- [代码规范](#代码规范)
- [测试](#测试)
- [提交信息规范](#提交信息规范)
- [报告 Bug](#报告-bug)
- [提出功能建议](#提出功能建议)

## 行为准则

本项目采用 [Contributor Covenant](https://www.contributor-covenant.org/) 行为准则。参与此项目即表示你同意遵守其条款。

## 如何贡献

### 报告 Bug

如果你发现了 bug，请创建一个 Issue 并包含：
- 清晰的标题和描述
- 复现步骤
- 期望行为与实际行为
- 环境信息（操作系统、Python 版本等）
- 相关日志或截图

### 提出功能建议

如果你想建议新功能，请创建一个 Issue 并说明：
- 功能描述和使用场景
- 为什么这个功能对用户有价值
- 可能的实现方案（如果有的话）

### 提交代码

我们欢迎所有类型的贡献：
- 🐛 Bug 修复
- ✨ 新功能
- 📝 文档改进
- 🎨 代码重构
- 🧪 测试用例
- 🌐 国际化翻译

## 开发环境设置

### 1. Fork 并克隆仓库

```bash
git clone https://github.com/YOUR_USERNAME/image-editor-v3.git
cd image-editor-v3
```

### 2. 创建虚拟环境

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置 API Key（用于测试）

```bash
# 创建配置文件
cp data/config.example.json data/config.json
# 编辑并添加你的 API Key
```

### 5. 运行程序

```bash
python src/main.py
```

## 提交 Pull Request

### 1. 创建特性分支

```bash
git checkout -b feature/amazing-feature
# 或
git checkout -b fix/bug-fix
```

分支命名规范：
- `feature/` - 新功能
- `fix/` - Bug 修复
- `docs/` - 文档更新
- `refactor/` - 代码重构
- `test/` - 测试相关

### 2. 提交更改

```bash
git add .
git commit -m "feat: add amazing feature"
```

### 3. 推送到你的 Fork

```bash
git push origin feature/amazing-feature
```

### 4. 创建 Pull Request

在 GitHub 上创建 Pull Request，并包含：
- 清晰的标题和描述
- 关联的 Issue（如果有）
- 测试截图或演示 GIF
- 任何需要注意的事项

## 代码规范

### Python 代码风格

我们遵循 [PEP 8](https://pep8.org/) 代码风格指南：

```python
# ✅ 好的示例
def generate_story(topic: str, age_group: str) -> str:
    """生成故事文本
    
    Args:
        topic: 故事主题
        age_group: 年龄组
        
    Returns:
        生成的故事文本
    """
    pass

# ❌ 避免的写法
def generateStory(t,a):
    pass
```

### 命名规范

- **函数/方法**: `snake_case`
- **类名**: `PascalCase`
- **常量**: `UPPER_SNAKE_CASE`
- **私有方法**: 前缀 `_`

### 类型注解

所有公共函数都应该包含类型注解：

```python
def calculate_duration(storyboard: List[Storyboard]) -> float:
    """计算分镜总时长"""
    return sum(sb.duration for sb in storyboard)
```

### 文档字符串

使用 Google 风格的文档字符串：

```python
def generate_image(
    prompt: str,
    style: str = "cartoon",
    size: Tuple[int, int] = (512, 512)
) -> Optional[Image.Image]:
    """生成图像
    
    Args:
        prompt: 图像描述提示
        style: 风格类型，默认为 "cartoon"
        size: 图像尺寸 (宽, 高)
        
    Returns:
        生成的图像对象，失败时返回 None
        
    Raises:
        APIError: API 调用失败时抛出
    """
    pass
```

## 测试

### 运行测试

```bash
# 场景关联体检
python _check_scene_align.py

# 契约单测
python _test_scene_contract.py

# 剧集全流程回归
python _test_episode_flow.py

# 网络韧性测试
python _test_network_resilience.py
```

### 添加测试

新功能应该包含相应的测试：
- 单元测试：测试单个函数/方法
- 集成测试：测试模块间交互
- 端到端测试：测试完整用户流程

## 提交信息规范

我们使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Type 类型

- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `style`: 代码格式（不影响代码运行）
- `refactor`: 代码重构
- `perf`: 性能优化
- `test`: 添加或修改测试
- `chore`: 构建过程或辅助工具变动

### 示例

```bash
feat(storyboard): add batch delete selected storyboards
fix(api): sanitize control characters in headers
docs(readme): update installation instructions
refactor(core): extract scene matching logic
test(episode): add multi-episode import test
```

## 代码审查流程

1. 创建 Pull Request
2. 自动 CI 检查通过
3. 至少一位维护者审查
4. 根据反馈进行修改
5. 审查通过后合并

## 发布流程

维护者负责发布新版本：
1. 更新版本号（`src/config/settings.py`）
2. 更新 CHANGELOG
3. 创建 Git Tag
4. 发布 GitHub Release

## 常见问题

### Q: 我的 PR 需要多长时间才能被审查？
A: 通常在 1-2 周内，取决于 PR 的复杂程度。

### Q: 我可以一次提交多个功能吗？
A: 不建议。每个 PR 应该只包含一个功能或修复，这样更容易审查和回滚。

### Q: 如何获取 API Key 用于测试？
A: 请访问 [Agnes AI](https://agnes-ai.com) 注册并获取 API Key。

## 致谢

感谢所有为本项目做出贡献的开发者！

---

**再次感谢你的贡献！** 🎉