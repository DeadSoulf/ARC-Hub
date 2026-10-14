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

## Веб-интерфейс ARC Web

После установки ARC Hub панель будет доступна по адресу `https://YOUR-DOMAIN/web/`.
Для входа используйте имя пользователя и **его персональный токен ARC**.
Токен `owner` хранится на VDS в `/etc/arc/owner-credentials.json`; не отправляйте
его в чат или GitHub. Доступны устройства, диагностика, экран, файлы,
PowerShell по правам, управление пользователями и журнал действий.

Обновление **уже установленного VDS** одной командой (проверьте исходник
[update-web.sh](scripts/update-web.sh) перед запуском с sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/DeadSoulf/ARC-Hub/main/scripts/update-web.sh | sudo bash
```

Скрипт обновляет сервер, сохраняет резервную копию и настраивает Caddy.
Если Caddy обслуживает другие сайты, скрипт откажется его перезаписывать.
Веб-вход по личным токенам **не заменяет OAuth/OIDC для общего ChatGPT-плагина**.

## Обновление сервера и клиент одним EXE

В **ARC Web** владелец `owner` получает кнопку **«Обновить сервер»**.
Она ставит запрос в очередь. Привилегированная задача `systemd`,
установленная вместе с ARC Hub, запускает **только фиксированный скрипт**
`/usr/local/libexec/arc-update-apply`, берёт код из
`DeadSoulf/ARC-Hub`, запускает тесты и сохраняет резервную копию.
Веб-сервер не имеет прав `root` и не принимает произвольные команды
обновления. При перезапуске активные веб-сессии могут завершиться.

Windows-клиент доступен единым исполняемым файлом
[ARC-Agent-Setup.exe](https://github.com/DeadSoulf/ARC-Hub/releases/download/v0.3.0-preview/ARC-Agent-Setup.exe).
Файл собирает **GitHub Actions на Windows** после обновления ветки `main`;
пока задача сборки не завершилась, ссылка на релиз может возвращать 404.
Установщик не требует отдельно установленного Python; использует
`%LOCALAPPDATA%\ARC`, скрытый фоновой запуск и Task Scheduler.
Первая версия **не подписана цифровой подписью**, перед запуском
проверьте SHA-256 и доверенность источника.

## Подключение компьютера по одноразовому коду

Теперь **без SCP и JSON-файлов**:

1. Администратор `owner` открывает **ARC Web → Компьютеры → Подключить ПК** и получает код вида `ABCD-EFGH-JKLM`.
2. На Windows установите `ARC-Agent-Setup.exe`, нажмите **«Подключить по одноразовому коду»**.
3. Укажите `https://vpn.homeaicore.ru` и код, нажмите **«Подключить компьютер»**.
4. ARC Hub регистрирует новый Device ID, создаёт отдельный секрет устройства и выдаёт владельцу только `status` и `inspect`. Агент начинает связываться с сервером по HTTPS.

Код действует 10 минут, только на один новый ПК, после использования недействителен. Доступ к созданию кода есть только у `owner`. Регистрация не заменяет секреты уже зарегистрированных устройств. Для экрана, файлов, PowerShell и управления нужны отдельные разрешения владельца.

Старый способ `enroll.ps1` / JSON остаётся резервным. Код исчезает при перезапуске ARC Hub — в таком случае просто создайте новый. Код не является паролем владельца и его нельзя публиковать.

## Отображение хода обновления

Войдите в ARC Web под владельцем \`owner\` и нажмите
**«Обновить сервер»**. На странице появится панель с процентом и этапами:

1. Проверка конфигурации → загрузка GitHub → автоматические тесты;
2. резервная копия → установка → перезапуск ARC Hub;
3. проверка API и сайта → службы → завершение.

Прогресс обновляется каждые ~2 секунды через защищённый
\`GET /web/api/server\`; **пользователи без роли owner не видят статус**.
В браузер не передаётся исходный журнал системных команд и секреты —
только фиксированные названия этапов и общие сообщения об ошибках.

На время перезапуска ARC Hub соединение может пропасть и сессия закончится:
войдите ещё раз под \`owner\`, история текущего обновления останется в
\`/var/lib/arc-hub/updates/status.json\`.

При ошибке выводится красный статус и сообщение об откате.
За более подробными сведениями (только на VDS):

\`\`\`bash
sudo systemctl status arc-update.service --no-pager
sudo journalctl -u arc-update.service -n 60 --no-pager
sudo tail -n 60 /var/log/arc/update.log
\`\`\`

Чтобы установить индикатор на **уже работающем VDS**, выполните из
доверенной SSH-сессии проверенный установщик:

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/DeadSoulf/ARC-Hub/main/scripts/update-web.sh | sudo bash
\`\`\`

Скрипт делает резервную копию, проверяет новую службу, обновляет
systemd-контроль и UI. Старые токены/реестр пользователей сохраняются.

## Исправление HTTP 503 и обновлений (VDS)

Если при подключении компьютера появлялось `HTTP 503`, прежний
`arc-hub.service` имел `ProtectSystem=strict`, но не разрешал
создавать временный файл реестра в `/etc/arc`. Теперь доступна запись
**только** в `/etc/arc`, `/var/log/arc` и
`/var/lib/arc-hub/updates`. Общая защита файловой системы сохраняется.

Применить новую службу и веб-интерфейс на уже установленном VDS:

```bash
curl -fsSL https://raw.githubusercontent.com/DeadSoulf/ARC-Hub/main/scripts/update-web.sh | sudo bash
```

После обновления проверьте `sudo systemctl status arc-hub`.
Если обновление из веб-панели по-прежнему завершается ошибкой,
выполните на VDS (редактируйте или удаляйте секреты перед отправкой лога):

```bash
sudo systemctl status arc-update.path arc-update.service --no-pager
sudo journalctl -u arc-update.service -n 50 --no-pager
sudo tail -n 60 /var/log/arc/update.log
```

В пользовательском UI видны только этап и общее сообщение, но не
привилегированные журналы и токены.

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
