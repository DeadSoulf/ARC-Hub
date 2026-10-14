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
# The privileged runner observes ONLY these constant stage markers; never
# forward untrusted git/test output or credentials into the browser.
progress() { printf 'ARC_PROGRESS:%s\n' "$1"; }
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
    progress rollback
    echo "ARC: update failed; restoring backed-up Hub and Caddy configuration" >&2
    cp -a "$BACKUP/arc_hub.py" "$ROOT/arc_hub.py" || true
    if [[ -f "$BACKUP/arc_web.py" ]]; then cp -a "$BACKUP/arc_web.py" "$ROOT/arc_web.py"; else rm -f "$ROOT/arc_web.py"; fi
    if [[ -f "$BACKUP/arc_pairing.py" ]]; then cp -a "$BACKUP/arc_pairing.py" "$ROOT/arc_pairing.py"; else rm -f "$ROOT/arc_pairing.py"; fi
    if [[ -f "$BACKUP/arc_oauth.py" ]]; then cp -a "$BACKUP/arc_oauth.py" "$ROOT/arc_oauth.py"; else rm -f "$ROOT/arc_oauth.py"; fi
    if [[ -f "$BACKUP/oauth.env" ]]; then cp -a "$BACKUP/oauth.env" /etc/arc/oauth.env; else rm -f /etc/arc/oauth.env; fi
    if [[ -f "$BACKUP/arc-hub.service" ]]; then cp -a "$BACKUP/arc-hub.service" /etc/systemd/system/arc-hub.service; fi
    if [[ -d "$BACKUP/web" ]]; then cp -a "$BACKUP/web" "$ROOT/"; else rm -rf "$ROOT/web"; fi
    cp -a "$BACKUP/Caddyfile" "$SITE" || true
    systemctl daemon-reload || true
    systemctl restart arc-hub || true
    if ! wait_for_http http://127.0.0.1:8877/healthz 30 "rollback ARC Hub"; then
      echo "ARC: rollback did not restore the HTTP service; inspect journalctl -u arc-hub -n 80" >&2
    fi
    systemctl reload caddy || true
  fi
  [[ -z "$TEMP" ]] || rm -rf -- "$TEMP"
}
trap cleanup EXIT
progress prepare
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

progress download
TEMP="$(mktemp -d /tmp/arc-web-upgrade.XXXXXXXX)"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch main \
  https://github.com/DeadSoulf/ARC-Hub.git "$TEMP/repo" || err "GitHub checkout failed"
for p in vds/arc_hub.py vds/arc_oauth.py vds/arc_web.py vds/arc_pairing.py vds/web/index.html vds/web/app.css vds/web/app.js vds/arc-update.path vds/arc-update.service vds/arc-update-runner.sh scripts/update-web.sh; do
  [[ -f "$TEMP/repo/$p" ]] || err "missing $p in GitHub checkout"
done
progress test
( cd "$TEMP/repo" &&
  python3 -m unittest discover -s tests -p 'test_*.py' -q &&
  python3 -m py_compile vds/arc_hub.py vds/arc_oauth.py vds/arc_web.py )
if command -v node >/dev/null; then node --check "$TEMP/repo/vds/web/app.js"; fi
cat > "$TEMP/Caddyfile" <<EOF
# Managed by ARC Hub installer (ARC Web enabled)
$domain {
    reverse_proxy 127.0.0.1:8877
}
EOF
caddy validate --config "$TEMP/Caddyfile" --adapter caddyfile >/dev/null ||
  err "invalid Caddyfile; nothing installed"
