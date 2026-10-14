#!/usr/bin/env bash
# ARC Web updater. Preserves VDS device/user credentials and older backups.
set -Eeuo pipefail
umask 077
ROOT=/opt/arc
SITE=/etc/caddy/Caddyfile
REGISTRY=/etc/arc/registry.json
TEMP=""
BACKUP=""
CHANGED=0
err() { printf 'ARC UPDATE ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  status=$?
  if ((status != 0 && CHANGED == 1)); then
    echo "ARC: update failed; restoring backed-up Hub and Caddy configuration" >&2
    cp -a "$BACKUP/arc_hub.py" "$ROOT/arc_hub.py" || true
    if [[ -f "$BACKUP/arc_web.py" ]]; then cp -a "$BACKUP/arc_web.py" "$ROOT/arc_web.py"; else rm -f "$ROOT/arc_web.py"; fi
    if [[ -d "$BACKUP/web" ]]; then cp -a "$BACKUP/web" "$ROOT/"; else rm -rf "$ROOT/web"; fi
    cp -a "$BACKUP/Caddyfile" "$SITE" || true
    systemctl restart arc-hub || true
    systemctl reload caddy || true
  fi
  [[ -z "$TEMP" ]] || rm -rf -- "$TEMP"
}
trap cleanup EXIT
[[ "$(id -u)" -eq 0 ]] || err "run through sudo"
[[ -f "$REGISTRY" && -f "$ROOT/arc_hub.py" ]] || err "ARC Hub must already be installed"
[[ -f "$SITE" ]] || err "Caddyfile not found"
command -v git >/dev/null || err "git is required"
command -v caddy >/dev/null || err "caddy is required"
. /etc/os-release
[[ "$ID" == "debian" ]] || err "supported OS: Debian"

# Refuse to overwrite unrelated Caddy sites/proxies.
sites="$(grep -Ec '^[[:space:]]*[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+[[:space:]]*\{' "$SITE" || true)"
[[ "$sites" == "1" ]] || err "expected exactly one ARC site in Caddyfile; edit manually if you host other sites"
grep -Fq 'reverse_proxy 127.0.0.1:8877' "$SITE" || err "Caddyfile does not proxy ARC Hub"
if ! grep -Fq '@arc path /mcp /agent/poll /agent/result' "$SITE" &&
   ! grep -Fq '# Managed by ARC Hub installer' "$SITE"; then
  err "Caddyfile is not an identified ARC configuration; refusing to replace it"
fi
domain="$(awk '$1 ~ /^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$/ && $2=="{" {print $1}' "$SITE" | head -n 1)"
[[ "$domain" =~ ^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$ ]] || err "invalid ARC DNS domain"

TEMP="$(mktemp -d /tmp/arc-web-upgrade.XXXXXXXX)"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch main \
  https://github.com/DeadSoulf/ARC-Hub.git "$TEMP/repo" || err "GitHub checkout failed"
for p in vds/arc_hub.py vds/arc_web.py vds/web/index.html vds/web/app.css vds/web/app.js; do
  [[ -f "$TEMP/repo/$p" ]] || err "missing $p in GitHub checkout"
done
( cd "$TEMP/repo" &&
  python3 -m unittest discover -s tests -p 'test_*.py' -q &&
  python3 -m py_compile vds/arc_hub.py vds/arc_web.py )
if command -v node >/dev/null; then node --check "$TEMP/repo/vds/web/app.js"; fi
cat > "$TEMP/Caddyfile" <<EOF
# Managed by ARC Hub installer (ARC Web enabled)
$domain {
    reverse_proxy 127.0.0.1:8877
}
EOF
caddy validate --config "$TEMP/Caddyfile" --adapter caddyfile >/dev/null ||
  err "invalid Caddyfile; nothing installed"
BACKUP="/var/backups/arc/arc-web-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP"
cp -a "$ROOT/arc_hub.py" "$BACKUP/arc_hub.py"
[[ ! -f "$ROOT/arc_web.py" ]] || cp -a "$ROOT/arc_web.py" "$BACKUP/arc_web.py"
[[ ! -d "$ROOT/web" ]] || cp -a "$ROOT/web" "$BACKUP/web"
cp -a "$SITE" "$BACKUP/Caddyfile"
CHANGED=1
install -m 0644 "$TEMP/repo/vds/arc_hub.py" "$ROOT/arc_hub.py"
install -m 0644 "$TEMP/repo/vds/arc_web.py" "$ROOT/arc_web.py"
install -d -m 0755 "$ROOT/web"
install -m 0644 "$TEMP/repo/vds/web/index.html" "$TEMP/repo/vds/web/app.css" \
  "$TEMP/repo/vds/web/app.js" "$ROOT/web/"
install -m 0644 "$TEMP/Caddyfile" "$SITE"
systemctl restart arc-hub
systemctl is-active --quiet arc-hub || err "ARC Hub failed to start"
curl -fsS --max-time 5 http://127.0.0.1:8877/healthz >/dev/null ||
  err "health check failed"
curl -fsS --max-time 5 http://127.0.0.1:8877/web/ >/dev/null ||
  err "web HTML is not served"
systemctl reload caddy
CHANGED=0
echo "ARC_WEB_READY https://$domain/web/"
echo "Owner login: owner. Private credential file: /etc/arc/owner-credentials.json"
echo "Never paste credentials into ChatGPT or GitHub."
echo "Original Hub and Caddy configuration backed up under $BACKUP"
