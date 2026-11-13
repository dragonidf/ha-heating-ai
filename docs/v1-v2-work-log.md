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
| E7 | PARTIAL | Candidate exact-head run 43311679116 PASS; Linux unit/frontend matrix passed on prior head. Saved real-state reference parity: 17,342 bins, 16 cycles, 3,170 actual points, 48 replay points; live Recorder healthy/verified and 2h write baseline captured. Tests 43311679018 still fails synthetic production fixture: missing `climate.set_temperature` registration; not a production-code verdict. Full telemetry backtest and post-cutover comparison remain |
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

### 12.11.2026 — GitHub-plugin checkpoint and exact-head CI triage

GitHub is updated through the connected plugin. Remote functional head is
`97bf637d7b7fbaa43c61556fb3794596f4113218`; its tree was built from verified
local blob SHAs. Commits after the implementation checkpoint align HA 2026.11
smokes with the three-method versioned API, attach the real durable command
journal, update the browser's −6/+24 heading, and route recovered HEAT through
the controller final guard. Production HA was not changed.

Candidate run 43311679116 is PASS. Tests run 43311679018 is not green yet:
native-production job 131509066663 reaches the intended guarded
`apply_action("heat")`, then the synthetic HA raises `ServiceNotFound` because
`tools/test_production_ha2026.py` registers only `climate.set_hvac_mode` and not
the `climate.set_temperature` service used by the real V1 action sequence.
Next agent: add a synthetic temperature handler that updates the thermostat
target attribute, retain the final intent guard, push via GitHub plugin, and
wait for both exact-head workflows. Do not merge/release this failing head.

Live read-only E7 baseline: legacy V1 remains 0.3.23, armed/automatic with fresh
heartbeat and model v6; storage is healthy and Recorder reports
`verified_same_state_attributes`. Existing private qualification remains
byte-unchanged saved-reference parity (17,342 energy bins, 16 cycles, 3,170
actual points, 48 replay points), explicitly not a full telemetry backtest.
Recorder over two hours returned complete, non-paginated samples at about
240 heartbeat, 148 controller, 19 diagnostics and 1 energy-total writes/hour.
Raw telemetry, energy values and configuration were not committed.

### 12.11.2026 — resume: browser gate and deployment destination

Owner уточнив: новий V1 встановлюємо замість SHADOW V2; старий AppDaemon V1
і всі його сутності НЕ видаляти. Для handoff лише зупинити його writer/watchdog;
очищення власник виконає окремо після успіху. V2 спочатку unload/backup,
не активувати фізичні команди. Не видаляти сторонні helpers/історію.

На aeb9228 candidate 43312563470 PASS, native-production і Python 3.12/3.13
PASS. Tests 43312563506 FAIL на browser assertion старої adjacency chart+plan:
новий погоджений layout має chart першою, потім hero/energy, потім plan.
Замінено перевірку adjacency на строгий порядок chart(first) < hero < plan <
details; UI не змінено. Тести на пряме доручення власника передано GitHub;
не повторювати повний локальний suite. Next: перевірити обидва workflows нового
head, виправляти лише фактичні failures; потім E6/E7 і E8/E9. Production unchanged.

### 12.11.2026 — remaining browser contract alignment

Перевірено решту старих browser assertions: mobile geometry тепер перевіряє
chart перед comfort і plan після comfort та gap до попередньої картки ≤16px.
Очікувана назва 24h horizon відповідає новому −6/+24 timeline. Overflow,
font sizes, touch targets і height bounds збережені. Next: GitHub exact-head CI;
E6/E7 ще PARTIAL. Production unchanged.

### 12.11.2026 — browser series selector regression

CI 43315225498: native-production/Python PASS, browser FAIL strict selector:
shared timeline має два dashed paths (future room curve і now boundary).
Theme test помилково обирав обидва. Вибір звужено до stroke var(--history-room)
і dasharray; перевірка однакових кольорів збережена. Це test selector fix,
не послаблення control/voltage чи renderer. Наступний head CI — обов'язковий.
Owner target: install instead of V2; legacy V1 and entities retained for separate
owner cleanup. E6/E7 PARTIAL; E8/E9 not executed. No production writes.

### 13.11.2026 — browser history readout scope

