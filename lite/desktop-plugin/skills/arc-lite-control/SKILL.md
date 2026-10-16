---
name: arc-lite-control
description: Use ARC Lite on the owner's Asus Windows laptop for local files, PowerShell, processes, applications, windows and desktop UI. Routes terminal tasks through the local ARC Lite CLI and real GUI work through the installed Computer Use skill, without VDS or tunnels.
---

# ARC Lite — Windows local control

Use this skill only on the owner's Asus PC. No VDS, Docker, public MCP endpoint or Secure MCP Tunnel. The local executable is C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py. Its parent directory is packaged in this plugin.

## Decide which control path is needed

**Files, terminal, process listing, system status:** Use the ARC Lite MCP tools if exposed. If the model cannot see the appropriate MCP tool, use the local Codex shell with ARC Lite CLI. It invokes the same validated backend from the logged-in Windows account.

**Applications, Windows UI, screen, mouse or keyboard:** ALWAYS use the separately installed OpenAI computer-use skill through its official node_repl + @oai/sky API. Read that skill, its guidance and confirmations documentation first. The Codex shell may run in CodexSandboxDesktop, which is different from the visible Default desktop. Never launch GUI software through ARC Lite CLI or raw PowerShell from that sandbox; a successful PID does not prove a visible window. Do not bypass the sandbox or invent an IPC channel. Do not mix custom PowerShell UI automation with Computer Use.

When no node_repl/Computer Use is available in a local session, explain that visible GUI actions cannot be executed from the sandbox and use a supported interactive desktop session instead. An error such as `Trusted RPC service is not configured: sky` means the Computer Use bridge is unavailable **on this Codex surface**, not that ARC Lite has failed. In that situation do not retry GUI calls indefinitely, do not fall back to sandboxed PowerShell, and do not claim that a launched PID means a visible window. Use ChatGPT Desktop in a local interactive Work/Codex session with Computer Use. Never claim a GUI result from an empty window list.

## File and terminal commands (local Codex shell)

When the Codex global policy requires RTK, prefix Python invocations with rtk python. Otherwise, use python normally.

    rtk python "C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py" status
    rtk python "C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py" processes
    rtk python "C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py" ls "D:\AI"
    rtk python "C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py" cat "D:\AI\example.txt"
    rtk python "C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py" ps "Write-Output ARC_OK" --confirm

CLI action names are `status`, `windows`, `processes`, `screenshot`, `app`, `ps`, `ls`, `cat`, `write`, and `tool`. There is **no** `files` action; use `ls`, `cat`, and `write`. Start with read-only calls, and confirm only the specific user-approved write. `rtk Get-Content` does not work because `Get-Content` is a PowerShell cmdlet rather than an executable; for reading a bundled skill via PowerShell use `rtk proxy powershell -NoProfile -Command "Get-Content -Raw '<path>'"` if necessary.

For an advanced operation, use the exact tool name and a local JSON arguments file:

    rtk python "C:\Users\TexNik\.codex\plugins\arc-lite\server\arc_cli.py" tool --tool file_info --params-file "D:\AI\arc-args.json"

Use --confirm only after user approval for the specific state-changing action. Do not write or delete the JSON parameters file without an appropriate user-approved workspace action. CLI commands needing long-lived process sessions stay in the persistent MCP interface; they cannot run from one-shot CLI.

## Windows GUI actions (installed Computer Use)

1. Read the installed computer-use SKILL.md and docs/guidance.md, docs/api.md, docs/confirmations.md.
2. Initialize sky once in node_repl with the supported @oai/sky import.
3. Use sky.list_apps() or sky.list_windows() to select the intended app or window from actual returned identifiers.
4. Launch only the user-requested application through sky.launch_app using its returned app id, or use an explicit verified .exe path if the app is absent.
5. Refresh sky.list_apps() / sky.list_windows(); do not claim launch success without a returned matching window. If more than one candidate window remains, resolve selection instead of guessing.
6. To click, type, scroll, press keys, or inspect screenshots, follow the official Computer Use skill. Respect all confirmation requirements and restrictions.

These sky operations target the actual user desktop even when the Codex shell has a sandboxed desktop. They do not change Windows security settings and do not bypass platform confirmations.

## Confirmations, boundaries and results

- Respect ChatGPT/Codex approval controls. For an explicitly requested ordinary app launch, proceed if allowed. Before deleting or overwriting, arbitrary PowerShell, sensitive screen sharing, or modifying files, obtain concrete authorization as appropriate.
- Do not attempt to inspect passwords, security dialogs, password managers or privacy settings through Computer Use. Do not use PowerShell as an alternative means to evade Computer Use restrictions.
- Report observed tool outputs only. The CLI returns JSON with ok true or false and a reason. Any GUI success requires verifying a window from Computer Use, not a PID.
- Run only on Asus in a local Codex/ChatGPT Desktop session. A web-only thread cannot directly use a local stdio process.
- Keep the previous Desktop Commander connection untouched until the user confirms ARC Lite works across app restarts.
