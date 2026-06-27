# MayaVC — Maya 版本控制插件

Maya 文件增量保存 + 版本历史管理，零依赖，开箱即用。
<img width="805" height="827" alt="image" src="https://github.com/user-attachments/assets/65fb2be4-356d-45ef-8d13-043f80053872" />


## 安装

1. 把整个 `MayaVersionControl` 文件夹放到任意位置（比如 `D:\MayaScripts\`）
2. 在 Maya 里打开 Script Editor（脚本编辑器），运行：

```python
exec(open(r"install.py的文件保存路径").read())
```

3. 重启 Maya 或刷新 Shelf，会看到 **VC History** 按钮，点击打开版本历史面板（所有功能都在面板里）

## 基本用法

### 增量保存
打开 **VC History** 面板，点击 **Incremental Save**：
1. 弹出保存对话框，默认文件名为当前文件 + 下一版本号
2. 填写描述（必填），点击 Commit
3. 文件保存为 `{文件名}_v{版本号}.{ma/mb}`

### 查看历史
点击 **VC History** 打开历史面板。

### 加载旧版本
- 双击表格行，或右键 → **Open**
- 弹出确认对话框，选择是否先保存当前工作

### 追加提交（Save w/ Commit）
在当前版本上追加描述，不创建新版本号。适合频繁保存同一版本。

### 重命名
右键 → **Rename**，输入新名称（不含扩展名），文件和记录同步更新。

### 改名后的文件追踪
插件通过 NTFS 备用数据流为每个文件写入 UUID。即使在资源管理器里改名，打开历史面板时也会自动识别和同步。

## 面板功能

| 区域 | 功能 |
|------|------|
| 项目按钮 | 切换/浏览项目文件夹 |
| 全部展开/收起 | 切换多提交消息的显示状态 |
| 只看最新 | 每个 base 只显示最高版本 |
| 只看当前 | 只显示与当前文件同 base 的版本 |
| 表格 | Name / Version / Date / Message 四列，点击列头排序 |
| Refresh | 手动刷新 |
| ？ 按钮 | Clear Cache（重载插件） / Performance（性能监控） |
| Incremental Save | 增量保存为新版本 |
| Save w/ Commit | 在当前版本追加提交 |
| Delete This Version | 删除选中版本（文件 + 记录） |
| Edit | 进入编辑模式，支持批量删除 |

### 右键菜单
- **Open** — 加载选中版本
- **Rename** — 重命名文件
- **Show in Folder** — 在资源管理器中定位文件

### 排序
- **Name**：按文件名排序
- **Version**：按版本号排序（Name 分组不变，组内 Version 切换升/降序）
- **Date / Message**：按日期或描述排序

底色区分仅在 Name 或 Version 排序时显示。

## 技术说明

- 版本记录存储在 `项目/scenes/.mayavc/versions.json`
- 文件追踪 UUID 存储在 NTFS 备用数据流中（仅 Windows NTFS 有效）
- 零外部依赖，仅使用 Maya 内置模块和 Python 标准库
- 支持 Maya 2025+ (PySide6 / Python 3.11+)
