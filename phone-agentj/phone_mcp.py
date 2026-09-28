#!/usr/bin/env python3
"""
phone_mcp.py - dependency-free MCP server (JSON-RPC 2.0 over stdio) that
controls an Android phone through Shizuku's `rish` shell inside Termux.

Rules learned the hard way:
  * rish execs argv directly: always use full paths (/system/bin/input, ...)
  * rish cannot exec sh/cat/ls: read files (e.g. dump.xml) from Termux itself
"""
import sys, json, subprocess
import xml.etree.ElementTree as ET

DUMP_PATH = "/sdcard/dump.xml"


def rish(*args):
    r = subprocess.run(["rish", *args], capture_output=True, text=True)
    return (r.stdout.strip() or r.stderr.strip())


def parse_bounds(b):
    x1, y1, x2, y2 = map(int, b.replace("][", ",").strip("[]").split(","))
    return (x1 + x2) // 2, (y1 + y2) // 2


def compact_ui_tree(xml_text):
    root = ET.fromstring(xml_text)
    lines = []
    for node in root.iter("node"):
        text = node.get("text", "").strip()
        desc = node.get("content-desc", "").strip()
        clickable = node.get("clickable") == "true"
        cls = node.get("class", "").split(".")[-1]
        bounds = node.get("bounds", "")
        label = text or desc
        if (not label and not clickable) or not bounds:
            continue
        try:
            cx, cy = parse_bounds(bounds)
        except Exception:
            continue
        tag = "clickable" if clickable else "text"
        lines.append(f"[{tag}] {cls} '{label}' @ ({cx},{cy})")
    return "\n".join(lines) if lines else "(no actionable elements found)"


def dump_ui():
    rish("/system/bin/uiautomator", "dump", DUMP_PATH)
    with open(DUMP_PATH) as f:
        return f.read()


TOOLS = {
    "tap": {
        "description": "Tap the screen at pixel coordinates (x, y).",
        "params": {"x": "integer", "y": "integer"},
        "fn": lambda x, y: (rish("/system/bin/input", "tap", str(x), str(y)), f"tapped ({x}, {y})")[1],
    },
    "long_press": {
        "description": "Long-press at (x, y) for duration_ms milliseconds.",
        "params": {"x": "integer", "y": "integer", "duration_ms": "integer"},
        "fn": lambda x, y, duration_ms=600: (
            rish("/system/bin/input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)),
            f"long-pressed ({x},{y}) for {duration_ms}ms")[1],
    },
    "swipe": {
        "description": "Swipe from (x1,y1) to (x2,y2) over duration_ms milliseconds.",
        "params": {"x1": "integer", "y1": "integer", "x2": "integer", "y2": "integer", "duration_ms": "integer"},
        "fn": lambda x1, y1, x2, y2, duration_ms=300: (
            rish("/system/bin/input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)),
            f"swiped ({x1},{y1})->({x2},{y2})")[1],
    },
    "type_text": {
        "description": "Type text into the currently focused input field.",
        "params": {"text": "string"},
        "fn": lambda text: (rish("/system/bin/input", "text", text), f"typed: {text}")[1],
    },
    "key_event": {
        "description": "Send a key event, e.g. KEYCODE_BACK, KEYCODE_HOME, KEYCODE_ENTER, KEYCODE_APP_SWITCH, KEYCODE_DEL.",
        "params": {"keycode": "string"},
        "fn": lambda keycode: (rish("/system/bin/input", "keyevent", keycode), f"sent {keycode}")[1],
    },
    "launch_app": {
        "description": "Launch an app by Android package name (e.g. com.whatsapp, com.spotify.music). Returns the launcher's real output.",
        "params": {"package": "string"},
        "fn": lambda package: rish("/system/bin/monkey", "-p", package, "-c",
                                   "android.intent.category.LAUNCHER", "1"),
    },
    "open_spotify_search": {
        "description": "Open Spotify directly on search results for a query. Prefer this over manual tap/type navigation when searching Spotify.",
        "params": {"query": "string"},
        "fn": lambda query: (
            rish("/system/bin/am", "start", "-a", "android.intent.action.VIEW",
                 "-d", "spotify:search:" + query.replace(" ", "%20")),
            f"opened Spotify search for '{query}'")[1],
    },
    "get_current_app": {
        "description": "Get the window/app currently in focus.",
        "params": {},
        "fn": lambda: rish("/system/bin/dumpsys", "window", "windows") or "(no output)",
    },
    "get_screen_state": {
        "description": "Get the current UI as a compact list of visible elements with tap coordinates.",
        "params": {},
        "fn": lambda: compact_ui_tree(dump_ui()),
    },
    "get_screen_state_raw": {
        "description": "Get raw UI hierarchy XML (only if the compact view is insufficient).",
        "params": {},
        "fn": lambda: dump_ui(),
    },
    "screenshot": {
        "description": "Save a screenshot to /sdcard/screen.png (returns confirmation, not image data).",
        "params": {},
        "fn": lambda: (rish("/system/bin/screencap", "-p", "/sdcard/screen.png"), "saved to /sdcard/screen.png")[1],
    },
}


def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "phone-control", "version": "0.3"}}
    if method == "tools/list":
        return {"tools": [
            {"name": n, "description": t["description"],
             "inputSchema": {"type": "object",
                             "properties": {k: {"type": v} for k, v in t["params"].items()},
                             "required": list(t["params"])}}
            for n, t in TOOLS.items()]}
    if method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": f"error: unknown tool '{name}'"}], "isError": True}
        try:
            return {"content": [{"type": "text", "text": str(TOOLS[name]["fn"](**args))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    return {"error": f"unknown method {method}"}


if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:  # notification, no reply
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}), flush=True)
