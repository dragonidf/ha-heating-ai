> DEPLOYMENT UNVERIFIED — 13.11.2026: 0.4.5 downloaded via Marketplace, HA Core restart requested, but HA MCP remains unavailable for several minutes. Before restart grid was not confirmed, voltage protection active and boiler off. Do not claim startup, panel or safety verification. Next: restore HA connectivity, check integration loaded, UI version, heartbeat, diagnostics, grid/voltage protection, and armed/mode; keep old writers disabled. GitHub Tests 43403845408 and release 43404381176 passed.

> RELEASE IN PROGRESS — 13.11.2026: user explicitly requested installation. Version 0.4.5 forecast-only UI merged; previous no-install instruction superseded. CI and Marketplace publication required before deployment. HA currently reports grid not confirmed: preserve voltage safety; verify current state before and after restart. Keep prior V1/V2 inactive and model/history intact.

> UI CHANGE READY — 13.11.2026: branch ui/forecast-only-home, code commit 493de3148070f28e43f950f3e4e1ebe63c32d471, GitHub Tests 43386341265 PASS. First-tab chart displays forecast only, no historical KWS/fireplace rows; warnings and extended horizon retained. Generated UI assets updated. Explicit user instruction: do not install the app. Production remains 0.4.4. Next: user decides when to merge, release and install; assign a new release version before publication.

> DEPLOYED 13.11.2026 09:14 Kyiv: 0.4.4 installed through Marketplace, native entry loaded, armed on, fresh plan 2bb59c0d80584b4b85254617f58106c5 has 3 heat segments, controller waits per plan, control_health healthy, control_error null. Plan calculation 37.8s in isolated process; power advice still pending. Thermostat confirmed heat mode with 20.5°C setpoint; this is not proof of physical heating. GitHub Tests 43382839157 and release 43383583747 passed. Old AppDaemon writer absent; V2 disabled. Model and history retained. Do not repeat migration. Check fresh live state before further action.

> CURRENT INCIDENT — 13.11.2026: native V1 temporarily DISABLED, boiler stockAUTO. Fix0.4.4 on latest main awaits CI/deployment. 0.4.3 spawn succeeds but replay rejects legitimate V1 blackout-preheat; do not treat it as completed repair. Latest work-log entry contains evidence/state/next steps. Diagnostic worker restored, File editor protected/stopped, all model/energy data retained. Do not repeat migration or re-enable old writers.

> INCIDENT IN PROGRESS — 13.11.2026 08:36 Kyiv. Native entry01M78Y3WSYWGH6RN1FME6587DP temporarily DISABLED, boiler confirmed stockAUTO, helpers automatic/armedON retained. Do not treat lower DEPLOYED banners as live. Core CPU fell~26% to~1% after native unload. v0.4.2 process isolation installed but live spawn lacked custom_components path; keep it disabled. Fix0.4.3 f2ad3e44bbb09bd84499ec9ffd88e8d666d7e77e adds child site initializer and submits process startup off HA loop. CI43379746735 pending. NEXT: wait fullCI/release, Marketplace0.4.3, restart Core, WAIT for actual return/outdoor temperatures before enabling entry (avoid5min invalid-input recompute pause), verify completed jobs/fresh forecast+heat segments/round-trip latency. Old AppDaemon cached writer cleared by necessary AppDaemon restart, absent from startup and reconnect; inactive YAML/modules/data retained. See latest work log, do not repeat data migration. Historical notification error count863 remains retained and is resolved only upon successful notification-dismiss recovery.

> HOTFIX DEPLOYED — 13.11.2026 07:59 Europe/Kyiv: native V1 0.4.1 installed via Marketplace; entry 01M78Y3WSYWGH6RN1FME6587DP loaded. GitHub Tests 43373239994 and release 43373784188 PASS, code 401dd2cb355250987c8b6b6f99db1a62bf9200ff. Notification echo now rejects missing/non-equipment entity targets; Recorder callbacks can use short HA-loop I/O closures. Original thermal modules unchanged.
> Live native heartbeat 07:58:42, control_health healthy, control_error null, armed on, actuation permitted on. Panel Опалення registered at /heating-ai-v1, asset 69b61be5e3e45937. Old fallback_command diagnostic is historical (count 863, last occurrence before update); do not clear audit history or treat it as a fresh exception.
> Core restart exposed AppDaemon bug: disable API fails AppConfig.update; cached old app restarted on HASS reconnect and native conflict safety removed its panel. Old app stopped again (terminated); original config retained as /app_configs/a0d7b954_appdaemon/apps/heating_climate.yaml.disabled, no active heating_climate.yaml. AppDaemon production_mode set false to restore config scanning. IMPORTANT: cached terminated object still exists in current AppDaemon process; verify no reactivation on next reconnect, do not claim reconnect-proof removal until cache is cleared. No shared AppDaemon restart performed.
> File editor enforce_basepath restored true and stopped. Models/history/energy retained. Do not repeat data migration. Continue with fresh live state if any conflict recurs.