Head 6db1f52: candidate 43316152056 PASS; Tests 43316152088 Python3.12/3.13,
native-production PASS; browser FAIL на strict selector .history-readout:
новий overview має два readouts. Keyboard/touch assertions тепер scoped до
.history-card (саме її .history-plot тест натискає), збережені котел/камін
assertions. UI/control code unchanged. Next: обидва workflows наступного head;
потім remaining E6/E7, release/store replacement of V2, single-writer handoff.
Legacy V1/helpers не видаляти. Production unchanged. Тести виконує GitHub.

### 13.11.2026 — all PR checks green, main merged; deployment pending

Functional PR head 9ee7d096ea051db7e176682f8b70618791d6b82c:
Tests 43316983165 і candidate 43316983137 SUCCESS. Chromium/WebKit,
accessibility/mobile, Python3.12/3.13, actual HA2026.11 native smoke пройдені.
PR25 merged squash у main 327b2ba0ac8ae65f170d34fb32a49fe48766cc31.
E8: merge DONE, release/install PENDING main push CI і release workflow.
E6: CI browser DONE; actual installed panel check pending. E7: preserved-state
parity available; full telemetry replay/fresh migration continuity не заявлені
PASS. E9 NOT STARTED. HA writes/cutover/V2 removal не виконані.
Live V1 0.3.23 model6 4032 samples, storage healthy; Recorder self-check
unverified (need retained identical-state decisions), не називати database fault.
Next GPT: latest main → main push Tests → v0.4.0 asset SHA → Marketplace install
inactive → private snapshot/continuity → disable V2 + old V1 writer/watchdog →
activate one native writer → readback/actual KWS/panel/rollback checks.
Не видаляти old V1/entities. Doc-only checkpoint не змінює tested code.

### 13.11.2026 — v0.4.0 installed through Marketplace, not activated

Immutable v0.4.0 targets tested main 327b2ba0ac8ae65f170d34fb32a49fe48766cc31;
asset heating_ai_v1_marketplace.zip 528718 bytes, SHA256
1dff999c294a3c129e3aa6e0caa9a8a3b8b19da6aaa0ce15714447a17c2ee704.
Owner confirmed previously reading/accepting Marketplace notice; backend explained
separate MCP-user gate and offered explicit acceptance. Dedicated tool schema
lacks accept_warning; official HA-MCP backend maps it to marketplace/warning/accept.
Used that documented one-shot command based on owner confirmation, readback true.
Added dragonidf/ha-heating-ai integration repo1421772152 and installed v0.4.0;
readback confirms installed_version v0.4.0. V2 v0.2.3 remains installed/loaded,
legacy V1 remains active. E8 release+package install DONE; runtime activation
NOT DONE. Next: load new Python integration (Core restart if required), prepare
fresh private migration snapshot/check model-energy-history continuity, unload
V2, disable only legacy heating writer/watchdog, activate native entry, verify
fresh cycle/KWS/UI/permissions. Preserve legacy files/helpers for owner cleanup.
Do not claim package install equals native production control. No equipment
commands or legacy disable have been issued in this install step.

### 13.11.2026 — DEPLOYED: native V1 0.4.0 replaces V2

Runtime entry 01M78Y3WSYWGH6RN1FME6587DP loaded, unique native_v1_controller.
Marketplace repo1421772152 installed immutable v0.4.0. sensor.heating_ui=0.4.0,
release_verified=true/runtime=native_v1, fresh native heartbeat across multiple
cycles, model6 and 4032 samples/coefficient values preserved. Automatic mode and
armed=on restored; healthy control/storage, Recorder verified_same_state_attributes.
Current decision waiting per plan; KWS measured ~2W/stage off. This is not a
new physical relay/HEAT test, and no synthetic voltage/grid was injected.

Private migration: installation4ce529c0e4db4855a07f573ced347aa5; 55729 files,
383883334 bytes copied and each source/destination checksum verified. Model6,
original-state and all telemetry/decision archive retained. State SHA256
50171b02f62db4b9d2571e924879ab85fbdb0f628c76c762b1bd12b0ad8c6e19;
manifest SHA256 0ef37a1c6ab6a6530fb887b2c1ece968e9be077ad49bdbe0c4669543558dee74.
Private rollback config/automation/permissions backup at Core
/config/heating-v1-handoff-20261113.json (File editor /homeassistant/...);
private native data Core /config/heating_ai_v1/<installation_id>.
Never upload private data. Full fresh telemetry numerical backtest is not claimed;
byte preservation plus earlier real saved-reference parity and CI are evidence.

