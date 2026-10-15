# ARC Hub — карта проекта / журнал действий

**Обновлено:** 15 октября 2026. Эта карта фиксирует историю разработки и фактический статус компонентов ARC. Полная локальная хронология хранится на Asus в **D:\AI\05_Documentation\ARC-Hub-PROJECT-MAP-2026-10-15.md**, ссылка в общей карте **D:\AI\00_START_HERE.md**. В документацию не включены пользовательские, серверные или аппаратные секреты.

## Главное состояние

**ARC Hub 0.3.0 развёрнут на VDS, Windows ARC Agent на Asus работает, но интеграция ChatGPT не завершена.** Пользователь сообщил **local MCP failure**; авторизованные вызовы arc_devices и arc_call(open_notepad) через ChatGPT ещё не подтверждены. Не считать готовым к рабочему удалённому управлению через чат.

| Компонент | Проверенный результат |
| --- | --- |
| ARC Hub, vpn.homeaicore.ru | Health, OAuth protected-resource и authorization-server discovery отвечают 200 |
| ARC MCP | POST initialize сообщает arc 0.3.0; tools/list включает arc_devices, arc_permissions, arc_take_control, arc_release_control, arc_call |
| ARC Web | Доступен на https://vpn.homeaicore.ru/web/; новые элементы прав apps.launch и «Открыть Блокнот» присутствуют |
| Asus / ARC Agent | 127.0.0.1:8896/healthz возвращает ok; apps=true, input=false, shell=false, write=false |
| Частный ChatGPT plugin | arc-hub-chat 0.2.1 (OAuth/PKCE/DCR); подключение через ChatGPT не подтверждено |
| Резервный удалённый доступ | Desktop Commander сохранён, не удалять до успешного end-to-end теста |

## История выполненных действий

### Очистка Asus (14.10.2026)

- Инвентаризированы Asus и server через резервный Desktop Commander после неуспешного запроса к старому ARC и закрытой локальной MCP-сессии.
- Удалены AnyDesk 9.7.16, его служба, процессы и оставшиеся данные; повторная проверка подтвердила отсутствие компонентов.
- Удалён Microsoft Quick Assist. Windows RDP уже был отключён.
- Остановлены AI Remote Commander Fleet Agent/Gateway, удалены задания автозапуска и установленная Fleet-папка в LocalAppData. Проект **D:\AI\ARC-Fleet** сохранён. Исходники не были удалены.
- Сохранены текущий ARC Agent и Desktop Commander. Старый AI Remote Commander Interactive Launcher требует отдельной ревизии.
- Устранён конфликт локального порта 8896: остановлен осиротевший HTTP-процесс, основной ARC supervisor сам восстановил listener.
- В Asus agent.json включено только apps=true с локальной резервной копией; input/shell/write остались false.

### GitHub и выпуск ARC Hub 0.3 (14.10.2026)