> DEPLOYED — 13.11.2026: native V1 0.4.0 ACTIVE in HA, entry
> 01M78Y3WSYWGH6RN1FME6587DP. Start from latest main + LIVE state.
> Do NOT repeat migration or re-enable the old writer. Legacy V1 stopped and
> disabled, watchdog off, V2 disabled/not_loaded, all retained for owner cleanup.
> Model6/4032 samples and 55729 files (~384MB) preserved with checksums.
> Automatic mode/armed on; fresh native heartbeat, healthy diagnostics; panel
> Опалення at /heating-ai-v1. Full details, rollback and evidence limits in the
> final DEPLOYED work-log entry. Old utility_meter total-alias writer disabled
> to prevent competing energy updates; daily/monthly/yearly meters retained.
> Remaining follow-ups: Recorder write-rate/energy monotonicity and actual live
> panel rendering; no new physical relay test/full raw telemetry replay claimed.
> Everything below this banner is historical preparation, not the current state.

> АКТУАЛЬНА ТОЧКА — 13.11.2026: PR25 merged у main, commit
> 327b2ba0ac8ae65f170d34fb32a49fe48766cc31. Починати з latest main,
> не з робочої гілки. Нижчі згадки draft/старих CI — історичні.
> PR head 9ee7d096: Tests 43316983165 і candidate 43316983137 PASS,
> включно Python3.12/3.13, Chromium/WebKit/accessibility і HA2026.11 smoke.
> Наступне: перевірити push Tests на latest main та автоматичний immutable
> release v0.4.0 з heating_ai_v1_marketplace.zip; потім Marketplace install.
> HA ще НЕ оновлено. Новий контролер ставимо замість V2 SHADOW.
> Перед physical activation потрібні fresh private snapshot/model/energy/archive
> continuity і single-writer handoff. Старий V1/helpers НЕ видаляти, лише
> disable його writer/watchdog під час handoff; очищення власник робить окремо.
> Приватні дані лишаються у HA, shared AppDaemon/Sky Radar не зупиняти.

# Почати тут: модернізація V1 з рішеннями V2

Оновлено: 12.11.2026, Europe/Kyiv. Це точка передачі іншому GPT/Codex.
Повний контекст чату не потрібний. Не трактуйте зелені тести як готовий production.

## Мета та авторизація

Власник доручив завершити модернізацію **робочого V1**, перенести погоджені
кращі рішення V2, опублікувати в основному репозиторії та фактично встановити
через магазин із можливістю наступних оновлень. Повторно питати дозвіл на ці
дії не потрібно; підтвердження інструментів не обходити. Не чекати сім діб:
використати наявну реальну історію, потім контрольовану перевірку запуску.

Код V1: `dragonidf/ha-heating-ai`. База main:
`78af130c2f5c7cf3cece048dba19b0ca486833fb` (0.3.23).
Власник називає його також 0.2.3 / першою версією; не плутати з V2.
Донор: `dragonidf/ha-heating-2`, PR43, commit
`dc292e280ac492dcf4cf97d049688f2b40199cb5`.
V2 залишається SHADOW без фізичних команд.

## Де продовжити

1. Відкрити **draft PR25**: https://github.com/dragonidf/ha-heating-ai/pull/25 .
2. Отримати свіжий remote head гілки `feature/v1-marketplace-carrier-20261112`,
   а не починати від main. Точний checkpoint SHA див. у найновішому записі
   `v1-v2-work-log.md`; не покладатися на старий `f9e707...`.
3. Прочитати [ТЗ](v1-v2-modernization-spec.md),
   [реєстр](v1-v2-work-log.md), [матрицю перенесення](v2-transfer-checklist.md)
   та кореневий AGENTS.md. Не повторювати загальний аудит.
4. Взяти latest head PR25. Native production smoke, Python3.12/3.13 і
   candidate PASS на попередніх heads. Browser gate має stale selectors після
   нового −6/+24 timeline: виправлено card order, geometry, horizon title та
   strict selector future room line (now marker теж dashed). Відкрити поточний
   head workflows; брати наступний failure з job logs, не припускати PASS.
   Тести виконувати GitHub. E6/E7 PARTIAL; E8/E9 ще не виконано.
   Новий контролер ставимо замість V2; старий V1 та helpers залишити, лише
   disable writer/watchdog під час handoff. Подальше очищення робить власник.
5. Після кожного завершеного етапу оновити реєстр і цю точку продовження,
   закомітити та перевірити віддалений SHA. Локальний файл не є checkpoint.

## Фактичний стан, який не можна плутати з планом

- PR25 **draft, не merged, реліз 0.4.0 ще не встановлений**. E1–E5 мають
  DONE-CODE, E6/E7 лишаються PARTIAL; це не дозвіл на release/cutover.
- Остання підтверджена жива V1: AppDaemon 0.3.23, модель v6. Ці дані є
  історичним зрізом: перед діями перечитати актуальні стани HA.
- Чинний контролер не вимкнений; новий writer не активований.
- Підготовлені лише резервна копія YAML і **неактивний** Python exporter;
  YAML реєстрації exporter не створено. Не видавати це за міграцію.
