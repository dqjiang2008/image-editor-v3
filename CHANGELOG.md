# 📝 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 和 [Conventional Commits](https://www.conventionalcommits.org/) 规范。

## [Unreleased]

### 新增
- 批量删除分镜功能：支持勾选多个分镜一次性删除
- HTTP Header 安全清理：自动清理 API Key 和参数中的控制字符，防止 header injection 攻击

### 修复
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