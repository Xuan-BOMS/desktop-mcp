import argparse
import base64
import json
import os
import re
import sys
import time
import winreg
from io import BytesIO
from typing import Any, Optional

import pyautogui
from litellm import completion


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.12


def _read_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _read_user_env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def _read_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = _read_user_env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return max(minimum, min(maximum, value))


def _read_user_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        raw, _ = winreg.QueryValueEx(key, name)
        return str(raw).strip()
    except OSError:
        return ""


MODEL_TIMEOUT_SEC = _read_float_env("DESKTOP_MCP_MODEL_TIMEOUT_SEC", 16.0, 5.0, 90.0)
MODEL_RETRIES = _read_int_env("DESKTOP_MCP_MODEL_RETRIES", 1, 0, 6)
MODEL_MAX_IMAGE_SIDE = _read_int_env("DESKTOP_MCP_IMAGE_MAX_SIDE", 1400, 640, 2560)
MODEL_IMAGE_QUALITY = _read_int_env("DESKTOP_MCP_IMAGE_QUALITY", 70, 30, 95)
GOAL_DEADLINE_SEC = _read_float_env("DESKTOP_MCP_GOAL_DEADLINE_SEC", 105.0, 20.0, 240.0)


def load_client() -> dict[str, str]:
    api_key = _read_user_env("OI_THIRD_PARTY_API_KEY")
    api_base = _read_user_env("OI_THIRD_PARTY_API_BASE")
    model = _read_user_env("OI_THIRD_PARTY_MODEL") or "gpt-5.3-codex"
    if "/" not in model:
        model = f"openai/{model}"
    if not api_key or not api_base:
        raise RuntimeError(
            "Missing third-party config. Set OI_THIRD_PARTY_API_BASE / OI_THIRD_PARTY_API_KEY / OI_THIRD_PARTY_MODEL"
        )
    return {"api_key": api_key, "api_base": api_base, "model": model}


def _capture_screenshot() -> tuple[Any, tuple[int, int], bytes]:
    img = pyautogui.screenshot()
    w, h = img.size
    buf = BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    return img, (w, h), raw


def _to_model_b64(img: Any) -> str:
    # Reduce payload size for lower latency and fewer transport errors.
    w, h = img.size
    if max(w, h) > MODEL_MAX_IMAGE_SIDE:
        img = img.copy()
        img.thumbnail((MODEL_MAX_IMAGE_SIDE, MODEL_MAX_IMAGE_SIDE))
    if getattr(img, "mode", "") not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=MODEL_IMAGE_QUALITY, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"model did not return JSON: {text[:220]}")
    return json.loads(m.group(0))