- File editor повернуто `enforce_basepath=true` і зупинено.
- Неповний пакет **не merge/release/install** до закриття E1–E7.

## Найновіший code checkpoint

У робочій гілці реалізовані durable manual epoch/final gate, critical command
journal PREPARED/SENT/HA_ECHO/UNKNOWN з boot fence, KWS provenance і planner
timeout, admin-only settings API, fan manual-hold/final gate та новий перший
факт→прогноз timeline. Деталі й тести — у записі `E1–E6 implementation
checkpoint` в `v1-v2-work-log.md`.

Remote `97bf637...` містить три CI-fixture виправлення після code checkpoint:
native API smoke очікує три versioned WebSocket methods; production smoke
використовує справжній durable boot-fenced journal; browser smoke очікує новий
заголовок −6/+24 timeline. Candidate run 100 зелений. У Tests `make check test`
на попередньому exact head пройшов на Python 3.12/3.13; останній незакритий
збій описаний вище і є неповним synthetic service fixture, а не підставою
дозволяти bare `call_service("heat")`.

Read-only live evidence 12.11: AppDaemon V1 0.3.23 продовжує працювати, heartbeat
свіжий, automatic/armed, model v6; `sensor.heating_diagnostics` має
`storage_health=healthy` і `recorder_status=verified_same_state_attributes`.
У приватній saved-state qualification раніше підтверджено 17,342 energy bins,
16 cycles, 3,170 actual temperature points і byte-unchanged 48-point reference
parity. Live Recorder має щонайменше три опубліковані energy-cycle records.
Двогодинний Recorder baseline без сирих значень: heartbeat ≈240 записів/год,
controller ≈148/год, diagnostics ≈19/год, energy total ≈1/год. Це baseline для
порівняння після native cutover, не повний telemetry backtest і не доказ
фізичного ACK.

Після кожного перевіреного блоку: оновити work-log і цей файл, commit, push,
звірити remote SHA. Якщо робота переривається — зробити явний WIP checkpoint із
failing test/next action; не залишати єдиний стан лише в локальному diff.

## Продовження з іншого акаунта

Для коду достатньо доступу до GitHub. Для фактичного розгортання потрібне
власне авторизоване підключення нового акаунта до HA; GitHub не дає такого
доступу. Секретів, повного config, state/telemetry/Recorder у Git немає.
Якщо HA недоступний — завершувати код/CI, але не стверджувати, що прод оновлено.
Приватна історія має залишитися на HA; не залежати від тимчасового /tmp
попередньої сесії. Повторно завантажити оригінал доступним дозволеним способом,
не реконструювати його з обрізаних відповідей.

## Приватні дані та handoff

Оригінал у HA: `/app_configs/a0d7b954_appdaemon/heating_data`;
конфіг `/app_configs/a0d7b954_appdaemon/apps/heating_climate.yaml`,
app key `heating_climate_controller`. Спільний AppDaemon **не зупиняти**.

Залишені підготовчі файли (звірити наявність і вміст перед використанням):
- `apps/heating_climate.yaml.backup-20261112-native-4ce529c0` у тому ж AppDaemon root;
- `apps/native_v1_export_4ce529c0.py` — неактивний exporter;
- planned installation ID `4ce529c0e4db4855a07f573ced347aa5`.

Exporter бачить `/config/heating_data`; спільний `/homeassistant/heating_ai_v1/<id>`
AppDaemon відповідає Core `/config/heating_ai_v1/<id>`. Не додавати цей каталог
у Git. Перед cutover зробити **нову** копію актуального YAML/режимів/writer inventory.

Відомі writers для перевірки: legacy app;
`automation.klimat_rezerv_pri_zupintsi_kontrolera` (watchdog);
`automation.nichna_tsirkuliatsiia_povitria` (event або прямий fan);
`automation.heating_ai_avtomatichne_onovlennia_hacs` (було OFF, залишити OFF:
зупиняє весь AppDaemon). V2 не вмикати.

Порядок фінального переходу: перевірений реліз → install неактивним → зберегти
permissions → вимкнути лише старий heating app і watchdog → дочекатися припинення
heartbeat → приватна копія/хеші/continuity → активувати один native writer →
відновити погоджені mode/armed → свіжий цикл/HA echo/фактичні KWS/панель.
Rollback: спершу unload/revoke нового writer, потім відновити старий YAML і
permissions/watchdog; ніколи не запускати обидва. Для тривалого rollback окремо
звірити energy cursor, не перезаписувати нову історію старою копією.

## Застереження про архітектуру

Редакція 2 початкового ТЗ радила спершу окремий HA App/container і забороняла
повне переписування Core runtime на ранньому етапі. Поточний PR містить native
I/O bridge для незмінної математики V1; це **відхилення від початкового маршруту**,
а не вже доведена parity. Не приховувати його. До release довести збереження
повної поведінки, незалежний safety, один writer, rollback і доставку магазину;
якщо доказів немає, не називати кандидат готовим.
