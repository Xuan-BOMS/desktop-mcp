# CC Switch Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add configurable CC Switch fallback for Desktop MCP text model calls.

**Architecture:** Keep the current third-party gateway as primary. Add a second client resolved from dedicated `DESKTOP_MCP_CC_SWITCH_*` env vars. Retry text-only model calls on fallback when the primary path returns gateway or availability errors.

**Tech Stack:** Python 3.10+, urllib, LiteLLM, Windows env/registry lookup

---

### Task 1: Resolve primary and fallback clients

**Files:**
- Modify: `desktop_mcp.py`

**Step 1: Add a small client builder**

Create a helper that normalizes `api_key`, `api_base`, `model`, and `source`.

**Step 2: Split primary and fallback resolution**

Resolve:
- primary from `OI_THIRD_PARTY_*` first
- fallback from `DESKTOP_MCP_CC_SWITCH_*`

**Step 3: Keep safe defaults**

Use:
- `http://127.0.0.1:15721/v1`
- `sk-local-test`
- primary model name without provider prefix

### Task 2: Add text-call fallback

**Files:**
- Modify: `desktop_mcp.py`

**Step 1: Classify retryable gateway failures**

Detect:
- `403`
- `1010`
- `5xx`
- timeout
- connection failure
- service unavailable

**Step 2: Retry text-only calls on CC Switch**

When `b64 is None`, retry once on fallback if the primary client fails with a retryable error.

**Step 3: Preserve image behavior**

Do not apply automatic fallback to image-based observe calls.

### Task 3: Improve request compatibility and diagnostics

**Files:**
- Modify: `desktop_mcp.py`

**Step 1: Keep explicit HTTP headers**

Send stable `User-Agent` and `Accept` headers on direct OpenAI-compatible calls.

**Step 2: Surface readable errors**

Return `HTTP status + response body` instead of opaque codes.

### Task 4: Document deployment

**Files:**
- Modify: `mcp.config.example.json`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Step 1: Add fallback env block to sample config**

Expose `DESKTOP_MCP_CC_SWITCH_*` so the endpoint and port are editable.

**Step 2: Document primary/fallback routing**

Explain which paths fall back automatically and which do not.

### Task 5: Verify

**Files:**
- Modify: none

**Step 1: Syntax check**

Run: `python -m py_compile desktop_mcp.py`

**Step 2: Verify local observe still works**

Run: `python desktop_mcp.py observe --local-only --json`

**Step 3: Verify fallback classification**

Run a minimal text request and confirm the error path is readable and fallback-capable.
