#!/usr/bin/env bash
# Root-only ARC Hub updater: fixed source/script, staged progress to a limited
# owner-visible JSON status. NEVER accept arbitrary shell commands from browser.
set -Eeuo pipefail
umask 077
REQUEST=/var/lib/arc-hub/updates/request
DIR=/var/lib/arc-hub/updates
LOG=/var/log/arc/update.log
[[ -f "$REQUEST" ]] || exit 0
rm -f -- "$REQUEST"
STARTED="$(date +%s)"

status() {
  local state="$1" step="$2" percent="$3" detail="$4" temp
  temp="$(mktemp "$DIR/.status.XXXXXXXX")"
  # Every argument is an allowlisted literal from this root-owned script.
  printf '{"state":"%s","step":"%s","percent":%s,"detail":"%s","started_unix":%s,"updated_unix":%s}\n' \
    "$state" "$step" "$percent" "$detail" "$STARTED" "$(date +%s)" >"$temp"
  chown arc-hub:arc-hub "$temp"
  chmod 0600 "$temp"
  mv -f -- "$temp" "$DIR/status.json"
}

# Stage markers come from a root-maintained script, but only a fixed list of
# exact values is accepted. No git logs, commands, API keys or paths are
# ever reflected to the browser.
process_stage() {
  case "$1" in
    prepare)  status running prepare 5 "Проверка сервера и конфигурации" ;;
    download) status running download 16 "Загрузка исходников ARC Hub из GitHub" ;;
    test)     status running test 30 "Автоматические тесты и проверка кода" ;;
    backup)   status running backup 44 "Создание резервной копии" ;;
    install)  status running install 57 "Установка файлов ARC Hub" ;;
    restart)  status running restart 68 "Перезапуск серверной службы" ;;
    health)   status running health 79 "Проверка доступности API и панели" ;;
    service)  status running service 89 "Обновление службы и прокси HTTPS" ;;
    rollback) status running rollback 92 "Ошибка; восстановление предыдущей версии" ;;
    complete) status running complete 97 "Завершение и проверка обновления" ;;
  esac
}

status running prepare 1 "Запуск обновления"
# The pipeline loop runs in a subshell; all status writes are atomic.
# Temporarily disable errexit to capture the updater's exit code safely.
set +e
/usr/local/libexec/arc-update-apply 2>&1 | while IFS= read -r line || [[ -n "$line" ]]; do
  printf '%s\n' "$line" >>"$LOG"
  case "$line" in
    ARC_PROGRESS:*) process_stage "${line#ARC_PROGRESS:}" ;;
  esac
done
code="${PIPESTATUS[0]}"
set -e
# On failure keep the last finite stage, so administrators know which
# operation failed without exposing unrestricted privileged log contents.
last_stage="failed"
if [[ -f "$DIR/status.json" ]]; then
  last_stage="$(sed -n 's/.*"step":"\([a-z]*\)".*/\1/p' "$DIR/status.json" | head -n 1)"
  case "$last_stage" in
    prepare|download|test|backup|install|restart|health|service|rollback|complete) ;;
    *) last_stage="failed" ;;
  esac
fi
if [[ "$code" -eq 0 ]]; then
  status success complete 100 "ARC Hub обновлён; проверка работоспособности пройдена"
  echo "ARC_UPDATE_SUCCESS"
else
  status failed "$last_stage" 100 "Ошибка обновления; попробовали восстановить предыдущую версию. Этап: $last_stage. Подробности в журнале VDS."
  echo "ARC_UPDATE_FAILED exit=$code" >&2
  exit "$code"
fi
