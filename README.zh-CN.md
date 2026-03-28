# Desktop MCP（屏幕读取 + 键鼠控制）
## Desktop MCP (Screen Read + Keyboard/Mouse Control)

Desktop MCP 是一个面向 Windows 的 MCP 桌面自动化服务，支持：
- 屏幕读取（截图）
- 键盘与鼠标控制
- 通过 `stdio` 暴露 MCP 工具，便于集成到支持 MCP 的客户端中

## 项目定位
这个项目用于把本地桌面自动化能力封装为 MCP 服务，方便 AI 助手或自动化流程执行桌面交互任务。

支持两种模式：
1. 直接动作执行：`desktop_action` / `desktop_batch`
2. 可选视觉模型辅助：`desktop_observe` / `desktop_goal`

## 主要功能
- 全屏/区域截图（可返回 base64）
- 鼠标控制：移动、点击、右键、中键、按下/抬起、拖拽、滚轮
- 键盘控制：输入、按键、按下/抬起、组合键、常见快捷动作
- 批量动作脚本执行
- MCP 标准 JSON-RPC（`initialize`、`tools/list`、`tools/call`）

## 目录结构
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

## 环境要求
- Windows 10/11
- Python 3.10 及以上
- 有桌面会话（本地或远程桌面）

## 安装步骤
```powershell
git clone https://github.com/Xuan-BOMS/desktop-mcp.git
cd desktop-mcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 启动方式
```powershell
python desktop_mcp.py mcp-serve
```

或：
```powershell
desktop-mcp.cmd mcp-serve
```

## MCP 配置
可参考 `mcp.config.example.json`。

示例：
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

## MCP 工具说明
- `desktop_capture`
  - 功能：截图
  - 常见参数：`include_base64`、`save`、`format`、`quality`、`region`

- `desktop_observe`
  - 功能：基于视觉模型输出屏幕摘要
  - 说明：需要模型网关环境变量

- `desktop_action`
  - 功能：执行单个动作
  - 常见动作：`position`、`move`、`click`、`right_click`、`drag_to`、`scroll`、`type`、`hotkey`、`shortcut`

- `desktop_batch`
  - 功能：按顺序执行一组动作

- `desktop_goal`
  - 功能：模型规划式桌面任务（可选）

## CLI 调试示例
```powershell
python desktop_mcp.py action position --json
python desktop_mcp.py capture --no-base64 --json
python desktop_mcp.py action hotkey --keys "win+r" --json
python desktop_mcp.py action type --text "https://www.google.com/search?q=mcp" --json
python desktop_mcp.py action press --key enter --json
```

## 健康检查
```powershell
# MCP 初始化 + 列出工具
@'
{"jsonrpc":"2.0","id":11,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":12,"method":"tools/list","params":{}}
'@ | python desktop_mcp.py mcp-serve
```

## 模型网关变量（可选）
`desktop_observe` 和 `desktop_goal` 会按优先级读取：

1. `OI_THIRD_PARTY_API_BASE` / `OI_THIRD_PARTY_API_KEY` / `OI_THIRD_PARTY_MODEL`
2. `OPENAI_BASE_URL`（或 `OPENAI_API_BASE`）/ `OPENAI_API_KEY` / `OPENAI_MODEL`

如果模型不可用，观察类工具会返回降级信息，避免整个服务崩溃。

## 安全建议
- 先做低风险动作测试
- 在真实业务环境执行前，先验证坐标与窗口焦点
- 保持 `pyautogui.FAILSAFE = True`（本项目默认已开启）

## 开源协议
MIT，见 `LICENSE`。
