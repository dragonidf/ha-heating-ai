# ТЗ: покращений V1, donor V2, оновлення через магазин

Редакція робочого ТЗ: 12.11.2026. Навігація: [точка продовження](CONTINUE-HERE.md),
[стан етапів](v1-v2-work-log.md), [8 груп донорських рішень](v2-transfer-checklist.md).
Пізніші прямі доручення власника дозволили повне виконання та production;
історичні обмеження «тільки аудит/етап A» більше не описують обсяг авторизації.

## Незмінні контракти

- V1 залишається основою: thermal model, планувальник, день/ніч, 30 хв ранкового
  прогріву, бюджет/тарифи, one-shot, outage queue 3.1, камін і циркуляція.
- Не переносити solver/shadow_plan або synthetic bindings V2. Не створювати
  паралельний облік енергії/другу thermal FSM. Зберегти helper/entity IDs.
- Не стирати модель, state, telemetry, decision_history, issued forecasts,
  coefficients, counters чи utility_meter; source bytes/checksums зберегти.
- Не змінювати Zigbee2MQTT, grid/TG логіку, Sky Radar, сторонні панелі/apps.
- Unknown/unavailable/stale не є нулем. HA echo не є фізичним ACK реле.

## Обов'язкові функції та приймання

### 1. Ручний намір, safety і команди

Ручний AUTO був свідомим вибором власника, не доведеним аварійним fallback.
Зберігати AUTO/STOP після нового плану, reconnect, повернення мережі, restart,
update та завершення старого worker. Модельний HEAT відновлюється лише за нової
явної дії користувача. Перевіряти актуальний намір/owner epoch **перед кожним**
equipment dispatch, у тому числі між set_temperature і set_hvac_mode.
Наявні manual/one-shot/fallback фізичні контракти не ламати.

Короткий memory-only safety pass незалежний від planner, AI, serial disk I/O.
STOP не чекає 60+с обчислення. Одне чинне завдання/type, bounded pending,
immutable snapshot/hash/revision, stale-result drop, timeout без неконтрольованих
повторних executor jobs. Виміряти latency на стенді; N100 live p99 не вигадувати.

Один command gateway; durable PREPARED/SENT/HA_ECHO/UNKNOWN, sequence/request ID,
boot/owner epoch, CAS/fence, reconcile незавершеного після crash. UNKNOWN не
дає права повторити HEAT. Журнал не є правом фізичного ownership. Бounded
storage/retention без втрати unresolved evidence і без частого великого save.

### 2. Напруга та джерела

Authoritative grid helper незалежний від cached voltage. Upstream phase values
не доводять наявність мережі. Не програмувати OEM/KWS thresholds.
Software trip: lower limit +6 V, upper limit −5 V, включно: для 190/260 В
вимкнення при ≤196/≥255 В. Повернення лише після всіх свіжих фаз 201–250 В
30 секунд; після кваліфікації діє гістерезис до реального trip. Зникнення
джерела/тривала прогалина скидає recovery. У UI причина, фаза та напруга.

KWS A/B/C=1/2/3, потужність — сума рівно трьох локальних каналів, energy —
один hardware total. Канонічний cumulative = legacy origin + hardware delta,
зберегти reset candidates, gaps та measured_since_switch. Continuity ≤1e-6 kWh.
Не інтегрувати вати вдруге. Зберегти 20с підтвердження стадії 3/6/9.
Відокремити value timestamp, HA report/liveness, unit/finite, online,
quality/provenance measured/last_known/unknown. Не вважати HA timestamp raw ACK.
`fallback_kws_zero/estimated` не виправляти без конкретної failing raw fixture.

### 3. Керування циркуляцією

Зберегти V1 fireplace phases/rising/burning/coasting, delta/гістерезис, demand
priority, нічний ліміт, verified backup та окремий fan gate. Ручний external
fan STOP/override не перезаписувати наступним tick. Фінальний guard безпосередньо
перед fan dispatch. Не спрощувати до одного температурного порога.

### 4. Налаштування та UI

Versioned authenticated API, admin для критичних змін, strict bounds/schema,
revision CAS, durable receipt/readback та rollback помилки. Не залишати обхід
через неавторизовану raw event mutation. Зберегти існуючі helper IDs.

Три вкладки **Дім / Історія / Налаштування**. Після header першим графік
−6 год факту → +24 год умовного прогнозу: room actual, ціль день/ніч,
оригінальний issued forecast тільки там, де є архів, future temperature,
heat/outage windows, фактичні KWS 3/6/9; touch/mouse inspect, gaps розривати.
Час Europe/Kyiv; mobile 375/768 та desktop, light/dark, без page horizontal scroll.
Діагностику сховати. Чітко відрізняти ручний AUTO, аварійний fallback,
спостереження, модельне керування, STOP; AUTO ≠ фізичний OFF. Старе error має
timestamp/freshness; не розслідувати NoneType без свіжого відтворення.
Content hash module URL + versioned custom element, не старий кеш JS.

### 5. Історія, магазин і фактичний запуск

Ідемпотентний migration dry-run, незмінні originals/checksums, parity з V1
model/energy/forecast archives. Використати реальну наявну історію, не чекати
7 діб і не називати синтетичний smoke історичним backtest.

Immutable реліз з installable ZIP тільки після CI точного SHA; не перевидавати
старий tag. Встановлення/наступне оновлення через чинний магазин HA. Приватні
дані поза managed code folder. Single-writer handoff та rollback за
[CONTINUE-HERE](CONTINUE-HERE.md); shared AppDaemon не перезапускати.
Перевірити реальний загружений код/version, новий heartbeat/cycle, model,
counter continuity, permissions, panel/cache, restart/update persistence,
Recorder/write rate. Service success, HA echo і KWS факт звітувати окремо.

## Докази завершення

Кожний етап: changed paths, tested commit SHA, сценарії/результат, CI URL,
production changed/unchanged, наступний крок. Статус DONE лише за доказами.
Перед commit виконувати вимоги AGENTS.md; поведінковий fix починати regression.
Не публікувати credentials, full site config, приватну telemetry чи Recorder.
