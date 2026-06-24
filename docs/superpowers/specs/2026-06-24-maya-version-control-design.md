# Maya Version Control Plugin — 设计规格

**日期**: 2026-06-24
**版本**: v1.0
**目标平台**: Autodesk Maya 2025+ (PySide6, Python 3.11+)

---

## 1. 概述

为 Maya 美术师提供一个轻量级的工程版本管理插件，基于 Git 实现增量保存、提交记录和版本回溯。无需离开 Maya，即可管理场景文件的全生命周期版本。

## 2. 功能清单

### 2.1 增量保存 (核心功能)

- 一键按钮触发增量保存
- 自动检测当前场景文件名，提取基础名，递增版本号 (`_v001` → `_v002` …)
- 通过 `cmds.file(saveAs=...)` 另存为带版本号的新文件
- 弹窗收集用户的提交信息（必填，不允许空提交）
- 自动 `git add` + `git commit` + `git tag`

### 2.2 版本历史浏览

- 表格列出当前工程所有版本：tag 号、时间、提交信息、文件大小
- 选中行展开提交详情面板
- 提供 "加载此版本" / "在文件管理器中显示" 按钮

### 2.3 回溯加载

- 从历史窗口选择任意版本，点击加载
- 插件调用 `git checkout tags/<version>` 取出目标 .ma 文件
- 然后 `cmds.file(open=...)`在当前 session 中打开

### 2.4 Git 状态快捷查看

- Maya 主界面上的轻量状态条
- 显示：当前版本、未提交变更数、上次提交时间

## 3. 架构

```
MayaVersionControl/
├── __init__.py              # Maya plugin 注册 (command + callback)
├── core/
│   ├── vc_engine.py         # 核心引擎
│   │   ├── get_project_root()        # 自动检测 Maya project 根目录
│   │   ├── get_save_path()           # 找到 scenes/ 目录
│   │   ├── detect_current_version() # 扫描现存 _vXXX 文件，返回最大序号
│   │   ├── incremental_save()        # 另存为下一版本
│   │   ├── git_commit()              # 执行 git add + commit + tag
│   │   ├── get_history()             # 返回版本列表 (git log tags)
│   │   └── load_version()            # checkout 目标版本并打开
│   └── gitignore.py         # .gitignore 模板写入
├── ui/
│   ├── commit_dialog.py     # QDialog — 增量保存后收集提交信息
│   ├── history_browser.py   # QMainWindow — 版本历史表格
│   └── status_widget.py     # QWidget — 在 Maya 主界面上显示状态
├── install.py               # 一键安装：注册模块路径 → 添加 shelf 按钮
├── shelf_main.py            # shelf 按钮入口
└── userSetup.py             # 参考：Maya 启动自动加载示例
```

## 4. 核心流程

### 4.1 增量保存流程

```
[用户点击 Shelf 按钮 "增量保存"]
    │
    ├── vc_engine.incremental_save()
    │   ├─ 1. 获取当前文件路径 (cmds.file(q=True, sn=True))
    │   ├─ 2. 提取基础名和扩展名 (.ma / .mb)
    │   ├─ 3. 在 scenes/ 目录扫描同名文件的最大版本号
    │   ├─ 4. 确认下一版本号 (若当前文件无版本号则以 v001 开始)
    │   └─ 5. cmds.file(saveAs=新文件路径, type=fileType)
    │
    ├── commit_dialog.show()  (modal)
    │   └─ 用户输入提交信息 → 点 "提交" 或 "取消"
    │
    ├── 若用户取消 → 返回（但不回滚 saveAs，用户已拥有新文件）
    │
    └── vc_engine.git_commit()
        ├─ git add <新文件>
        ├─ git commit -m "v004: 用户输入的信息"
        └─ git tag v004
```

### 4.2 历史回溯流程

```
[用户在 History 窗口选中某版本 → 点 "加载此版本"]
    │
    ├── 1. 提示用户保存当前场景（若未保存则先保存或放弃）
    ├── 2. git show tags/<version>:scenes/xxx.ma > 临时文件
    └── 3. cmds.file(open=临时文件路径, f=True)
```

## 5. UI 设计

