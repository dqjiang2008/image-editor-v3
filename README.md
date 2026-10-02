# 🎬 AI Video Production Studio

<div align="center">

**基于 AI 的多模态动画片自动生成系统**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](https://github.com/)
[![Release](https://img.shields.io/github/v/release/YOUR_USERNAME/image-editor-v3)](https://github.com/YOUR_USERNAME/image-editor-v3/releases)

[English](#-english) | [中文文档](#-功能特性)

</div>

---

## 📸 界面预览

> 💡 **提示**：请将实际截图保存到 `screenshots/` 目录，然后替换下方的占位图片路径。

<div align="center">

### 主界面
![主界面](screenshots/main-interface.png)

### 故事生成
![故事生成](screenshots/story-generation.png)

### 角色管理
![角色管理](screenshots/character-management.png)

### 分镜编辑
![分镜编辑](screenshots/storyboard-editing.png)

**[查看更多截图 →](SCREENSHOTS.md)**

</div>

---

## 🌟 功能特性

一款面向内容创作者的 AI 辅助动画制作工具，通过向导式流程帮助用户快速生成完整的动画片。

### ✨ 核心亮点

- **🤖 AI 驱动全流程**：故事生成 → 角色设计 → 场景构建 → 分镜制作 → 视频合成
- **🎯 零门槛上手**：无需动画制作经验，输入主题即可自动生成完整动画
- **🎨 多模态融合**：文本、图像、视频三种 AI 能力无缝集成
- **📚 资产库管理**：角色、场景、道具统一管理，跨项目共享复用
- **🔄 断点续作**：网络抖动不丢失进度，支持批量生成与自动重试
- **🎬 剧集模式**：支持多集长篇动画制作，逐集追加，互不干扰

### 🎯 适用场景

| 场景 | 说明 |
|------|------|
| 📱 自媒体创作 | 快速生成短视频内容 |
| 🎓 教育教学 | 制作教学动画、课件素材 |
| 📺 动画制作 | 独立动画制作人快速原型 |
| 🎮 游戏开发 | 生成剧情动画、过场视频 |

---

## 🚀 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行程序

```bash
python src/main.py
```

Windows 用户也可双击 `run.bat` 启动。

### 配置 API Key

程序支持 3 种配置方式（任选其一）：

#### 方式一：图形界面（推荐）
1. 启动程序后点击工具栏 **⚙️ 系统设置**
2. 在 API Key 输入框填写你的密钥
3. 点击 **💾 保存**（自动保存到 `data/config.json`）

#### 方式二：环境变量
```bash
# Windows
set AGNES_API_KEY=your_key_here && python src/main.py

# Linux/Mac
export AGNES_API_KEY=your_key_here && python src/main.py
```

#### 方式三：配置文件
直接编辑 `data/config.json`，修改 `api_key` 字段。

---

## 📖 使用指南

### 五种制作模式

#### 1️⃣ 向导模式（推荐新手）
点击工具栏 **🧙 向导模式** → 选择 **✨ 模板自动生成** 或 **✏️ 手动逐步完成**

#### 2️⃣ 故事模式（单集/短视频）
**📖 故事** 页 → 生成/导入故事 → **🔧 处理故事** → 一键提取角色、场景、分镜

#### 3️⃣ 剧集模式（多集长篇）
适合剧本类长篇动画（如《逆天布衣》1-10 集）：
1. **📦 解析全剧资产** → 导入资产总表 + 分集正文
2. **📚 解析集数分镜** → 逐集追加，自动去重
3. 选择节 → 生成分镜 → 合并视频

#### 4️⃣ 模板生成（快速体验）
输入主题 → AI 自动生成完整项目（故事+角色+场景+分镜）

#### 5️⃣ 单独导入资产
各资产页独立导入按钮，支持按角色/道具/场景分批补充

### 视频生成模式

分镜页左上角可切换视频生成方式：

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **auto**（默认） | 自动按项目类型选择 | 推荐使用 |
| **text** | 纯文本生成，无需首帧图 | 故事模式、教学讲解 |
| **reference** | 参考图生成，保持角色一致性 | 剧集模式、角色动画 |

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+N | 新建项目 |
| Ctrl+O | 打开项目 |
| Ctrl+S | 保存项目 |
| Ctrl+Z | 撤销 |
| Ctrl+Y | 重做 |
| Space | 播放/暂停 |
| Esc | 取消操作 |

---

## 🏗️ 技术架构

### 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    UI 层 (PyQt6)                        │
│  故事编辑 │ 角色管理 │ 场景管理 │ 分镜编辑 │ 视频合成    │
├─────────────────────────────────────────────────────────┤
│                  业务逻辑层                              │
│  AnimationProducer (制作管理器)                          │
│  AgnesAIClient (AI 客户端)                               │
├─────────────────────────────────────────────────────────┤
│                    数据层                                │
│  Project (JSON) │ Template (JSON) │ Config (JSON)       │
└─────────────────────────────────────────────────────────┘
```

### 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| GUI | PyQt6 | 跨平台桌面应用 |
| 异步网络 | aiohttp | 异步 HTTP 客户端 |
| AI 服务 | Agnes AI API | 文本/图像/视频生成 |
| 数据存储 | JSON + 文件系统 | 项目配置、模板、媒体文件 |
| 图像处理 | Pillow | 图片缩放、格式转换 |

### 项目结构

```
image-editor-v3/
├── src/
│   ├── config/              # 配置模块
│   ├── core/                # 核心业务逻辑
│   ├── gui/                 # 图形界面
│   ├── models/              # 数据模型
│   ├── services/            # AI 服务
│   └── main.py              # 主入口
├── data/                    # 数据目录（运行时生成）
│   ├── config.json          # 用户配置
│   ├── projects/            # 项目文件
│   └── logs/                # 日志文件
├── requirements.txt         # Python 依赖
└── README.md                # 本文档
```

---

## 📦 数据模型

### 核心实体关系

```
Project ──┬── Character (角色)
          ├── Scene (场景)
          ├── Prop (道具)
          └── Storyboard (分镜)
```

### 角色类型

| 类型 | 说明 | 示例 |
|------|------|------|
| 人类 | 主角/配角 | 陈炎、老叫花子 |
| 动物/妖兽 | 非人类角色 | 黑驴、妖兽军团 |
| 群体 | 群体角色 | 村民、士兵 |
| 能量体 | 特殊存在 | 银色电网、天道意志 |

---

## 🔧 开发指南

### 依赖要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| PyQt6 | ≥6.6.0 | GUI 框架 |
| aiohttp | ≥3.9.0 | 异步 HTTP 客户端 |
| Pillow | ≥10.0.0 | 图像处理 |
| pydantic | ≥2.0.0 | 数据校验 |

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

### 冒烟测试

```bash
python _smoke_story_import.py   # 资产导入流程
python _e2e_verify.py           # 端到端验证
```

---

## 🌐 English

### Overview

An AI-powered animation production tool that helps content creators generate complete animations through a wizard-style workflow.

### Features

- **AI-Driven Workflow**: Story → Characters → Scenes → Storyboard → Video
- **Zero Experience Required**: Input a topic, get a complete animation
- **Multi-Modal Integration**: Text, image, and video AI capabilities
- **Asset Library Management**: Cross-project sharing of characters, scenes, and props
- **Resilient Generation**: Network error recovery with automatic retry
- **Episode Mode**: Multi-episode animation support with incremental imports

### Quick Start

```bash
pip install -r requirements.txt
python src/main.py
```

### API Configuration

Set your API key via:
1. **GUI**: Settings dialog (recommended)
2. **Environment variable**: `AGNES_API_KEY=your_key`
3. **Config file**: Edit `data/config.json`

---

## 📝 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v2.4.2 | 2026-09-25 | 数据源统一、global_*同步优化、AI 自动决定分镜数量 |
| v2.4.1 | 2026-09-25 | 分镜动态调整、无角色故事适配、Bug 修复 |
| v2.4 | 2026-09-25 | 故事/剧集模式视频生成约定、auto 模式、帧图自动回填 |
| v2.3.1 | 2026-09-24 | seed 字段修复、依赖补充、勾选框高对比度渲染 |
| v2.3 | 2026-09-14 | API Key 安全存储、重试逻辑优化 |
| v2.2 | 2026-09-01 | 20 分钟长篇动画支持 |
| v2.1 | 2026-09-01 | API 认证修复、模板系统优化 |
| v2.0 | 2026-08-31 | 模板系统、向导模式、项目管理 |
| v1.0 | 2026-08-29 | 初始版本 |

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- [Agnes AI](https://agnes-ai.com) - 提供强大的 AI API 服务
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) - 优秀的跨平台 GUI 框架
- 所有贡献者和使用者

---

<div align="center">

**Made with ❤️ by AI Assistant**

[⭐ Star this repo](https://github.com/YOUR_USERNAME/image-editor-v3) | [🐛 Report Bug](https://github.com/YOUR_USERNAME/image-editor-v3/issues) | [💡 Request Feature](https://github.com/YOUR_USERNAME/image-editor-v3/issues)

</div>