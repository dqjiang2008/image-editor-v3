# 🎉 AI Video Production Studio v2.4.2

## 📦 下载

| 平台 | 文件 | 大小 |
|------|------|------|
| Windows | `image-editor-v3-v2.4.2.zip` | ~XX MB |
| 源码 | Source code (zip) | - |

## ✨ 新功能

### 🎯 批量删除分镜
- 支持勾选多个分镜一次性删除
- 智能确认对话框，显示将删除的分镜数量
- 删除后自动重新编号

### 🔒 HTTP Header 安全增强
- 自动清理 API Key 和参数中的控制字符
- 防止 "Forbidden control character detected" 错误
- 提升 API 请求安全性

## 🐛 Bug 修复

- 修复分镜删除不完整的问题（只删除当前行而非所有勾选项）
- 修复 HTTP 请求头包含控制字符导致的请求失败

## 📋 系统要求

- **操作系统**: Windows 10/11, macOS 10.15+, Linux
- **Python**: 3.10 或更高版本
- **内存**: 至少 4GB RAM
- **网络**: 稳定的网络连接（用于 AI API 调用）

## 🚀 快速开始

### 方式一：使用预打包版本（Windows）

1. 下载 `image-editor-v3-v2.4.2.zip`
2. 解压到任意目录
3. 双击 `run.bat` 启动程序
4. 配置你的 API Key

### 方式二：从源码运行

```bash
# 1. 克隆仓库
git clone https://github.com/YOUR_USERNAME/image-editor-v3.git
cd image-editor-v3

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行程序
python src/main.py
```

## ⚙️ 配置 API Key

程序支持 3 种配置方式：

1. **图形界面**（推荐）：启动后点击 ⚙️ 系统设置
2. **环境变量**：`set AGNES_API_KEY=your_key && python src/main.py`
3. **配置文件**：编辑 `data/config.json`

## 📝 完整更新日志

查看 [CHANGELOG.md](CHANGELOG.md) 了解所有版本变更。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！详见 [CONTRIBUTING.md](CONTRIBUTING.md)

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 🙏 致谢

感谢所有贡献者和使用者！

---

**遇到问题？** 请提交 [Issue](https://github.com/YOUR_USERNAME/image-editor-v3/issues) 或查看 [文档](README.md)