def _call_json(
    client: dict[str, str],
    system_prompt: str,
    user_text: str,
    with_screenshot: bool = True,
    screenshot_b64: Optional[str] = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    if with_screenshot:
        b64 = screenshot_b64
        if not b64:
            img, _, _ = _capture_screenshot()
            b64 = _to_model_b64(img)
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    last_exc: Optional[Exception] = None
    for attempt in range(MODEL_RETRIES + 1):
        try:
            resp = completion(
                model=client["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                api_key=client["api_key"],
                api_base=client["api_base"],
                temperature=1,
                timeout=MODEL_TIMEOUT_SEC,
            )
            raw = (resp.choices[0].message.content or "").strip()
            return _extract_json(raw)
        except Exception as exc:
            last_exc = exc
            message = str(exc).lower()
            retryable = any(
                token in message
                for token in (
                    "ssl",
                    "eof",
                    "timeout",
                    "timed out",
                    "connection",
                    "temporar",
                    "429",
                    "500",
                    "502",
                    "503",
                    "504",
                    "internalservererror",
                )
            )
            if attempt >= MODEL_RETRIES or not retryable:
                break
            time.sleep(min(0.8 * (2**attempt), 2.2))
    if last_exc is None:
        raise RuntimeError("model call failed without exception")
    raise last_exc


def _inspect_screen(client: dict[str, str], verify_condition: str = "", screenshot_b64: Optional[str] = None) -> dict[str, Any]:
    user_text = "Describe current screen"
    if verify_condition.strip():
        user_text = f"Condition: {verify_condition.strip()}"
    system_prompt = (
        "You are a desktop vision observer and verifier. Return strict JSON only: "
        '{"summary":"...","focus_window":"...","key_text":["..."],"ok":true/false,"reason":"..."}. '
        "If no condition is provided, set ok=true and reason='n/a'. Keep summary short and factual."
    )
    return _call_json(client, system_prompt, user_text, with_screenshot=True, screenshot_b64=screenshot_b64)


def _summarize_screen(client: dict[str, str]) -> dict[str, Any]:
    data = _inspect_screen(client)
    return {
        "summary": data.get("summary", ""),
        "focus_window": data.get("focus_window", ""),
        "key_text": data.get("key_text", []),
    }


def _verify(client: dict[str, str], condition: str, tries: int, interval: float) -> tuple[bool, str]:
    reason = ""
    for _ in range(max(1, tries)):
        data = _inspect_screen(client, verify_condition=condition)
        ok = bool(data.get("ok", False))
        reason = str(data.get("reason", "")).strip()
        if ok:
            return True, reason
        time.sleep(max(0.5, interval))
    return False, reason


def _clamp_xy(x: int, y: int) -> tuple[int, int]:
    sw, sh = pyautogui.size()
    return max(0, min(sw - 1, int(x))), max(0, min(sh - 1, int(y)))


def run_action(action: dict[str, Any]) -> str:
    t = str(action.get("type", "")).strip().lower()
    if t in ("move", "click", "double_click"):
        x, y = _clamp_xy(action.get("x", 0), action.get("y", 0))
        if t == "move":
            pyautogui.moveTo(x, y, duration=0.12)
        elif t == "click":
            pyautogui.click(x=x, y=y)
        else:
            pyautogui.doubleClick(x=x, y=y)
        return f"{t}({x},{y})"
    if t == "type":
        text = str(action.get("text", ""))
        pyautogui.write(text, interval=0.01)
        return f"type({len(text)} chars)"
    if t == "hotkey":
        keys = action.get("keys", [])
        if isinstance(keys, list) and keys:
            keys = [str(k).strip().lower() for k in keys if str(k).strip()]
            pyautogui.hotkey(*keys)
            return "hotkey(" + "+".join(keys) + ")"
        return "hotkey(noop)"
    if t == "press":
        key = str(action.get("key", "")).strip().lower()
        if key:
            pyautogui.press(key)
            return f"press({key})"
        return "press(noop)"
    if t == "scroll":
        amount = int(action.get("amount", -300))
        pyautogui.scroll(amount)
        return f"scroll({amount})"
    if t == "wait":
        sec = float(action.get("seconds", 0.8))
        sec = max(0.0, min(12.0, sec))
        time.sleep(sec)
        return f"wait({sec:.1f}s)"
    if t == "read_screen":
        return "read_screen"
    raise ValueError(f"unsupported action type: {t}")


def decide_next_step(client: dict[str, str], goal: str, history: list[str]) -> dict[str, Any]:
    sw, sh = pyautogui.size()
    px, py = pyautogui.position()
    history_text = "\n".join(history[-10:]) if history else "(empty)"
    system_prompt = f"""
You are the ONLY planner for desktop GUI tasks.
Return strict JSON only in this schema:
{{
  "done": true/false,
  "reason": "short reason",
  "action": {{
    "type":"move|click|double_click|type|hotkey|press|scroll|wait|read_screen",
    "x":0,"y":0,"text":"","keys":[],"key":"","amount":0,"seconds":0.8
  }},
  "verify": "one visual condition after this action"
}}
Rules:
- One action per step.
- Coordinates must be in screen bounds x:[0,{sw-1}] y:[0,{sh-1}].
- If uncertain, use read_screen or wait, never random click.
- done=true only when goal is visibly completed.
Current mouse: ({px},{py})
""".strip()
    user_text = f"Goal: {goal}\nHistory:\n{history_text}\nGive next step."
    return _call_json(client, system_prompt, user_text, with_screenshot=True)


def cmd_observe(args: argparse.Namespace) -> int:
    client = load_client()
    img, (w, h), raw = _capture_screenshot()
    b64 = _to_model_b64(img)
    data = _inspect_screen(client, screenshot_b64=b64)
    data["screen_size"] = {"width": w, "height": h}
    data.pop("ok", None)
    data.pop("reason", None)
    if args.save:
        path = os.path.abspath(args.save)
        with open(path, "wb") as f:
            f.write(raw)
        data["screenshot"] = path
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(f"summary: {data.get('summary', '')}")
        print(f"focus_window: {data.get('focus_window', '')}")
        keys = data.get("key_text", [])
        if isinstance(keys, list) and keys:
            print("key_text:")
            for k in keys[:12]:
                print(f"- {k}")
    return 0


def cmd_action(args: argparse.Namespace) -> int:
    action: dict[str, Any]
    if args.action_name in {"move", "click", "double_click"}:
        action = {"type": args.action_name, "x": args.x, "y": args.y}
    elif args.action_name == "type":
        action = {"type": "type", "text": args.text}
    elif args.action_name == "hotkey":
        keys = [k.strip().lower() for k in re.split(r"[+\s]+", args.keys) if k.strip()]
        action = {"type": "hotkey", "keys": keys}
    elif args.action_name == "press":
        action = {"type": "press", "key": args.key}
    elif args.action_name == "scroll":
        action = {"type": "scroll", "amount": args.amount}
    else:
        action = {"type": "wait", "seconds": args.seconds}

    result = run_action(action)
    print(result)
    return 0


def cmd_goal(args: argparse.Namespace) -> int:
    client = load_client()
    goal = args.goal.strip()
    history: list[str] = []
    deadline = time.monotonic() + GOAL_DEADLINE_SEC

    if args.json:
        logs: list[dict[str, Any]] = []

    for step in range(1, max(1, args.max_steps) + 1):
        required_budget = MODEL_TIMEOUT_SEC * (1 + max(1, int(args.verify_tries))) + 3.0
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (step > 1 and remaining < required_budget):
            if args.json:
                print(
                    json.dumps(
                        {"ok": False, "error": "deadline_exceeded", "deadline_sec": GOAL_DEADLINE_SEC, "logs": logs},
                        ensure_ascii=False,
                    )
                )
            else:
                print("stop: deadline exceeded")
            return 1

        decision = decide_next_step(client, goal, history)
        done = bool(decision.get("done", False))
        reason = str(decision.get("reason", "")).strip()

        if done:
            if args.json:
                logs.append({"step": step, "done": True, "reason": reason})
                print(json.dumps({"ok": True, "logs": logs}, ensure_ascii=False))
            else:
                print(f"done: {reason or 'completed'}")
            return 0

        action = decision.get("action", {})
        if not isinstance(action, dict) or not action:
            err = "invalid action from model"
            if args.json:
                print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
            else:
                print(f"error: {err}")
            return 2

        verify = str(decision.get("verify", "")).strip() or "screen moved toward goal"
        result = run_action(action)

        observe_condition = "" if str(action.get("type", "")).lower() == "read_screen" else verify
        obs: dict[str, Any] = {}
        ok = not observe_condition
        reason2 = "screen captured"
        for attempt in range(max(1, args.verify_tries)):
            obs = _inspect_screen(client, verify_condition=observe_condition)
            if not observe_condition:
                break
            ok = bool(obs.get("ok", False))
            reason2 = str(obs.get("reason", "")).strip()
            if ok or attempt >= max(1, args.verify_tries) - 1:
                break
            time.sleep(max(0.5, float(args.verify_interval)))

        history.append(
            f"step={step}; action={result}; verify={verify}; ok={ok}; reason={reason2}; summary={obs.get('summary','')}"
        )

        if args.json:
            logs.append(
                {
                    "step": step,
                    "action": result,
                    "verify": verify,
                    "verify_ok": ok,
                    "verify_reason": reason2,
                    "summary": obs.get("summary", ""),
                }
            )
        else:
            print(f"[{step}] action: {result}")
            print(f"    verify: {'ok' if ok else 'no'} | {verify}")
            if reason2:
                print(f"    reason: {reason2}")
            print(f"    observe: {obs.get('summary','')}")

    if args.json:
        print(json.dumps({"ok": False, "error": "max_steps_reached", "logs": logs}, ensure_ascii=False))
    else:
        print("stop: max steps reached")
    return 1


def mcp_tools_list() -> list[dict[str, Any]]:
    return [
        {
            "name": "desktop_observe",
            "description": "Capture current screen and return vision summary (third-party model).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "save": {"type": "string", "description": "Optional png path to save screenshot."}
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "desktop_goal",
            "description": "Execute a GUI task by model planning with per-step visual verification.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "max_steps": {"type": "integer", "minimum": 1, "default": 8},
                    "verify_tries": {"type": "integer", "minimum": 1, "default": 2},
                    "verify_interval": {"type": "number", "minimum": 0.5, "default": 1.8},
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
        },
        {
            "name": "desktop_action",
            "description": "Run one direct desktop action.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["move", "click", "double_click", "type", "hotkey", "press", "scroll", "wait"],
                    },
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "text": {"type": "string"},
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "key": {"type": "string"},
                    "amount": {"type": "integer"},
                    "seconds": {"type": "number"},
                },
                "required": ["type"],
                "additionalProperties": False,
            },
        },
    ]


