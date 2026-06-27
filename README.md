# MayaVC — Maya 版本控制插件

[![Version](https://img.shields.io/badge/version-v1.0.3-blue)](https://github.com/JamyM0819/MayaVersionControl/releases)

Maya 文件增量保存 + 版本历史管理，零依赖，开箱即用。

## 安装

**拖 `install.py` 进 Maya 窗口** — 自动安装到当前 Shelf 标签页，完成。

或 Script Editor 运行：
```python
exec(open(r"F:\path\to\install.py", encoding="utf-8").read())
```
弹出文件夹选择 → 选 `MayaVersionControl` 文件夹 → 完成。

## 基本用法

| 操作 | 说明 |
|------|------|
| **Incremental Save** | 保存为新版本号（v001 → v002 → ...） |
| **Save w/ Commit** | 在当前版本追加提交，不增加版本号 |
| **双击表格行** | 加载旧版本（弹出保存确认） |
| **点击消息单元格** | 查看完整提交消息 |
| **右键菜单** | Open / Rename / Show in Folder / Delete / Edit Description |

## 面板功能

- **项目切换**：按钮 + 📂 浏览
- **全部展开/收起**：切换多提交消息显示
- **只看最新**：每个 base 只显示最高版本
- **只看当前**：只显示与当前文件同 base 的版本
- **无极滚动** + **网格线加重**
- 四列排序：Name / Version / Date / Message

## 文件追踪

插件通过 NTFS 备用数据流写入 UUID，即使资源管理器改名也能自动识别。

## 技术说明

- 版本数据：`项目/scenes/.mayavc/versions.json`
- UUID 存储：NTFS alternate data stream（仅 Windows NTFS）
- 零外部依赖，仅 Maya 内置模块 + Python 标准库
- Maya 2025+ (PySide6 / Python 3.11+)
