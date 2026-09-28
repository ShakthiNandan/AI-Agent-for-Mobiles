"""Shared helpers for agent-groq.py and agent-local.py."""
import os, sys, json, subprocess
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
        return json.loads(self.proc.stdout.readline())

    def list_tools(self):
        return self._call("tools/list")["result"]["tools"]

    def call_tool(self, name, arguments):
        r = self._call("tools/call", {"name": name, "arguments": arguments})
        content = r.get("result", {}).get("content", [])
        return content[0]["text"] if content else str(r)

    def close(self):
        self.proc.terminate()


def has_focused_text_field(screen):
    """True only if an EditText is currently marked FOCUSED in the screen dump."""
    if not screen:
        return False
    return any("EditText" in l and "FOCUSED" in l for l in screen.splitlines())


class ScreenHistory:
    """Keeps only the NEWEST screen dump in the message list; older dumps are
    replaced by a stub. This is what keeps token usage flat across steps."""
    def __init__(self):
        self._old = []  # (message_index, action_only_text)

    def append_tool(self, messages, tool_msg, has_screen=False, action_part=""):
        if has_screen:
            for idx, action in self._old:
                messages[idx]["content"] = (action + " " if action else "") + "(older screen state omitted)"
            self._old = []
        messages.append(tool_msg)
        if has_screen:
            self._old.append((len(messages) - 1, action_part))


def approx_tokens(messages):
    return len(json.dumps(messages, default=str)) // 4
