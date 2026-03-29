import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
import winreg
from io import BytesIO
from typing import Any, Optional

import pyautogui

try:
    from litellm import completion
except Exception:
    completion = None

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:
    RapidOCR = None

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.06
_OCR_ENGINE: Any = None


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        raw, _ = winreg.QueryValueEx(k, name)
        return str(raw).strip()
    except OSError:
        return ""


def _pick(*names: str, default: str = "") -> str:
    for n in names:
        v = _env(n)
        if v:
            return v
    return default


def _to_int(v: Any, d: int) -> int:
    try:
        return int(v)
    except Exception:
        return d


def _to_float(v: Any, d: float) -> float:
    try:
        return float(v)
    except Exception:
        return d


def _to_bool(v: Any, d: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return d
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return d


def _normalize_model(model: str, provider: str = "openai") -> str:
    m = str(model or "").strip()
    if not m:
        return ""
    if "/" not in m and not m.startswith("azure/"):
        return f"{provider}/{m}"
    return m


def _openai_http_model(model: str) -> str:
    m = str(model or "").strip()
    if "/" in m and not m.startswith("azure/"):
        head, tail = m.split("/", 1)
        if head in {"openai", "anthropic"} and tail:
            return tail
    return m


def _client_from_values(api_key: str, api_base: str, model: str, source: str, provider: str = "openai") -> dict[str, str]:
    key = str(api_key or "").strip()
    base = str(api_base or "").strip()
    raw_model = str(model or "").strip()
    if not key or not base or not raw_model:
        raise RuntimeError(f"Missing gateway env for {source}.")
    return {
        "api_key": key,
        "api_base": base,
        "model": _normalize_model(raw_model, provider=provider),
        "source": source,
    }


def _client() -> dict[str, str]:
    if completion is None:
        raise RuntimeError("litellm not installed")
    if _env("OI_THIRD_PARTY_API_BASE") and _env("OI_THIRD_PARTY_API_KEY"):
        return _client_from_values(
            _env("OI_THIRD_PARTY_API_KEY"),
            _env("OI_THIRD_PARTY_API_BASE"),
            _pick("OI_THIRD_PARTY_MODEL", default="gpt-5.3-codex"),
            "primary",
            provider="openai",
        )
    if _env("OPENAI_BASE_URL") and _env("OPENAI_API_KEY"):
        return _client_from_values(
            _env("OPENAI_API_KEY"),
            _env("OPENAI_BASE_URL"),
            _pick("OPENAI_MODEL", default="gpt-5.3-codex"),
            "primary",
            provider="openai",
        )
    if _env("OPENAI_API_BASE") and _env("OPENAI_API_KEY"):
        return _client_from_values(
            _env("OPENAI_API_KEY"),
            _env("OPENAI_API_BASE"),
            _pick("OPENAI_MODEL", default="gpt-5.3-codex"),
            "primary",
            provider="openai",
        )
    if _env("LITELLM_BASE_URL") and _pick("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        return _client_from_values(
            _pick("OPENAI_API_KEY", "OPENROUTER_API_KEY"),
            _env("LITELLM_BASE_URL"),
            _pick("LITELLM_MODEL", "OPENAI_MODEL", default="gpt-5.3-codex"),
            "primary",
            provider="openai",
        )
    if _env("ANTHROPIC_BASE_URL") and _env("ANTHROPIC_API_KEY"):
        return _client_from_values(
            _env("ANTHROPIC_API_KEY"),
            _env("ANTHROPIC_BASE_URL"),
            _pick("ANTHROPIC_MODEL", default="gpt-5.3-codex"),
            "primary",
            provider="anthropic",
        )
    raise RuntimeError("Missing gateway env. Set OI_THIRD_PARTY_*, OPENAI_* or ANTHROPIC_*.")


def _fallback_cc_switch_client(primary: dict[str, str]) -> Optional[dict[str, str]]:
    enabled = _to_bool(_pick("DESKTOP_MCP_CC_SWITCH_ENABLED", default="true"), True)
    if not enabled:
        return None
    base = _pick("DESKTOP_MCP_CC_SWITCH_BASE_URL", "ANTHROPIC_BASE_URL", default="http://127.0.0.1:15721/v1")
    key = _pick("DESKTOP_MCP_CC_SWITCH_API_KEY", "ANTHROPIC_API_KEY", default="sk-local-test")
    model = _pick("DESKTOP_MCP_CC_SWITCH_MODEL", "ANTHROPIC_MODEL", default=_openai_http_model(primary.get("model", "")))
    fb = _client_from_values(key, base, model, "fallback_cc_switch", provider="openai")
    if fb["api_base"].rstrip("/") == str(primary.get("api_base", "")).rstrip("/"):
        return None
    return fb


def _is_retryable_gateway_error(err: Exception) -> bool:
    s = str(err or "").strip().lower()
    if not s:
        return False
    needles = (
        "error code: 1010",
        "http 403",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "service temporarily unavailable",
        "timed out",
        "timeout",
        "connection refused",
        "connection reset",
        "remote end closed",
        "temporarily unavailable",
    )
    return any(n in s for n in needles)


def _screen() -> tuple[int, int]:
    s = pyautogui.size()
    return int(s.width), int(s.height)


def _xy(x: int, y: int) -> tuple[int, int]:
    w, h = _screen()
    return max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))


def _button(v: Any) -> str:
    s = str(v or "left").strip().lower()
    m = {"l": "left", "r": "right", "m": "middle", "left": "left", "right": "right", "middle": "middle"}
    if s not in m:
        raise ValueError(f"unsupported button: {v}")
    return m[s]


def _key(v: Any) -> str:
    s = str(v or "").strip().lower()
    if not s:
        raise ValueError("key required")
    return {
        "control": "ctrl",
        "ctl": "ctrl",
        "windows": "win",
        "command": "win",
        "cmd": "win",
        "return": "enter",
        "del": "delete",
        "pgup": "pageup",
        "pgdn": "pagedown",
    }.get(s, s)


def _keys(v: Any) -> list[str]:
    if isinstance(v, list):
        raw = [str(x).strip() for x in v if str(x).strip()]
    else:
        raw = [x.strip() for x in re.split(r"[+,\s]+", str(v or "")) if x.strip()]
    out = [_key(x) for x in raw]
    if not out:
        raise ValueError("keys required")
    return out


