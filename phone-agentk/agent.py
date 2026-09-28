#!/usr/bin/env python3
"""
agent.py - vendor-agnostic phone agent with an interactive menu.

Talks to any OpenAI-compatible /chat/completions endpoint (Groq, Ollama, Gemini,
OpenRouter, ...) using only the standard library. Phone control goes through the
local phone_mcp.py (Shizuku/rish), whatever backend you pick.

Run:  python agent.py
"""
import os, sys, json, time, getpass, urllib.request, urllib.error
from datetime import datetime
from common import (HERE, ts, MCPClient, has_focused_text_field,
                    ScreenHistory, approx_tokens)

RUNS_FILE = os.path.join(HERE, "runs.jsonl")
OLLAMA_ROOT = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# ---------------------------------------------------------------- backends
BACKENDS = [
    dict(id="groq", name="Groq (cloud)",
         base_url="https://api.groq.com/openai/v1",
         model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
         key_env="GROQ_API_KEY", tool_msg_name=False),
    dict(id="ollama", name="Ollama (local, private)",
         base_url=OLLAMA_ROOT + "/v1",
         model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
         key_env=None, tool_msg_name=False),
    dict(id="gemini", name="Gemini (cloud)",
         base_url="https://generativelanguage.googleapis.com/v1beta/openai",
         model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
         key_env="GEMINI_API_KEY", tool_msg_name=True),
]
if os.environ.get("LLM_BASE_URL"):  # any other OpenAI-compatible provider
    BACKENDS.append(dict(id="custom", name="Custom (LLM_BASE_URL)",
                         base_url=os.environ["LLM_BASE_URL"],
                         model=os.environ.get("LLM_MODEL", ""),
                         key_env="LLM_API_KEY", tool_msg_name=False))

# Keys are saved in Termux's PRIVATE home (not shared storage, which other apps can read).
KEYS_FILE = os.environ.get("PHONE_AGENT_KEYS",
                           os.path.expanduser("~/.config/phone-agent/keys.json"))


