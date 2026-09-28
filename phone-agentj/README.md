# Phone agent (Termux + Shizuku)

LLM agent that controls your Android phone. Phone control is always local
(Shizuku `rish` + `phone_mcp.py`); only the reasoning model differs.

## Files
- `phone_mcp.py`   - stdlib-only MCP server (JSON-RPC over stdio), 11 phone tools
- `agent-groq.py`  - agent using Groq (`openai/gpt-oss-120b`), reliable on multi-step goals
- `agent-local.py` - agent using Ollama (`llama3.2:3b`), fine for short 1-3 step goals

## One-time setup (Termux)
1. Shizuku running (wireless debugging pairing), `rish` + `rish_shizuku.dex` in `$PREFIX/bin`
   with `RISH_APPLICATION_ID=com.termux`, `chmod +x rish`.
2. `termux-setup-storage`
3. `pip install groq ollama`  (do NOT `pip install mcp` on Termux: broken Rust wheels)
4. Sanity check: `rish /system/bin/input keyevent KEYCODE_HOME`

## Run
    export GROQ_API_KEY="..."        # never paste it in chats/commits
    python agent-groq.py "Open WhatsApp, go into the first chat, and send 'hi'"

    ollama pull llama3.2:3b
    python agent-local.py "Open WhatsApp"

## Notes
- `rish` execs argv directly: always full paths (/system/bin/input), and it cannot
  exec sh/cat/ls, so files are read from Termux itself.
- `type_text` is blocked unless the last screen state contains an EditText.
- After tap/launch_app/key_event/open_spotify_search the agent auto-dumps the screen.
- `open_spotify_search` (am start spotify:search:...) is untested on your phone.
- The agents can act on anything on the phone: consider adding an app whitelist
  and a confirm step before send/purchase actions.
