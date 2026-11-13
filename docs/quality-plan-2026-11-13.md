# План стабілізації V1 після аудиту 0.4.7

Оновлено: 13.11.2026, Europe/Kyiv. Робочий журнал для продовження іншим GPT/Codex.

## Обмеження

- Не змінювати стан Home Assistant, Recorder або `.storage` з репозиторію.
- Робочі виправлення не повинні вимагати повного перезапуску HA. Виняток: після заміни файлів custom integration Marketplace може потребувати одного restart для очищення Python module cache; це не є способом роботи history callback.
- Не додавати новий polling або окремий фоновий цикл.
- V2 залишається shadow/offline і не стає другим writer фізичних команд.

## Статус

| Пріоритет | Робота | Статус | Критерій готовності |
|---|---|---|---|
| P0 | Відновлення ledger fence без reboot | DONE — v0.4.9, GitHub CI PASS | Один bounded STOP через наявний tick, HA echo відкриває fence; повторні ticks не дублюють команду |
| P0 | Контракт heartbeat/health | DONE — v0.4.11, GitHub CI PASS | Діагностика показує свіжість останнього успішного tick і стан writer |
| P0 | Зменшення Recorder flood `power_balance` | DONE — v0.4.11, GitHub CI PASS | Незмінна телеметрія не публікується частіше ніж раз на 60 с; матеріальні переходи публікуються одразу |
| P1 | Класифікація та rate-limit діагностики | TODO | Повторювані однакові помилки не створюють лавину записів |
| P1 | Single-flight/timeouts довгих planner jobs | DONE — попередня реалізація, regression CI PASS | Один актуальний розрахунок, застарілий результат не застосовується |
| P1 | Rate-limit повторного incident logging | DONE — попередня реалізація, regression CI PASS | Повторення одного інциденту агрегується |
| P1 | Явна семантика unknown для power | DONE — попередня реалізація, regression CI PASS | Невідомий вимір не стає нульовим/виміряним теплом |
| P1 | Розділення HVAC state та control permission в UI | DONE — v0.4.10, GitHub CI PASS | Фактичний HVAC стан і дозвіл команд показані окремо |
| P2 | Browser QA на `ha.dragonidf.pp.ua` | DONE — CI browser QA | Перевірка без введення секретів користувача |
| P2 | Single-writer exclusivity audit | TODO | Немає одночасних publisher V1/V2 |

## Журнал виконання

- 2026-11-13 — аудит live 0.4.7, обох репозиторіїв і історії PR завершено.
- 2026-11-13 — fence recovery реалізовано та випущено у v0.4.9 без polling і повного reboot.
- 2026-11-13 — v0.4.10 додав людськомовне розрізнення warning/error для проблем контролера.
- 2026-11-13 — PR #34 об’єднав два малих P0: heartbeat/writer health контракт і bounded power telemetry Recorder publications.
- 2026-11-13 — PR #35 об’єднав heartbeat/writer health і bounded power telemetry, пройшов GitHub CI та випустив v0.4.11; HACS встановив v0.4.11.
- 2026-11-13 — live entry reload виявив mixed-module setup_error через кеш Python після оновлення custom integration. Повний Core restart не виконано через обмеження безпеки; поточний запис не завантажений, heartbeat off. Це P0-блокер для наступного bundled fix: activation без reboot або явно погоджений одноразовий restart.

- 2026-11-13 — після ручного перезапуску власником v0.4.11 фактично завантажена: heartbeat свіжий, writer owned, нових setup-помилок немає; поточний plan safety-blocked лише через `grid_not_confirmed`.
- 2026-11-13 — live QA відтворив повільну історію: після 5 секунд сторінка лишалась у loading з 0/168 год. Причина — повний scan `telemetry.jsonl`; також старі записи не містили стан каміна.
- 2026-11-13 — підготовлено bundled v0.4.12: bounded telemetry tail, fireplace telemetry/markers, зрозуміліший loading/legend, тести виконуються GitHub.

## Контрольна точка після v0.4.13

- 2026-11-13 — PR #36 злитий, v0.4.12 встановлений через Marketplace; live entry reload після заміни файлів показав mixed-module setup_error, тому для активації нового custom integration виконано один необхідний повний restart HA.
- 2026-11-13 — після restart v0.4.12 завантажився без нових heating_ai помилок; HACS і entry були підтверджені.
- 2026-11-13 — PR #38 злитий, v0.4.13 опублікований і встановлений через Marketplace; main CI run 43504700916 та release run 43503923842 зелені.
- 2026-11-13 — v0.4.13 live entry має state loaded, HACS installed_version=v0.4.13, pending_upgrade=false. Панель показує 0.4.13.
- 2026-11-13 — live history QA після запуску 7-денного порівняння все ще показує «Читаємо архів температури…» без завершеного результату. Зменшення хвоста до 16 MiB зменшує верхню межу читання, але не доводить завершення callback. History пункт переведено в P0 IN PROGRESS.

## Оновлений backlog

| Пріоритет | Наступна дія | Готово коли |
|---|---|---|
| P0 | Знайти точку зависання history callback та додати timeout/error path | 7-денний запит завершується success або людською помилкою, без нескінченного loading |
| P0 | Підтвердити фактичний формат/шлях telemetry на live HA | bounded reader бачить потрібні записи й не сканує весь архів |
| P1 | Додати компактний time-index/sidecar або позиційний seek | 24 год і 7 днів відкриваються швидко при великому архіві |
| P1 | Покращити український UI помилки графіка та кнопку повтору | користувач розуміє проблему, стан і наступну дію |
| P1 | Rate-limit diagnostics та відокремити historical ledger warnings | повтори агреговані, поточна проблема видима |
| P2 | Перевірити single-writer V1/V2 і завершити live acceptance | немає другого publisher, усі періоди/маркери перевірені на HA |