def _region(v: Any) -> Optional[tuple[int, int, int, int]]:
    if not v:
        return None
    if isinstance(v, dict):
        x, y, w, h = _to_int(v.get("x"), 0), _to_int(v.get("y"), 0), _to_int(v.get("width"), 0), _to_int(v.get("height"), 0)
    elif isinstance(v, list) and len(v) == 4:
        x, y, w, h = [_to_int(i, 0) for i in v]
    else:
        raise ValueError("region must be {x,y,width,height} or [x,y,width,height]")
    sw, sh = _screen()
    x = max(0, min(sw - 1, x))
    y = max(0, min(sh - 1, y))
    w = max(1, min(sw - x, w))
    h = max(1, min(sh - y, h))
    return x, y, w, h


def _encode(img: Any, fmt: str = "png", quality: int = 82) -> tuple[bytes, str, str]:
    f = str(fmt or "png").lower()
    if f in {"jpg", "jpeg"}:
        f = "jpeg"
    elif f != "png":
        raise ValueError("format must be png|jpeg")
    if getattr(img, "mode", "") not in {"RGB", "L"}:
        img = img.convert("RGB")
    q = max(30, min(95, int(quality)))
    b = BytesIO()
    if f == "jpeg":
        img.save(b, format="JPEG", quality=q, optimize=True)
        return b.getvalue(), "image/jpeg", f
    img.save(b, format="PNG", optimize=True)
    return b.getvalue(), "image/png", f


def _write_file(path: str, raw: bytes) -> str:
    p = os.path.abspath(path)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "wb") as f:
        f.write(raw)
    return p


def _default_screenshot_path(prefix: str = "desktop-mcp-observe", ext: str = "png") -> str:
    root = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return os.path.join(root, f"{prefix}-{stamp}.{ext}")


