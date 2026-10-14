#!/usr/bin/env bash
# Fail-closed OAuth recovery for the existing vpn.homeaicore.ru ARC Hub.
# Never uploads, displays or changes stored user/device credentials.
set -Eeuo pipefail
umask 077

DOMAIN="vpn.homeaicore.ru"
ORIGIN="https://$DOMAIN"
ROOT="/opt/arc"
REGISTRY="/etc/arc/registry.json"
ENVFILE="/etc/arc/oauth.env"
UNIT="/etc/systemd/system/arc-hub.service"
REPOSITORY="https://github.com/DeadSoulf/ARC-Hub.git"

usage() {
  echo "Usage: sudo bash repair-oauth.sh --ref EXACT_40_CHAR_GITHUB_SHA"
}
if [[ "$#" -eq 1 && "$1" == "--help" ]]; then usage; exit 0; fi
if [[ "$#" -ne 2 || "$1" != "--ref" ]]; then usage >&2; exit 2; fi
REF="$2"
[[ "$REF" =~ ^[0-9a-f]{40}$ ]] || { usage >&2; exit 2; }
[[ "$EUID" -eq 0 ]] || { echo "Run from an authenticated root/sudo Debian console" >&2; exit 2; }
fail() { printf 'ARC RECOVERY FAILED: %s\n' "$*" >&2; exit 1; }
info() { printf 'ARC RECOVERY: %s\n' "$*"; }

for tool in curl git python3 systemctl install cp mktemp getent; do
  command -v "$tool" >/dev/null 2>&1 || fail "Missing command: $tool"
done
[[ -r /etc/os-release ]] || fail "No OS information"
. /etc/os-release
[[ "$ID" == "debian" && "$VERSION_ID" =~ ^(12|13)([.]|$) ]] || fail "Debian 12/13 required"
[[ -f "$ROOT/arc_hub.py" && -f "$ROOT/arc_web.py" && -f "$REGISTRY" && -f "$UNIT" ]] ||
  fail "Existing ARC Hub service and registry not found"
[[ ! -L "$ROOT" && ! -L /etc/arc ]] || fail "Symlinked installation not supported"
grep -Fq '/opt/arc/arc_hub.py' "$UNIT" || fail "Unexpected systemd unit"
getent passwd arc-hub >/dev/null && getent group arc-hub >/dev/null || fail "Missing ARC system user/group"
if [[ -f "$ENVFILE" ]] && ! grep -Fxq "ARC_PUBLIC_URL=$ORIGIN" "$ENVFILE"; then
  fail "Existing OAuth issuer differs; refusing overwrite"
fi
if systemctl is-active --quiet arc-update.service; then
  fail "An ARC update is already running"
fi
python3 - "$REGISTRY" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    obj = json.load(stream)
assert obj.get("version") == 2
assert isinstance(obj.get("users"), dict)
assert isinstance(obj.get("devices"), dict)
PY
curl -fsS --max-time 7 http://127.0.0.1:8877/healthz |
  python3 -c 'import json,sys; v=json.load(sys.stdin); assert v.get("status")=="ok" and v.get("service")=="arc-hub"' ||
  fail "The expected local ARC Hub is not running"

TMP=""
BACKUP=""
MODIFIED=0
restore() {
  local name="$1" dest="$2"
  if [[ -f "$BACKUP/$name" ]]; then
    cp -a "$BACKUP/$name" "$dest" || true
  else
    rm -f -- "$dest" || true
  fi
}
finish() {
  local result=$?
  trap - EXIT
  if (( result != 0 && MODIFIED == 1 )); then
    info "Repair failed; restoring previous ARC files and systemd configuration"
    cp -a "$BACKUP/opt-arc/." "$ROOT/" || true
    [[ -f "$BACKUP/opt-arc/arc_oauth.py" ]] || rm -f "$ROOT/arc_oauth.py"
    cp -a "$BACKUP/arc-hub.service" "$UNIT" || true
    restore oauth.env "$ENVFILE"
    restore arc-update-apply /usr/local/libexec/arc-update-apply
    restore arc-update-runner /usr/local/libexec/arc-update-runner
    restore arc-update.path /etc/systemd/system/arc-update.path
    restore arc-update.service /etc/systemd/system/arc-update.service
    systemctl daemon-reload || true
    systemctl restart arc-hub || true
    info "Protected backup remains at $BACKUP"
  fi
  [[ -z "$TMP" ]] || rm -rf -- "$TMP"
  exit "$result"
}
trap finish EXIT

TMP="$(mktemp -d /tmp/arc-oauth-repair.XXXXXXXX)"
info "Retrieving reviewed ARC sources without changing the running server"
git clone --quiet --depth 1 --branch main "$REPOSITORY" "$TMP/repo" ||
  fail "GitHub checkout failed"
