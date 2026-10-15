# ARC Lite — минимальный вариант управления Asus из ChatGPT

Статус: **локальный MVP на Windows проверен**, подключение из ChatGPT через Secure MCP Tunnel пока **не подтверждено**. Это самостоятельная ветка ARC-Hub: существующие ARC Hub на VDS, ARC Agent, Fleet, HOME AI Bridge и Desktop Commander не изменяются.

## Упрощённая схема

ChatGPT → OpenAI Secure MCP Tunnel → `tunnel-client.exe` на Asus → `arc_lite.py` (MCP по stdio) → Windows.

* **Не нужны:** VDS, Docker, Caddy, публичный порт, OAuth-провайдер, собственный веб-сервер, база данных, отдельный агент-воркер или входящие соединения на Windows.
* **Нужны:** Windows 10/11 с Python 3.11+; официальный проверенный `tunnel-client.exe`; приватный Platform Tunnel ID; Restricted Runtime API Key с доступом Tunnels Read + Use; разрешение ChatGPT создавать приватные MCP-плагины.
* **Важно:** туннель и пользовательский ChatGPT workspace должны быть связаны в Platform. Нельзя считать подключение состоявшимся по локальному self-test.

## Что уже готово на Asus

Исходники и PowerShell-запускатель находятся в `D:\AI\04_Workspaces\ARC-Lite-MVP`. На устройстве найден официальный `tunnel-client` 0.0.16. Вызовы `python arc_lite.py --self-test` с `--enable-apps` и без него, 6 unittest и запуск через `start.ps1 -Preflight -EnableApps` прошли.

В каталоге GitHub этот код: `lite/arc_lite.py`, `lite/start.ps1`, `lite/test_lite.py`.

## Проверка без ключей и сети

Откройте обычный PowerShell под вошедшим в Windows пользователем:

```powershell
cd 'D:\AI\04_Workspaces\ARC-Lite-MVP'
python -m unittest discover -s . -p test_lite.py -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Preflight -EnableApps
```

Ожидается `6 tests OK` и `ARC_LITE_PREFLIGHT_OK`. Ни одно внешнее приложение при таком preflight не запускается. В обычном режиме разрешены только `pc_status`, `list_shared_files`, `read_shared_text`; `-EnableApps` добавляет единственное действие `open_notepad`.

## Подключить ChatGPT без VDS

1. В [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels) создайте отдельный **приватный** туннель для Asus; добавьте целевой ChatGPT workspace. Используйте отдельный Restricted Runtime API Key с Tunnels Read + Use. **Ни один ключ не пересылайте в чат**.
2. В обычном PowerShell на Asus:

```powershell
cd 'D:\AI\04_Workspaces\ARC-Lite-MVP'
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -EnableApps
```

3. Введите только Tunnel ID, затем Runtime API Key в скрытом локальном поле. Скрипт создаёт профиль `arc-lite-asus` с локальным MCP stdio-командным запуском, исполняет `tunnel-client doctor --explain`, затем запускает туннель. Ключ передаётся дочерним процессам только через переменную окружения и не сохраняется в профиле или GitHub.
4. Когда `doctor` успешен и клиент запущен, откройте **ChatGPT → Plugins → + → Add custom MCP server → Connection: Tunnel** и укажите этот Tunnel ID. Создайте отдельный приватный плагин ARC Lite (старый ARC Hub OAuth-плагин не годится, потому что указывает на VDS).
5. В ChatGPT вызовите `pc_status`, затем `open_notepad` c уникальным `request_id`. Проверьте окно Блокнота на Asus и результат инструмента. Без успешного вызова **из ChatGPT** проект нельзя считать полноценно подключённым.
6. Остановка: Ctrl+C в терминале туннеля. Автозапуск сознательно не добавлен до живой проверки.

## Ограничения безопасности

* По умолчанию у агента **нет** PowerShell, мыши, клавиатуры, снимков экрана, записи или удаления файлов, произвольного запуска программ и доступа ко всему диску.
* Файлы можно читать только из `%LOCALAPPDATA%\ARC-Lite\shared`; путь проверяется после разрешения ссылок, чтение ограничено 64 КиБ.
* Опция `-EnableApps` разрешает только Notepad без аргументов. Повтор одного `request_id` не создаёт второй процесс в рамках текущей сессии агента.
* Токены не логируются, по stdio обрабатывается только MCP JSON-RPC, не открывается TCP-порт.
* Remote Desktop Commander не удалять, пока не пройдут: ChatGPT → pc_status → open_notepad → повтор после перезапуска.
* Используйте только доверенный официальный `tunnel-client.exe`; установленный локальный бинарник не обновляется автоматически.

## Официальная документация

- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)

Основная цель: сначала одно гарантированно рабочее действие из ChatGPT, и только затем расширение функций.
