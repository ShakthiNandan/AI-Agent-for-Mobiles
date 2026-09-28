# agent.py
import subprocess, json, time, sys, os
from groq import Groq

client_llm = Groq(api_key=os.environ["GROQ_API_KEY"])

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


def mcp_tools_to_openai(mcp_tools):
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
Use get_screen_state after actions that change the screen (tap, launch_app, key_event) \
to see the new state before deciding the next action. \
When the goal is fully achieved, respond with plain text starting with "DONE:" \
and a short summary — do not call a tool in that final turn."""


#def run_agent(goal, model="llama-3.3-70b-versatile", max_steps=15):
def run_agent(goal, model="openai/gpt-oss-120b", max_steps=15):
    client = MCPClient()
    tools = mcp_tools_to_openai(client.list_tools())

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Goal: {goal}"},
    ]

    last_screen_state = None

    for step in range(1, max_steps + 1):
        print(f"\n--- step {step} ---", file=sys.stderr)

        response = client_llm.chat.completions.create(
            model=model, messages=messages, tools=tools
        )
        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(f"Model response: {msg.content}", file=sys.stderr)
            if msg.content and msg.content.strip().upper().startswith("DONE"):
                return msg.content
            messages.append({"role": "user", "content": "Call a tool to act, or respond with 'DONE: <summary>' if finished."})
            continue

        for tool_call in msg.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            print(f"Calling {name}({args})", file=sys.stderr)

            result = client.call_tool(name, args)

            if name == "get_screen_state":
                if result == last_screen_state:
                    result += "\n\n[NOTE: screen state is unchanged since your last check.]"
                last_screen_state = result

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    client.close()
    return "STOPPED: max steps reached without completion."


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else input("Goal: ")
    result = run_agent(goal)
    print("\n=== RESULT ===")
    print(result)