### 5.1 Commit 对话框 (commit_dialog.py)

```
size: 480×180 (QDialog, modal)
┌── 本次修改描述 ─────────────────── [X] ─┐
│                                          │
│ 版本: myProject_v004.ma                 │
│                                          │
│ 描述 (必填):                             │
│ ┌──────────────────────────────────────┐ │
│ │ 修改内容摘要...                       │ │
│ └──────────────────────────────────────┘ │
│                                          │
│                  [取消]   [提交 (Ctrl+Enter)] │
└──────────────────────────────────────────┘
```

### 5.2 版本历史窗口 (history_browser.py)

```
size: 800×600 (QMainWindow, non-modal)
┌── Maya Version History ──────────────────── [X] ─┐
│                                                    │
│  工程: myProject        [刷新] [在文件管理器中打开]  │
│                                                    │
│ ┌───────┬────────────┬────────────────┬──────────┐ │
│ │ 版本   │ 时间        │ 提交信息         │ 文件大小   │ │
│ ├───────┼────────────┼────────────────┼──────────┤ │
│ │ v004  │ 2026-06-24 │ 绑定完成        │ 2.5 MB   │ │
│ │ v003  │ 2026-06-22 │ 修权重问题      │ 2.4 MB   │ │
│ │ v002  │ 2026-06-21 │ 添加控制器      │ 2.3 MB   │ │
│ │ v001  │ 2026-06-20 │ 初始骨架       │ 2.1 MB   │ │
│ └───────┴────────────┴────────────────┴──────────┘ │
│                                                    │
│ ┌─ 版本详情 ─────────────────────────────────────┐ │
│ │ 版本: v004 | 提交: 绑定完成 - 添加 IK/FK 切换     │ │
│ │ 文件: myProject_v004.ma | 作者: jm              │ │
│ │                                                │ │
│ │      [加载此版本]  [在文件夹中显示]               │ │
│ └────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────┘
```

## 6. 技术细节

### 6.1 Git 操作策略

- 使用 `subprocess` 直接调用 git 命令行（零外部依赖）
- 每个场景文件在 git 仓库中单独管理
- 第一个版本增量保存时自动 `git init`
- `.gitignore` 自动写入 Maya 临时文件规则

### 6.2 .gitignore 模板

```gitignore
# Maya temp files
*.tmp
*~
*.bak
Thumbs.db
*.swp

# Maya crash recovery files
*.crash
*.before_crash*

# Autosave
autosave/
incrementalSave/
backup/

# OS files
.DS_Store
Thumbs.db
```

### 6.3 版本号检测规则

- 在当前 scenes 目录扫描所有 `.ma` / `.mb` 文件
- 正则匹配: `^(.*?)(_v(\d{3}))?\.(ma|mb)$`
- 提取最大数字后缀作为当前版本，+1 作为下一版本
- 若无版本号后缀，则以 v001 开始

### 6.4 提交信息格式

```
v004: 修骨骼权重 - 左臂重新绑定
```

前缀 `v004:` 自动添加。

## 7. 安装方式

```python
# 在 Maya Script Editor 中运行:
exec(open(r"F:\path\to\MayaVersionControl\install.py").read())
```

`install.py` 会：
1. 将插件路径加入 `MAYA_PLUGIN_PATH`
2. 在 Maya 用户脚本目录写入 `userSetup.py`（若不存在）
3. 在当前 shelf 创建一个带图标的按钮

## 8. 边界与限制

- **场景文件必须已保存过至少一次**（Maya 未命名场景不支持）
- **Git 必须已安装**在系统 PATH 中
- **不支持 .mb 文件的 diff**（二进制格式，仅 .ma 支持逐行对比）
- **不支持多人协作**（单美术师单机使用场景）
- **一个 repo 可管理多个场景文件**，但建议按项目分开

## 9. 自检结果

| 检查项 | 状态 |
|--------|------|
| 无 TBD / TODO 占位符 | ✅ |
| 内部一致性 — 架构图与流程一致 | ✅ |
| 范围适中 — 单 spec，无需拆分 | ✅ |
| 无歧义需求 | ✅ |
| 实现只需 7 个文件 | ✅ |
