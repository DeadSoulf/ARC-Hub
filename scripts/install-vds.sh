#!/usr/bin/env bash
# ARC Hub one-command installer for a fresh Debian 12/13 VDS.
# Source: https://github.com/DeadSoulf/ARC-Hub
set -Eeuo pipefail
umask 077

REPO="https://github.com/DeadSoulf/ARC-Hub.git"
REF="main"
DOMAIN=""
CADDYFILE="/etc/caddy/Caddyfile"
TMP=""
usage() {
  cat <<'EOF'
ARC Hub quick installer (Debian 12/13)
Usage:
  curl -fsSL https://raw.githubusercontent.com/DeadSoulf/ARC-Hub/main/scripts/install-vds.sh | sudo bash
  # Installer will prompt for domain; alternatively pass --domain arc.your-domain.com

Options:
  --domain NAME    Required DNS hostname for ARC Hub
  --ref GIT_REF    Optional repository branch or tag; default: main
  --help           Show this help without modifying the system

Runs public ARC Hub code as root: inspect GitHub sources before installing.
Does not open firewall ports or enable shared ChatGPT OAuth sign-in.
EOF
}
fail() { printf 'ARC ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf 'ARC: %s\n' "$*"; }
cleanup() { if [[ -n "$TMP" && -d "$TMP" ]]; then rm -rf -- "$TMP"; fi; }
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --domain)
      (($# >= 2)) || fail "--domain requires a hostname"
      DOMAIN="${2,,}"; shift 2 ;;
    --ref)
      (($# >= 2)) || fail "--ref requires a Git branch or tag"
      REF="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
if [[ -z "$DOMAIN" ]]; then
  if [[ -t 2 && -r /dev/tty ]]; then
    printf 'ARC: Enter your HTTPS DNS hostname (e.g. arc.mydomain.com): ' >/dev/tty
    IFS= read -r DOMAIN </dev/tty || fail "could not read hostname"
    DOMAIN="${DOMAIN,,}"
  else
    fail "no interactive terminal found; pass --domain arc.your-domain.com"
  fi
fi
[[ -n "$DOMAIN" ]] || fail "domain is required"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] ||
  fail "invalid DNS hostname"
[[ "$DOMAIN" != "arc.example.com" && "$DOMAIN" != *.example.com && "$DOMAIN" != *.example.net && "$DOMAIN" != *.example.org ]] ||
  fail "replace example domain with your real hostname"
[[ "$REF" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,90}$ && "$REF" != *".."* && "$REF" != *"//"* ]] ||
  fail "invalid Git ref"
[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "run through sudo"
[[ -f /etc/os-release ]] || fail "cannot read OS release"
. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_ID%%.*}" =~ ^(12|13)$ ]] ||
  fail "supported OS: Debian 12 or 13 (detected: ${PRETTY_NAME:-unknown})"
command -v apt-get >/dev/null || fail "apt-get missing"

# Never overwrite an unrelated reverse proxy configuration.
if [[ -s "$CADDYFILE" ]]; then
  if grep -Fq '# Managed by ARC Hub installer' "$CADDYFILE"; then
    info "existing ARC-managed Caddy configuration detected"
  elif grep -Eq '^[[:space:]]*:80[[:space:]]*\{' "$CADDYFILE" &&
       grep -Fq '/usr/share/caddy' "$CADDYFILE" &&
       ! grep -Eq 'reverse_proxy|import|tls[[:space:]]|https://' "$CADDYFILE"; then
    info "default Caddy welcome page detected"
  else
    fail "custom Caddyfile present; refusing to overwrite $CADDYFILE"
  fi
fi

info "installing required Debian packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates git curl python3 caddy
TMP="$(mktemp -d /tmp/arc-install.XXXXXXXX)"
info "downloading ARC Hub ref $REF"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$REF" "$REPO" "$TMP/repo" ||
  fail "GitHub clone failed (ref=$REF)"
for file in arc_hub.py arc_admin.py arc_web.py arc_pairing.py web/index.html web/app.css web/app.js arc-hub.service install-vds.sh; do
  [[ -f "$TMP/repo/vds/$file" ]] || fail "downloaded repo missing vds/$file"
done
info "installing ARC Hub service"
( cd "$TMP/repo/vds" && bash ./install-vds.sh )
systemctl is-active --quiet arc-hub || fail "ARC Hub service did not start"
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8877/healthz >/dev/null ||
  fail "ARC Hub local health check failed"

info "configuring HTTPS for $DOMAIN"
config="$TMP/Caddyfile"
cat > "$config" <<EOF
# Managed by ARC Hub installer
# ARC Hub itself binds exclusively to 127.0.0.1:8877.
$DOMAIN {
    reverse_proxy 127.0.0.1:8877
}
EOF
caddy validate --config "$config" --adapter caddyfile >/dev/null ||
  fail "invalid Caddy config; original not modified"
install -d -m 0755 /etc/caddy
if [[ -e "$CADDYFILE" ]]; then
  cp -p "$CADDYFILE" "$CADDYFILE.backup-arc-$(date +%Y%m%d-%H%M%S)"
fi
install -m 0644 "$config" "$CADDYFILE"
systemctl enable --now caddy >/dev/null
systemctl reload caddy || fail "Caddy reload failed; check journalctl -u caddy"

printf '\n'
info "ARC_HUB_INSTALLED (service and local API healthy)"
info "public URL: https://$DOMAIN"
info "TLS requires correct DNS and inbound TCP ports 80 and 443"
info "owner credentials stored privately at /etc/arc/owner-credentials.json"
info "DO NOT publish credentials in GitHub, ChatGPT or terminal logs"
info "Shared ChatGPT sign-in requires OAuth/OIDC (not yet implemented)"
printf '\nCheck: curl -i -X POST https://%s/mcp  # expect 401 until authorized\n' "$DOMAIN"
