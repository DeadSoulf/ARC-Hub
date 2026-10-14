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
# systemd restart returns before the Python HTTP listener is necessarily ready.
# Wait for actual HTTP success, not merely the systemd Active state.
wait_for_http() {
  local url="$1" max_attempts="${2:-30}" label="${3:-ARC Hub}"
  local attempt
  for ((attempt=1; attempt<=max_attempts; attempt++)); do
    if curl --fail --silent --max-time 3 "$url" >/dev/null; then
      printf 'ARC: %s ready (attempt %s/%s)\n' "$label" "$attempt" "$max_attempts"
      return 0
    fi
    sleep 1
  done
  printf 'ARC: %s not ready after %s attempts: %s\n' "$label" "$max_attempts" "$url" >&2
  return 1
}
cleanup() {
  status=$?
  if ((status != 0 && CHANGED == 1)); then
    echo "ARC: update failed; restoring backed-up Hub and Caddy configuration" >&2
    cp -a "$BACKUP/arc_hub.py" "$ROOT/arc_hub.py" || true
    if [[ -f "$BACKUP/arc_web.py" ]]; then cp -a "$BACKUP/arc_web.py" "$ROOT/arc_web.py"; else rm -f "$ROOT/arc_web.py"; fi
    if [[ -d "$BACKUP/web" ]]; then cp -a "$BACKUP/web" "$ROOT/"; else rm -rf "$ROOT/web"; fi
    cp -a "$BACKUP/Caddyfile" "$SITE" || true
    systemctl restart arc-hub || true
    if ! wait_for_http http://127.0.0.1:8877/healthz 30 "rollback ARC Hub"; then
      echo "ARC: rollback did not restore the HTTP service; inspect journalctl -u arc-hub -n 80" >&2
    fi
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
for p in vds/arc_hub.py vds/arc_web.py vds/web/index.html vds/web/app.css vds/web/app.js vds/arc-update.path vds/arc-update.service vds/arc-update-runner.sh scripts/update-web.sh; do
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
# Python HTTP server may need several seconds after systemd marks it active.
if ! wait_for_http http://127.0.0.1:8877/healthz 30 "new ARC Hub"; then
  echo "ARC: systemd status and recent Python logs:" >&2
  systemctl --no-pager --full status arc-hub >&2 || true
  journalctl -u arc-hub -n 40 --no-pager >&2 || true
  err "ARC Hub did not become ready; rolling back"
fi
if ! wait_for_http http://127.0.0.1:8877/web/ 10 "ARC Web"; then
  err "web HTML is not served; rolling back"
fi
systemctl reload caddy
# Install an owner-triggered, root-owned fixed updater. The browser never runs shell.
install -d -o arc-hub -g arc-hub -m 0700 /var/lib/arc-hub/updates
install -d -m 0755 /usr/local/libexec
install -m 0755 "$TEMP/repo/vds/arc-update-runner.sh" /usr/local/libexec/arc-update-runner.new
mv -f /usr/local/libexec/arc-update-runner.new /usr/local/libexec/arc-update-runner
install -m 0755 "$TEMP/repo/scripts/update-web.sh" /usr/local/libexec/arc-update-apply.new
mv -f /usr/local/libexec/arc-update-apply.new /usr/local/libexec/arc-update-apply
install -m 0644 "$TEMP/repo/vds/arc-update.path" "$TEMP/repo/vds/arc-update.service" /etc/systemd/system/
install -m 0644 "$TEMP/repo/vds/arc-hub.service" /etc/systemd/system/
( cd "$TEMP/repo" && git rev-parse --short=12 HEAD ) > /opt/arc/REVISION
systemctl daemon-reload
systemctl enable --now arc-update.path
CHANGED=0
echo "ARC_WEB_READY https://$domain/web/"
echo "Owner login: owner. Private credential file: /etc/arc/owner-credentials.json"
echo "Never paste credentials into ChatGPT or GitHub."
echo "Original Hub and Caddy configuration backed up under $BACKUP"
