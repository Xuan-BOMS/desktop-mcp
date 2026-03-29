# Desktop MCP

基于 Windows 的桌面自动化 MCP 服务，提供截图观察、键鼠控制、本地 OCR，以及面向图像模型的截图 handoff 桥接。

## 本次同步内容

当前仓库已经与本机正在使用的新版 `desktop_mcp.py` 对齐，并补齐了针对 `CC Switch` 场景的混合方案：

- 文本规划默认走主网关，失败时自动回退到 `CC Switch`
- 截图优先走本地 OCR
- OCR 不足时，生成 handoff 截图包，交给当前对话里的模型继续看图
- 看图完成后可自动或手动清理临时截图
- 文本模型调用在主网关失败时，会自动回退到可配置的本机 `CC Switch`

这个方案的目标不是强行让代理支持图片，而是把“文字规划”和“图片读取”拆开，各走最稳定的链路。

## 功能

- 屏幕截图
- 鼠标操作
  - 移动、点击、双击、右键、中键、拖拽、滚轮
- 键盘操作
  - 输入、按键、组合键、按下、抬起
- MCP 工具
  - `desktop_observe`
  - `desktop_action`
  - `desktop_goal`
  - `desktop_cleanup`
- 本地观察模式
  - `--local-only`
  - `--local-ocr`
  - `--handoff`
- `goal` 模式下的 OCR 优先执行与 handoff 回退

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
- Python 3.10+
- 可用桌面会话

## 安装

```powershell
git clone https://github.com/Xuan-BOMS/desktop-mcp.git
cd desktop-mcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 启动

本地 CLI 调试：

```powershell
python desktop_mcp.py action position --json
python desktop_mcp.py observe --local-only --json
```

作为 MCP 服务运行：

```powershell
python desktop_mcp.py mcp-serve
```

或：

```powershell
desktop-mcp.cmd mcp-serve
```

## MCP 客户端配置

可直接参考 `mcp.config.example.json`。

基础示例：

```json
{
  "type": "stdio",
  "command": "C:/Python312/python.exe",
  "args": [
    "C:/path/to/desktop-mcp/desktop_mcp.py",
    "mcp-serve"
  ],
  "env": {
    "OI_THIRD_PARTY_API_BASE": "https://your-primary-gateway/v1",
    "OI_THIRD_PARTY_API_KEY": "sk-your-primary-key",
    "OI_THIRD_PARTY_MODEL": "gpt-5.3-codex",
    "DESKTOP_MCP_CC_SWITCH_ENABLED": "true",
    "DESKTOP_MCP_CC_SWITCH_BASE_URL": "http://127.0.0.1:15721/v1",
    "DESKTOP_MCP_CC_SWITCH_API_KEY": "sk-local-test",
    "DESKTOP_MCP_CC_SWITCH_MODEL": "gpt-5.3-codex"
  }
}
```

## CC Switch 适配说明

新版 `desktop_mcp.py` 现在把文本模型链路拆成两层：

1. 主网关
   - `OI_THIRD_PARTY_API_BASE`、`OI_THIRD_PARTY_API_KEY`、`OI_THIRD_PARTY_MODEL`
   - 如果没配，再回落到通用 OpenAI 兼容环境变量
2. `CC Switch` 回退网关
   - `DESKTOP_MCP_CC_SWITCH_BASE_URL`
   - `DESKTOP_MCP_CC_SWITCH_API_KEY`
   - `DESKTOP_MCP_CC_SWITCH_MODEL`

对于本地 `CC Switch`，推荐这样配：

```powershell
$env:DESKTOP_MCP_CC_SWITCH_ENABLED = "true"
$env:DESKTOP_MCP_CC_SWITCH_BASE_URL = "http://127.0.0.1:15721/v1"
$env:DESKTOP_MCP_CC_SWITCH_API_KEY = "sk-local-test"
$env:DESKTOP_MCP_CC_SWITCH_MODEL = "gpt-5.3-codex"
```

说明：

- `DESKTOP_MCP_CC_SWITCH_BASE_URL` 就是给他人部署时可直接修改的主机/端口入口
- 文本调用如果在主网关遇到 `403/1010/5xx/超时/连接失败`，会自动切到 `CC Switch`
- 图片型观察不会自动切到 `CC Switch`，截图依然优先走本地 OCR 或 handoff
- 如果你本地已经在用 `ANTHROPIC_*` 接 `CC Switch`，当前实现也会兼容读取

## 三种观察方式

### 1. `local-only`

只截图，不做 OCR，也不调模型：

```powershell
python desktop_mcp.py observe --local-only --json
```

适合先确认截图链路正常。

### 2. `local-ocr`

截图后直接在本机使用 `rapidocr_onnxruntime` 识别：

```powershell
python desktop_mcp.py observe --local-ocr --json
```

如果希望读取后自动删除截图：

```powershell
python desktop_mcp.py observe --local-ocr --delete-after-read --json
```

适合按钮、输入框、标题、列表等以文字为主的界面。

### 3. `handoff`

当 OCR 不足以继续任务时，生成截图交接包，让当前对话里的模型继续看图判断：

```powershell
python desktop_mcp.py observe --handoff --goal "Open Google and search google" --question "Inspect the screenshot and decide the next desktop action." --json
```

输出里会包含：

- `job_id`
- 截图路径
- 给当前模型/操作者的下一步提示词

图像读完后清理：

```powershell
python desktop_mcp.py cleanup --job-id <JOB_ID> --json
```

## 混合方案的实际逻辑

推荐理解成下面这条链路：

1. 桌面动作和文本规划继续由 MCP + 代理模型完成
2. 需要读图时，先尝试本地 OCR
3. 如果 OCR 不足，立即返回 `handoff_required`
4. 你把截图交给当前模型读取后，它再继续调用 `desktop-mcp` 做点击、输入、再次截图、继续判断

也就是：

- OCR 不是唯一方案
- handoff 不是兜底边角料，而是和 OCR 同级的重要通道
- `goal --local-ocr` 会优先 OCR，但会在不确定时尽快切到 handoff

## `goal` 模式

适合跑真实的小任务：

```powershell
python desktop_mcp.py goal "Open Google and search google" --max-steps 8 --local-ocr --json
```

流程：

- 先 OCR 观察屏幕
- 模型决定下一步动作
- 执行动作后再次观察
- 如果 OCR 无法可靠验证，就返回 handoff 包

这就是 `CC Switch` 环境下推荐的默认工作方式。

## 自动回退逻辑

自动回退只作用于纯文本模型调用，例如：

- `goal --local-ocr`
- 本地 OCR 的条件校验
- 纯文本规划路径

触发条件包括：

- `403 / 1010`
- `5xx`
- 超时
- 连接失败

这样做的目的，是让主网关继续承担默认文本规划，而 `CC Switch` 只在主网关异常时接管，不影响本地 OCR、handoff、截图和键鼠行为。

## MCP 工具说明

- `desktop_observe`：截图观察，支持本地 OCR、模型观察、handoff 打包
- `desktop_action`：执行单步桌面动作
- `desktop_goal`：执行多步桌面任务
- `desktop_cleanup`：清理 handoff 产生的临时截图和元数据

## 健康检查

```powershell
@'
{"jsonrpc":"2.0","id":11,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":12,"method":"tools/list","params":{}}
'@ | python desktop_mcp.py mcp-serve
```

## 安全建议

- 先从低风险桌面任务开始
- 执行真实输入前先确认窗口焦点和坐标
- 项目默认开启 `pyautogui.FAILSAFE = True`

## 协议

MIT，见 `LICENSE`。
