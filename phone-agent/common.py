"""Shared helpers for agent.py."""
import os, re, sys, json, subprocess
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class MCPClient:
    """Persistent stdio JSON-RPC connection to phone_mcp.py."""
    def __init__(self, server_path=None):
        server_path = server_path or os.path.join(HERE, "phone_mcp.py")
        self.proc = subprocess.Popen([sys.executable, server_path], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, bufsize=1)
        self._id = 0

    def _call(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("phone_mcp.py exited unexpectedly")
        return json.loads(line)

    def list_tools(self):
        return self._call("tools/list")["result"]["tools"]

    def call_tool_full(self, name, arguments):
        """-> dict(text, images=[{data, mime}], is_error)"""
        r = self._call("tools/call", {"name": name, "arguments": arguments}).get("result", {})
        blocks = r.get("content", [])
        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        images = [{"data": b["data"], "mime": b.get("mimeType", "image/jpeg")}
                  for b in blocks if b.get("type") == "image"]
        return dict(text=text or str(r), images=images, is_error=bool(r.get("isError")))

    def call_tool(self, name, arguments):
        return self.call_tool_full(name, arguments)["text"]

    def close(self):
        self.proc.terminate()


def has_focused_text_field(screen):
    """True only if an EditText is currently marked FOCUSED in the screen dump."""
    if not screen:
        return False
    return any("EditText" in l and "FOCUSED" in l for l in screen.splitlines())


class ScreenHistory:
    """Keeps only the NEWEST screen dump and the NEWEST screenshot message in the
    message list; older ones become short stubs. Keeps token usage flat."""
    def __init__(self):
        self._old = []      # (message_index, action_only_text)
        self._img_idx = None

    def append_tool(self, messages, tool_msg, has_screen=False, action_part=""):
        if has_screen:
            for idx, action in self._old:
                messages[idx]["content"] = (action + " " if action else "") + "(older screen state omitted)"
            self._old = []
        messages.append(tool_msg)
        if has_screen:
            self._old.append((len(messages) - 1, action_part))

    def append_images(self, messages, images, note="[screenshot from the tool call above]"):
        """Add one user message carrying images; stub out the previous image message."""
        if self._img_idx is not None:
            messages[self._img_idx] = {"role": "user", "content": "(older screenshot omitted)"}
        parts = [{"type": "text", "text": note}]
        for im in images:
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{im['mime']};base64,{im['data']}"}})
        messages.append({"role": "user", "content": parts})
        self._img_idx = len(messages) - 1


def approx_tokens(messages):
    """Rough token estimate; images count as ~1500 tokens instead of their base64 length."""
    raw = json.dumps(messages, default=str)
    n_img = raw.count("data:image/")
    raw = re.sub(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+", "IMG", raw)
    return len(raw) // 4 + 1500 * n_img
