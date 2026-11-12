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
4. Взяти latest head PR25. Виправлення synthetic temperature service вже є
   у aeb9228. Його native-production/Python PASS; browser gate мав застарілу
   вимогу chart+plan adjacency. Поточний checkpoint перевіряє chart першою та
   порядок chart < hero < plan < details. Дочекатися обох GitHub workflows;
   наступні failures брати з job logs. Повні тести виконувати GitHub за прямим
   дорученням власника. E6/E7 ще PARTIAL; не оголошувати production готовим.
   Встановлення — замість V2. Старий V1 та його сутності не видаляти: лише
   disable його writer/watchdog під час handoff, очищення власник зробить окремо.
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
