#!/usr/bin/env python3
"""
phone_mcp.py - dependency-free MCP server (JSON-RPC 2.0 over stdio) that controls an
Android phone through Shizuku's `rish` inside Termux. Tool set mirrors the members of
Anthropic's computer use toolset, adapted to a touchscreen.

Rules learned the hard way:
  * rish execs argv directly: always use full paths (/system/bin/input, ...)
  * rish cannot exec sh/cat/ls: read files (dump.xml, screen.png) from Termux itself
  * Coordinates everywhere are SCREEN PIXELS (same space as get_screen_state / screenshot)
Optional for image tools: `pkg install python-pillow` (or imagemagick).
"""
import sys, os, re, io, json, time, base64, shutil, subprocess, tempfile
import xml.etree.ElementTree as ET

DUMP_PATH = os.environ.get("PHONE_DUMP_PATH", "/sdcard/dump.xml")
SHOT_PATH = os.environ.get("PHONE_SHOT_PATH", "/sdcard/screen.png")
RISH = os.environ.get("PHONE_RISH", "rish")
INPUT, UIA, SCREENCAP = "/system/bin/input", "/system/bin/uiautomator", "/system/bin/screencap"
AM, PM, WM, DUMPSYS, MONKEY = ("/system/bin/am", "/system/bin/pm", "/system/bin/wm",
                                "/system/bin/dumpsys", "/system/bin/monkey")


class ToolError(Exception):
    """Reported to the model as a failed tool call (isError)."""


def rish(*args, check=False):
    r = subprocess.run([RISH, *args], capture_output=True, text=True)
    out = r.stdout.strip() or r.stderr.strip()
    # Only treat a non-zero exit as failure if something was printed, so a silent
    # non-zero exit can never break an otherwise working action.
    if check and r.returncode != 0 and out:
        raise ToolError(out)
    return out


def act(*args):
    return rish(*args, check=True)


# ------------------------------------------------------------------ registry
TOOLS = {}


def tool(description, params=None, optional=()):
    def deco(fn):
        TOOLS[fn.__name__] = dict(description=description, params=params or {},
                                  optional=set(optional), fn=fn)
        return fn
    return deco


# ------------------------------------------------------------------ UI reading
MAX_ELEMENTS = 70
MAX_LABEL = 60


def parse_bounds(b):
    x1, y1, x2, y2 = map(int, b.replace("][", ",").strip("[]").split(","))
    return x1, y1, x2, y2


def center(b):
    x1, y1, x2, y2 = parse_bounds(b)
    return (x1 + x2) // 2, (y1 + y2) // 2


def dump_ui():
    started = time.time() - 1
    out = rish(UIA, "dump", DUMP_PATH)
    if "ERROR" in out.upper() and "dumped" not in out.lower():
        raise ToolError(f"uiautomator dump failed: {out}")
    try:
        if os.path.getmtime(DUMP_PATH) < started:
            raise ToolError(f"UI dump is stale (uiautomator said: {out or 'nothing'})")
        with open(DUMP_PATH) as f:
            return f.read()
    except OSError as e:
        raise ToolError(f"cannot read UI dump: {e}")


def compact_ui_tree(xml_text):
    root = ET.fromstring(xml_text)
    lines, pkg = [], None
    for node in root.iter("node"):
        pkg = pkg or node.get("package")
        text = node.get("text", "").strip()
        desc = node.get("content-desc", "").strip()
        clickable = node.get("clickable") == "true"
        focused = node.get("focused") == "true"
        cls = node.get("class", "").split(".")[-1]
        bounds = node.get("bounds", "")
        label = (text or desc).replace("\n", " ")
        if len(label) > MAX_LABEL:
            label = label[:MAX_LABEL] + "..."
        if (not label and not clickable) or not bounds:
            continue
        try:
            cx, cy = center(bounds)
        except Exception:
            continue
        tag = "clickable" if clickable else "text"
        if focused:
            tag += " FOCUSED"
        lines.append(f"[{tag}] {cls} '{label}' @ ({cx},{cy})")
    if len(lines) > MAX_ELEMENTS:
        extra = len(lines) - MAX_ELEMENTS
        lines = lines[:MAX_ELEMENTS] + [f"(... {extra} more elements omitted)"]
    return f"[app] {pkg or 'unknown'}\n" + ("\n".join(lines) if lines else "(no actionable elements found)")


