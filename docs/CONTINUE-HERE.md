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
2. Отримати свіжий head гілки `feature/v1-marketplace-carrier-20261112`, а не
   починати від main. Останній функціональний head перед цим документом:
   `f9e707fdd4e5579114c56bbfa04e56fe5c43d384`;
   tree `4d9a937328d44f9cd4f4a6799a5acef8d5bd1164`.
3. Прочитати [ТЗ](v1-v2-modernization-spec.md),
   [реєстр](v1-v2-work-log.md), [матрицю перенесення](v2-transfer-checklist.md)
   та кореневий AGENTS.md. Не повторювати загальний аудит.
4. **Наступний етап E1 (частково)**: final-dispatch live mode/armed guard вже
   має регресії; завершити durable intent/owner epoch guard і safety pass, який не
   чекає serial callback/диска/planner. Теплову математику не переписувати.
5. Після кожного завершеного етапу оновити реєстр і цю точку продовження,
   закомітити та перевірити віддалений SHA. Локальний файл не є checkpoint.

## Фактичний стан, який не можна плутати з планом

- PR25 **draft, не merged, реліз 0.4.0 ще не встановлений**.
- Остання підтверджена жива V1: AppDaemon 0.3.23, модель v6. Ці дані є
  історичним зрізом: перед діями перечитати актуальні стани HA.
- Чинний контролер не вимкнений; новий writer не активований.
- Підготовлені лише резервна копія YAML і **неактивний** Python exporter;
  YAML реєстрації exporter не створено. Не видавати це за міграцію.
- File editor повернуто `enforce_basepath=true` і зупинено.
- Неповний пакет **не merge/release/install** до закриття E1–E7.

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
