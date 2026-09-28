# agent3.py
import subprocess, json, time, sys
from datetime import datetime
import ollama

def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

class MCPClient:
    """Persistent connection to phone_mcp.py over stdio."""
    def __init__(self, server_path="phone_mcp.py"):
        self.proc = subprocess.Popen(
            ["python", server_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1
        )
        self._id = 0

    def _call(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def list_tools(self):
        return self._call("tools/list")["result"]["tools"]

    def call_tool(self, name, arguments):
        result = self._call("tools/call", {"name": name, "arguments": arguments})
        content = result.get("result", {}).get("content", [])
        return content[0]["text"] if content else str(result)

    def close(self):
        self.proc.terminate()


def mcp_tools_to_ollama(mcp_tools):
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"],
            },
        }
        for t in mcp_tools
    ]


SYSTEM_PROMPT = """You are an autonomous phone-control agent. You are given a goal and the \
current screen state (a compact list of visible elements with tap coordinates). \
Call exactly one tool per turn to make progress toward the goal. \
After every tap, the current screen state is automatically shown to you so you can confirm \
the tap landed correctly and a text field is actually focused before typing into it. \
You cannot call type_text unless an EditText element is visible in the last screen state — \
the system will block the call and tell you if this isn't the case. \
If a dedicated shortcut tool exists for what you're trying to do (e.g. open_spotify_search), \
prefer it over manual tap/type navigation — it is faster and more reliable. \
Never respond with DONE until you have called get_screen_state at least once after your \
final action, and that screen state visibly confirms the goal was achieved \
(e.g. a play/pause control is visible for music playback, a message appears as sent, \
search results show the expected content). \
If verification shows the goal was NOT achieved, keep working — do not declare DONE."""


VERIFY_AFTER = {"tap", "launch_app", "key_event", "open_spotify_search"}


def has_focused_text_field(screen_state: str) -> bool:
    """Heuristic: does the last screen state show an EditText element?"""
    return bool(screen_state) and "EditText" in screen_state


def run_agent(goal, model="llama3.2:3b", max_steps=15):
    client = MCPClient()
    tools = mcp_tools_to_ollama(client.list_tools())

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Goal: {goal}"},
    ]

    last_screen_state = None

    for step in range(1, max_steps + 1):
        print(f"\n[{ts()}] --- step {step} ---", file=sys.stderr)

        t0 = time.time()
        response = ollama.chat(model=model, messages=messages, tools=tools)
        elapsed = time.time() - t0
        msg = response["message"]
        messages.append(msg)

        print(f"[{ts()}] (model responded in {elapsed:.2f}s)", file=sys.stderr)

        if not msg.get("tool_calls"):
            print(f"[{ts()}] Model response: {msg.get('content')}", file=sys.stderr)
            if msg.get("content", "").strip().upper().startswith("DONE"):
                return msg["content"]
            messages.append({
                "role": "user",
                "content": "Call a tool to act, or respond with 'DONE: <summary>' if finished."
            })
            continue

        for tool_call in msg["tool_calls"]:
            name = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]
            print(f"[{ts()}] Calling {name}({args})", file=sys.stderr)

            # Hard guard: block type_text if no EditText was seen in the
            # last known screen state, instead of trusting the model to
            # notice and self-correct.
            if name == "type_text" and not has_focused_text_field(last_screen_state):
                result = (
                    "BLOCKED: no focused text input field (EditText) detected in the "
                    "last screen state. Find and tap a search/text field first, then "
                    "call get_screen_state to confirm an EditText is present before "
                    "calling type_text again."
                )
                print(f"[{ts()}] BLOCKED type_text — no EditText in last screen state", file=sys.stderr)
                messages.append({"role": "tool", "content": result, "name": name})
                continue

            t1 = time.time()
            result = client.call_tool(name, args)
            tool_elapsed = time.time() - t1
            print(f"[{ts()}] (tool executed in {tool_elapsed:.2f}s)", file=sys.stderr)

            if name in VERIFY_AFTER:
                time.sleep(0.5)
                verify = client.call_tool("get_screen_state", {})
                print(f"[{ts()}] (auto-verified screen state after {name})", file=sys.stderr)
                result += f"\n\n[Screen state after {name}:]\n{verify}"
                last_screen_state = verify

            if name == "get_screen_state":
                if result == last_screen_state:
                    result += "\n\n[NOTE: screen state is unchanged since your last check.]"
                last_screen_state = result

            messages.append({
                "role": "tool",
                "content": result,
                "name": name,
            })

    client.close()
    return "STOPPED: max steps reached without completion."


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else input("Goal: ")
    start = time.time()
    result = run_agent(goal)
    total = time.time() - start
    print(f"\n=== RESULT ({total:.2f}s total) ===")
    print(result)
