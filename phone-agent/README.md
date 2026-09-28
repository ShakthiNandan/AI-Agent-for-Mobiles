# Phone agent (Termux + Shizuku)

One menu-driven agent, any OpenAI-compatible LLM backend. Phone control is always local
(Shizuku `rish` + `phone_mcp.py`); only the reasoning model differs.
No pip packages needed (Python stdlib only). Optional: `pkg install python-pillow` (or
`imagemagick`) so screenshot/zoom images can be compressed and cropped.

## Files
- `agent.py`      - the menu app (run this)
- `phone_mcp.py`  - stdlib MCP server (JSON-RPC over stdio), 21 phone tools
- `common.py`     - MCP client, focus guard, screen/screenshot history pruning
- `runs.jsonl`    - created on first run: past runs (goal + result, plain text, local only)

## One-time setup (Termux)
1. Shizuku running, `rish` + `rish_shizuku.dex` in `$PREFIX/bin`
   (`RISH_APPLICATION_ID=com.termux`, `chmod +x rish`).
2. `termux-setup-storage`
3. Check: `rish /system/bin/input keyevent KEYCODE_HOME`

## Run
    python agent.py

Menu: `1` ask (give a goal) | `0` switch backend | `4` show past runs | `3` quit.
After picking a backend you can override the model name and choose whether to send
screenshots (vision; needs a vision-capable model).

## API keys
If a backend has no key, the app asks (hidden input) and saves it to
`~/.config/phone-agent/keys.json` (chmod 600, Termux private home). Keys saved this way take
priority over env vars. A rejected key (401/403) offers to replace it. Delete the file to forget.
Never paste keys into chats or commit them.

## Backends
| Backend | Env |
|---|---|
| Groq   | `GROQ_API_KEY` (optional), `GROQ_MODEL` |
| Ollama | none; `OLLAMA_MODEL`, `OLLAMA_URL`; run `ollama serve &` |
| Gemini | `GEMINI_API_KEY` (optional), `GEMINI_MODEL` |
| Custom | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (OpenRouter, etc.) |
Default model names go stale: if a backend returns 404, type a current one at the prompt.

## Tools (modelled on Anthropic's computer use toolset, adapted to touch)
Coordinates everywhere are screen pixels.
- See: `get_screen_state` (compact tree, FOCUSED marks the focused field), `screenshot`, `zoom`
  (vision only), `get_screen_state_raw`, `get_current_app`
- Touch: `tap`, `tap_text`, `long_press`, `swipe`, `drag`, `scroll`, `touch` (DOWN/MOVE/UP)
- Keys: `type_text` (ASCII), `key_event` (names, `ctrl+a` combos, repeat), `hold_key`
- Flow: `wait`, `wait_for_text`
- Apps: `launch_app`, `find_package`, `open_url`, `open_spotify_search`
No touchscreen equivalent (skipped): mouse_move, middle/triple click, cursor_position.
`double_click` is skipped: each rish call takes ~1 s, too slow for a double tap.

## Behaviour (mirrors computer use conventions)
- The model may issue several actions in one turn; they run in order and stop at the first
  failure, the rest are answered "Not executed: an earlier action in this turn failed."
- The screen state is attached only after the LAST action of a turn.
- `type_text` needs an EditText marked FOCUSED (state is refreshed first inside a batch).
- Confirmation gate: before tapping something labelled send/pay/delete/install/... you get
  `y / N / a (allow all this run)`. Disable with `PHONE_AGENT_CONFIRM=0`.
- On-screen text is treated as untrusted data (prompt-injection defence in the prompt), but the
  confirmation gate is the real safeguard.
- Only the newest screen dump and newest screenshot stay in the LLM context; 413/429/5xx are
  retried with backoff.
- Ctrl+C during a run stops it and returns to the menu.

## Notes
- If Termux runs as a floating window it can swallow keystrokes: use full-screen or split.
- Privacy: cloud backends receive whatever is visible on screen (and screenshots if vision is on).
  Use Ollama for sensitive things.
- `rish` execs argv directly: full paths only; it can't exec sh/cat/ls.
- Tested with a fake rish and fake LLM server, NOT on a real phone. Unverified on your ROM:
  `input keycombination` (Android 13+), `input motionevent` (12+), `input draganddrop`,
  `--longpress`, `pm list packages` via rish, `open_spotify_search`, Gemini, vision.
