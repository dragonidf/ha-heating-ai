# План стабілізації V1 і реліз Heating AI 0.5.0

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
| P0 | Bounded history completion | RELEASE CANDIDATE — v0.5.0, CI pending | Запит завершується даними або українською помилкою; запізнілий callback не змінює стан |

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

## Release candidate v0.5.0

- Гілка: `release/v0.5.0-history-ui` від `main`.
- `30aafc8078506ab056a1b6cfb9db679c07e718a4` — одноразовий bounded watchdog для history request; без polling.
- `42e56523edf88ae4d1b232fb97bbcbd73184e938` — regression для timeout і запізнілого callback.
- `13e45ce2ea4c8a2ecbe19642a182d054b40bd1f7` — зрозумілий loading/retry UI і компактні двоколонкові картки каміна/циркуляції.
- Фінальний релізний commit виправляє знайдений contract mismatch `publish_plan_history`, узгоджує 20-секундний серверний watchdog із 25-секундним UI deadline, запускає останній queued request після timeout і приймає результат лише для точного активного `request_id`.
- `VERSION`, обидва manifest, frontend VERSION, changelog, generated `heating_ui_assets.py` та дев’ять SHA-256 у `release.json` узгоджено на `0.5.0` через `python tools/build_ui.py`.
- До публікації всі Python/Node/browser/HA 2026.11 перевірки мають пройти лише у GitHub Actions. Локальні тести й Windows не використовуються.
- Під час підготовки Home Assistant працює лише як заборонена для запису live-система: install/reload/restart і будь-які зміни стану не виконуються.

## Acceptance після ручного встановлення

1. Створити й завантажити повний backup Home Assistant.
2. Встановити Heating AI `0.5.0` через HACS одним цілісним оновленням; не замінювати дев’ять модулів окремо.
3. Виконати один ручний restart Home Assistant, потрібний для очищення кешу Python custom integration.
4. Підтвердити `loaded`, свіжий heartbeat, єдиного writer, footer `Heating AI 0.5.0` і відсутність `pending_upgrade`.
5. Перевірити 24 год, 7 днів і власний період. Кожний запит має завершитися графіком або не пізніше 25 секунд показати українську помилку й можливість повтору; нескінченний loading неприйнятний.
6. Перевірити plan/fact, boiler/fireplace markers, zoom, retry і дві компактні картки каміна/циркуляції на телефоні.

## Backlog релізу 0.5.0

| Пріоритет | Дія | Статус / готово коли |
|---|---|---|
| P0 | Серверний timeout/error path | Реалізовано; очікується повний GitHub CI |
| P0 | Захист від late callback і queued retry | Реалізовано; exact request identity, без polling |
| P1 | Український timeout/retry UI | Реалізовано; UI deadline 25 с |
| P1 | Компактний mobile fireplace layout | Реалізовано; Chromium/WebKit regression у CI |
| P1 | Sidecar/index | Не потрібен для bounded-completion контракту 0.5.0; повернутися лише якщо live acceptance покаже систематичні timeout |
| P2 | Publish/Marketplace/release checkpoints | Очікуються після green CI, PR merge та автоматичного release workflow |
| P2 | Live acceptance | Єдиний незакритий пункт після публікації; виконує власник вручну після backup → HACS → restart |
