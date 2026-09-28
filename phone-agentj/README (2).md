# Phone agent (Termux + Shizuku)

One menu-driven agent, any OpenAI-compatible LLM backend. Phone control is always
local (Shizuku `rish` + `phone_mcp.py`); only the reasoning model differs.
No pip packages needed: everything is Python stdlib.

## Files
- `agent.py`      - the menu app (run this)
- `phone_mcp.py`  - stdlib MCP server (JSON-RPC over stdio), 11 phone tools
- `common.py`     - MCP client, focus guard, screen-history pruning
- `runs.jsonl`    - created on first run: log of past runs (goal + result, stays local)

## One-time setup (Termux)
1. Shizuku running, `rish` + `rish_shizuku.dex` in `$PREFIX/bin`
   (`RISH_APPLICATION_ID=com.termux`, `chmod +x rish`).
2. `termux-setup-storage`
3. Check: `rish /system/bin/input keyevent KEYCODE_HOME`

## Run
    python agent.py

Menu: `1` ask (give a goal) | `0` switch backend | `4` show past runs | `3` quit

## API keys
If a backend has no key, the app asks for it (hidden input) and saves it to
`~/.config/phone-agent/keys.json` (chmod 600) inside Termux's private home, so you only
enter it once. Keys saved this way take priority over env vars. If a key is rejected
(401/403) the app offers to replace it. To forget a key, delete that file.
Never paste keys into chats or commit them.

## Backends
| Backend | Env |
|---|---|
| Groq   | `GROQ_API_KEY` (optional), optional `GROQ_MODEL` |
| Ollama | none; optional `OLLAMA_MODEL`, `OLLAMA_URL`; run `ollama serve &` |
| Gemini | `GEMINI_API_KEY` (optional), optional `GEMINI_MODEL` |
| Custom | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (optional) (OpenRouter, etc.) |

The model name can also be overridden at the prompt after picking a backend.
Default model names go stale: if a backend returns 404, type a current one.

## Notes
- `type_text` is blocked unless the latest screen shows an EditText marked FOCUSED.
- Only the newest screen dump is kept in the LLM context (protects small TPM limits);
  429/413/5xx are retried with backoff (honours Retry-After).
- Ctrl+C during a run stops that run and returns to the menu.
- If Termux runs as a floating window it can swallow keystrokes: use full-screen or split.
- Privacy: cloud backends receive whatever is visible on screen. Use Ollama for sensitive stuff.
- `rish` execs argv directly: always full paths (/system/bin/input); it can't exec sh/cat/ls.
- `open_spotify_search` is untested on your phone.
