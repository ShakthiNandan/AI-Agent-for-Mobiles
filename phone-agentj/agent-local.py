#!/usr/bin/env python3
"""agent-local.py - phone agent using a local Ollama model + local phone_mcp.py.
Usage: python agent-local.py "Open WhatsApp"
Needs: pip install ollama ; ollama pull llama3.2:3b
Best for short 1-3 step goals; small models get unreliable on long chains.
Override model: OLLAMA_MODEL=qwen2.5:7b-instruct python agent-local.py "..."
"""
import subprocess, json, time, sys, os
from datetime import datetime
import ollama

MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class MCPClient:
    def __init__(self, server_path="phone_mcp.py"):
        self.proc = subprocess.Popen(["python", server_path], stdin=subprocess.PIPE,
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


def to_ollama_tools(mcp_tools):
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": t["inputSchema"]}} for t in mcp_tools]


def try_extract_fallback_tool_call(content):
    """Small models sometimes dump a tool call as JSON text in .content
    instead of using tool_calls. Salvage the first {"name":..., "parameters":...}."""
    if not content or '"name"' not in content:
        return None
    cleaned = content.replace('\\"', '"')
    dec = json.JSONDecoder()
    start = cleaned.find("{")
    while start != -1:
        try:
            obj, _ = dec.raw_decode(cleaned[start:])
            if isinstance(obj, dict) and "name" in obj:
                return obj["name"], (obj.get("parameters") or obj.get("arguments") or {})
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{", start + 1)
    return None


SYSTEM_PROMPT = """You are an autonomous phone-control agent. You get a goal and the \
current screen state (compact list of visible elements with tap coordinates). \
Call one tool per turn using the proper tool-calling format. After tap, launch_app, \
key_event and open_spotify_search the new screen state is shown automatically. \
type_text is blocked unless an EditText is visible in the last screen state. \
Prefer shortcut tools (open_spotify_search) over manual navigation. Use exact package \
names (Spotify: com.spotify.music, WhatsApp: com.whatsapp). \
Never reply DONE until the latest screen state confirms the goal was achieved. \
Final reply must start with "DONE:"."""

VERIFY_AFTER = {"tap", "launch_app", "key_event", "open_spotify_search"}


def has_text_field(s):
    return bool(s) and "EditText" in s


def run_agent(goal, max_steps=15):
    client = MCPClient()
    mcp_tools = client.list_tools()
    known = {t["name"] for t in mcp_tools}
    tools = to_ollama_tools(mcp_tools)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Goal: {goal}"}]
    last_screen = None

    def execute(name, args):
        nonlocal last_screen
        if name not in known:
            return f"ERROR: unknown tool '{name}'. Valid tools: {sorted(known)}"
        if name == "type_text" and not has_text_field(last_screen):
            print(f"[{ts()}] BLOCKED type_text", file=sys.stderr)
            return ("BLOCKED: no EditText in the last screen state. Tap a text field "
                    "and confirm with get_screen_state before typing.")
        result = client.call_tool(name, args)
        if name in VERIFY_AFTER:
            time.sleep(1.0)
            verify = client.call_tool("get_screen_state", {})
            result += f"\n\n[Screen after {name}:]\n{verify}"
            last_screen = verify
        elif name == "get_screen_state":
            last_screen = result
        return result

    for step in range(1, max_steps + 1):
        print(f"\n[{ts()}] --- step {step} ---", file=sys.stderr)
        t0 = time.time()
        response = ollama.chat(model=MODEL, messages=messages, tools=tools)
        print(f"[{ts()}] (model {time.time()-t0:.2f}s)", file=sys.stderr)
        msg = response["message"]
        messages.append(msg)

        if not msg.get("tool_calls"):
            content = msg.get("content") or ""
            print(f"[{ts()}] Model: {content}", file=sys.stderr)
            if content.strip().upper().startswith("DONE"):
                client.close()
                return content
            fb = try_extract_fallback_tool_call(content)
            if fb:
                name, args = fb
                print(f"[{ts()}] Fallback-parsed: {name}({args})", file=sys.stderr)
                messages.append({"role": "tool", "content": execute(name, args), "name": name})
                continue
            messages.append({"role": "user", "content":
                             "Call a tool using the proper tool-calling format, or reply "
                             "'DONE: <summary>' if verified complete."})
            continue

        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            print(f"[{ts()}] Calling {name}({args})", file=sys.stderr)
            messages.append({"role": "tool", "content": execute(name, args), "name": name})

    client.close()
    return "STOPPED: max steps reached."


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else input("Goal: ")
    start = time.time()
    out = run_agent(goal)
    print(f"\n=== RESULT ({time.time()-start:.1f}s) ===\n{out}")