progress backup
BACKUP="/var/backups/arc/arc-web-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP"
cp -a "$ROOT/arc_hub.py" "$BACKUP/arc_hub.py"
[[ ! -f "$ROOT/arc_web.py" ]] || cp -a "$ROOT/arc_web.py" "$BACKUP/arc_web.py"
[[ ! -f "$ROOT/arc_pairing.py" ]] || cp -a "$ROOT/arc_pairing.py" "$BACKUP/arc_pairing.py"
[[ ! -f "$ROOT/arc_oauth.py" ]] || cp -a "$ROOT/arc_oauth.py" "$BACKUP/arc_oauth.py"
[[ ! -f /etc/arc/oauth.env ]] || cp -a /etc/arc/oauth.env "$BACKUP/oauth.env"
[[ ! -f /etc/systemd/system/arc-hub.service ]] || cp -a /etc/systemd/system/arc-hub.service "$BACKUP/arc-hub.service"
[[ ! -d "$ROOT/web" ]] || cp -a "$ROOT/web" "$BACKUP/web"
cp -a "$SITE" "$BACKUP/Caddyfile"
CHANGED=1
progress install
install -m 0644 "$TEMP/repo/vds/arc_hub.py" "$ROOT/arc_hub.py"
install -m 0644 "$TEMP/repo/vds/arc_web.py" "$ROOT/arc_web.py"
install -m 0644 "$TEMP/repo/vds/arc_pairing.py" "$ROOT/arc_pairing.py"
install -m 0644 "$TEMP/repo/vds/arc_oauth.py" "$ROOT/arc_oauth.py"
install -d -m 0755 "$ROOT/web"
install -m 0644 "$TEMP/repo/vds/web/index.html" "$TEMP/repo/vds/web/app.css" \
  "$TEMP/repo/vds/web/app.js" "$ROOT/web/"
install -m 0644 "$TEMP/Caddyfile" "$SITE"
# Domain came from a validated, ARC-managed Caddyfile. No token is stored here.
printf 'ARC_PUBLIC_URL=https://%s
' "$domain" > /etc/arc/oauth.env
chown root:arc-hub /etc/arc/oauth.env
chmod 0640 /etc/arc/oauth.env
# Make the updated unit load ARC_PUBLIC_URL before restarting the service.
install -m 0644 "$TEMP/repo/vds/arc-hub.service" /etc/systemd/system/arc-hub.service
systemctl daemon-reload
progress restart
systemctl restart arc-hub
# Python HTTP server may need several seconds after systemd marks it active.
progress health
if ! wait_for_http http://127.0.0.1:8877/healthz 30 "new ARC Hub"; then
  echo "ARC: systemd status and recent Python logs:" >&2
  systemctl --no-pager --full status arc-hub >&2 || true
  journalctl -u arc-hub -n 40 --no-pager >&2 || true
  err "ARC Hub did not become ready; rolling back"
fi
if ! wait_for_http http://127.0.0.1:8877/web/ 10 "ARC Web"; then
  err "web HTML is not served; rolling back"
fi
if ! curl --fail --silent --max-time 5 http://127.0.0.1:8877/.well-known/oauth-authorization-server | python3 -c 'import json,sys; j=json.load(sys.stdin); assert j["code_challenge_methods_supported"]==["S256"] and j["issuer"].startswith("https://")'; then
  err "OAuth discovery is unhealthy; rolling back"
fi
progress service
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
# Keep state directory owned by the non-privileged Hub process.
chown arc-hub:arc-hub /etc/arc
chmod 0700 /etc/arc
( cd "$TEMP/repo" && git rev-parse --short=12 HEAD ) > /opt/arc/REVISION
systemctl daemon-reload
# Reload ARC Hub once more so the new systemd sandbox ReadWritePaths
# includes /var/lib/arc-hub/updates; otherwise the browser cannot queue jobs.
systemctl restart arc-hub
if ! wait_for_http http://127.0.0.1:8877/healthz 30 "ARC Hub with update permissions"; then
  err "ARC Hub did not become ready after systemd unit refresh; rolling back"
fi
systemctl enable --now arc-update.path
progress complete
CHANGED=0
echo "ARC_WEB_READY https://$domain/web/"
echo "Owner login: owner. Private credential file: /etc/arc/owner-credentials.json"
echo "Never paste credentials into ChatGPT or GitHub."
echo "Original Hub and Caddy configuration backed up under $BACKUP"
