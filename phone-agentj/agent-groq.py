#!/usr/bin/env python3
"""agent-groq.py - phone agent using Groq cloud LLM + local phone_mcp.py.
Usage: python agent-groq.py "Open WhatsApp, go into the first chat, and send 'hi'"
Needs: pip install groq ; export GROQ_API_KEY=...
"""
import subprocess, json, time, sys, os
from datetime import datetime
from groq import Groq

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
llm = Groq(api_key=os.environ["GROQ_API_KEY"])


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


def to_openai_tools(mcp_tools):
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": t["inputSchema"]}} for t in mcp_tools]


SYSTEM_PROMPT = """You are an autonomous phone-control agent. You get a goal and the \
current screen state (compact list of visible elements with tap coordinates). \
Call one tool per turn. After tap, launch_app, key_event and open_spotify_search the \
new screen state is shown automatically. type_text is blocked unless an EditText is \
visible in the last screen state. Prefer dedicated shortcut tools (e.g. \
open_spotify_search) over manual navigation. Use exact package names \
(Spotify: com.spotify.music, WhatsApp: com.whatsapp). \
Never reply DONE until the latest screen state visibly confirms the goal was achieved \
(e.g. playback controls showing the right track, message shown as sent). \
If not achieved, keep working. Final reply must start with "DONE:"."""

VERIFY_AFTER = {"tap", "launch_app", "key_event", "open_spotify_search"}


def has_text_field(s):
    return bool(s) and "EditText" in s


def run_agent(goal, max_steps=20):
    client = MCPClient()
    mcp_tools = client.list_tools()
    known = {t["name"] for t in mcp_tools}
    tools = to_openai_tools(mcp_tools)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Goal: {goal}"}]
    last_screen = None

    for step in range(1, max_steps + 1):
        print(f"\n[{ts()}] --- step {step} ---", file=sys.stderr)
        t0 = time.time()
        resp = llm.chat.completions.create(model=MODEL, messages=messages, tools=tools)
        print(f"[{ts()}] (model {time.time()-t0:.2f}s)", file=sys.stderr)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(f"[{ts()}] Model: {msg.content}", file=sys.stderr)
            if msg.content and msg.content.strip().upper().startswith("DONE"):
                client.close()
                return msg.content
            messages.append({"role": "user", "content":
                             "Call a tool, or reply 'DONE: <summary>' if verified complete."})
            continue

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"[{ts()}] Calling {name}({args})", file=sys.stderr)

            if name not in known:
                result = f"ERROR: unknown tool '{name}'. Valid tools: {sorted(known)}"
            elif name == "type_text" and not has_text_field(last_screen):
                result = ("BLOCKED: no EditText in the last screen state. Tap a text "
                          "field and confirm with get_screen_state before typing.")
                print(f"[{ts()}] BLOCKED type_text", file=sys.stderr)
            else:
                result = client.call_tool(name, args)
                if name in VERIFY_AFTER:
                    time.sleep(1.0)
                    verify = client.call_tool("get_screen_state", {})
                    result += f"\n\n[Screen after {name}:]\n{verify}"
                    last_screen = verify
                elif name == "get_screen_state":
                    last_screen = result

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    client.close()
    return "STOPPED: max steps reached."


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else input("Goal: ")
    start = time.time()
    out = run_agent(goal)
    print(f"\n=== RESULT ({time.time()-start:.1f}s) ===\n{out}")
