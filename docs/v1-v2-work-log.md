# Реєстр робіт і наступні етапи

Контекст: [CONTINUE-HERE](CONTINUE-HERE.md). Оновлено 12.11.2026.
DONE-CODE ≠ DEPLOYED. Частково зроблене не відмічати виконаним.

## Зроблено до цього checkpoint

| ID | Статус | Дія / доказ |
|---|---|---|
| C1 | DONE | Відновлено погоджені 8 напрямів; donor PR43 SHA dc292e280ac492dcf4cf97d049688f2b40199cb5; матриця v2-transfer-checklist.md |
| C2 | DONE-CODE | Native V1 bridge, 9 оригінальних модулів із перевіркою зворотної byte parity, повна панель: native_bridge.py, production.py, tools/build_marketplace.py |
| C3 | DONE-CODE | Voltage trip/recovery/hysteresis + UI reason: native_voltage.py, production.py; test_native_v1_voltage.py, test_native_v1_production.py |
| C4 | DONE-CODE | Private migration, незмінний original/checksum, path/symlink/size захист, owner lease/conflict: production_migration.py, config_flow.py, production.py |
| C5 | DONE-CODE | Exporter без source overwrite та equipment commands: tools/native_migration_exporter.py; в HA лише неактивний Python, не запуск |
| C6 | DONE-CODE | Hash/version panel loader, immutable release ZIP pipeline і real HA 2026.11 smoke у CI |
| C7 | PASS-LIMITED | Offline saved-reference parity: model v6, 17,342 energy bins, 16 cycles, 3,170 actual points, 48-point reference identical; це не повний telemetry backtest |
| C8 | PASS | На функціональному f9e707fdd4e5579114c56bbfa04e56fe5c43d384 локально 593 Python +50 JS; CI Tests 43289117680 та candidate 43289117695 success; real HA job 131430327022 |
| C9 | DONE-PREP | Source YAML backup + inactive exporter staged; File editor basepath restored і stopped; старий V1 працює, cutover НЕ зроблено |

## Черга виконання (позначати після кожного етапу)

| ID | Статус | Конкретне завершення / перевірка |
|---|---|---|
| E0 | DONE-DOCS | ТЗ, цей реєстр і continuation збережені в робочій гілці PR25; PR body має явно відображати незакриті пункти; checkpoint commit містить лише документацію |
| E1 | DONE-CODE | Durable manual intent epoch + live memory-only final dispatch guard; stale plan rejected навіть після AUTO→назад; blocked-planner regression PASS. Не розгорнуто |
| E2 | DONE-CODE | PREPARED/SENT/HA_ECHO/UNKNOWN journal, boot fence/owner epoch, bounded confirmed retention, no blind HEAT retry, confirmed climate STOP reconciliation. Не розгорнуто |
| E3 | DONE-CODE | KWS value/report timestamps, provenance/unit/finite/online quality; zero/20s/hardware energy preserved; 60s bounded single-flight timeout drops stale result. Не розгорнуто |
| E4 | DONE-CODE | Versioned admin-only websocket settings read/save, CAS revision, durable receipt/readback; native runtime refuses raw settings event. Не розгорнуто |
| E5 | DONE-CODE | External fan change creates durable manual hold, drops ownership; explicit permission change clears it; live final fan gate rechecks device/epoch/STOP. Не розгорнуто |
| E6 | PARTIAL | Три вкладки Дім/Історія/Налаштування; перший спільний −6/+24 timeline із факт/прогноз/KWS/outage та touch/mouse/keyboard readout; manual AUTO distinct. 53 JS PASS; real browser/mobile/light/dark check after inactive install remains |
| E7 | PARTIAL | Targeted safety/model suites and production bundle tests PASS; local full discovery reached 571 tests but Windows sandbox denied Python temp writes (100 errors, one cascading assertion) and lacks jinja2/hypothesis. Exact-head Linux CI, historical replay/continuity and Recorder write-rate remain |
| E8 | TODO | Merge/release immutable 0.4.0 лише після E1–E7; перевірити artifact SHA, доставку магазину та install неактивним |
| E9 | TODO | Controlled single-writer handoff, originals/hashes/rollback, actual native cycle/panel/KWS; відновити permissions, cleanup exporter, документувати DEPLOYED |

## Як оновлювати журнал

Після етапу змінити рядок статусу, додати короткий append-only запис нижче:
дата; ID; functional SHA (документ може бути наступним commit); paths;
перевірки; CI; фактичні зміни HA; next. При перериванні зазначити failing test
і незакомічений diff, commit WIP у робочу гілку, не merge main. Не додавати секрети.

### 12.11.2026 — checkpoint E0

Створено самодостатні ТЗ/реєстр/continuation для іншого акаунта. Поточний
функціональний baseline f9e707fdd4e5579114c56bbfa04e56fe5c43d384, PR25 draft.
Немає заяви про готовність 8/8 або production. Next: E1. Для deployment іншому
акаунту потрібен окремий авторизований HA connection, не лише GitHub.

### 12.11.2026 — E1a, final-dispatch manual guard

Checkpoint E0: 7e5d8af7f66d93bceedb21dccc6612b5f2c51d0c, віддалений Git.
Дві регресії перед fix відтворили пропущений HEAT після ручного AUTO та
позитивну уставку після disarm. Додано live mode/armed read перед кожною
командою; manual rejection не відправляє OFF поверх вибраного AUTO, voltage
rejection зберігає emergency OFF. Original release lowering-target semantics
та explicit manual fallback збережені. Paths: production.py, native_bridge.py,
test_native_v1_production.py. `make check test`: 595 Python +50 JS PASS.
Production не змінено. E1 ще PARTIAL: продовжити
durable epoch та independent safety, не ставити DONE за цей окремий fix.

### 12.11.2026 — E1–E6 implementation checkpoint before exact-head CI

Завершено code-level safety blocks: durable manual intent epoch; crash-safe
critical command journal with boot fence and HA echo; voltage repeat timestamp
fix; KWS source-quality/provenance; planner single-flight timeout; admin-only
settings API; external fan manual hold and live final gate. Native runtime remains
inactive, live AppDaemon V1 was not stopped or modified.

UI now opens with one shared actual −6h → forecast +24h timeline and three
plain tabs. Timeline supports touch, mouse and keyboard inspection and separates
manual `Штатний auto` from controller error/staleness. Generated embedded UI and
release hashes were refreshed for 0.4.0.

Evidence: frontend 53/53 PASS; background-control 17/17, KWS 9/9, voltage 9/9,
production bundle 14/14 and critical-ledger crash/fence/STOP targeted scenarios
PASS in local targeted runners. Compile/UI/JS/manifest checks PASS. Full Windows
discovery is not a release verdict: Python-created temp directories become
unwritable in this host and optional jinja2/hypothesis are absent; it ran 571
tests with 100 permission/import errors and one dependent assertion. Next:
commit/push checkpoint, run exact-head Linux CI, fix any genuine CI regression,
then E7 continuity/replay and inactive Marketplace delivery. No production
cutover and no release tag yet.
