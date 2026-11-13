# Продовження роботи — реліз Heating AI v0.5.0

Дата контрольної точки: 13.11.2026, Europe/Kyiv.

## Що виконано

- Аудит встановленої V1 та репозиторіїв dragonidf/ha-heating-ai і dragonidf/ha-heating-2 завершено.
- P0 попереднього плану виконані в релізах v0.4.9–v0.4.11: fence recovery, health/heartbeat контракт, bounded power telemetry, людські повідомлення про проблеми.
- PR #36 об’єднав history/UI пакет і опублікований як v0.4.12.
- PR #38 об’єднав наступний малий history performance patch і опублікований як v0.4.13.
- v0.4.13 встановлено через Marketplace/HACS; після необхідного для оновлення custom integration повного restart запис heating_ai_legacy має стан loaded, HACS показує installed_version=v0.4.13, pending_upgrade=false.
- Нові telemetry records містять fire_phase і fire_active; history UI має людську легенду «котел / камін», підтримує 24 год / 7 днів / 30 днів / власні дати та не додає polling.
- History reader обмежений хвостом 16 MiB і працює у background executor; додано regression test для bounded reader та маркера каміна.
- GitHub Actions run 43503923842 і main після merge пройшли: Python 3.12, Python 3.13, native Home Assistant 2026.11, browser/responsive QA.

## Важливий фактичний результат live QA

Після встановлення v0.4.13 панель і контролер працюють, але на вкладці «Історія» після запиту 7 днів стан досі лишається «Читаємо архів температури…» без результату. Отже проблема повільного/порожнього графіка ще не доведена як повністю виправлена. Не закривати цей пункт лише за зеленим CI.

Поточний безпечний стан керування: controller завантажений; нагрів може бути заблокований штатним захистом при невідомому підтвердженні мережі. Це не змінювати під час history work.

## Що додано у v0.5.0

- Одноразовий server watchdog: 20 секунд, без polling і без нового фонового циклу.
- Валідна paged history відповідь із `error_code=history_timeout` та українським повідомленням, якщо background read не завершився.
- Точна перевірка `request_id`: callback старого або timed-out запиту не може замінити нові дані.
- Останній queued request запускається одразу після timeout попереднього.
- Панель припиняє loading через 25 секунд і показує retry; статус помилки має warning presentation.
- Картки температури каміна та циркуляції лишаються у двох компактних квадратних колонках на вузькому екрані.
- Regression охоплює реальну сигнатуру `publish_plan_history`, bounded timeout, late callback, queued request, 25-секундний deadline і Chromium/WebKit responsive layout.

Точні checkpoint commits до фінального release commit:

- `30aafc8078506ab056a1b6cfb9db679c07e718a4`
- `42e56523edf88ae4d1b232fb97bbcbd73184e938`
- `13e45ce2ea4c8a2ecbe19642a182d054b40bd1f7`

Home Assistant під час підготовки v0.5.0 не змінювався. Заборонені install/reload/restart і будь-який запис до live HA до готовності релізу.

## Залишилося до публікації

1. Запустити push CI гілки у GitHub Actions і виправити всі Python 3.12/3.13, HA 2026.11, Node, Chromium та WebKit failures.
2. Відкрити один PR у `main`; merge дозволений лише після повного green CI.
3. Дочекатися green main CI та автоматичного `release.yml`.
4. Перевірити immutable tag/release `v0.5.0` і asset `heating_ai_v1_marketplace.zip`.
5. Доповнити цей файл точними commit/PR/run/release URLs. Після цього release backlog закритий; live acceptance лишається за власником.

## Ручне встановлення після публікації

1. Home Assistant → **Налаштування → Система → Резервні копії** → створити повний backup. Дочекатися завершення й завантажити копію поза HA.
2. Переконатися, що поточний Heating AI стабільний, а в HACS немає іншого незавершеного оновлення.
3. HACS → **Integrations → Heating AI** → завантажити версію `0.5.0`. Не копіювати окремі Python-файли і не змінювати AppDaemon YAML.
4. Після завершення HACS виконати один ручний **Restart Home Assistant** через Налаштування → Система. Reload інтеграції не замінює restart для цього bundled custom integration.
5. Після запуску перевірити, що запис `heating_ai_legacy` має стан `loaded`, heartbeat свіжий, writer один, HACS показує `0.5.0` і `pending_upgrade=false`.
6. Оновити/перезавантажити сторінку браузера та перевірити footer `Heating AI 0.5.0`.
7. На вкладці **Історія** послідовно перевірити 24 год, 7 днів і власний діапазон. Очікуваний контракт: графік або українська помилка з повтором максимум за 25 секунд — ніколи нескінченний loading.
8. Перевірити повтор після помилки, zoom, plan/fact, маркери котла/каміна та двоколонкові картки каміна/циркуляції на телефоні.
9. Якщо integration не завантажується, не виконувати часткову заміну файлів: відновити backup або попередню HACS-версію й зробити restart.

## Правила продовження

- Усі тести запускати в GitHub Actions; локальні Windows-дії не потрібні без конкретної причини.
- Усі взаємопов’язані history/UI зміни об’єднані в один реліз `0.5.0`.
- Після кожного релізу оновлювати цей файл і docs/quality-plan-2026-11-13.md.
- Не вважати history work завершеним, поки live UI не покаже завершений графік, а не лише loading.
