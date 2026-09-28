# Phone agent: project memory (as of 2026-09-29)

## Goal
Autonomous agent that operates my Android phone (personal project), running from
Termux on the phone. LLM decides, local MCP-style server acts.

## Architecture
```
agent.py (menu, LLM loop)  --JSON-RPC/stdio-->  phone_mcp.py  --rish-->  Shizuku shell  -->  Android
        |
        +--HTTPS (OpenAI-compatible /chat/completions, stdlib urllib)--> Groq | Ollama | Gemini | custom
```
- Perceive: `uiautomator dump` -> compact element list with tap coordinates.
- Act: `input tap/swipe/text/keyevent`, `monkey`, `am start`, `screencap` via `rish`.
- No pip packages needed anywhere (agent and server are stdlib-only).

## Environment facts
- Phone: device code RE6090L1, ~11.4 GB RAM, Termux, Python 3.14, Shizuku + `rish`.
- Files live in `/storage/emulated/0/Download/Termux/phone-agent/` (shared storage).
- API keys are saved by the app in `~/.config/phone-agent/keys.json` (chmod 600, Termux
  private home). Keys saved that way beat env vars. Delete the file to forget them.
- The Groq key pasted into a chat earlier is exposed: revoke it and create a new one.

## Hard-won rules (don't relearn these)
- `rish` execs argv directly: always full paths (`/system/bin/input`, `/system/bin/uiautomator`).
  `rish input ...` fails (no PATH). `rish` with no args opens an interactive shell.
- `rish` cannot exec `sh`, `cat`, `ls` (`syntax error: unexpected '('`). Read files
  (dump.xml, screen.png) from Termux itself after `termux-setup-storage`.
- `pip install mcp` is broken on Termux (cryptography Rust wheel ABI error at import):
  hand-rolled stdio JSON-RPC server instead.
- If typed text echoes in the Termux terminal, `input text` went to Termux, not the app
  (Termux floating window holds input focus, or no field was focused). Tap the field first;
  type_text is blocked unless an EditText is marked FOCUSED in the latest screen state.
- `launch_app` must return real `monkey` output (an earlier version faked success).
- Tapping a chat row's round avatar (x~106) opens a photo preview; tap the row middle (x~540).
- Screen dumps contain whatever is on screen (private chats): cloud backends see it.

## LLM backend findings
- functiongemma (300 MB): too small, refuses/hallucinates with 10 tools. Don't use.
- llama3.2:3b (local): fast, fine for 1-3 step goals, degrades on long chains (emits
  tool calls as text / hallucinated package names). Fallback JSON parser added.
- qwen2.5:7b (4.7 GB): RAM/swap pressure on this phone; never validated end to end.
- Groq `openai/gpt-oss-120b`: worked end to end (WhatsApp "hi"). Free tier limit is
  8000 tokens/min: hit it because screen dumps accumulated in context -> fixed by keeping
  only the newest dump, capping elements/labels, and backoff on 413/429/5xx.
- This Groq key's models: gpt-oss-120b, gpt-oss-20b, gpt-oss-safeguard-20b, qwen/qwen3.8-27b
  (text+image, tools), plus audio/guard models. `llama-3.3-70b-versatile` returned 404.
- Gemini OpenAI-compatible base URL: `https://generativelanguage.googleapis.com/v1beta/openai/`.
  Model-name defaults go stale; the menu lets you type one. Gemini tool messages get a `name`
  field (assumed required; unverified).

## Delivered (phone-agent.zip, rebuilt 2026-09-29)
- `agent.py`: menu (1 ask, 0 switch backend, 4 past runs, 3 quit); backends Groq/Ollama/Gemini/
  custom via `LLM_BASE_URL`; per-run vision toggle; `runs.jsonl` log (goals+results, plain text,
  local); key prompt + persistence; 401/403 -> offer to replace key; Ctrl+C returns to menu.
- `phone_mcp.py` v0.5: 21 tools modelled on Anthropic's computer use toolset (docs:
  `computer_toolset_20260801`, 17 members), adapted to touch.
- `common.py`: MCP client (text + image results), FOCUSED-field guard, history pruning
  (newest screen dump + newest screenshot only), token estimate.
- Agent behaviour copied from computer use: batched actions run in order and stop at first
  failure ("Not executed: an earlier action in this turn failed."), screen state attached after
  the last action of a turn, confirmation gate before send/pay/delete/install-type taps
  (y / N / allow-all; `PHONE_AGENT_CONFIRM=0` disables), on-screen text treated as untrusted.

| Computer use member | Android equivalent |
|---|---|
| screenshot / zoom | `screenshot` / `zoom` (JPEG image; vision models only; Pillow or ImageMagick) |
| left_click | `tap`, `tap_text(text, index)` |
| right_click | `long_press` |
| left_click_drag | `drag` (`input draganddrop`) |
| scroll | `scroll(direction, amount, x, y)` via swipe |
| type | `type_text` (ASCII only) |
| key (+repeat) / hold_key | `key_event` (names, `+` combos, repeat) / `hold_key` (`--longpress`) |
| wait | `wait`, `wait_for_text` |
| left_mouse_down/up | `touch(DOWN/MOVE/UP, x, y)` (Android 12+) |
| mouse_move, middle/triple click, cursor_position | no touchscreen equivalent (skipped) |
| double_click | skipped: each rish call ~1 s, too slow for a double tap |
Also added: `find_package`, `open_url`, `get_current_app` (focus lines only).

## Testing status
- Passed against a FAKE rish (logs every command) and a scripted fake LLM server: all 21 tools,
  arg coercion, error paths, scroll math, screenshot/zoom images, batch halt, confirmation
  gate, focus guard, image pruning, tool-call ordering (incl. missing ids), vision on/off,
  menu flow.
- NOT yet run on the real phone. Unverified on this ROM: `input keycombination` (Android 13+),
  `input motionevent` (12+), `input draganddrop`, `--longpress`, `pm list packages` via rish,
  `open_spotify_search`, Gemini backend (incl. `name` on tool messages), vision.

## Next steps
1. `unzip -o phone-agent.zip`, run `python agent.py`, retry the WhatsApp goal, then Spotify
   "play Lover" (`open_spotify_search`, `tap_text`, `scroll`).
2. `pkg install python-pillow` for screenshots/zoom; try Gemini or Groq `qwen/qwen3.8-27b`
   with vision on.
3. Revoke the Groq key that was pasted into a chat earlier.
4. Ideas not built: clipboard typing for non-ASCII text, app allowlist, macros/skills
   replay without an LLM, notification reading (sensitive).

## Preferences / decisions
- Terminal-first, wants local speed and privacy where possible, but chose Groq for reliability
  on multi-step tasks; Ollama stays as the private option.
- Vendor-agnostic single `agent.py` over per-vendor scripts.
