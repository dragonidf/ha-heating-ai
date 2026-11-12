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
| E1 | TODO — NEXT | Regression manual AUTO/STOP між setpoint/mode, durable manual intent/owner epoch; memory-only fast safety незалежно serial/disk; blocked planner scenario |
| E2 | TODO | Production command evidence PREPARED/SENT/HA_ECHO/UNKNOWN + crash reconciliation/fence, bounded journal, no blind HEAT retry |
| E3 | TODO | Source timestamps/provenance/finite/unit; preserved KWS zero/20s/energy tests; bounded single-flight/timeout stale drop |
| E4 | TODO | Versioned admin-only production settings API; CAS/readback і raw-event bypass rejection |
| E5 | TODO | External fan manual override/STOP + final fan gate; зберегти fireplace/night/backup policy |
| E6 | TODO | Дім/Історія/Налаштування, перший −6/+24 timeline, manual AUTO/error freshness; mobile/light/dark/browser checks |
| E7 | TODO | Повна матриця 8/8 з code/test evidence; реальна historical replay/format/energy/model continuity, Recorder write-rate; final make check test та exact-head CI |
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