def mcp_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "desktop_observe":
        client = load_client()
        img, (w, h), raw = _capture_screenshot()
        b64 = _to_model_b64(img)
        data = _inspect_screen(client, screenshot_b64=b64)
        data["screen_size"] = {"width": w, "height": h}
        data.pop("ok", None)
        data.pop("reason", None)
        save_path = str(arguments.get("save", "")).strip()
        if save_path:
            path = os.path.abspath(save_path)
            with open(path, "wb") as f:
                f.write(raw)
            data["screenshot"] = path
        return data

    if name == "desktop_goal":
        ns = argparse.Namespace(
            goal=str(arguments.get("goal", "")),
            max_steps=int(arguments.get("max_steps", 8)),
            verify_tries=int(arguments.get("verify_tries", 2)),
            verify_interval=float(arguments.get("verify_interval", 1.8)),
            json=True,
        )
        if not ns.goal:
            raise ValueError("goal is required")
        old_stdout = sys.stdout
        try:
            from io import StringIO

            buf = StringIO()
            sys.stdout = buf
            code = cmd_goal(ns)
            output = buf.getvalue().strip()
        finally:
            sys.stdout = old_stdout
        if output:
            try:
                payload = json.loads(output)
            except Exception:
                payload = {"ok": code == 0, "raw": output}
        else:
            payload = {"ok": code == 0}
        return payload

    if name == "desktop_action":
        t = str(arguments.get("type", "")).strip().lower()
        action: dict[str, Any] = {"type": t}
        if t in {"move", "click", "double_click"}:
            action["x"] = int(arguments["x"])
            action["y"] = int(arguments["y"])
        elif t == "type":
            action["text"] = str(arguments.get("text", ""))
        elif t == "hotkey":
            keys = arguments.get("keys", [])
            if isinstance(keys, str):
                keys = [k for k in re.split(r"[+\s]+", keys) if k]
            action["keys"] = keys
        elif t == "press":
            action["key"] = str(arguments.get("key", ""))
        elif t == "scroll":
            action["amount"] = int(arguments.get("amount", -300))
        elif t == "wait":
            action["seconds"] = float(arguments.get("seconds", 0.8))
        result = run_action(action)
        return {"result": result}

    raise ValueError(f"unknown tool: {name}")


