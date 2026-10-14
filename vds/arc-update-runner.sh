#!/usr/bin/env bash
# Invoked ONLY by root-owned arc-update.service, never by the web process.
set -Eeuo pipefail
umask 077
REQUEST=/var/lib/arc-hub/updates/request
DIR=/var/lib/arc-hub/updates
LOG=/var/log/arc/update.log
[[ -f "$REQUEST" ]] || exit 0
# Consume request atomically BEFORE updating; systemd.path re-arms afterwards.
rm -f -- "$REQUEST"
status() {
  local state="$1" detail="$2" temp
  temp="$(mktemp "$DIR/.status.XXXXXXXX")"
  printf '{"state":"%s","detail":"%s","updated_unix":%s}\n' \
    "$state" "$detail" "$(date +%s)" >"$temp"
  chown arc-hub:arc-hub "$temp"
  chmod 0600 "$temp"
  mv -f -- "$temp" "$DIR/status.json"
}
status running "Installing reviewed ARC Hub source from GitHub"
if /usr/local/libexec/arc-update-apply >>"$LOG" 2>&1; then
  status success "ARC Hub updated and health check passed"
  echo "ARC_UPDATE_SUCCESS"
else
  code=$?
  status failed "Update failed; inspect /var/log/arc/update.log; automatic rollback attempted"
  echo "ARC_UPDATE_FAILED exit=$code" >&2
  exit "$code"
fi
