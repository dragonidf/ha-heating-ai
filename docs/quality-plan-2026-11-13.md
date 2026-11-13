# План стабілізації V1 після аудиту 0.4.7

Оновлено: 13.11.2026, Europe/Kyiv. Робочий журнал для продовження іншим GPT/Codex.

## Обмеження

- Не змінювати стан Home Assistant, Recorder або `.storage` з репозиторію.
- Виправлення не повинно вимагати повного перезапуску HA: зміни підхоплюються оновленням/перезавантаженням entry через магазин.
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