def find_matches(query):
    """Elements whose text/content-desc matches: exact first, then substring; clickable first."""
    q = query.strip().lower()
    root = ET.fromstring(dump_ui())
    exact, partial = [], []
    for node in root.iter("node"):
        label = (node.get("text", "").strip() or node.get("content-desc", "").strip())
        bounds = node.get("bounds", "")
        if not label or not bounds:
            continue
        low = label.replace("\n", " ").lower()
        try:
            cx, cy = center(bounds)
        except Exception:
            continue
        item = (label.replace("\n", " ")[:MAX_LABEL], cx, cy, node.get("clickable") == "true")
        if low == q:
            exact.append(item)
        elif q in low:
            partial.append(item)
    key = lambda it: (not it[3])
    return sorted(exact, key=key) + sorted(partial, key=key)


_size = None


def screen_size():
    global _size
    if _size:
        return _size
    found = re.findall(r"(\d+)x(\d+)", rish(WM, "size"))
    if found:
        _size = tuple(map(int, found[-1]))  # "Override size" (listed last) wins
    else:
        root = ET.fromstring(dump_ui()).find("node")
        _, _, w, h = parse_bounds(root.get("bounds"))
        _size = (w, h)
    return _size


# ------------------------------------------------------------------ images
def _encode(png, region=None):
    """PNG bytes -> (bytes, mime, (w, h)). Region is (x0,y0,x1,y1) in screen pixels."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB")
        if region:
            x0, y0, x1, y1 = region
            im = im.crop((max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)))
            long_edge = max(im.size)
            if 0 < long_edge < 800:  # small crops: upscale so text is legible
                k = 800 / long_edge
                im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return buf.getvalue(), "image/jpeg", im.size
    except ImportError:
        pass
    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "s.png"), os.path.join(d, "o.jpg")
            with open(src, "wb") as f:
                f.write(png)
            cmd = [magick, src]
            if region:
                x0, y0, x1, y1 = region
                cmd += ["-crop", f"{x1 - x0}x{y1 - y0}+{x0}+{y0}", "+repage"]
            cmd += ["-quality", "72", dst]
            subprocess.run(cmd, check=True, capture_output=True)
            with open(dst, "rb") as f:
                return f.read(), "image/jpeg", None
    if region:
        raise ToolError("zoom needs Pillow or ImageMagick: pkg install python-pillow")
    return png, "image/png", None


def take_shot(region=None):
    started = time.time() - 1
    out = rish(SCREENCAP, "-p", SHOT_PATH)
    try:
        if os.path.getmtime(SHOT_PATH) < started:
            raise ToolError(f"screencap produced no new image ({out or 'no output'})")
        with open(SHOT_PATH, "rb") as f:
            png = f.read()
    except OSError as e:
        raise ToolError(f"cannot read screenshot: {e}")
    try:
        os.remove(SHOT_PATH)  # don't leave screenshots lying around shared storage
    except OSError:
        pass
    data, mime, size = _encode(png, region)
    sw, sh = screen_size()
    shape = f"{size[0]}x{size[1]}px image" if size else "image"
    where = f" of region {list(region)}" if region else ""
    return dict(text=f"{shape}{where}. Screen is {sw}x{sh} px; ALL tool coordinates are screen pixels.",
                image=base64.b64encode(data).decode(), mime=mime)


# ------------------------------------------------------------------ keys
KEY_ALIASES = {
    "return": "KEYCODE_ENTER", "enter": "KEYCODE_ENTER", "tab": "KEYCODE_TAB",
    "escape": "KEYCODE_ESCAPE", "esc": "KEYCODE_ESCAPE", "backspace": "KEYCODE_DEL",
    "delete": "KEYCODE_FORWARD_DEL", "space": "KEYCODE_SPACE", "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME", "up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT", "right": "KEYCODE_DPAD_RIGHT", "ctrl": "KEYCODE_CTRL_LEFT",
    "shift": "KEYCODE_SHIFT_LEFT", "alt": "KEYCODE_ALT_LEFT", "super": "KEYCODE_META_LEFT",
    "page_up": "KEYCODE_PAGE_UP", "page_down": "KEYCODE_PAGE_DOWN",
    "volume_up": "KEYCODE_VOLUME_UP", "volume_down": "KEYCODE_VOLUME_DOWN",
    "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE", "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS", "app_switch": "KEYCODE_APP_SWITCH",
    "recents": "KEYCODE_APP_SWITCH", "menu": "KEYCODE_MENU",
}


def to_keycode(k):
    k = k.strip()
    if k.upper().startswith("KEYCODE_"):
        return k.upper()
    kl = k.lower()
    if kl in KEY_ALIASES:
        return KEY_ALIASES[kl]
    if len(k) == 1 and k.isalnum():
        return "KEYCODE_" + k.upper()
    if re.fullmatch(r"f\d{1,2}", kl):
        return "KEYCODE_" + k.upper()
    raise ToolError(f"unknown key '{k}'")


# ------------------------------------------------------------------ tools
@tool("Tap the screen at pixel coordinates (x, y).", {"x": "integer", "y": "integer"})
def tap(x, y):
    act(INPUT, "tap", str(x), str(y))
    return f"tapped ({x}, {y})"


@tool("Tap the element whose text/description matches `text` (case-insensitive; exact match "
      "preferred). Much more reliable than guessing coordinates. `index` picks among several matches.",
      {"text": "string", "index": "integer"}, optional=["index"])
def tap_text(text, index=0):
    matches = find_matches(text)
    if not matches:
        labels = [m[0] for m in find_matches("")][:20]
        raise ToolError(f"no element matches '{text}'. Visible labels: {labels}")
    i = max(0, min(int(index), len(matches) - 1))
    label, cx, cy, _ = matches[i]
    act(INPUT, "tap", str(cx), str(cy))
    return f"tapped '{label}' at ({cx},{cy}) [match {i + 1} of {len(matches)}]"


@tool("Long-press at (x, y) (like a right-click / context menu).",
      {"x": "integer", "y": "integer", "duration_ms": "integer"}, optional=["duration_ms"])
def long_press(x, y, duration_ms=600):
    act(INPUT, "swipe", str(x), str(y), str(x), str(y), str(duration_ms))
    return f"long-pressed ({x},{y}) for {duration_ms}ms"


@tool("Swipe from (x1,y1) to (x2,y2).",
      {"x1": "integer", "y1": "integer", "x2": "integer", "y2": "integer", "duration_ms": "integer"},
      optional=["duration_ms"])
def swipe(x1, y1, x2, y2, duration_ms=300):
    act(INPUT, "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
    return f"swiped ({x1},{y1})->({x2},{y2})"


@tool("Press at (x1,y1), drag to (x2,y2) and release (drag-and-drop: reorder items, move icons).",
      {"x1": "integer", "y1": "integer", "x2": "integer", "y2": "integer", "duration_ms": "integer"},
      optional=["duration_ms"])
def drag(x1, y1, x2, y2, duration_ms=800):
    act(INPUT, "draganddrop", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
    return f"dragged ({x1},{y1})->({x2},{y2})"


@tool("Scroll the view in a direction, like a mouse wheel: 'down' reveals content further down. "
      "`amount` = wheel clicks (default 3, max 10). Optional (x,y) = where to scroll (default screen centre).",
      {"direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
       "amount": "integer", "x": "integer", "y": "integer"}, optional=["amount", "x", "y"])
def scroll(direction, amount=3, x=None, y=None):
    if direction not in ("up", "down", "left", "right"):
        raise ToolError("direction must be up, down, left or right")
    w, h = screen_size()
    cx = w // 2 if x is None else int(x)
    cy = h // 2 if y is None else int(y)
    n = max(1, min(int(amount), 10))
    dy, dx = min(int(h * 0.12 * n), int(h * 0.8)), min(int(w * 0.12 * n), int(w * 0.8))
    clamp = lambda v, hi: max(1, min(v, hi - 1))
    # finger moves opposite to the scroll direction
    x1, y1, x2, y2 = {
        "down": (cx, cy + dy // 2, cx, cy - dy // 2),
        "up": (cx, cy - dy // 2, cx, cy + dy // 2),
        "right": (cx + dx // 2, cy, cx - dx // 2, cy),
        "left": (cx - dx // 2, cy, cx + dx // 2, cy),
    }[direction]
    x1, x2, y1, y2 = clamp(x1, w), clamp(x2, w), clamp(y1, h), clamp(y2, h)
    act(INPUT, "swipe", str(x1), str(y1), str(x2), str(y2), "350")
    return f"scrolled {direction} x{n}"


@tool("Type literal text into the focused input field (ASCII only).", {"text": "string"})
def type_text(text):
    if any(ord(c) > 127 for c in text):
        raise ToolError("type_text supports ASCII only (emoji/non-Latin text would be dropped silently)")
    act(INPUT, "text", text)
    return f"typed: {text}"


@tool("Press a key or combo. Names: Return, Tab, Escape, BackSpace, Back, Home, Up/Down/Left/Right, "
      "volume_up, play_pause, next, app_switch, single letters, or KEYCODE_*. Combos with '+' "
      "(e.g. ctrl+a; Android 13+). `repeat` presses it several times (max 10).",
      {"keycode": "string", "repeat": "integer"}, optional=["repeat"])
def key_event(keycode, repeat=1):
    n = max(1, min(int(repeat), 10))
    parts = [to_keycode(p) for p in keycode.split("+") if p.strip()]
    if not parts:
        raise ToolError("empty key")
    for _ in range(n):
        if len(parts) > 1:
            act(INPUT, "keycombination", *parts)
        else:
            act(INPUT, "keyevent", parts[0])
    return f"sent {'+'.join(parts)}" + (f" x{n}" if n > 1 else "")


@tool("Hold a key down (long-press), e.g. hold_key('power') or 'back'. Duration is the system long-press time.",
      {"keycode": "string"})
def hold_key(keycode):
    code = to_keycode(keycode)
    act(INPUT, "keyevent", "--longpress", code)
    return f"long-pressed {code}"


@tool("Low-level touch for gestures the other tools can't express: action DOWN, MOVE, UP or CANCEL "
      "at (x,y). Send DOWN, then MOVEs, then UP. Needs Android 12+.",
      {"action": {"type": "string", "enum": ["DOWN", "MOVE", "UP", "CANCEL"]},
       "x": "integer", "y": "integer"})
def touch(action, x, y):
    a = str(action).upper()
    if a not in ("DOWN", "MOVE", "UP", "CANCEL"):
        raise ToolError("action must be DOWN, MOVE, UP or CANCEL")
    act(INPUT, "motionevent", a, str(x), str(y))
    return f"touch {a} at ({x},{y})"


@tool("Pause for a number of seconds (max 60), e.g. while an app loads.", {"seconds": "number"})
def wait(seconds):
    s = max(0.0, min(float(seconds), 60.0))
    time.sleep(s)
    return f"waited {s:g}s"


@tool("Poll the screen until an element containing `text` appears (default timeout 10s).",
      {"text": "string", "timeout": "integer"}, optional=["timeout"])
def wait_for_text(text, timeout=10):
    end = time.time() + max(1, min(int(timeout), 60))
    while True:
        matches = find_matches(text)
        if matches:
            label, cx, cy, _ = matches[0]
            return f"found '{label}' at ({cx},{cy})"
        if time.time() >= end:
            raise ToolError(f"timed out waiting for '{text}'")
        time.sleep(1)


@tool("Launch an app by package name (e.g. com.whatsapp). Use find_package if unsure of the name.",
      {"package": "string"})
def launch_app(package):
    try:
        out = act(MONKEY, "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
    except ToolError as e:
        raise ToolError(f"cannot launch '{package}': {e}")
    if "No activities found" in out or "monkey aborted" in out:
        raise ToolError(f"cannot launch '{package}': {out}")
    return out or f"launched {package}"


@tool("Search installed packages by name fragment, e.g. find_package('whatsapp').", {"query": "string"})
def find_package(query):
    out = rish(PM, "list", "packages")
    hits = [l.split(":", 1)[1] for l in out.splitlines()
            if l.startswith("package:") and query.lower() in l.lower()]
    if not hits:
        raise ToolError(f"no installed package matches '{query}'")
    return "\n".join(hits[:15])


@tool("Open a URL or deep link (https://..., tel:, geo:, wa.me links, etc.).", {"url": "string"})
def open_url(url):
    if ":" not in url:
        raise ToolError("url needs a scheme, e.g. https://")
    out = act(AM, "start", "-a", "android.intent.action.VIEW", "-d", url)
    if "Error" in out and "Starting" not in out:
        raise ToolError(out)
    return f"opened {url}"


@tool("Open Spotify directly on search results for a query. Prefer this over manual navigation.",
      {"query": "string"})
def open_spotify_search(query):
    act(AM, "start", "-a", "android.intent.action.VIEW", "-d", "spotify:search:" + query.replace(" ", "%20"))
    return f"opened Spotify search for '{query}'"


@tool("Which app/window currently has focus.")
def get_current_app():
    out = rish(DUMPSYS, "window")
    lines = [l.strip() for l in out.splitlines() if "mCurrentFocus" in l or "mFocusedApp" in l]
    return "\n".join(lines[:4]) or "(unknown)"


@tool("Current UI as a compact list of visible elements with tap coordinates (FOCUSED marks the focused field).")
def get_screen_state():
    return compact_ui_tree(dump_ui())


@tool("Raw UI hierarchy XML (only if the compact view is insufficient).")
def get_screen_state_raw():
    return dump_ui()


@tool("Capture the screen and return it as an image (needs a vision-capable model). Coordinates in "
      "all tools are screen pixels.")
def screenshot():
    return take_shot()


@tool("Inspect a screen region at full resolution (returns an upscaled crop). Use it to read small "
      "text or dense UI. Coordinates are screen pixels: (x0,y0) top-left, (x1,y1) bottom-right.",
      {"x0": "integer", "y0": "integer", "x1": "integer", "y1": "integer"})
def zoom(x0, y0, x1, y1):
    w, h = screen_size()
    x0, x1 = max(0, min(int(x0), w)), max(0, min(int(x1), w))
    y0, y1 = max(0, min(int(y0), h)), max(0, min(int(y1), h))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ToolError("zoom region too small or outside the screen")
    return take_shot((x0, y0, x1, y1))


# ------------------------------------------------------------------ protocol
def _prop(spec):
    return {"type": spec} if isinstance(spec, str) else spec


def _coerce(args, params):
    out = {}
    for k, v in args.items():
        if v is None:
            continue
        t = _prop(params[k])["type"] if k in params else None
        try:
            if t == "integer":
                v = int(float(v))
            elif t == "number":
                v = float(v)
        except (TypeError, ValueError):
            raise ToolError(f"parameter '{k}' must be a {t}, got {v!r}")
        out[k] = v
    return out


def call_tool(name, args):
    if name not in TOOLS:
        raise ToolError(f"unknown tool '{name}'")
    t = TOOLS[name]
    args = _coerce(args or {}, t["params"])
    missing = [k for k in t["params"] if k not in t["optional"] and k not in args]
    if missing:
        raise ToolError(f"missing required parameter(s): {missing}")
    extra = [k for k in args if k not in t["params"]]
    if extra:
        raise ToolError(f"unexpected parameter(s): {extra}")
    return t["fn"](**args)


def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "phone-control", "version": "0.5"}}
    if method == "tools/list":
        return {"tools": [
            {"name": n, "description": t["description"],
             "inputSchema": {"type": "object",
                             "properties": {k: _prop(v) for k, v in t["params"].items()},
                             "required": [k for k in t["params"] if k not in t["optional"]]}}
            for n, t in TOOLS.items()]}
    if method == "tools/call":
        try:
            res = call_tool(msg["params"]["name"], msg["params"].get("arguments", {}))
        except ToolError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"ERROR: {type(e).__name__}: {e}"}], "isError": True}
        if isinstance(res, dict):
            content = [{"type": "text", "text": res["text"]}]
            if res.get("image"):
                content.append({"type": "image", "data": res["image"],
                                "mimeType": res.get("mime", "image/jpeg")})
            return {"content": content}
        return {"content": [{"type": "text", "text": str(res)}]}
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
        if "id" not in msg:  # notification: no reply
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}), flush=True)