def _safe_remove(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _state_dir() -> str:
    root = os.path.join(os.path.expanduser("~"), ".codex", "mcp", "desktop-mcp")
    os.makedirs(root, exist_ok=True)
    return root


def _handoff_dir() -> str:
    path = os.path.join(_state_dir(), "handoffs")
    os.makedirs(path, exist_ok=True)
    return path


def _new_job_id() -> str:
    return datetime.now().strftime("handoff-%Y%m%d-%H%M%S-%f")


def _handoff_meta_path(job_id: str) -> str:
    return os.path.join(_handoff_dir(), f"{job_id}.json")


def _write_json(path: str, data: dict[str, Any]) -> str:
    p = os.path.abspath(path)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def _read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _ocr_engine() -> Any:
    global _OCR_ENGINE
    if RapidOCR is None:
        raise RuntimeError("rapidocr_onnxruntime not installed")
    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _ocr_entries(path: str) -> list[dict[str, Any]]:
    result, _ = _ocr_engine()(path)
    entries: list[dict[str, Any]] = []
    for item in result or []:
        if len(item) < 3:
            continue
        quad, text, score = item
        txt = re.sub(r"\s+", " ", str(text or "")).strip()
        if not txt:
            continue
        xs = [float(p[0]) for p in quad]
        ys = [float(p[1]) for p in quad]
        x1, y1 = int(min(xs)), int(min(ys))
        x2, y2 = int(max(xs)), int(max(ys))
        entries.append(
            {
                "text": txt,
                "score": round(float(score), 4),
                "x": x1,
                "y": y1,
                "width": max(1, x2 - x1),
                "height": max(1, y2 - y1),
                "cx": int((x1 + x2) / 2),
                "cy": int((y1 + y2) / 2),
            }
        )
    entries.sort(key=lambda it: (it["y"], it["x"]))
    return entries


def _ocr_digest(entries: list[dict[str, Any]], item_limit: int = 160, line_limit: int = 120) -> dict[str, Any]:
    if not entries:
        return {"summary": "no_text_detected", "focus_window": "", "key_text": [], "ocr_items": [], "ocr_text": ""}
    seen: set[str] = set()
    key_text: list[str] = []
    for entry in entries:
        text = entry["text"]
        if text in seen:
            continue
        seen.add(text)
        key_text.append(text)
        if len(key_text) >= 24:
            break
    top_bar = [entry["text"] for entry in entries if entry["y"] <= 90][:6]
    lines = [
        f'{idx + 1}. "{entry["text"]}" @ ({entry["cx"]},{entry["cy"]}) box=({entry["x"]},{entry["y"]},{entry["width"]},{entry["height"]}) score={entry["score"]:.2f}'
        for idx, entry in enumerate(entries[:line_limit])
    ]
    return {
        "summary": " / ".join(key_text[:8]),
        "focus_window": " | ".join(dict.fromkeys(top_bar)),
        "key_text": key_text,
        "ocr_items": entries[:item_limit],
        "ocr_text": "\n".join(lines),
    }


def _goal_keywords(goal: str) -> list[str]:
    raw = re.findall(r"[\w\u4e00-\u9fff]{2,}", str(goal or "").lower())
    stop = {
        "open", "click", "type", "press", "scroll", "wait", "screen", "desktop", "page", "task",
        "淘宝", "页面", "打开", "点击", "输入", "搜索", "继续", "完成", "任务", "当前", "窗口",
    }
    return [token for token in raw if token not in stop][:24]


def _ocr_needs_handoff(goal: str, obs: dict[str, Any]) -> tuple[bool, str]:
    if not _to_bool(obs.get("ok"), False):
        return True, "ocr_failed"
    items = obs.get("ocr_items", [])
    if not isinstance(items, list):
        items = []
    if len(items) < 8:
        return True, "too_few_ocr_items"
    scores = [float(item.get("score", 0.0)) for item in items if isinstance(item, dict)]
    avg_score = sum(scores) / max(1, len(scores))
    top_text = " ".join(str(x) for x in obs.get("key_text", [])).lower()
    if avg_score < 0.72:
        return True, "low_ocr_confidence"
    if len(top_text.strip()) < 24:
        return True, "too_little_text"
    keywords = _goal_keywords(goal)
    if keywords:
        hits = sum(1 for key in keywords if key and key in top_text)
        if hits == 0 and len(items) < 24:
            return True, "goal_keywords_missing"
    return False, "ocr_sufficient"


def _cleanup_saved_path(path: str) -> bool:
    return bool(path) and _safe_remove(path)


def _build_handoff_payload(
    saved_path: str,
    goal: str = "",
    question: str = "",
    last_action: str = "",
    source: str = "manual",
    obs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    job_id = _new_job_id()
    payload: dict[str, Any] = {
        "job_id": job_id,
        "status": "handoff_required",
        "saved_path": os.path.abspath(saved_path),
        "goal": str(goal or "").strip(),
        "question": str(question or "").strip() or "Please inspect the screenshot and decide the next UI action.",
        "last_action": str(last_action or "").strip(),
        "source": source,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "cleanup_after_read": True,
    }
    if obs:
        payload["ocr_summary"] = str(obs.get("summary", "") or "")
        payload["ocr_focus_window"] = str(obs.get("focus_window", "") or "")
        payload["ocr_key_text"] = list(obs.get("key_text", [])[:16]) if isinstance(obs.get("key_text"), list) else []
    payload["assistant_prompt"] = (
        "Desktop handoff required. Read the attached screenshot in this conversation, "
        f"use job_id `{job_id}` as the pending cleanup handle, answer the question `{payload['question']}`, "
        "then continue the desktop task by calling desktop-mcp actions or desktop-mcp goal as needed. "
        f"After the image has been read, call `desktop-mcp cleanup --job-id {job_id}`."
    )
    payload["user_prompt"] = (
        f"请把截图 `{payload['saved_path']}` 发到当前对话，然后让我继续。"
        f" 任务目标：{payload['goal'] or 'n/a'}；当前问题：{payload['question']}"
    )
    payload["meta_path"] = _write_json(_handoff_meta_path(job_id), payload)
    return payload


def cleanup(job_id: str = "", path: str = "") -> dict[str, Any]:
    meta_path = ""
    payload: dict[str, Any] = {}
    target_path = os.path.abspath(path) if path else ""
    if job_id:
        meta_path = _handoff_meta_path(job_id)
        if os.path.exists(meta_path):
            payload = _read_json(meta_path)
            target_path = str(payload.get("saved_path", "") or target_path)
    removed_file = _cleanup_saved_path(target_path)
    removed_meta = _safe_remove(meta_path) if meta_path else False
    return {
        "ok": removed_file or removed_meta,
        "job_id": job_id,
        "removed_file": removed_file,
        "removed_meta": removed_meta,
        "saved_path": target_path,
        "meta_path": meta_path,
    }


def capture(include_base64: bool = True, save: str = "", fmt: str = "png", quality: int = 82, region: Any = None) -> dict[str, Any]:
    rg = _region(region)
    img = pyautogui.screenshot(region=rg) if rg else pyautogui.screenshot()
    raw, mime, fn = _encode(img, fmt=fmt, quality=quality)
    w, h = img.size
    sw, sh = _screen()
    out: dict[str, Any] = {"ok": True, "format": fn, "mime": mime, "width": int(w), "height": int(h), "screen_size": {"width": sw, "height": sh}}
    if rg:
        out["region"] = {"x": rg[0], "y": rg[1], "width": rg[2], "height": rg[3]}
    if include_base64:
        out["image_b64"] = base64.b64encode(raw).decode("ascii")
    if save:
        out["save_path"] = _write_file(save, raw)
    return out


def _capture_screen() -> tuple[Any, bytes, int, int]:
    img = pyautogui.screenshot()
    w, h = img.size
    raw, _, _ = _encode(img, fmt="png", quality=90)
    return img, raw, int(w), int(h)


def _model_b64(img: Any) -> str:
    mx, q = 1440, 72
    w, h = img.size
    if max(w, h) > mx:
        img = img.copy()
        img.thumbnail((mx, mx))
    if getattr(img, "mode", "") not in {"RGB", "L"}:
        img = img.convert("RGB")
    b = BytesIO()
    img.save(b, format="JPEG", quality=q, optimize=True)
    return base64.b64encode(b.getvalue()).decode("ascii")


def _observe_result(out: dict[str, Any], raw_png: bytes, w: int, h: int, include_base64: bool) -> dict[str, Any]:
    out["screen_size"] = {"width": w, "height": h}
    if include_base64:
        out["image_b64"] = base64.b64encode(raw_png).decode("ascii")
    return out


def _extract_json(text: str) -> dict[str, Any]:
    t = text.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        raise ValueError(f"model did not return json: {t[:180]}")
    return json.loads(m.group(0))


def _call_openai_chat_json(client: dict[str, str], sys_prompt: str, user_text: str) -> dict[str, Any]:
    model = _openai_http_model(client["model"])
    url = client["api_base"].rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_text}],
        "temperature": 1,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {client['api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "desktop-mcp/1.0 (+https://github.com/Xuan-BOMS/desktop-mcp)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e
    except Exception as e:
        raise RuntimeError(str(e)) from e
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    out = _extract_json(content)
    if isinstance(out, dict):
        out["_client_source"] = str(client.get("source", "") or "")
    return out


def _call_json_once(client: dict[str, str], sys_prompt: str, user_text: str, b64: Optional[str] = None) -> dict[str, Any]:
    content: Any = user_text
    if b64:
        content = [{"type": "text", "text": user_text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
    if b64 is None and client["model"].startswith("openai/"):
        return _call_openai_chat_json(client, sys_prompt, user_text)
    r = completion(
        model=client["model"],
        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": content}],
        api_key=client["api_key"],
        api_base=client["api_base"],
        timeout=18.0,
        temperature=1,
    )
    out = _extract_json((r.choices[0].message.content or "").strip())
    if isinstance(out, dict):
        out["_client_source"] = str(client.get("source", "") or "")
    return out


def _call_json(client: dict[str, str], sys_prompt: str, user_text: str, b64: Optional[str] = None) -> dict[str, Any]:
    retries = 1
    last: Optional[Exception] = None
    for i in range(retries + 1):
        try:
            return _call_json_once(client, sys_prompt, user_text, b64=b64)
        except Exception as e:
            last = e
            if i >= retries:
                break
            time.sleep(min(0.8 * (2**i), 2.2))
    if b64 is None and last is not None and _is_retryable_gateway_error(last):
        fb = _fallback_cc_switch_client(client)
        if fb is not None:
            return _call_json_once(fb, sys_prompt, user_text, b64=None)
    raise last or RuntimeError("model call failed")


def observe(
    condition: str = "",
    include_base64: bool = False,
    save: str = "",
    local_only: bool = False,
    local_ocr: bool = False,
    delete_after_read: bool = False,
    handoff: bool = False,
    goal: str = "",
    question: str = "",
    last_action: str = "",
) -> dict[str, Any]:
    img, raw_png, w, h = _capture_screen()
    path = save or _default_screenshot_path()
    if local_only:
        out: dict[str, Any] = {
            "summary": "local_capture_saved",
            "focus_window": "",
            "key_text": [],
            "ok": True,
            "reason": "local_only",
            "saved_path": _write_file(path, raw_png),
        }
        out["screenshot"] = out["saved_path"]
        return _observe_result(out, raw_png, w, h, include_base64)
    if handoff:
        saved_path = _write_file(path, raw_png)
        out = _build_handoff_payload(
            saved_path=saved_path,
            goal=goal,
            question=question or condition,
            last_action=last_action,
            source="observe_handoff",
        )
        out["ok"] = True
        out["screenshot"] = saved_path
        return _observe_result(out, raw_png, w, h, include_base64)
    if local_ocr:
        saved_path = _write_file(path, raw_png)
        try:
            digest = _ocr_digest(_ocr_entries(saved_path))
            out = {
                "summary": digest["summary"],
                "focus_window": digest["focus_window"],
                "key_text": digest["key_text"],
                "ok": True,
                "reason": "local_ocr",
                "saved_path": saved_path,
                "ocr_items": digest["ocr_items"],
                "ocr_text": digest["ocr_text"],
            }
        except Exception as e:
            out = {
                "summary": "local_ocr_failed",
                "focus_window": "",
                "key_text": [],
                "ok": False,
                "reason": str(e),
                "saved_path": saved_path,
                "ocr_items": [],
                "ocr_text": "",
            }
        out["screenshot"] = saved_path
        if delete_after_read and not save:
            out["deleted_after_read"] = _safe_remove(saved_path)
            out["deleted_path"] = saved_path
        return _observe_result(out, raw_png, w, h, include_base64)
    sys_prompt = (
        "You are a desktop vision observer/verifier. Return strict JSON only: "
        '{"summary":"...","focus_window":"...","key_text":["..."],"ok":true/false,"reason":"..."}. '
        "If no condition provided, set ok=true and reason='n/a'."
    )
    user = f"Condition: {condition.strip()}" if condition.strip() else "Describe current screen"
    try:
        client = _client()
        out = _call_json(client, sys_prompt, user, b64=_model_b64(img))
    except Exception as e:
        out = {
            "summary": "model_observe_unavailable",
            "focus_window": "",
            "key_text": [],
            "ok": False,
            "reason": str(e),
        }
        out["saved_path"] = _write_file(path, raw_png)
    if isinstance(out, dict) and out.get("_client_source"):
        out["model_source"] = out.get("_client_source")
    if save:
        out["screenshot"] = _write_file(save, raw_png)
    return _observe_result(out, raw_png, w, h, include_base64)


def _maybe_cleanup_obs(obs: dict[str, Any], enabled: bool) -> None:
    if not enabled:
        return
    path = str(obs.get("saved_path", "") or "")
    if path:
        _cleanup_saved_path(path)


def _arg_value(src: Any, name: str, default: Any = None) -> Any:
    if isinstance(src, dict):
        return src.get(name, default)
    return getattr(src, name, default)


def _observe_kwargs(src: Any) -> dict[str, Any]:
    return {
        "condition": str(_arg_value(src, "condition", "") or ""),
        "include_base64": _to_bool(_arg_value(src, "include_base64", False), False),
        "save": str(_arg_value(src, "save", "") or ""),
        "local_only": _to_bool(_arg_value(src, "local_only", False), False),
        "local_ocr": _to_bool(_arg_value(src, "local_ocr", False), False),
        "delete_after_read": _to_bool(_arg_value(src, "delete_after_read", False), False),
        "handoff": _to_bool(_arg_value(src, "handoff", False), False),
        "goal": str(_arg_value(src, "goal", "") or ""),
        "question": str(_arg_value(src, "question", "") or ""),
        "last_action": str(_arg_value(src, "last_action", "") or ""),
    }


def _goal_kwargs(src: Any) -> dict[str, Any]:
    return {
        "goal": str(_arg_value(src, "goal", "") or ""),
        "max_steps": max(1, _to_int(_arg_value(src, "max_steps", 10), 10)),
        "local_ocr": _to_bool(_arg_value(src, "local_ocr", False), False),
        "delete_after_read": _to_bool(_arg_value(src, "delete_after_read", False), False),
        "handoff_on_uncertain": _to_bool(_arg_value(src, "handoff_on_uncertain", True), True),
    }


def _ocr_prompt_blob(obs: dict[str, Any], limit: int = 120) -> str:
    text = str(obs.get("ocr_text", "") or "").strip()
    if text:
        return text
    items = obs.get("ocr_items", [])
    if not isinstance(items, list):
        return ""
    lines: list[str] = []
    for idx, item in enumerate(items[:limit], start=1):
        if not isinstance(item, dict):
            continue
        lines.append(
            f'{idx}. "{item.get("text", "")}" @ ({item.get("cx", 0)},{item.get("cy", 0)}) '
            f'box=({item.get("x", 0)},{item.get("y", 0)},{item.get("width", 0)},{item.get("height", 0)}) '
            f'score={float(item.get("score", 0.0)):.2f}'
        )
    return "\n".join(lines)


def _verify_local_ocr(client: dict[str, str], condition: str, obs: dict[str, Any]) -> tuple[bool, str]:
    cond = str(condition or "").strip()
    if not cond:
        return True, "n/a"
    sys_prompt = 'Return strict JSON only: {"ok":true/false,"reason":"..."} based only on OCR text and coordinates.'
    user = (
        f"Condition: {cond}\n"
        f"Summary: {obs.get('summary', '')}\n"
        f"Focus: {obs.get('focus_window', '')}\n"
        f"OCR:\n{_ocr_prompt_blob(obs)}"
    )
    try:
        out = _call_json(client, sys_prompt, user)
        return _to_bool(out.get("ok"), False), str(out.get("reason", "") or "")
    except Exception as e:
        return False, str(e)


SHORTCUTS = {
    "copy": ["ctrl", "c"],
    "paste": ["ctrl", "v"],
    "cut": ["ctrl", "x"],
    "undo": ["ctrl", "z"],
    "redo": ["ctrl", "y"],
    "save": ["ctrl", "s"],
    "select_all": ["ctrl", "a"],
    "find": ["ctrl", "f"],
    "close_window": ["alt", "f4"],
    "task_manager": ["ctrl", "shift", "esc"],
    "lock_screen": ["win", "l"],
    "show_desktop": ["win", "d"],
    "file_explorer": ["win", "e"],
    "run": ["win", "r"],
    "settings": ["win", "i"],
    "snipping_tool": ["win", "shift", "s"],
    "emoji": ["win", "."],
    "new_desktop": ["ctrl", "win", "d"],
    "close_desktop": ["ctrl", "win", "f4"],
    "next_desktop": ["ctrl", "win", "right"],
    "prev_desktop": ["ctrl", "win", "left"],
    "delete": ["delete"],
    "hard_delete": ["shift", "delete"],
}


def _mouse() -> dict[str, int]:
    p = pyautogui.position()
    return {"x": int(p.x), "y": int(p.y)}


def act(a: dict[str, Any]) -> dict[str, Any]:
    t = str(a.get("type", "")).strip().lower()
    if not t:
        raise ValueError("action.type required")

    if t == "position":
        w, h = _screen()
        return {"ok": True, "action": t, "mouse": _mouse(), "screen_size": {"width": w, "height": h}}

    if t == "screenshot":
        return capture(
            include_base64=_to_bool(a.get("include_base64"), True),
            save=str(a.get("save", "") or a.get("save_path", "")).strip(),
            fmt=str(a.get("format", "png")),
            quality=_to_int(a.get("quality"), 82),
            region=a.get("region"),
        )

    if t in {"move", "move_to"}:
        x, y = _xy(_to_int(a.get("x"), 0), _to_int(a.get("y"), 0))
        pyautogui.moveTo(x, y, duration=max(0.0, min(8.0, _to_float(a.get("duration"), 0.12))))
        return {"ok": True, "action": "move", "result": f"move({x},{y})", "mouse": _mouse()}

    if t in {"move_rel", "move_relative"}:
        dx, dy = _to_int(a.get("dx"), 0), _to_int(a.get("dy"), 0)
        pyautogui.moveRel(dx, dy, duration=max(0.0, min(8.0, _to_float(a.get("duration"), 0.12))))
        return {"ok": True, "action": "move_rel", "result": f"move_rel({dx},{dy})", "mouse": _mouse()}

    if t in {"click", "double_click", "right_click", "middle_click"}:
        if t == "right_click":
            b, c = "right", 1
        elif t == "middle_click":
            b, c = "middle", 1
        else:
            b, c = _button(a.get("button", "left")), (2 if t == "double_click" else max(1, min(6, _to_int(a.get("clicks"), 1))))
        iv = max(0.0, min(2.0, _to_float(a.get("interval"), 0.08)))
        x, y = a.get("x"), a.get("y")
        if x is not None and y is not None:
            px, py = _xy(_to_int(x, 0), _to_int(y, 0))
            pyautogui.click(x=px, y=py, clicks=c, interval=iv, button=b)
        else:
            pyautogui.click(clicks=c, interval=iv, button=b)
        return {"ok": True, "action": "click", "result": f"click(button={b},clicks={c})", "mouse": _mouse()}

    if t in {"mouse_down", "mouse_up"}:
        b = _button(a.get("button", "left"))
        x, y = a.get("x"), a.get("y")
        if x is not None and y is not None:
            px, py = _xy(_to_int(x, 0), _to_int(y, 0))
            (pyautogui.mouseDown if t == "mouse_down" else pyautogui.mouseUp)(x=px, y=py, button=b)
        else:
            (pyautogui.mouseDown if t == "mouse_down" else pyautogui.mouseUp)(button=b)
        return {"ok": True, "action": t, "result": f"{t}(button={b})", "mouse": _mouse()}

    if t in {"drag_to", "drag"}:
        x, y = _xy(_to_int(a.get("x"), 0), _to_int(a.get("y"), 0))
        b = _button(a.get("button", "left"))
        pyautogui.dragTo(x, y, duration=max(0.0, min(12.0, _to_float(a.get("duration"), 0.2))), button=b)
        return {"ok": True, "action": "drag_to", "result": f"drag_to({x},{y},button={b})", "mouse": _mouse()}

    if t in {"drag_rel", "drag_relative"}:
        dx, dy = _to_int(a.get("dx"), 0), _to_int(a.get("dy"), 0)
        b = _button(a.get("button", "left"))
        pyautogui.dragRel(dx, dy, duration=max(0.0, min(12.0, _to_float(a.get("duration"), 0.2))), button=b)
        return {"ok": True, "action": "drag_rel", "result": f"drag_rel({dx},{dy},button={b})", "mouse": _mouse()}

    if t in {"scroll", "wheel"}:
        amt = _to_int(a.get("amount"), -300)
        x, y = a.get("x"), a.get("y")
        if x is not None and y is not None:
            px, py = _xy(_to_int(x, 0), _to_int(y, 0))
            pyautogui.scroll(amt, x=px, y=py)
        else:
            pyautogui.scroll(amt)
        return {"ok": True, "action": "scroll", "result": f"scroll({amt})", "mouse": _mouse()}

    if t in {"hscroll", "scroll_horizontal"}:
        if not hasattr(pyautogui, "hscroll"):
            raise RuntimeError("hscroll not supported")
        amt = _to_int(a.get("amount"), -200)
        x, y = a.get("x"), a.get("y")
        if x is not None and y is not None:
            px, py = _xy(_to_int(x, 0), _to_int(y, 0))
            pyautogui.hscroll(amt, x=px, y=py)
        else:
            pyautogui.hscroll(amt)
        return {"ok": True, "action": "hscroll", "result": f"hscroll({amt})", "mouse": _mouse()}

    if t == "type":
        txt = str(a.get("text", ""))
        if not txt:
            raise ValueError("type requires text")
        pyautogui.write(txt, interval=max(0.0, min(0.5, _to_float(a.get("interval"), 0.01))))
        return {"ok": True, "action": "type", "result": f"type({len(txt)} chars)", "mouse": _mouse()}

    if t in {"press", "key_press"}:
        k = _key(a.get("key"))
        n = max(1, min(300, _to_int(a.get("presses"), 1)))
        pyautogui.press(k, presses=n, interval=max(0.0, min(2.0, _to_float(a.get("interval"), 0.05))))
        return {"ok": True, "action": "press", "result": f"press({k},presses={n})", "mouse": _mouse()}

    if t in {"delete", "backspace"}:
        n = max(1, min(300, _to_int(a.get("presses"), 1)))
        pyautogui.press("delete" if t == "delete" else "backspace", presses=n, interval=0.03)
        return {"ok": True, "action": t, "result": f"{t}(presses={n})", "mouse": _mouse()}

    if t == "key_down":
        k = _key(a.get("key"))
        pyautogui.keyDown(k)
        return {"ok": True, "action": t, "result": f"key_down({k})", "mouse": _mouse()}

    if t == "key_up":
        k = _key(a.get("key"))
        pyautogui.keyUp(k)
        return {"ok": True, "action": t, "result": f"key_up({k})", "mouse": _mouse()}

    if t == "hotkey":
        ks = _keys(a.get("keys"))
        pyautogui.hotkey(*ks)
        return {"ok": True, "action": t, "result": "hotkey(" + "+".join(ks) + ")", "mouse": _mouse()}

    if t == "shortcut":
        n = str(a.get("name", "")).strip().lower()
        if not n:
            raise ValueError("shortcut requires name")
        ks = SHORTCUTS.get(n)
        if not ks:
            raise ValueError("unknown shortcut")
        pyautogui.hotkey(*ks)
        return {"ok": True, "action": t, "name": n, "result": "hotkey(" + "+".join(ks) + ")", "mouse": _mouse()}

    if t == "wait":
        s = max(0.0, min(120.0, _to_float(a.get("seconds"), 0.8)))
        time.sleep(s)
        return {"ok": True, "action": t, "result": f"wait({s:.2f}s)", "mouse": _mouse()}

    raise ValueError(f"unsupported action type: {t}")


def batch(actions: list[dict[str, Any]], continue_on_error: bool = False, sleep_between: float = 0.0) -> dict[str, Any]:
    logs: list[dict[str, Any]] = []
    for i, a in enumerate(actions, start=1):
        try:
            logs.append({"index": i, "ok": True, "action": a, "result": act(a)})
        except Exception as e:
            logs.append({"index": i, "ok": False, "action": a, "error": str(e)})
            if not continue_on_error:
                return {"ok": False, "stopped_at": i, "logs": logs}
        if sleep_between > 0 and i < len(actions):
            time.sleep(min(10.0, max(0.0, sleep_between)))
    return {"ok": all(x.get("ok") for x in logs), "logs": logs}


def goal_run(
    goal: str,
    max_steps: int,
    local_ocr: bool = False,
    delete_after_read: bool = False,
    handoff_on_uncertain: bool = True,
) -> dict[str, Any]:
    client = _client()
    logs: list[dict[str, Any]] = []
    hist: list[str] = []
    last_action_result = ""
    deadline = time.monotonic() + 120.0
    for step in range(1, max(1, max_steps) + 1):
        if time.monotonic() > deadline:
            return {"ok": False, "error": "deadline_exceeded", "logs": logs}
        if local_ocr:
            obs0 = observe(local_ocr=True, delete_after_read=False)
            w = _to_int(obs0.get("screen_size", {}).get("width"), 0) if isinstance(obs0.get("screen_size"), dict) else 0
            h = _to_int(obs0.get("screen_size", {}).get("height"), 0) if isinstance(obs0.get("screen_size"), dict) else 0
            if handoff_on_uncertain:
                need_handoff, handoff_reason = _ocr_needs_handoff(goal, obs0)
                if need_handoff:
                    handoff = _build_handoff_payload(
                        saved_path=str(obs0.get("saved_path", "") or ""),
                        goal=goal,
                        question=f"OCR is insufficient ({handoff_reason}). Inspect the screenshot and decide the next desktop action.",
                        last_action=last_action_result,
                        source="goal_precheck",
                        obs=obs0,
                    )
                    logs.append({"step": step, "handoff": True, "reason": handoff_reason, "summary": obs0.get("summary", "")})
                    return {"ok": False, "error": "handoff_required", "handoff": handoff, "logs": logs}
            sys_prompt = (
                "Return strict JSON only: "
                '{"done":true/false,"handoff":true/false,"reason":"...","question":"...","action":{"type":"move|click|double_click|right_click|type|hotkey|press|scroll|wait|screenshot","x":0,"y":0,"text":"","keys":[],"key":"","amount":0,"seconds":0.8},"verify":"..."}. '
                "You are operating a Windows desktop from OCR text boxes only. Each OCR line has visible text and coordinates. "
                "If OCR is insufficient or the next decision depends on non-textual visual structure, set handoff=true and explain what must be inspected in the screenshot. "
                "Prefer keyboard shortcuts when they are more reliable than coordinate guesses. "
                "If you need to type into an existing field, first focus that field with a click unless the history clearly shows it is already focused. "
                "When OCR merges a field label and its current value into one line, you may click that line's center to focus the field. "
                "For dialog submission after typing, prefer clicking a visible confirm button such as OK/Open/Run/Confirm instead of relying on Enter if focus is uncertain. "
                "If clicking text, use the provided OCR center coordinates. One action per step."
            )
            user = (
                f"Goal: {goal}\n"
                f"Screen: {w}x{h}\n"
                f"Summary: {obs0.get('summary', '')}\n"
                f"Focus: {obs0.get('focus_window', '')}\n"
                f"OCR:\n{_ocr_prompt_blob(obs0)}\n"
                "History:\n" + ("\n".join(hist[-12:]) if hist else "(empty)")
            )
            dec = _call_json(client, sys_prompt, user)
        else:
            img, _, w, h = _capture_screen()
            sys_prompt = (
                "Return strict JSON only: "
                '{"done":true/false,"reason":"...","action":{"type":"move|click|double_click|right_click|type|hotkey|press|scroll|wait|screenshot","x":0,"y":0,"text":"","keys":[],"key":"","amount":0,"seconds":0.8},"verify":"..."}. '
                "One action per step. Use in-screen coordinates only."
            )
            user = f"Goal: {goal}\nScreen: {w}x{h}\nHistory:\n" + ("\n".join(hist[-12:]) if hist else "(empty)")
            dec = _call_json(client, sys_prompt, user, b64=_model_b64(img))
        model_source = str(dec.get("_client_source", "") or "")
        if _to_bool(dec.get("done"), False):
            if local_ocr:
                _maybe_cleanup_obs(obs0, delete_after_read)
            logs.append({"step": step, "done": True, "reason": str(dec.get("reason", "")), "model_source": model_source})
            return {"ok": True, "logs": logs}
        if local_ocr and _to_bool(dec.get("handoff"), False):
            handoff = _build_handoff_payload(
                saved_path=str(obs0.get("saved_path", "") or ""),
                goal=goal,
                question=str(dec.get("question", "") or dec.get("reason", "") or "Inspect the screenshot and decide the next action."),
                last_action=last_action_result,
                source="goal_model",
                obs=obs0,
            )
            logs.append({"step": step, "handoff": True, "reason": str(dec.get("reason", "") or ""), "model_source": model_source})
            return {"ok": False, "error": "handoff_required", "handoff": handoff, "logs": logs}
        action = dec.get("action")
        if not isinstance(action, dict):
            if local_ocr:
                _maybe_cleanup_obs(obs0, delete_after_read)
            return {"ok": False, "error": "invalid action", "logs": logs}
        verify = str(dec.get("verify", "")).strip() or "screen moved toward goal"
        ar = act(action)
        last_action_result = str(ar.get("result", "") or "")
        if local_ocr:
            _maybe_cleanup_obs(obs0, delete_after_read)
            obs = observe(local_ocr=True, delete_after_read=False)
            if handoff_on_uncertain:
                need_handoff, handoff_reason = _ocr_needs_handoff(goal, obs)
                if need_handoff:
                    handoff = _build_handoff_payload(
                        saved_path=str(obs.get("saved_path", "") or ""),
                        goal=goal,
                        question=f"Action `{last_action_result}` ran, but OCR is insufficient for verification ({handoff_reason}). Inspect the screenshot and determine the next action.",
                        last_action=last_action_result,
                        source="goal_verify_precheck",
                        obs=obs,
                    )
                    logs.append({"step": step, "action": ar, "handoff": True, "reason": handoff_reason, "summary": obs.get("summary", "")})
                    return {"ok": False, "error": "handoff_required", "handoff": handoff, "logs": logs}
            ok, rs = _verify_local_ocr(client, verify, obs)
        else:
            obs = observe(condition=verify)
            ok = _to_bool(obs.get("ok"), False)
            rs = str(obs.get("reason", "")).strip()
        hist.append(f"step={step}; action={ar.get('result','')}; verify={verify}; ok={ok}; reason={rs}; summary={obs.get('summary','')}")
        logs.append({"step": step, "action": ar, "verify": verify, "verify_ok": ok, "verify_reason": rs, "summary": obs.get("summary", ""), "model_source": model_source})
        if local_ocr:
            _maybe_cleanup_obs(obs, delete_after_read)
    return {"ok": False, "error": "max_steps_reached", "logs": logs}


def tools() -> list[dict[str, Any]]:
    return [
        {"name": "desktop_capture", "description": "Capture screenshot.", "inputSchema": {"type": "object"}},
        {"name": "desktop_observe", "description": "Observe screenshot with local OCR, model vision, or handoff packaging.", "inputSchema": {"type": "object"}},
        {"name": "desktop_action", "description": "Run one mouse/keyboard action.", "inputSchema": {"type": "object"}},
        {"name": "desktop_batch", "description": "Run action batch.", "inputSchema": {"type": "object"}},
        {"name": "desktop_goal", "description": "Optional model-planned task.", "inputSchema": {"type": "object"}},
        {"name": "desktop_cleanup", "description": "Delete a pending handoff screenshot/job.", "inputSchema": {"type": "object"}},
    ]


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "desktop_capture":
        return capture(
            include_base64=_to_bool(args.get("include_base64"), True),
            save=str(args.get("save", "")).strip(),
            fmt=str(args.get("format", "png")),
            quality=_to_int(args.get("quality"), 82),
            region=args.get("region"),
        )
    if name == "desktop_observe":
        return observe(**_observe_kwargs(args))
    if name == "desktop_action":
        a = dict(args)
        if isinstance(a.get("keys"), str):
            a["keys"] = _keys(a["keys"])
        return act(a)
    if name == "desktop_batch":
        acts = args.get("actions", [])
        if not isinstance(acts, list):
            raise ValueError("actions must be list")
        return batch(
            acts,
            continue_on_error=_to_bool(args.get("continue_on_error"), False),
            sleep_between=max(0.0, min(10.0, _to_float(args.get("sleep_between"), 0.0))),
        )
    if name == "desktop_goal":
        return goal_run(**_goal_kwargs(args))
    if name == "desktop_cleanup":
        return cleanup(job_id=str(args.get("job_id", "")).strip(), path=str(args.get("path", "")).strip())
    raise ValueError(f"unknown tool: {name}")


def handle(req: dict[str, Any]) -> Optional[dict[str, Any]]:
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    try:
        if method == "initialize":
            r = {
                "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                "serverInfo": {"name": "desktop-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {"listChanged": False}},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            r = {}
        elif method == "tools/list":
            r = {"tools": tools()}
        elif method == "tools/call":
            data = call_tool(str(params.get("name", "")).strip(), params.get("arguments") or {})
            r = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
        else:
            if rid is None:
                return None
            raise ValueError(f"unsupported method: {method}")
        if rid is None:
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": r}
    except Exception as e:
        if rid is None:
            return None
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)}}


def _read_msg() -> tuple[Optional[str], str]:
    s = sys.stdin.buffer
    first = s.readline()
    if not first:
        return None, "eof"
    if not first.lower().startswith(b"content-length:"):
        t = first.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
        if not t:
            return _read_msg()
        return t, "jsonline"
    h = first.decode("ascii", errors="ignore").strip()
    m = re.match(r"content-length:\s*(\d+)", h, flags=re.IGNORECASE)
    if not m:
        raise ValueError("invalid Content-Length header")
    n = int(m.group(1))
    while True:
        line = s.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
    b = s.read(n)
    if b is None:
        return None, "eof"
    return b.decode("utf-8", errors="replace").lstrip("\ufeff"), "framed"


def _write_msg(resp: dict[str, Any], mode: str) -> None:
    t = json.dumps(resp, ensure_ascii=False)
    o = sys.stdout.buffer
    if mode == "jsonline":
        o.write((t + "\n").encode("utf-8"))
    else:
        p = t.encode("utf-8")
        o.write(f"Content-Length: {len(p)}\r\n\r\n".encode("ascii"))
        o.write(p)
    o.flush()


def cmd_mcp_serve(_: argparse.Namespace) -> int:
    while True:
        raw, mode = _read_msg()
        if raw is None:
            return 0
        raw = raw.strip()
        if not raw:
            continue
        resp = handle(json.loads(raw))
        if resp is not None:
            _write_msg(resp, mode)


def cmd_mcp(args: argparse.Namespace) -> int:
    raw = args.request if args.request else sys.stdin.read()
    req = json.loads(raw.lstrip("\ufeff").strip())
    print(json.dumps(handle(req), ensure_ascii=True))
    return 0


def cmd_observe(args: argparse.Namespace) -> int:
    data = observe(**_observe_kwargs(args))
    if args.json:
        print(json.dumps(data, ensure_ascii=True))
    else:
        print(f"summary: {data.get('summary', '')}")
        print(f"focus_window: {data.get('focus_window', '')}")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    rg = None
    if args.x is not None and args.y is not None and args.width is not None and args.height is not None:
        rg = {"x": args.x, "y": args.y, "width": args.width, "height": args.height}
    data = capture(include_base64=not args.no_base64, save=str(args.save or ""), fmt=args.format, quality=args.quality, region=rg)
    print(json.dumps(data, ensure_ascii=True) if args.json else data.get("save_path", f"capture {data.get('width')}x{data.get('height')}"))
    return 0


def _act_cli(args: argparse.Namespace) -> dict[str, Any]:
    a: dict[str, Any] = {"type": args.action_name}
    for k in ["x", "y", "dx", "dy", "text", "key", "keys", "button", "clicks", "interval", "duration", "amount", "seconds", "presses", "name", "save", "format", "quality", "include_base64"]:
        v = getattr(args, k, None)
        if v is not None:
            a[k] = v
    if args.region:
        p = [i.strip() for i in args.region.split(",") if i.strip()]
        if len(p) != 4:
            raise ValueError("--region must be x,y,width,height")
        a["region"] = {"x": _to_int(p[0], 0), "y": _to_int(p[1], 0), "width": _to_int(p[2], 0), "height": _to_int(p[3], 0)}
    if isinstance(a.get("keys"), str):
        a["keys"] = _keys(a["keys"])
    return a


def cmd_action(args: argparse.Namespace) -> int:
    name = args.action_name
    if name in {"move", "move_to", "drag_to", "drag"} and (args.x is None or args.y is None):
        raise ValueError(f"{name} requires --x and --y")
    if name == "type" and args.text is None:
        raise ValueError("type requires --text")
    if name in {"press", "key_press", "key_down", "key_up"} and args.key is None:
        raise ValueError(f"{name} requires --key")
    if name == "hotkey" and args.keys is None:
        raise ValueError("hotkey requires --keys")
    if name == "shortcut" and args.name is None:
        raise ValueError("shortcut requires --name")
    data = act(_act_cli(args))
    print(json.dumps(data, ensure_ascii=True) if args.json else data.get("result", "ok"))
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    if args.file:
        with open(args.file, "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
    elif args.actions:
        payload = json.loads(args.actions)
    else:
        raise ValueError("batch requires --file or --actions")
    if isinstance(payload, dict) and "actions" in payload:
        acts = payload["actions"]
        cont = _to_bool(payload.get("continue_on_error"), args.continue_on_error)
        gap = _to_float(payload.get("sleep_between"), args.sleep_between)
    else:
        acts = payload
        cont = args.continue_on_error
        gap = args.sleep_between
    if not isinstance(acts, list):
        raise ValueError("actions must be list")
    data = batch(acts, continue_on_error=cont, sleep_between=gap)
    print(json.dumps(data, ensure_ascii=True) if args.json else f"ok: {data.get('ok')}")
    return 0 if data.get("ok") else 1


def cmd_goal(args: argparse.Namespace) -> int:
    data = goal_run(**_goal_kwargs(args))
    print(json.dumps(data, ensure_ascii=True) if args.json else ("done" if data.get("ok") else data.get("error", "failed")))
    return 0 if data.get("ok") else 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    data = cleanup(job_id=str(args.job_id or ""), path=str(args.path or ""))
    print(json.dumps(data, ensure_ascii=True) if args.json else ("cleaned" if data.get("ok") else "not_found"))
    return 0 if data.get("ok") else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="desktop-mcp", description="Windows desktop MCP bridge")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("observe")
    o.add_argument("--condition", default="")
    o.add_argument("--save")
    o.add_argument("--local-only", action="store_true")
    o.add_argument("--local-ocr", action="store_true")
    o.add_argument("--delete-after-read", action="store_true")
    o.add_argument("--handoff", action="store_true")
    o.add_argument("--goal", default="")
    o.add_argument("--question", default="")
    o.add_argument("--last-action", default="")
    o.add_argument("--include-base64", action="store_true")
    o.add_argument("--json", action="store_true")
    o.set_defaults(func=cmd_observe)

    c = sub.add_parser("capture")
    c.add_argument("--save")
    c.add_argument("--format", default="png", choices=["png", "jpeg", "jpg"])
    c.add_argument("--quality", type=int, default=82)
    c.add_argument("--no-base64", action="store_true")
    c.add_argument("--x", type=int)
    c.add_argument("--y", type=int)
    c.add_argument("--width", type=int)
    c.add_argument("--height", type=int)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_capture)

    g = sub.add_parser("goal")
    g.add_argument("goal")
    g.add_argument("--max-steps", type=int, default=10)
    g.add_argument("--local-ocr", action="store_true")
    g.add_argument("--delete-after-read", action="store_true")
    g.add_argument("--handoff-on-uncertain", action="store_true", default=True)
    g.add_argument("--json", action="store_true")
    g.set_defaults(func=cmd_goal)

    cl = sub.add_parser("cleanup")
    cl.add_argument("--job-id", default="")
    cl.add_argument("--path", default="")
    cl.add_argument("--json", action="store_true")
    cl.set_defaults(func=cmd_cleanup)

    a = sub.add_parser("action")
    a.add_argument("action_name", choices=["position", "screenshot", "move", "move_to", "move_rel", "move_relative", "click", "double_click", "right_click", "middle_click", "mouse_down", "mouse_up", "drag_to", "drag", "drag_rel", "drag_relative", "scroll", "wheel", "hscroll", "scroll_horizontal", "type", "press", "key_press", "delete", "backspace", "key_down", "key_up", "hotkey", "shortcut", "wait"])
    a.add_argument("--x", type=int)
    a.add_argument("--y", type=int)
    a.add_argument("--dx", type=int)
    a.add_argument("--dy", type=int)
    a.add_argument("--text")
    a.add_argument("--key")
    a.add_argument("--keys")
    a.add_argument("--button", default="left")
    a.add_argument("--clicks", type=int)
    a.add_argument("--interval", type=float)
    a.add_argument("--duration", type=float)
    a.add_argument("--amount", type=int)
    a.add_argument("--seconds", type=float)
    a.add_argument("--presses", type=int)
    a.add_argument("--name")
    a.add_argument("--save")
    a.add_argument("--format", choices=["png", "jpeg", "jpg"])
    a.add_argument("--quality", type=int)
    a.add_argument("--include-base64", action="store_true")
    a.add_argument("--region", help="x,y,width,height")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_action)

    b = sub.add_parser("batch")
    b.add_argument("--file")
    b.add_argument("--actions")
    b.add_argument("--continue-on-error", action="store_true")
    b.add_argument("--sleep-between", type=float, default=0.0)
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_batch)

    m = sub.add_parser("mcp")
    m.add_argument("--request")
    m.set_defaults(func=cmd_mcp)

    ms = sub.add_parser("mcp-serve")
    ms.set_defaults(func=cmd_mcp_serve)
    return p


def main() -> int:
    if len(sys.argv) == 1:
        return cmd_mcp_serve(argparse.Namespace())
    p = parser()
    args = p.parse_args()
    try:
        return int(args.func(args))
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