def mcp_handle_request(req: dict[str, Any]) -> Optional[dict[str, Any]]:
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    try:
        if method == "initialize":
            client_proto = params.get("protocolVersion")
            result = {
                "protocolVersion": client_proto or "2024-11-05",
                "serverInfo": {"name": "desktop-mcp", "version": "0.1.0"},
                "capabilities": {"tools": {"listChanged": False}},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": mcp_tools_list()}
        elif method == "tools/call":
            name = str(params.get("name", "")).strip()
            arguments = params.get("arguments") or {}
            data = mcp_call_tool(name, arguments)
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
        else:
            if req_id is None:
                return None
            raise ValueError(f"unsupported method: {method}")
        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as exc:
        if req_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def _read_stdio_message() -> tuple[Optional[str], str]:
    stream = sys.stdin.buffer
    first = stream.readline()
    if not first:
        return None, "eof"

    # JSON line mode fallback
    if not first.lower().startswith(b"content-length:"):
        text = first.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
        if not text:
            return _read_stdio_message()
        return text, "jsonline"

    # Content-Length framed mode
    header_line = first.decode("ascii", errors="ignore").strip()
    m = re.match(r"content-length:\s*(\d+)", header_line, flags=re.IGNORECASE)
    if not m:
        raise ValueError("invalid Content-Length header")
    content_length = int(m.group(1))

    while True:
        line = stream.readline()
        if not line:
            break
        if line in (b"\r\n", b"\n"):
            break

    body = stream.read(content_length)
    if body is None:
        return None, "eof"
    return body.decode("utf-8", errors="replace").lstrip("\ufeff"), "framed"


def _write_stdio_message(resp: dict[str, Any], mode: str) -> None:
    text = json.dumps(resp, ensure_ascii=False)
    out = sys.stdout.buffer
    if mode == "jsonline":
        out.write((text + "\n").encode("utf-8"))
    else:
        payload = text.encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        out.write(header)
        out.write(payload)
    out.flush()


def cmd_mcp_serve(_: argparse.Namespace) -> int:
    while True:
        raw, mode = _read_stdio_message()
        if raw is None:
            return 0
        raw = raw.strip()
        if not raw:
            continue
        req = json.loads(raw)
        resp = mcp_handle_request(req)
        if resp is not None:
            _write_stdio_message(resp, mode)


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.request:
        raw = args.request
    else:
        raw = sys.stdin.read()
    raw = raw.lstrip("\ufeff").strip()
    req = json.loads(raw)
    resp = mcp_handle_request(req)
    print(json.dumps(resp, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desktop-mcp", description="Desktop automation bridge (third-party model planner)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_obs = sub.add_parser("observe", help="Read current screen by model")
    p_obs.add_argument("--save", help="save screenshot png path")
    p_obs.add_argument("--json", action="store_true", help="print JSON")
    p_obs.set_defaults(func=cmd_observe)

    p_goal = sub.add_parser("goal", help="Run a model-planned GUI goal")
    p_goal.add_argument("goal", help="goal text")
    p_goal.add_argument("--max-steps", type=int, default=8)
    p_goal.add_argument("--verify-tries", type=int, default=2)
    p_goal.add_argument("--verify-interval", type=float, default=1.8)
    p_goal.add_argument("--json", action="store_true", help="print JSON logs")
    p_goal.set_defaults(func=cmd_goal)

    p_action = sub.add_parser("action", help="Run one direct input action")
    p_action.add_argument("action_name", choices=["move", "click", "double_click", "type", "hotkey", "press", "scroll", "wait"])
    p_action.add_argument("--x", type=int)
    p_action.add_argument("--y", type=int)
    p_action.add_argument("--text")
    p_action.add_argument("--keys")
    p_action.add_argument("--key")
    p_action.add_argument("--amount", type=int, default=-300)
    p_action.add_argument("--seconds", type=float, default=0.8)
    p_action.set_defaults(func=cmd_action)

    p_mcp = sub.add_parser("mcp", help="One-shot MCP-like JSON-RPC request")
    p_mcp.add_argument("--request", help="JSON-RPC request string. If omitted, read stdin.")
    p_mcp.set_defaults(func=cmd_mcp)

    p_mcp_serve = sub.add_parser("mcp-serve", help="Run persistent stdio MCP server")
    p_mcp_serve.set_defaults(func=cmd_mcp_serve)

    return parser


def validate_action_args(args: argparse.Namespace) -> None:
    name = args.action_name
    if name in {"move", "click", "double_click"}:
        if args.x is None or args.y is None:
            raise ValueError("--x and --y are required for move/click/double_click")
    elif name == "type":
        if args.text is None:
            raise ValueError("--text is required for type")
    elif name == "hotkey":
        if args.keys is None:
            raise ValueError("--keys is required for hotkey")
    elif name == "press":
        if args.key is None:
            raise ValueError("--key is required for press")


def main() -> int:
    # Default behavior for Codex MCP stdio launch: no args => persistent MCP server.
    if len(sys.argv) == 1:
        return cmd_mcp_serve(argparse.Namespace())

    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.cmd == "action":
            validate_action_args(args)
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
