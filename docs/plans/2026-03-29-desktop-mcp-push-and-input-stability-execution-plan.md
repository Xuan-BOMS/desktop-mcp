# Execution Plan: Desktop MCP Push And Input Stability

## Internal Grade

L

## Scope

1. Freeze governed runtime artifacts.
2. Verify the current fallback/config/docs changes are in a shippable state.
3. Commit and push the current repo state to `origin/master`.
4. Implement deterministic input-path improvements.
5. Re-run focused regressions and write cleanup receipts.

## Ownership

- Main agent owns all edits in this run.
- Write scope: `desktop_mcp.py`, docs/config files if needed, governed runtime artifacts.

## Wave Structure

### Wave 1

- Create `vibe` requirement/plan/runtime artifacts.
- Validate repo state and current modified files.

### Wave 2

- Run minimal verification on the current fallback work.
- Commit and push the current repo state.

### Wave 3

- Implement input stability improvements.
- Prefer deterministic primitives over prompt-only behavior.

### Wave 4

- Re-run focused regressions:
  - syntax validation
  - fallback sanity call
  - low-risk `goal`
  - input-oriented desktop regression

### Wave 5

- Cleanup temporary state.
- Write final receipts and summarize residual risk.

## Verification Commands

- `python -m py_compile C:\Users\Xuan\Desktop\desktop-mcp\desktop_mcp.py`
- `desktop-mcp observe --local-ocr --json`
- minimal `_call_json` fallback sanity check
- `desktop-mcp goal "Open the Windows Run dialog using the keyboard shortcut and stop when the Run dialog is visible." --max-steps 4 --local-ocr --json`
- input-focused regression after code changes

## Rollback Rules

- If commit/push fails, stop before further feature work and report the exact failure.
- If new input changes break fallback or low-risk goal execution, revert only the new input-related edits.
- If the local desktop state becomes too dirty, reset only by safe UI actions, not destructive git commands.

## Cleanup Expectations

- Close transient dialogs opened for tests when practical.
- Remove temporary screenshots/jobs when applicable.
- Keep runtime receipts under `outputs/runtime/vibe-sessions/...`.
- Record residual hazards explicitly if CC Switch overload or OCR ambiguity persists.