Legacy heating YAML has disable:true and was explicitly stopped through the
AppDaemon documented admin/app/stop API: YAML edit alone did not stop its live
callbacks. Terminate logged and old heartbeat ceased before import/activation.
Shared AppDaemon/Sky Radar remain running. Legacy watchdog off, legacy updater
left off. V2 entry01M77EWFY07YYBD33TWCNGW3TZ disabled/not_loaded, retained.
No legacy files/helpers/entities were deleted.

Night fan automation1791057373005 now sends only heating_fan_night_request900:
its schedule and grid condition are byte-equivalent on readback. Removed its
parallel direct fan path so native manual hold/STOP owns physical arbitration.
Original automation config is in private rollback backup. Temporary exporter
heating_native_migration_once has disable:true and was explicitly stopped;
File editor enforce_basepath=true, stop succeeded, readback state error/not running.

Energy alias conflict found live: old utility_meter entry
01M4B9MAVHVBENEW37K7TGV8H7 also wrote sensor.kotel_spozhita_energiia from
sensor.heating_energy_total. It was disabled/unloaded (not removed/config edited).
Native still publishes both IDs; readback both633.5573401398849kWh with the same
native context. Daily/monthly/yearly utility meters remain enabled. Exact inverse:
re-enable this entry only after stopping native writer if rolling back. A complete
post-cutover Recorder-rate/statistics study is still a follow-up, not proven PASS.

Panel registered /heating-ai-v1, title Опалення, versioned module hash0b4bf6563865149d.
Browser/mobile/light-dark/accessibility CI PASS; actual registered panel confirmed
by get_panels. No screenshot of user's actual phone was obtained.

E8 release/store installation DONE. E9 core single-writer cutover DONE with the
above evidence limits. Next agent: start from latest main and LIVE state; do not
repeat migration/enable old writer. Owner will separately delete old V1/unneeded
entities. First follow-up: compare post-cutover Recorder write rates and energy
monotonicity over available history; inspect actual installed panel if needed.

### 13.11.2026 — 0.4.1 production traceback fix pending CI/deployment

Live native0.4.0 repeatedly reports AttributeError NoneType.lower. Confirmed
traceback: native_bridge.call_service invokes async_equipment_command_echo for
persistent_notification/dismiss without entity_id; HA StateMachine.get(None)
throws. Restrict echo lookup to climate/fan with a string entity target; retain
all equipment echo/ledger checks. Separate Recorder coroutine traceback calls
synchronous state closure on HA loop; _on_loop now executes that short closure
directly when already on the HA thread, avoiding a throw/deadlock.

Two focused production-method regressions FAIL before fix, PASS after. Original
eight executable V1 sources remain byte-identical; only native adapter/bridge,
release versions and regenerated frontend/version hashes changed. Version0.4.1.
User delegated full testing to GitHub. Next: main exact-head Tests/release,
Marketplace update repo1421772152 to v0.4.1, load new Python code via necessary
Core restart (not full AppDaemon), verify fresh successful cycles, error counts
stop increasing, actual control quality and temporary fallback clearance. Preserve
native private model/history and manual intent; do not blindly force HEAT.
Legacy V1/V2 stay disabled. Production has NOT received0.4.1 at this checkpoint.


## 2026-11-13 — 0.4.1 production hotfix and sidebar recovery

