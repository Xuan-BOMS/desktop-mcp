# Desktop MCP

Windows desktop automation over MCP `stdio`, with screenshot observation, keyboard and mouse control, local OCR, and a screenshot handoff bridge for image-reading models.

## What Changed

This repository now matches the current `desktop_mcp.py` implementation used locally and adds a mixed workflow for `CC Switch` style setups:

- Text planning can use a primary third-party gateway with automatic `CC Switch` fallback
- Screenshots can be processed locally with OCR
- When OCR is not enough, the tool can package a screenshot as a handoff for the current chat model to inspect manually
- Temporary screenshot jobs can be cleaned up after reading
- Text-only model calls can retry on a configurable local `CC Switch` endpoint when the primary gateway is blocked or unavailable

This avoids depending on proxy-side image support while keeping MCP automation usable.

## Features

- Screenshot capture
- Desktop actions:
  - Mouse move, click, double click, right click, middle click, drag, scroll
  - Keyboard type, press, hotkey, key down, key up
- MCP tools:
  - `desktop_observe`
  - `desktop_action`
  - `desktop_goal`
  - `desktop_cleanup`
- Local observation modes:
  - `--local-only`
  - `--local-ocr`
  - `--handoff`
- Goal mode with OCR-first verification and handoff fallback

## Repository Layout

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

## Requirements

- Windows 10/11
- Python 3.10+
- An active desktop session

## Install

```powershell
git clone https://github.com/Xuan-BOMS/desktop-mcp.git
cd desktop-mcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

Local CLI debugging:

```powershell
python desktop_mcp.py action position --json
python desktop_mcp.py observe --local-only --json
```

Run as MCP server:

```powershell
python desktop_mcp.py mcp-serve
```

Or:

```powershell
desktop-mcp.cmd mcp-serve
```

## MCP Client Config

Use `mcp.config.example.json` as a template.

Basic stdio example:

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

## CC Switch Adaptation

`desktop_mcp.py` now uses two text-planning channels:

1. Primary gateway:
   - `OI_THIRD_PARTY_API_BASE`, `OI_THIRD_PARTY_API_KEY`, `OI_THIRD_PARTY_MODEL`
   - or generic OpenAI-compatible envs if `OI_THIRD_PARTY_*` is not set
2. Fallback `CC Switch` gateway:
   - `DESKTOP_MCP_CC_SWITCH_BASE_URL`
   - `DESKTOP_MCP_CC_SWITCH_API_KEY`
   - `DESKTOP_MCP_CC_SWITCH_MODEL`

Recommended fallback config for a local `CC Switch` proxy:

```powershell
$env:DESKTOP_MCP_CC_SWITCH_ENABLED = "true"
$env:DESKTOP_MCP_CC_SWITCH_BASE_URL = "http://127.0.0.1:15721/v1"
$env:DESKTOP_MCP_CC_SWITCH_API_KEY = "sk-local-test"
$env:DESKTOP_MCP_CC_SWITCH_MODEL = "gpt-5.3-codex"
```

Notes:

- `DESKTOP_MCP_CC_SWITCH_BASE_URL` is the deployable knob for changing host or port
- Text-only model calls retry on `CC Switch` when the primary path fails with gateway or availability errors
- Image-based observe calls do not auto-fallback to `CC Switch`; use local OCR or handoff for screenshots instead
- `ANTHROPIC_*` is still accepted as a compatibility fallback source if you already use it locally

## Observation Modes

### 1. Local-only screenshot

Just capture and save metadata without OCR or model calls:

```powershell
python desktop_mcp.py observe --local-only --json
```

### 2. Local OCR

Capture the screenshot and run local OCR with `rapidocr_onnxruntime`:

```powershell
python desktop_mcp.py observe --local-ocr --json
```

Auto-delete the saved screenshot after the response has been consumed:

```powershell
python desktop_mcp.py observe --local-ocr --delete-after-read --json
```

### 3. Handoff bridge

If OCR is insufficient, package the screenshot for the current model to inspect in chat:

```powershell
python desktop_mcp.py observe --handoff --goal "Open Google and search google" --question "Inspect the screenshot and decide the next desktop action." --json
```

The output includes:

- `job_id`
- saved screenshot path
- prompt text telling the operator/model what to inspect next

After the screenshot has been read, remove temporary files:

```powershell
python desktop_mcp.py cleanup --job-id <JOB_ID> --json
```

## Goal Mode

Use OCR-first desktop execution with automatic fallback to handoff when the screen contains information OCR cannot confidently verify:

```powershell
python desktop_mcp.py goal "Open Google and search google" --max-steps 8 --local-ocr --json
```

Behavior:

- Observe the screen with local OCR
- Let the model decide the next desktop action
- Re-observe after actions
- If OCR is insufficient, return `handoff_required` with a screenshot package

This is the recommended mode for `CC Switch` style environments.

## Gateway Fallback

Automatic fallback applies to text-only model calls, including:

- `goal --local-ocr`
- local OCR verification
- text-only planning paths

Fallback is triggered on gateway-style failures such as:

- `403 / 1010`
- `5xx`
- timeout
- connection failure

This means Desktop MCP can keep running even if the primary proxy is temporarily blocked, while still preserving local OCR and handoff as the reliable image path.

## MCP Tools

- `desktop_observe`: observe screenshot with local OCR, model analysis, or handoff packaging
- `desktop_action`: run one desktop action
- `desktop_goal`: run a multi-step task
- `desktop_cleanup`: delete temporary screenshot files from handoff jobs

## MCP JSON-RPC Health Check

```powershell
@'
{"jsonrpc":"2.0","id":11,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":12,"method":"tools/list","params":{}}
'@ | python desktop_mcp.py mcp-serve
```

## Safety

- Start with low-risk actions
- Confirm focus and coordinates before sending real inputs
- `pyautogui.FAILSAFE = True` is enabled

## License

MIT. See `LICENSE`.