ACTUAL="$(git -C "$TMP/repo" rev-parse HEAD)"
[[ "$ACTUAL" == "$REF" ]] || fail "Repository HEAD changed; expected $REF, found $ACTUAL"
for file in vds/arc_hub.py vds/arc_oauth.py vds/arc_web.py vds/arc_pairing.py \
            vds/arc_admin.py vds/arc-hub.service vds/web/index.html vds/web/app.css \
            vds/web/app.js vds/arc-update.path vds/arc-update.service \
            vds/arc-update-runner.sh scripts/update-web.sh; do
  [[ -f "$TMP/repo/$file" ]] || fail "Missing checked release file $file"
done

info "Running regression tests before installing"
for script in "$TMP/repo/scripts/update-web.sh" "$TMP/repo/vds/install-vds.sh"; do
  bash -n "$script" || fail "Invalid shell syntax"
done
python3 -m py_compile "$TMP/repo/vds/arc_hub.py" "$TMP/repo/vds/arc_oauth.py" ||
  fail "Invalid Python syntax"
if command -v node >/dev/null 2>&1; then
  node --check "$TMP/repo/vds/web/app.js" || fail "Invalid JavaScript syntax"
fi
(cd "$TMP/repo" && python3 -m unittest discover -s tests -p 'test_*.py' -q) >"$TMP/tests.log" 2>&1 ||
  { tail -n 25 "$TMP/tests.log" >&2; fail "Regression tests failed, nothing installed"; }

install -d -m 0700 /var/backups/arc
BACKUP="$(mktemp -d /var/backups/arc/oauth-repair.XXXXXXXX)"
chmod 0700 "$BACKUP"
info "Creating a private pre-update backup"
cp -a "$ROOT" "$BACKUP/opt-arc"
cp -a "$REGISTRY" "$BACKUP/registry.json"
cp -a "$UNIT" "$BACKUP/arc-hub.service"
[[ ! -f "$ENVFILE" ]] || cp -a "$ENVFILE" "$BACKUP/oauth.env"
for file in arc-update-apply arc-update-runner; do
  [[ ! -f "/usr/local/libexec/$file" ]] || cp -a "/usr/local/libexec/$file" "$BACKUP/$file"
done
for file in arc-update.path arc-update.service; do
  [[ ! -f "/etc/systemd/system/$file" ]] || cp -a "/etc/systemd/system/$file" "$BACKUP/$file"
done

MODIFIED=1
info "Installing OAuth Hub; device registration and Caddy remain unchanged"
install -m 0644 "$TMP/repo/vds/arc_hub.py" "$TMP/repo/vds/arc_oauth.py" \
  "$TMP/repo/vds/arc_web.py" "$TMP/repo/vds/arc_pairing.py" \
  "$TMP/repo/vds/arc_admin.py" "$ROOT/"
install -d -m 0755 "$ROOT/web"
install -m 0644 "$TMP/repo/vds/web/index.html" "$TMP/repo/vds/web/app.css" \
  "$TMP/repo/vds/web/app.js" "$ROOT/web/"
install -m 0644 "$TMP/repo/vds/arc-hub.service" "$UNIT"
printf 'ARC_PUBLIC_URL=%s\n' "$ORIGIN" >"$ENVFILE"
chown root:arc-hub "$ENVFILE"
chmod 0640 "$ENVFILE"
install -d -m 0755 /usr/local/libexec
install -m 0755 "$TMP/repo/scripts/update-web.sh" /usr/local/libexec/arc-update-apply
install -m 0755 "$TMP/repo/vds/arc-update-runner.sh" /usr/local/libexec/arc-update-runner
install -m 0644 "$TMP/repo/vds/arc-update.path" "$TMP/repo/vds/arc-update.service" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl restart arc-hub

info "Validating the local and public OAuth endpoints"
READY=0
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS --max-time 3 http://127.0.0.1:8877/healthz >/dev/null 2>&1 &&
    curl -fsS --max-time 3 http://127.0.0.1:8877/.well-known/oauth-authorization-server |
      python3 -c 'import json,sys; x=json.load(sys.stdin); assert x["issuer"]=="https://vpn.homeaicore.ru" and "S256" in x["code_challenge_methods_supported"]' >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
(( READY == 1 )) || fail "New local OAuth endpoint failed its health check"
curl -fsS --max-time 10 "$ORIGIN/.well-known/oauth-protected-resource" |
  python3 -c 'import json,sys; x=json.load(sys.stdin); assert x["resource"]=="https://vpn.homeaicore.ru/mcp"' ||
  fail "Public resource metadata is unreachable"
curl -fsS --max-time 10 "$ORIGIN/.well-known/oauth-authorization-server" |
  python3 -c 'import json,sys; x=json.load(sys.stdin); assert x["issuer"]=="https://vpn.homeaicore.ru"' ||
  fail "Public authorization metadata is unreachable"

printf '%s\n' "$REF" >"$ROOT/REVISION"
if systemctl is-enabled --quiet arc-update.path; then systemctl restart arc-update.path; fi
MODIFIED=0
info "ARC_OAUTH_REPAIR_OK"
info "User/device credentials preserved; protected backup: $BACKUP"
info "NEXT: reconnect ARC in ChatGPT and grant apps.launch for Asus through ARC Web."
