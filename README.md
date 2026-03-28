# Desktop MCP / 桌面 MCP
## Screen Reading + Keyboard/Mouse Control / 屏幕读取与键鼠控制

## English
### Overview
Desktop MCP is a Windows desktop automation MCP server.
It provides screenshot reading and keyboard/mouse control over standard `stdio` MCP JSON-RPC.

### Features
- Screen capture (`desktop_capture`) for full screen or region
- Optional model-based screen understanding (`desktop_observe`)
- Desktop actions (`desktop_action`):
  - Mouse: move, click, right click, middle click, drag, scroll
  - Keyboard: type, press, hotkey, shortcut, key down/up, delete/backspace
- Batch execution (`desktop_batch`)
- Optional goal mode (`desktop_goal`)
- CLI mode for local debugging

### Repository Structure
```text
desktop-mcp/
  desktop_mcp.py
  desktop-mcp.cmd
  requirements.txt
  mcp.config.example.json
  README.md
  README.zh-CN.md
  LICENSE
```

### Requirements
- Windows 10/11
- Python 3.10+
- Active desktop session (local/RDP)

### Installation
```powershell
git clone https://github.com/Xuan-BOMS/desktop-mcp.git
cd desktop-mcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Run (Local Debug)
```powershell
python desktop_mcp.py action position --json
python desktop_mcp.py capture --no-base64 --json
```

### Run as MCP Server
```powershell
python desktop_mcp.py mcp-serve
```

Or:
```powershell
desktop-mcp.cmd mcp-serve
```

### MCP Client Config (stdio)
Use `mcp.config.example.json` as template:

```json
{
  "type": "stdio",
  "command": "C:/Python312/python.exe",
  "args": [
    "C:/path/to/desktop-mcp/desktop_mcp.py",
    "mcp-serve"
  ],
  "env": {}
}
```

### Health Check
```powershell
@'
{"jsonrpc":"2.0","id":11,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":12,"method":"tools/list","params":{}}
'@ | python desktop_mcp.py mcp-serve
```

### Optional Model Gateway Variables
For `desktop_observe` and `desktop_goal`:

Priority:
1. `OI_THIRD_PARTY_API_BASE`, `OI_THIRD_PARTY_API_KEY`, `OI_THIRD_PARTY_MODEL`
2. `OPENAI_BASE_URL` / `OPENAI_API_BASE`, `OPENAI_API_KEY`, `OPENAI_MODEL`

### Safety
- Desktop automation can cause irreversible operations.
- Start with low-risk commands.
- `pyautogui.FAILSAFE = True` is enabled by default.

### License
MIT (see `LICENSE`).

---

## 中文
### 项目简介
Desktop MCP 是一个面向 Windows 的桌面自动化 MCP 服务，支持通过 `stdio` 的 MCP JSON-RPC 调用本地屏幕读取与键鼠控制能力。

### 主要功能
- 屏幕截图：`desktop_capture`（全屏/区域）
- 可选视觉观察：`desktop_observe`
- 单步动作：`desktop_action`
  - 鼠标：移动、点击、右键、中键、拖拽、滚轮
  - 键盘：输入、按键、组合键、快捷动作、按下/抬起、删除
- 批量动作：`desktop_batch`
- 可选规划模式：`desktop_goal`
- 本地 CLI 调试

### 仓库结构
```text
desktop-mcp/
  desktop_mcp.py
  desktop-mcp.cmd
  requirements.txt
  mcp.config.example.json
  README.md
  README.zh-CN.md
  LICENSE
```

### 环境要求
- Windows 10/11
- Python 3.10 及以上
- 可用桌面会话（本地/RDP）

### 安装步骤
```powershell
git clone https://github.com/Xuan-BOMS/desktop-mcp.git
cd desktop-mcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 本地调试
```powershell
python desktop_mcp.py action position --json
python desktop_mcp.py capture --no-base64 --json
```

### 作为 MCP 服务启动
```powershell
python desktop_mcp.py mcp-serve
```

或：
```powershell
desktop-mcp.cmd mcp-serve
```

### MCP 客户端配置（stdio）
参考 `mcp.config.example.json`：

```json
{
  "type": "stdio",
  "command": "C:/Python312/python.exe",
  "args": [
    "C:/path/to/desktop-mcp/desktop_mcp.py",
    "mcp-serve"
  ],
  "env": {}
}
```

### 可用性验证（健康检查）
```powershell
@'
{"jsonrpc":"2.0","id":11,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":12,"method":"tools/list","params":{}}
'@ | python desktop_mcp.py mcp-serve
```

### 可选模型网关变量
`desktop_observe` 与 `desktop_goal` 会按以下优先级读取：

1. `OI_THIRD_PARTY_API_BASE` / `OI_THIRD_PARTY_API_KEY` / `OI_THIRD_PARTY_MODEL`
2. `OPENAI_BASE_URL`（或 `OPENAI_API_BASE`）/ `OPENAI_API_KEY` / `OPENAI_MODEL`

### 安全建议
- 桌面自动化可能触发不可逆操作
- 先做低风险命令验证
- 默认已开启 `pyautogui.FAILSAFE = True`

### 许可证
MIT（见 `LICENSE`）。
