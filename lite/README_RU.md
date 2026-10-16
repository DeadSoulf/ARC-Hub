# ARC Lite — локальное управление Windows без серверов

## Архитектура

**ChatGPT Desktop (локальная сессия) / Codex CLI → локальный MCP-процесс через stdio → Windows.**

Без VDS, Docker, Caddy, браузерного HTTP, localhost HTTP, входящих портов, Secure MCP Tunnel, OAuth и отдельного сервера. `arc_lite.py` — один исполняемый Python-файл, запускаемый самим MCP-клиентом. **Никакой API-ключ для MCP не нужен.** Модель ChatGPT при этом по-прежнему работает в облаке OpenAI; если нужна работа без интернета вообще, это отдельная задача с локальной LLM.

**Веб-чат не может сам запустить локальный stdio-процесс.** Нужен ChatGPT Desktop с локальной сессией (или Codex CLI/IDE); конфигурация MCP у них общая. Документация: https://learn.chatgpt.com/docs/extend/mcp

## Что сделано на Asus

- Источники: `D:\AI\04_Workspaces\ARC-Lite-MVP` и `ARC-Hub/lite` в GitHub.
- Python 3.13 и Codex CLI уже установлены, ChatGPT Desktop запущен.
- Успешные локальные тесты `arc_lite.py --self-test`, 6 unit/stdio tests, `start.ps1 -Preflight -EnableApps`.
- `start.ps1 -EnableApps` зарегистрировал `arc_lite` через `codex mcp add`. `codex mcp get arc_lite` подтверждает `enabled: true`, `transport: stdio`, путь к Python и локальному агенту. Конфигурация сохранена в `~/.codex/config.toml` с резервной копией.
- Никакого туннеля, локального HTTP-сервера, автозапуска и вмешательства в ARC Hub/VDS не было.
- Полный вызов через UI ChatGPT Desktop надо подтвердить в **новой локальной сессии**. Отдельный локальный MCP-тест не доказывает видимость в текущем веб-чате.

## Проверка и установка

Обычный PowerShell на Asus, без администраторских прав:

```powershell
cd 'D:\AI\04_Workspaces\ARC-Lite-MVP'
python -m unittest discover -s . -p test_lite.py -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Preflight -EnableApps
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -EnableApps
codex mcp get arc_lite
```

Второй запуск идемпотентен. Для изменения прав после установки используйте `-Reinstall`, например без `-EnableApps` для режима только чтения.

## Подключение к ChatGPT Desktop

1. Перезапустите ChatGPT Desktop (после регистрации локального MCP).
2. Откройте **Settings → MCP servers** и проверьте `arc_lite` (тип **STDIO**, enabled), либо нажмите **Restart** в настройках сервера.
3. Создайте **новый локальный** чат, не облачный Work-thread и не веб-страницу.
4. Введите `/mcp` для просмотра доступных серверов.
5. Попросите: **«Вызови локальный инструмент arc_lite pc_status и покажи имя Asus»**.
6. Потом: **«Используй arc_lite open_notepad с новым request_id и открой Блокнот на этом компьютере»**.
7. Проверьте в Windows, что появилось окно, а в чате — ответ вызова инструмента.

Если сервер не отображается, сначала проверьте `codex mcp get arc_lite`, перезапустите настольный клиент, убедитесь, что выбрана локальная сессия и Python доступен по указанному абсолютному пути.

## Текущие инструменты

| MCP tool | Назначение |
|---|---|
| `pc_status` | статус и имя компьютера |
| `list_shared_files` | перечислить выделенную папку |
| `read_shared_text` | прочитать UTF-8 файл до 64 КиБ внутри выделенной папки |
| `open_notepad` | открыть Блокнот; только если установлен с `-EnableApps` |

Папка `%LOCALAPPDATA%\ARC-Lite\shared` предназначена для файлов, которые разрешено видеть агенту. За пределы разрешённого каталога переход запрещён, включая `..\` и ссылки.

## Ограничения и безопасность

По умолчанию запрещены PowerShell, произвольный запуск программ, снимки экрана, ввод мышью/клавиатурой, запись, удаление файлов. Разрешение `-EnableApps` добавляет только Блокнот. Вызов `open_notepad` принимает `request_id` для устранения повторов в текущем процессе.

Прежде чем добавлять остальные функции, надо провести проверку: ChatGPT Desktop → `pc_status` → `open_notepad` → перезапуск ChatGPT Desktop → повтор. **Не удаляйте действующий Desktop Commander**, пока эта цепочка не заработает.

Удаление только нашей регистрации, не файлов: `codex mcp remove arc_lite`.

Отдельное примечание: ранее существовавший локальный плагин `ai-remote-commander-local` находился в личном marketplace со статусом **not installed**; его файлы и настройки не менялись.