def load_saved_keys():
    try:
        with open(KEYS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


saved_keys = load_saved_keys()


def save_key(backend_id, key):
    """Persist a key (chmod 600). Returns True on success."""
    saved_keys[backend_id] = key
    try:
        os.makedirs(os.path.dirname(KEYS_FILE), mode=0o700, exist_ok=True)
        tmp = KEYS_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(saved_keys, f)
        os.replace(tmp, KEYS_FILE)
        os.chmod(KEYS_FILE, 0o600)
        return True
    except OSError as e:
        print(f"! Could not save key ({e}); using it for this session only.")
        return False


def get_key(b):
    """Keys saved via this app take priority over environment variables."""
    if not b["key_env"]:
        return None
    return saved_keys.get(b["id"]) or os.environ.get(b["key_env"])


def ask_for_key(b, reason):
    """Prompt (hidden) for a key and persist it. Returns True if a key was set."""
    print(reason)
    k = getpass.getpass(f"Paste {b['name']} API key (hidden; Enter to cancel): ").strip()
    if not k:
        return False
    if save_key(b["id"], k):
        print(f"Saved to {KEYS_FILE} (private to Termux, chmod 600).")
    return True


class LLMError(Exception):
    pass


class LLMAuthError(LLMError):
    pass


def http_chat(sel, messages, tools, timeout=180):
    b = sel["backend"]
    url = b["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({"model": sel["model"], "messages": messages, "tools": tools}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "phone-agent/1.0"}
    key = get_key(b)
    if key:
        headers["Authorization"] = "Bearer " + key
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            if e.code in (413, 429, 500, 502, 503) and attempt < 4:
                try:
                    wait = int(float(e.headers.get("Retry-After", "")))
                except ValueError:
                    wait = 20 + 10 * attempt
                wait = max(1, min(wait, 90))
                print(f"[{ts()}] {b['id']} returned {e.code}; waiting {wait}s "
                      f"(retry {attempt + 1}/4)", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (401, 403):
                raise LLMAuthError(f"HTTP {e.code} from {b['id']}: key rejected? {detail}")
            raise LLMError(f"HTTP {e.code} from {b['id']}: {detail}")
        except urllib.error.URLError as e:
            raise LLMError(f"cannot reach {b['id']} ({e.reason})")
        except TimeoutError:
            raise LLMError(f"{b['id']} timed out after {timeout}s")


# ---------------------------------------------------------------- menus
def ollama_models():
    try:
        with urllib.request.urlopen(OLLAMA_ROOT + "/api/tags", timeout=3) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return None


def choose_backend(current=None):
    print("\nChoose backend:")
    for i, b in enumerate(BACKENDS, 1):
        ready = "ready" if (not b["key_env"] or get_key(b)) else "no key"
        cur = "  <- current" if current and current["backend"]["id"] == b["id"] else ""
        print(f" {i}  {b['name']:<26} {b['model']}  [{ready}]{cur}")
    while True:
        raw = input("Select (Enter = " + ("keep current" if current else "quit") + "): ").strip()
        if not raw:
            return current
        if raw.isdigit() and 1 <= int(raw) <= len(BACKENDS):
            b = BACKENDS[int(raw) - 1]
            break
        print("Invalid choice.")
    if b["key_env"] and not get_key(b):
        if not ask_for_key(b, f"No API key found for {b['name']} ({b['key_env']} not set)."):
            return current
    if b["id"] == "ollama":
        models = ollama_models()
        if models is None:
            print(f"! Ollama not reachable at {OLLAMA_ROOT}. Start it with: ollama serve &")
        elif models:
            print("  installed: " + ", ".join(models))
    model = input(f"Model [{b['model']}] (Enter to keep): ").strip() or b["model"]
    if not model:
        print("A model name is required.")
        return current
    return {"backend": b, "model": model}


def load_runs():
    runs = []
    if os.path.exists(RUNS_FILE):
        with open(RUNS_FILE) as f:
            for line in f:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return runs


def log_run(entry):
    with open(RUNS_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def show_runs(limit=15):
    runs = load_runs()
    if not runs:
        print("\nNo past runs yet.")
        return
    start = max(0, len(runs) - limit)
    print(f"\nPast runs (last {len(runs) - start} of {len(runs)}):")
    for n in range(start, len(runs)):
        r = runs[n]
        when = r.get("time", "")[5:16].replace("T", " ")
        print(f"#{n + 1:<3} {when}  {r.get('backend', '?')}  {r.get('status', '?')}  "
              f"{r.get('steps', '?')} steps  {r.get('seconds', '?')}s")
        goal = r.get("goal", "")
        print("     " + (goal[:60] + "..." if len(goal) > 60 else goal))
    raw = input("Run # for details (Enter to go back): ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(runs):
        r = runs[int(raw) - 1]
        print(f"\n--- run #{raw} ---")
        for k in ("time", "backend", "model", "status", "steps", "seconds"):
            print(f"{k}: {r.get(k)}")
        print(f"goal: {r.get('goal')}\nresult: {r.get('result')}")


# ---------------------------------------------------------------- agent
SYSTEM_PROMPT = """You are an autonomous phone-control agent. You get a goal and the \
current screen state (compact list of visible elements with tap coordinates). \
Call one tool per turn using the proper tool-calling format. After tap, launch_app, \
key_event and open_spotify_search the new screen state is shown automatically. \
Prefer dedicated shortcut tools (e.g. open_spotify_search) over manual navigation. \
Use exact package names (Spotify: com.spotify.music, WhatsApp: com.whatsapp). \
To open a chat/list item, tap the middle of its row (name text or wide row), never the \
small round avatar at the far left (that only opens a photo preview). \
To type: first tap the EditText field; type_text is only allowed while an EditText is \
marked FOCUSED in the latest screen state. Then tap Send and confirm the message appears. \
Never reply DONE until the latest screen state visibly confirms the goal was achieved \
(e.g. playback controls showing the right track, message shown as sent). \
If not achieved, keep working. Final reply must start with "DONE:"."""

VERIFY_AFTER = {"tap", "launch_app", "key_event", "open_spotify_search"}


def to_openai_tools(mcp_tools):
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": t["inputSchema"]}} for t in mcp_tools]


def try_extract_fallback_tool_call(content):
    """Small models sometimes write a tool call as JSON text instead of using
    tool_calls. Salvage the first {"name":..., "parameters":...} object."""
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


def parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def run_agent(goal, sel, max_steps=20):
    """Returns dict(status, result, steps, seconds). Never raises for LLM errors."""
    start = time.time()
    steps = 0
    client = MCPClient(os.environ.get("PHONE_MCP"))
    try:
        mcp_tools = client.list_tools()
        known = {t["name"] for t in mcp_tools}
        tools = to_openai_tools(mcp_tools)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Goal: {goal}"}]
        history = ScreenHistory()
        last_screen = None

        def execute(name, args):
            """-> (result_text, has_screen, action_part)"""
            nonlocal last_screen
            if name not in known:
                return f"ERROR: unknown tool '{name}'. Valid tools: {sorted(known)}", False, ""
            if name == "type_text" and not has_focused_text_field(last_screen):
                print(f"[{ts()}] BLOCKED type_text (no FOCUSED EditText)", file=sys.stderr)
                return ("BLOCKED: no focused text field. Tap the EditText first, then "
                        "check it is marked FOCUSED before typing."), False, ""
            if name == "get_screen_state":
                result = client.call_tool(name, args)
                last_screen = result
                return result, True, ""
            action = client.call_tool(name, args)
            if name in VERIFY_AFTER:
                time.sleep(1.0)
                verify = client.call_tool("get_screen_state", {})
                last_screen = verify
                return f"{action}\n\n[Screen after {name}:]\n{verify}", True, action
            return action, False, action

        for step in range(1, max_steps + 1):
            steps = step
            print(f"\n[{ts()}] --- step {step} (~{approx_tokens(messages)} tokens) ---",
                  file=sys.stderr)
            t0 = time.time()
            resp = http_chat(sel, messages, tools)
            print(f"[{ts()}] (model {time.time() - t0:.2f}s)", file=sys.stderr)
            msg = resp["choices"][0]["message"]
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
            for i, tc in enumerate(tool_calls):
                tc.setdefault("id", f"call_{step}_{i}")
                tc.setdefault("type", "function")

            # Keep only standard fields; tool_calls are kept whole (some providers,
            # e.g. Gemini 3, need extra fields inside them echoed back).
            assistant = {"role": "assistant", "content": content}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
                if not content:
                    del assistant["content"]
            messages.append(assistant)

            if not tool_calls:
                print(f"[{ts()}] Model: {content}", file=sys.stderr)
                if content.strip().upper().startswith("DONE"):
                    return dict(status="done", result=content, steps=steps,
                                seconds=round(time.time() - start, 1))
                fb = try_extract_fallback_tool_call(content)
                if fb:
                    name, args = fb
                    print(f"[{ts()}] Fallback-parsed: {name}({args})", file=sys.stderr)
                    result, has_screen, action = execute(name, args)
                    history.append_tool(
                        messages, {"role": "user", "content": f"[result of {name}]\n{result}"},
                        has_screen=has_screen, action_part=action)
                else:
                    messages.append({"role": "user", "content":
                                     "Call a tool using the proper tool-calling format, or "
                                     "reply 'DONE: <summary>' if verified complete."})
                continue

            for tc in tool_calls:
                name = tc["function"]["name"]
                args = parse_args(tc["function"].get("arguments"))
                print(f"[{ts()}] Calling {name}({args})", file=sys.stderr)
                result, has_screen, action = execute(name, args)
                tmsg = {"role": "tool", "tool_call_id": tc["id"], "content": result}
                if sel["backend"]["tool_msg_name"]:
                    tmsg["name"] = name
                history.append_tool(messages, tmsg, has_screen=has_screen, action_part=action)

        return dict(status="stopped", result="max steps reached without DONE",
                    steps=steps, seconds=round(time.time() - start, 1))
    except LLMError as e:
        print(f"\n[{ts()}] LLM error: {e}", file=sys.stderr)
        return dict(status="error", result=str(e), steps=steps,
                    seconds=round(time.time() - start, 1),
                    auth_failed=isinstance(e, LLMAuthError))
    except KeyboardInterrupt:
        print(f"\n[{ts()}] interrupted", file=sys.stderr)
        return dict(status="interrupted", result="stopped by user", steps=steps,
                    seconds=round(time.time() - start, 1))
    finally:
        client.close()


# ---------------------------------------------------------------- main
def main():
    print("Phone agent")
    sel = choose_backend()
    if not sel:
        return
    while True:
        print("\n" + "-" * 34)
        print(f" Backend: {sel['backend']['name']} | {sel['model']}")
        print(" 1  Ask (give the agent a goal)")
        print(" 0  Switch backend")
        print(" 4  Show past runs")
        print(" 3  Quit")
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice == "1":
            try:
                goal = input("Goal: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            if not goal:
                continue
            out = run_agent(goal, sel)
            auth_failed = out.pop("auth_failed", False)
            print(f"\n=== {out['status'].upper()} ({out['seconds']}s, {out['steps']} steps) ===")
            print(out["result"])
            log_run(dict(time=datetime.now().isoformat(timespec="seconds"),
                         backend=sel["backend"]["id"], model=sel["model"], goal=goal, **out))
            if auth_failed and sel["backend"]["key_env"]:
                if input("The key was rejected. Enter a new one now? [y/N] ").strip().lower() == "y":
                    ask_for_key(sel["backend"], "Replacing the saved key.")
        elif choice == "0":
            sel = choose_backend(sel) or sel
        elif choice == "4":
            show_runs()
        elif choice == "3":
            break
        else:
            print("Enter 1, 0, 4 or 3.")
    print("Bye.")


if __name__ == "__main__":
    main()