- [PR #13](https://github.com/DeadSoulf/ARC-Hub/pull/13) объединена с main (merge c3da81b9f92044101032a185ee557c5cf11f2353). Включает OAuth 2.1 + PKCE S256, public DCR, раздельные разрешения, короткоживущие токены, MCP authorization/discovery, ARC Web и тесты.
- Новый ARC Web умеет по подтверждённому действию владельца разрешать apps.launch для конкретного устройства, а также выполнять тест «Открыть Блокнот» при наличии эксклюзивного сеанса.
- Тесты на Asus: **47 tests, OK (10 skipped)**. Проверки Linux GitHub Actions прошли. Дополнительная имитация проверяла цепочку авторизации, контрольную аренду и вызов open_notepad, но это НЕ подтверждение запуска на реальном устройстве через ChatGPT.
- Документация выпуска: [PRODUCTION-RUNBOOK.md](PRODUCTION-RUNBOOK.md).
- [PR #14](https://github.com/DeadSoulf/ARC-Hub/pull/14) объединена с main (merge 0ae845fe231754fdf85a6092994ad4cecefa9500). Содержит безопасное развёртывание OAuth с резервной копией, тестами и попыткой отката: [repair-oauth.sh](../scripts/repair-oauth.sh).
- Приватный плагин arc-hub-chat обновлён 0.1.0 → 0.2.0 → **0.2.1**, https://chatgpt.com/plugins/plugins_6acf4a675760819194996e484337b40a.
- Локальные исходники и тесты: **D:\AI\ARC**, **D:\AI\04_Workspaces\ARC-Hub-v0.3.0**, **D:\AI\04_Workspaces\ARC-OAuth-Test**.
- Локальная папка выпуска: **D:\AI\02_Releases\ARC-Hub\0.3.0**. В ней архив исходников, Windows Agent 0.2.0 и SHA256. **Её source ZIP был создан до PR #14 и должен быть пересобран из свежего main.**

### VDS recovery (14.10.2026)

- Прежний сервер возвращал 404 на OAuth endpoints. Доверенного SSH-доступа с Asus и server к VDS не было; в обход проверки ключа SSH не шли.
- Владелец выполнил с root-консоли Debian ams-1-vm-nplq проверенный SHA256 скрипт repair-oauth.sh по коммиту 0ae845fe231754fdf85a6092994ad4cecefa9500.
- Установщик завершился **ARC_OAUTH_REPAIR_OK** и сообщил, что учётные записи пользователей/устройств сохранены.
- VDS backup: **/var/backups/arc/oauth-repair.sxY1rlxy**; не удалять до принятого релиза.
- Проверено: /healthz 200; /.well-known/oauth-protected-resource 200; /.well-known/oauth-authorization-server 200; /web/ доступен. POST initialize сообщает **arc 0.3.0**, tools/list перечисляет 5 инструментов, unauthenticated tools/call выдаёт OAuth challenge. Public DCR и consent page отвечали 201/200 в тестах без персонального входа.

### MCP-плагин и остающийся сбой

- Пользователь сообщил **local MCP failure** уже после успешного обновления VDS.
- Сервер и Windows Agent повторно отвечают на health checks, однако реальный вызов из ChatGPT не прошёл.
- Проверка регистрации OAuth: стандартный ChatGPT callback /connector_platform_oauth_redirect был принят (201); тестовый ID-специфичный callback /connector/oauth/... отклонён (400). Это *возможная*, но не доказанная причина.
- Версия плагина 0.2.1 опубликована, однако read-back выявил, что поле type в mcp.json и .mcp.json по-прежнему **streamable-http**. Предположения о несовместимости типа пока не подтверждены успешным исправлением.
- Попытка обновить allowed OAuth callbacks GitHub была заблокирована механизмом безопасности, изменения не вошли в main. Не заявлять этот шаг выполненным.
- **Текущая диагностика 15.10.2026:** Hub /healthz и обе OAuth discovery — 200; /web/app.js — 200 с поддержкой новых кнопок; локальный Asus /healthz — ok; apps=true, input/shell/write=false; AnyDesk отсутствует; Fleet tasks отсутствуют.

## Открытые задачи (по важности)

1. **P0:** диагностировать и исправить *именно* local MCP failure в ChatGPT. Установить фактический redirect URI, OAuth-процесс и транспортные требования клиента; не менять callback allowlist вслепую.
2. **P0:** пройти персональный OAuth вход в ARC, получить список устройств через arc_devices и разрешения через arc_permissions *из ChatGPT*, без публикации токенов.
3. **P0:** проверить/выдать в ARC Web scope apps.launch конкретному пользователю и Asus. Локальное apps=true само по себе недостаточно.
4. **P0:** провести живой тест arc_take_control → arc_call(open_notepad, {}) → arc_release_control, проверить запущенное окно и запись аудита.
5. **P1:** пересобрать локальный исходный ZIP из коммита нового main; актуализировать контрольные суммы и документацию.
6. **P1:** только после устойчивого end-to-end теста и проверки после перезапуска рассмотреть удаление Desktop Commander и устаревшего Launcher.

## Принципы безопасности

Не сохранять owner/device токены, OAuth access/refresh токены или ключи в документации, чате, репозитории и диагностических логах. Не уничтожать registry, каталог исходников Fleet и резервные копии. Не расширять управление мышью, shell или файлами без отдельного явно выраженного разрешения. Не отключать Desktop Commander до подтверждённого альтернативного пути управления.

**Финальный вердикт на 15.10.2026:** инфраструктура ARC Hub восстановлена, продукт ещё не принят из-за ошибки интеграции ChatGPT.
