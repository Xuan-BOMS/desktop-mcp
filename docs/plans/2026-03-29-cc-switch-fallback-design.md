# CC Switch Fallback Design

## Goal

Let Desktop MCP use the current third-party gateway as the primary text-planning path, and automatically fall back to a configurable local CC Switch endpoint when the primary path is blocked or unavailable.

## Scope

- Add a configurable CC Switch fallback client
- Apply fallback to text-only model calls, not local OCR or desktop actions
- Keep screenshot, OCR, handoff, and mouse/keyboard behavior unchanged
- Update sample config and README docs for deployment

## Approach

### Option A

Reuse `ANTHROPIC_*` as the fallback channel.

- Pros: fewer new env vars
- Cons: conflicts with existing gateway precedence and makes deployment ambiguous

### Option B

Add dedicated `DESKTOP_MCP_CC_SWITCH_*` env vars and keep `OI_THIRD_PARTY_*` as the primary channel.

- Pros: explicit, deployable, easy to document, no ambiguity
- Cons: one more env block in config

Recommended: Option B.

## Behavior

- Primary client: `OI_THIRD_PARTY_*`, then generic OpenAI-compatible envs if needed
- Fallback client: `DESKTOP_MCP_CC_SWITCH_BASE_URL`, `DESKTOP_MCP_CC_SWITCH_API_KEY`, `DESKTOP_MCP_CC_SWITCH_MODEL`
- Default fallback values:
  - base URL: `http://127.0.0.1:15721/v1`
  - API key: `sk-local-test`
  - model: reuse the primary model name without provider prefix

## Fallback Trigger

Only text-only model calls should fall back automatically.

Trigger fallback on:

- `403` / `1010`
- `5xx`
- timeout
- connection failure
- `Service temporarily unavailable`

Do not auto-fallback image-based observe calls, because CC Switch is not the reliable image path in this project.

## Files

- Modify `desktop_mcp.py`
- Modify `README.md`
- Modify `README.zh-CN.md`
- Modify `mcp.config.example.json`
