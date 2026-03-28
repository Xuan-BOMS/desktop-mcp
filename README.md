# Desktop MCP (Screen Read + Keyboard/Mouse Control)
## 桌面 MCP（屏幕读取 + 键鼠控制）

Desktop MCP is a Windows desktop automation MCP server.
It can capture the screen, run keyboard/mouse actions, and expose these features through `stdio` MCP tools.

Desktop MCP 是一个 Windows 桌面自动化 MCP 服务。
它支持屏幕截图、键盘鼠标控制，并通过 `stdio` MCP 工具对外提供能力。

## Features
- Screen capture (full screen or region)
- Optional model-based screen observation
- Keyboard and mouse control: move, click, right click, drag, scroll, type, hotkey, shortcut, etc.
- Batch action execution
- MCP JSON-RPC over stdio (`initialize`, `tools/list`, `tools/call`)
- CLI mode for local debugging

## 功能特性
- 屏幕截图（全屏/区域）
- 可选的模型视觉观察
- 键鼠控制：移动、点击、右键、拖拽、滚轮、输入、组合键、快捷操作等
- 批量动作执行
- 基于 stdio 的 MCP JSON-RPC（`initialize`、`tools/list`、`tools/call`）
- 支持 CLI 方式本地调试

## Project Structure
```text
desktop-mcp/
  desktop_mcp.py             # Main MCP server
  desktop-mcp.cmd            # Windows launcher
  requirements.txt           # Python dependencies
  mcp.config.example.json    # MCP stdio config example
  README.md                  # English + Chinese quick doc
  README.zh-CN.md            # Chinese detailed doc
  LICENSE                    # MIT license
```

## Requirements
- Windows 10/11
- Python 3.10+
- GUI session available (RDP/local desktop)

## Installation
```powershell
git clone https://github.com/Xuan-BOMS/desktop-mcp.git
cd desktop-mcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run as MCP Server
```powershell
python desktop_mcp.py mcp-serve
```

Or:
```powershell
desktop-mcp.cmd mcp-serve
```

## MCP Config Example
Use `mcp.config.example.json` as a template.

Typical Windows stdio config:
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

## Available MCP Tools
- `desktop_capture`: Screenshot capture
- `desktop_observe`: Model-based visual summary (requires gateway model env)
- `desktop_action`: Single keyboard/mouse action
- `desktop_batch`: Multiple actions in sequence
- `desktop_goal`: Optional model-planned GUI task

## CLI Quick Examples
```powershell
python desktop_mcp.py action position --json
python desktop_mcp.py capture --no-base64 --json
python desktop_mcp.py action hotkey --keys "win+r" --json
python desktop_mcp.py action type --text "https://www.google.com/search?q=mcp" --json
python desktop_mcp.py action press --key enter --json
```

## Health Check
```powershell
# MCP handshake + tool list
@'
{"jsonrpc":"2.0","id":11,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":12,"method":"tools/list","params":{}}
'@ | python desktop_mcp.py mcp-serve
```

## Optional Model Gateway Variables
`desktop_observe` and `desktop_goal` need model gateway env vars.

Priority:
1. `OI_THIRD_PARTY_API_BASE`, `OI_THIRD_PARTY_API_KEY`, `OI_THIRD_PARTY_MODEL`
2. `OPENAI_BASE_URL` / `OPENAI_API_BASE`, `OPENAI_API_KEY`, `OPENAI_MODEL`

If model is unavailable, observation returns fallback result instead of crashing the whole server.

## Safety Notes
- Desktop automation can trigger irreversible actions.
- Use low-risk tests first.
- Keep `pyautogui.FAILSAFE = True` (already enabled).

## License
MIT. See `LICENSE`.
