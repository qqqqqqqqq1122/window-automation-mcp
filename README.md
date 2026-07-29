# Window Automation MCP Server

**基于 HWND（窗口句柄）的 Windows 后台无焦点 UI 自动化 MCP 服务器**

让 AI Agent（Claude 等）在 Windows 上执行**后台**、**无侵入**的 UI 自动化操作 — 不抢焦点、不置顶窗口、不影响你当前的工作。

---

## 为什么需要这个工具？

传统的全屏截图自动化方案迫使目标窗口必须置顶，遮挡你的视线，无法并发工作。本服务器通过 **HWND（窗口句柄）** + **分层回退机制** 彻底解决了这个问题：

| 痛点 | 解决方案 |
|------|----------|
| 截图必须置顶窗口 | 后台捕获（WGC / PrintWindow / BitBlt 三级回退） |
| 点击/输入抢占焦点 | UIA InvokePattern / SendMessage（纯后台消息） |
| 多窗口混淆 | 所有 API 以不可变的 `hwnd: int` 作为唯一标识 |
| 最小化窗口被偷偷恢复 | 显式 `minimized` 状态检查 —— 不会偷偷恢复 |

---

## 架构设计

### 图形捕获三级回退（WGC → PrintWindow → BitBlt）
1. **WGC**（Windows Graphics Capture）— 最适合 DirectX / WebView2 / Tauri / Electron 应用
2. **PrintWindow** + `PW_RENDERFULLCONTENT` (0x00000002) — 支持 DirectComposition 窗口
3. **GDI BitBlt** — 最通用的兜底方案

### UI 操作三级回退（UIA → SendMessage → 前台模拟）
1. **UI Automation Patterns** — `InvokePattern`、`ValuePattern`、`ScrollPattern`（纯后台）
2. **SendMessage / PostMessage** — 基于窗口内部相对坐标的消息
3. **前台物理模拟** — 激活窗口后执行物理键鼠操作（兜底）

---

## 工具接口列表（共 12 个 API）

| # | 工具名 | 功能说明 |
|---|--------|----------|
| 1 | `list_windows` | 列出所有可见顶层窗口，支持标题关键词过滤 |
| 2 | `get_window_state` | 获取窗口完整物理/几何状态（dpi、缩放、最小化、焦点） |
| 3 | `capture_window` | 后台截图 → 返回 base64 PNG + 元数据 |
| 4 | `get_ui_tree` | 提取 UIA 控件层次树（支持指定深度） |
| 5 | `find_element` | 按名称、控件类型或 AutomationId 搜索 UI 元素 |
| 6 | `click_element` | 按标识符点击元素（UIA → 消息 → 前台 三级回退） |
| 7 | `click_coordinate` | 在窗口客户区坐标 (x, y) 处点击 |
| 8 | `type_text` | 向元素输入文本（ValuePattern → WM_CHAR → 键盘模拟） |
| 9 | `send_hotkey` | 发送快捷键（如 Ctrl+C、Alt+F4、Esc） |
| 10 | `scroll` | 滚动元素/窗口（ScrollPattern → WM_MOUSEWHEEL） |
| 11 | `wait_for_ui_change` | 轮询 UIA 树变化直到变化或超时（替代固定 sleep） |
| 12 | `activate` | 显式激活并将窗口前置（还原最小化窗口） |

---

## 安装

### 环境要求
- **Windows 10 / 11**
- **Python 3.10+**

### 快速安装

```bash
# 克隆仓库
git clone https://github.com/laoer2/window-automation-mcp.git
cd window-automation-mcp

# 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\pip install mcp uiautomation pywin32 Pillow comtypes pywinauto
```

### 注册到 Claude Code

在 `%USERPROFILE%\.claude\settings.json` 中添加：

```json
{
  "mcpServers": {
    "window-automation": {
      "command": "<绝对路径>\\.venv\\Scripts\\python.exe",
      "args": ["<绝对路径>\\window_automation_mcp.py"]
    }
  }
}
```

### 注册到 Claude Desktop

在 `%APPDATA%\Claude\claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "window-automation": {
      "command": "python",
      "args": ["<绝对路径>\\window_automation_mcp.py"]
    }
  }
}
```

---

## 使用示例

### 列出所有窗口
```
工具: list_windows
参数: { "title_keyword": "记事本" }
→ 返回 JSON 数组，包含每个窗口的 hwnd、pid、title、bounds
```

### 后台截取窗口画面
```
工具: capture_window
参数: { "hwnd": 123456 }
→ 返回 base64 PNG 图片 + 元数据（捕获方法、尺寸、dpi）
```

### 按名称点击按钮
```
工具: click_element
参数: { "hwnd": 123456, "element_id_or_name": "保存" }
→ 优先使用 UIA InvokePattern 后台点击，失败时回退到坐标消息
```

### 通过 UIA 输入文本
```
工具: type_text
参数: { "hwnd": 123456, "element_id_or_name": "Edit", "text": "你好世界" }
→ 使用 UIA ValuePattern.SetValue 实现纯后台文本注入
```

---

## 设计原则

1. **HWND 作为绝对标识** — 不依赖标题字符串；标题会变，HWND（在会话内）不会变
2. **最小化窗口保护** — 操作前检查 `is_minimized`，最小化状态下拒绝执行并给出明确提示
3. **回退路径透明** — 每次截图都报告实际使用的方法（`wgc` / `printwindow` / `bitblt`）
4. **优雅降级** — UIA 失败 → 尝试 SendMessage → 再失败 → 前台模拟兜底

---

## 项目结构

```
window-automation-mcp/
├── window_automation_mcp.py   # 主 MCP 服务器（12 个工具）
├── test_server.py             # 综合功能测试套件
├── test_edge_cases.py         # 边界情况与鲁棒性测试
├── test_e2e.py                # 端到端测试（打开记事本、截图、打字、快捷键）
├── README.md                  # 本文件
├── .gitignore
└── requirements.txt           # （用 pip freeze 生成）
```

---

## 测试

项目包含三套测试：

```bash
# 基础功能测试（覆盖全部 12 个工具）
python test_server.py

# 边界与鲁棒性测试（并发调用、边界值、Unicode）
python test_edge_cases.py

# 端到端测试（自动打开记事本 → 截图 → 输入文字 → 快捷键操作）
python test_e2e.py
```

---

## 依赖库

- `mcp` — Model Context Protocol SDK
- `uiautomation` — UI Automation 封装
- `pywin32` — Windows API 绑定
- `Pillow` — 图像处理
- `comtypes` — COM 类型库
- `pywinauto` — Windows 自动化工具包

全部可通过 pip 在 Python 3.10+ 虚拟环境中一键安装。

---

## License

MIT

---

## 作者

Window Automation MCP — 专为需要在 Windows GUI 应用程序中执行后台操作的 AI Agent 打造。

---

## 修订历史

- **2026-07-29 17:05:00** — v2.0.0：首次公开发布，包含 12 个 MCP 工具、三级捕获回退链、三级输入回退链、完整测试套件。
