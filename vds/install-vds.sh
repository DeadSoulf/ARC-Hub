#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" != 0 ]]; then
  echo 'Run as root using sudo on the intended Debian VDS' >&2
  exit 1
fi
command -v python3 >/dev/null || { echo 'Python3 is required' >&2; exit 1; }
[[ -f arc_hub.py && -f arc_admin.py && -f arc_web.py && -f web/index.html && -f arc-hub.service && -f arc-update.service && -f arc-update.path && -f arc-update-runner.sh && -f ../scripts/update-web.sh ]] || {
  echo 'Run inside the unpacked ARC VDS package directory' >&2; exit 1;
}
if ! id -u arc-hub >/dev/null 2>&1; then
  useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin arc-hub
fi
install -d -o root -g root -m 0755 /opt/arc
install -d -o arc-hub -g arc-hub -m 0700 /etc/arc
install -d -o arc-hub -g arc-hub -m 0700 /var/log/arc
install -d -o arc-hub -g arc-hub -m 0700 /var/lib/arc-hub/updates
install -d -o root -g root -m 0755 /usr/local/libexec
install -m 0644 arc_hub.py arc_admin.py arc_web.py /opt/arc/
install -d -o root -g root -m 0755 /opt/arc/web
install -m 0644 web/index.html web/app.css web/app.js /opt/arc/web/
install -m 0644 arc-hub.service /etc/systemd/system/arc-hub.service
install -m 0644 arc-update.path arc-update.service /etc/systemd/system/
install -m 0755 arc-update-runner.sh /usr/local/libexec/arc-update-runner
install -m 0755 ../scripts/update-web.sh /usr/local/libexec/arc-update-apply
if [[ ! -f /etc/arc/registry.json ]]; then
  runuser -u arc-hub -- python3 /opt/arc/arc_admin.py \
    --config /etc/arc/registry.json bootstrap \
    --owner-credentials /etc/arc/owner-credentials.json
fi
systemctl daemon-reload
systemctl enable --now arc-hub
systemctl enable --now arc-update.path
systemctl is-active --quiet arc-hub || { systemctl --no-pager status arc-hub; exit 1; }
systemctl --no-pager --full status arc-hub | sed -n '1,25p'
echo 'ARC_HUB_INSTALLED localhost:8877'
echo 'NEXT: configure public HTTPS domain in Caddy/Nginx with ACME certificate.'
echo 'Owner credential file: /etc/arc/owner-credentials.json (DO NOT SHARE)'
