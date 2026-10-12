# 📝 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 和 [Conventional Commits](https://www.conventionalcommits.org/) 规范。

## [Unreleased]

## [v2.7.0] - 2026-10-12

### 修复
- **修复剧集模式全局资产被覆盖丢失的严重 bug**：切换剧集或保存项目时，当前集子集会无条件覆盖全局资产库，导致"选全部"只显示当前集的少量角色/场景
  - 根因：`save_project()` 中 `global_characters = list(characters)` 无条件执行，`characters` 在剧集模式下只是当前集子集
  - 修复：所有写入路径改为保存 `global_*` 到磁盘，仅在 `global_*` 为空时才从 `characters` 补全
  - 资产列表数据源从 `project.characters` 改为 `project.global_characters`（带 fallback）
  - `producer.py` 分镜提取完成后不再无条件用当前集子集覆盖 `global_*`

### 优化
- **资产库自动恢复**：加载项目时，如果资产库数据比 `global_*` 更完整则自动恢复，修复旧 bug 导致的磁盘数据损坏
- **全局资产架构强化**：确立"所有资产生成都写入全局，剧集只引用全局"的架构原则
  - `global_characters`/`global_scenes`/`global_props` = 全剧资产库（提取/生成时写入）
  - `characters`/`scenes`/`props` = 当前集视图（切集时按引用名筛选恢复）
  - `episode_data` = 每集资产引用快照
- 图像生成完成后同步 `image_path` 到 `global_*`，确保造型图/场景图在全局资产中可见

## [v2.6.0] - 2026-10-12

### 新增
- **💬 对话测试页**：新增独立 AI 对话页面，方便直接测试文本模型
  - 支持流式输出（SSE streaming）和非流式两种模式
  - 支持温度、Max Tokens 参数调节
  - 支持深度推理（reasoning_content）折叠展示
  - 消息气泡式渲染，简易 Markdown 格式化
- **🎨 画图测试页**：新增独立 AI 图像生成页面，方便直接测试图像模型
  - 支持文生图和图生图两种模式切换
  - 图生图模式支持选择本地参考图文件
  - 支持尺寸（1024×1024 / 1792×1024 / 1024×1792）和数量（1-4）设置
  - 生成结果以卡片网格展示，支持保存和删除
- 导航面板新增对话和画图两个入口，置于项目页前面，便于快速测试
- **智能时长推断**：根据故事文本实际内容量（字数）智能推断目标时长和分镜数，不再仅依赖固定档位映射
  - 用户选择的长度档位作为下限保障（尊重用户意图）
  - 故事字数越多，自动推高目标时长和分镜数
  - 分镜生成 prompt 从"参考上限"改为"目标值"，引导 AI 生成足够数量的分镜
  - 生成后校验：如果 AI 实际时长低于目标 50%，输出警告提示
- **🤖 AI 生成分镜按钮**：分镜页左侧新增"AI生成分镜"按钮
  - 非剧集模式下也可一键重新生成分镜脚本
  - 按故事字数智能推断目标时长和分镜数，确认后清空旧分镜重新生成
  - 生成完成可直接跳转生成分镜图片

### 优化
- 批量删除分镜功能：支持勾选多个分镜一次性删除
- HTTP Header 安全清理：自动清理 API Key 和参数中的控制字符，防止 header injection 攻击

### 修复
- **修复故事长度档位被覆盖的 bug**：提取分镜后 AI 实际生成时长可能短于用户选择，之前会用实际时长反推并覆盖 `project.length`，导致返回故事页时长度显示错误。现在保留用户原始选择，仅在日志中提示偏差
- 修复分镜删除不完整的问题（只删除当前行而非所有勾选项）
- 修复 HTTP 请求头包含控制字符导致的 "Forbidden control character" 错误

## [v2.4.2] - 2026-09-25

### 优化
- **数据源统一**：角色/道具/场景数据只从项目目录 JSON 文件加载，消除重复显示问题
- **global_* 同步优化**：保存前统一同步，简化内存同步逻辑
- **保存逻辑简化**：始终写入项目目录 JSON 文件，确保数据完整

## [v2.4.1] - 2026-09-25

### 新增
- **分镜动态调整**：AI 自主决定分镜数量和时长，不再硬性约束
- **无角色故事适配**：支持教学类无角色视频生成

### 修复
- 修复 `_infer_length_from_duration` 调用导致的 `AttributeError`
- 日志措辞优化：由"目标分镜数"改为"参考分镜上限"

## [v2.4] - 2026-09-25

### 新增
- **auto 视频生成模式**：按项目类型自动选择 text 或 reference 模式
- **帧图自动回填机制**：加载项目时自动关联已生成的帧图片
- **故事/剧集模式约定**：
  - 故事模式：分镜直接生成（text），不需要角色
  - 剧集模式：必须图生图（reference），需要角色

### 优化
- 分镜数量和时长由 AI 根据故事内容自行决定
- 处理故事后自动更新长度档位
- 清理旧版硬编码角色残留数据
- 故事页按钮分组，各带模式说明提示

## [v2.3.1] - 2026-09-24

### 修复
- 修复 `Prop`/`Scene` 缺少 `seed` 字段导致加载项目崩溃
- `requirements.txt` 补充 `pydantic` 依赖

### 优化
- 角色/道具/场景列表勾选框改用高对比度自定义渲染
- 解决部分显示器看不到勾选标记的问题

## [v2.3] - 2026-09-14

### 安全
- 优化 API Key 安全存储，支持 3 种配置方式

### 优化
- 优化图像/视频生成重试逻辑，提升生成成功率
- 网络错误退避重试机制

## [v2.2] - 2026-09-01

### 新增
- 支持 20 分钟长篇动画
- 场景数扩展到 40 个
- 分镜时长支持 5 秒
- 故事长度支持 5/10/20 分钟

## [v2.1] - 2026-09-01

### 修复
- 修复 API 认证问题
- 修复模板保存/加载
- 修复项目与模板绑定
- 修复删除功能
- 修复日志显示

## [v2.0] - 2026-08-31

### 新增
- 模板系统
- 向导模式
- 项目管理功能

## [v1.0] - 2026-08-29

### 新增
- 初始版本
- 基础图像生成功能

---

## 版本说明

### 语义化版本说明

- **主版本号 (Major)**：不兼容的 API 修改
- **次版本号 (Minor)**：向下兼容的功能性新增
- **修订号 (Patch)**：向下兼容的问题修正

### 标签说明

- `新增` - 新功能
- `修复` - Bug 修复
- `优化` - 性能或体验优化
- `安全` - 安全相关更新
- `破坏性变更` - 不兼容的修改（如有会单独标注）

[Unreleased]: https://github.com/your-repo/image-editor-v3/compare/v2.4.2...HEAD
[v2.4.2]: https://github.com/your-repo/image-editor-v3/compare/v2.4.1...v2.4.2
[v2.4.1]: https://github.com/your-repo/image-editor-v3/compare/v2.4...v2.4.1
[v2.4]: https://github.com/your-repo/image-editor-v3/compare/v2.3.1...v2.4
[v2.3.1]: https://github.com/your-repo/image-editor-v3/compare/v2.3...v2.3.1
[v2.3]: https://github.com/your-repo/image-editor-v3/compare/v2.2...v2.3
[v2.2]: https://github.com/your-repo/image-editor-v3/compare/v2.1...v2.2
[v2.1]: https://github.com/your-repo/image-editor-v3/compare/v2.0...v2.1
[v2.0]: https://github.com/your-repo/image-editor-v3/compare/v1.0...v2.0
[v1.0]: https://github.com/your-repo/image-editor-v3/releases/tag/v1.0