> HOTFIX DEPLOYED — 13.11.2026 07:59 Europe/Kyiv: native V1 0.4.1 installed via Marketplace; entry 01M78Y3WSYWGH6RN1FME6587DP loaded. GitHub Tests 43373239994 and release 43373784188 PASS, code 401dd2cb355250987c8b6b6f99db1a62bf9200ff. Notification echo now rejects missing/non-equipment entity targets; Recorder callbacks can use short HA-loop I/O closures. Original thermal modules unchanged.
> Live native heartbeat 07:58:42, control_health healthy, control_error null, armed on, actuation permitted on. Panel Опалення registered at /heating-ai-v1, asset 69b61be5e3e45937. Old fallback_command diagnostic is historical (count 863, last occurrence before update); do not clear audit history or treat it as a fresh exception.
> Core restart exposed AppDaemon bug: disable API fails AppConfig.update; cached old app restarted on HASS reconnect and native conflict safety removed its panel. Old app stopped again (terminated); original config retained as /app_configs/a0d7b954_appdaemon/apps/heating_climate.yaml.disabled, no active heating_climate.yaml. AppDaemon production_mode set false to restore config scanning. IMPORTANT: cached terminated object still exists in current AppDaemon process; verify no reactivation on next reconnect, do not claim reconnect-proof removal until cache is cleared. No shared AppDaemon restart performed.
> File editor enforce_basepath restored true and stopped. Models/history/energy retained. Do not repeat data migration. Continue with fresh live state if any conflict recurs.



## 2026-11-13 — 0.4.2 performance repair pending CI/deployment

A/B measured Core CPU25.98–27.22% with native controller, 1.52% after unload. Native entry temporarily disabled; boiler confirmed stock AUTO/target20.5, automatic/armed helpers retained. Pure numerical plan/advice now use2 spawn processes outside HA GIL; original V1 algorithms/command checks unchanged. Local spawn/error regression PASS. Private historical direct/process replay identical energy27kWh, minimum20.58C,56heat segments, runtime5.06/5.60s. NEXT: GitHubCI/release, Marketplace0.4.2, load new code, re-enable native; verify fresh accepted forecast/heat segments, CPU and sidebar. Old AppDaemon cache remains a reconnect risk (disabled file retained); fix lifecycle before restart, retain old data. No new physical relay test claimed.


## 2026-11-13 — 0.4.3 deployment import fix pending CI

0.4.2 installed after fullCI43378146216 PASS and release43378849066 PASS. Live spawn failed ModuleNotFoundError custom_components: HA package loader does not put /config on sys.path. Entry disabled again, thermostat stockAUTO; no fake measurements. Fix uses stdlib site.addsitedir initializer to expose component root inside child and offloads pool.submit startup outside HA loop. Regression now removes parent import shortcut; local tests PASS. Actual probe/outside readings returned25C/6.69C; do not override them. Old AppDaemon cached object cleared by one necessary app restart; absent from subsequent startup/reconnect, old files retained. 0.4.3 also resolves old notification incident active flag only upon successful dismissal; audit count retained. NEXT: fullGitHubCI, Marketplace0.4.3, Core restart, re-enable entry, verify numerical jobs complete/fresh heat plan/latency/CPU, stockAUTO replaced by native only after guarded command acceptance. Original V1 numerical modules unchanged.


## 2026-11-13 — 0.4.4 valid-preheat replay repair pending CI

0.4.3 fullCI43379746735 and real-HA spawn smoke PASS; released/installed v0.4.3 asset530205bytes sha25678263398e4354ffb0df7c1b6df1d976a6fec5a06a0917bb728161bef8c74c3b0. Live spawn works: plan34–40s, control tick0.2s. Identical-input replay still rejected valid results at heating_stability.py73: actual segment13Nov19:59:36–20:04:36, no outage overlap, basewindow23:00–07:00. V1 optimiser authorised blackout-preheat extensions; replay discarded them, causing endless recomputation. Temporary read-only diagnostic worker backed up/restored exactly; File editor enforce_basepathTRUE/stopped. Native entry temporarily DISABLED, thermostat stockAUTO, private data/model preserved. 0.4.4 carries exact optimiser-authorised intervals tied to identical published outage ranges into replay/reference; missing/stale approval or outage overlap still reject. Five new safety regressions and nine existing stability tests PASS locally. Optimiser numerical decisions unchanged. Advice deadline180s for3scenarios instead of60s; plan deadline unchanged. NEXT: fullCI/release, Marketplace0.4.4, restartCore, wait actual return/outdoor data BEFORE enable; verify fresh accepted forecast/heat plan, latency, settled job load and diagnostics. Old AppDaemon writer absent after cache-clearing restart, files retained inactive.
