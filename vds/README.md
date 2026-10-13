# ARC Hub 0.2 — Linux VDS

**Status:** multi-user prototype, tested locally on Asus. Not yet deployed on an external VDS.

## Install on Debian (once VDS address/SSH access is configured)

1. Upload ARC-Hub-v0.2.tar.gz over authenticated SSH/SFTP; verify SHA-256.
2. `tar -xzf ARC-Hub-v0.2.tar.gz`, change to extracted vds directory.
3. Install required OS Python3, then `sudo bash install-vds.sh`.
4. Point a DNS hostname at the VDS and install Caddy/Nginx with HTTPS certificates.
5. Configure Caddyfile.example with the *actual* hostname and reload Caddy.
6. Allow inbound TCP 443 only for the proxy; **do not expose 8877**.
7. Verify `curl http://127.0.0.1:8877/healthz` on VDS.

ARC Hub requires a Linux VDS with root or sudo (setup only), DNS host,
outbound HTTPS from Windows devices to the VDS, and a TLS proxy. No ports
need to be opened toward Windows devices.

## Administrators / users

Registry and private credentials are under /etc/arc (mode 0700).
The local CLI below must run as the arc-hub service user through sudo:

    sudo -u arc-hub python3 /opt/arc/arc_admin.py --config /etc/arc/registry.json add-user alice --role viewer --credentials /etc/arc/alice-login.json

For every user, generate separate credentials and grant *specific* devices
and scopes. The token is stored only in owner-readable credentials file,
not printed in console or distributed inside agent installer.

    sudo -u arc-hub python3 /opt/arc/arc_admin.py --config /etc/arc/registry.json add-device PC02 --name Office --gateway-url https://arc.example.com --enrollment /etc/arc/PC02-enroll.json --scopes status,inspect,files.read

    sudo -u arc-hub python3 /opt/arc/arc_admin.py --config /etc/arc/registry.json grant alice PC02 --scopes status

Service restart is required to reload changes:

    sudo systemctl restart arc-hub

The enrollment file is secret; provision it securely to the correct
Windows computer and erase the transfer copy. Rotate compromised keys.

**Role ceilings:** viewer=status, operator=non-shell controls only with
explicit grants, admin=all scopes but still limited by per-device grants.
The gateway **never** selects a default PC; every action requires device_id.
All tools including mutating tools are disabled until permissions have
been granted to user AND enabled for the device AND locally enabled on PC.

Before modifying a computer or controlling its keyboard/mouse, call
`arc_take_control(device_id)` to acquire a 90-second exclusive lease.
The lease is renewed by authorized mutating operations. Other operators
can read in parallel but cannot write or control the same device while
another user owns the lease. Call `arc_release_control(device_id)` when done.
The ARC Hub MCP tool name list is: arc_devices, arc_permissions,
arc_take_control, arc_release_control, arc_call.

## ChatGPT authentication caveat

This version authenticates each user with a distinct bearer token.
For a shared ChatGPT plugin that supports separate sign-in for different
people, implement OAuth 2.1/OIDC identity with token validation on each
MCP request (a single shared API token is NOT sufficient). Do not enable
a shared plugin with one administrator token.

The current Asus Secure MCP Tunnel remains active on the *old*
local server and is not connected automatically to the new ARC Hub.
You can test the Hub with direct HTTPS URL + individual API key during
private development. OAuth is needed before team-wide plugin rollout.

## Auditing

/var/log/arc/audit.jsonl logs user, device, tool, outcome and timestamp
without tool arguments or results. Keep audit logs private and implement
centralized logging/alerting for a production environment.
