# ARC Lite 0.3.2 (Windows Asus)

A self-contained, local-only plugin: `mcp.json` starts `server/arc_lite.py` over stdio. No VDS, Docker, public ports, tunnel, or separate MCP API key. ChatGPT itself still needs network access to OpenAI for cloud models.

**Tested on Asus (16 Oct 2026)**: installed `arc-lite@home-ai-local` 0.3.2; packaged Python CLI returns `hostname=Asus`; a Codex model followed `arc-lite-control` to read status, create a specifically authorized temporary file with `write --confirm`, and read it back using `cat`. The test file was removed. The main local project passed 35 tests; this packaged plugin's Windows tests passed 6/6; GitHub Actions Windows smoke passed.

## Use

For files, terminal, and processes, ask an interactive local Codex/Work session to use the **arc-lite-control** skill. It prefers local MCP tools but can fall back to the bundled `arc_cli.py`. Example:

```powershell
rtk python "C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py" status
```

The CLI actions are `status`, `windows`, `processes`, `screenshot`, `app`, `ps`, `ls`, `cat`, `write`, and `tool`. Writes require specific authorization and `--confirm` where applicable.

For **visible desktop** windows, mouse/keyboard and app launching, use **Computer Use in an interactive ChatGPT Desktop Work/Codex session**. The non-interactive `codex exec` test discovered Computer Use but returned `Trusted RPC service is not configured: sky`: that surface cannot be treated as proof of working GUI control. Do not bypass the sandbox by trying to launch GUI from PowerShell. A successful PID alone does not verify a user-visible window.

## Acceptance

1. In ChatGPT Desktop, open a *local* Work/Codex session with plugin ARC Lite enabled.
2. Test reading Asus status.
3. Using supported Computer Use, open Calculator and verify its actual visible window.
4. Repeat after restarting ChatGPT Desktop.
5. Keep any existing Desktop Commander backup until this GUI test passes.

The public/web ChatGPT chat does not directly run this local stdio process. See `skills/arc-lite-control/SKILL.md` and `tests/test_plugin.py`.

No passwords, tokens, or SSH private config belong in this repository. All commands run with the permissions of the logged-in Windows user, with action-specific confirmations.
