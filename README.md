# ARC — AI Remote Commander

**ARC Hub 0.2** is a multi-user MCP gateway prototype for Windows computers running **ARC Agent**.

- **ARC Hub** (`vds/`): Python MCP gateway for a Debian VDS with independent user/device credentials, roles, per-device grants, audit records and a short exclusive-control lease.
- **ARC Agent** (`windows/`): background Windows agent (Python 3.11+), loopback MCP and **outbound HTTPS** communication to Hub.
- **ARC plugin** (`plugin/`): short-name ChatGPT plugin **template**; not yet configured for a real domain or individual OAuth sign-in.
- **Tests** (`tests/`): authentication, user isolation, explicit device routing and control lease tests.

## Status

**Prototype — not production-ready.** The code was tested locally on an Asus PC. A remote VDS deployment and multi-user OAuth/OIDC login are **not yet completed**. The existing Asus pilot on port 8766 and Fleet 0.1 on 8876 should remain available until the new system is proven. ARC Agent 0.2 was installed side by side on port 8896.

## Layout

| Path | Purpose |
| --- | --- |
| `vds/arc_hub.py` | Authenticated MCP gateway and device broker |
| `vds/arc_admin.py` | Local CLI to register users, computers and grants |
| `vds/install-vds.sh` | Debian systemd setup |
| `windows/install.cmd` | Windows agent install using Task Scheduler |
| `plugin/` | Manifest template for the shorter name **ARC** |
| `tests/` | Standard-library Python unittest suite |

## Running tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile vds/arc_hub.py vds/arc_admin.py
```

## One-command VDS installation (Debian 12/13)

On a new VDS, connect using SSH and run:

```bash
curl -fsSL https://raw.githubusercontent.com/DeadSoulf/ARC-Hub/main/scripts/install-vds.sh | sudo bash
```

Enter the **real DNS hostname** pointing at your VDS when prompted. The script installs the systemd service, sets up Caddy HTTPS, and checks localhost health. DNS must point at the VDS; inbound ports 80/443 must be allowed by the provider. A more explicit non-interactive variant is:

```bash
curl -fsSL https://raw.githubusercontent.com/DeadSoulf/ARC-Hub/main/scripts/install-vds.sh | sudo bash -s -- --domain arc.yourdomain.com
```

**Review [the installer source](scripts/install-vds.sh) before running remote code as root.** Do not publish your owner credentials. The installer does **not** implement multi-user OAuth for ChatGPT; see the [full VDS guide](vds/README.md).

## Installation

Follow the [VDS installation guide](vds/README.md) and [Windows agent guide](windows/README.md).
ARC Hub binds to localhost; place a secure HTTPS proxy in front of it on VDS. Do not expose a Windows agent port to the public internet.

## Secrets and safety

This repository is **public**. Never commit API keys, private keys, registry/agent configuration, enrollment files, screenshots, logs, credentials or individual user data. Only source code and templates belong here.

Each user and device must have their own credentials. **A shared ChatGPT plugin cannot safely use a single administrator token.** Before rolling out multi-user sign-in, add standards-compliant OAuth/OIDC identity validation, conduct a security review, and test permissions and revocations.

## Next milestones

1. Deploy the central Hub to the chosen Debian VDS with HTTPS.
2. Implement identity-aware OAuth/OIDC for the shared ChatGPT plugin.
3. Enroll Asus and a second Windows PC independently.
4. Test authorization, concurrent operators, reconnections, rotations and backup/restore.
5. Only then migrate from the legacy Asus connector.
