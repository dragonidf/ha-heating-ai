# Changelog

## 0.3.22 — 2026-11-11

- Add opt-in KWS phase measurement (Tuya Local) with unit/freshness checks, voltage-supported quiet zeros, existing nominal hysteresis and 20-second stage confirmation. Preserve unknown readings; never use the delayed aggregate power sensor.
- Anchor hardware-counter deltas to the existing accumulated total in state.json; preserve the anchor across restart, reject out-of-order/implausible readings and confirm resets without subtracting historical energy. Never integrate phase watts in parallel. Preserve interval bounds across repeated counter readings and split tariff allocations at hourly boundaries; allocations remain estimates.
- Activation: after updating through HACS, choose «KWS · прямий фазовий вимір і лічильник» in System settings → boiler accounting source. Keep the physical boiler profile unchanged. No production configuration was changed during preparation.


## 0.3.21 — 2026-11-03

- Batch command-loop reads into a thread-local Home Assistant snapshot to avoid repeated synchronous event-loop round trips while numerical workers are busy. Refresh the snapshot immediately before dispatch so grid, permissions and thermostat checks use current states; preserve native namespace/domain semantics and clear snapshots after success or failure.
- Keep same-frame synthetic publications visible to subsequent reads, including fallback status. Add regressions for cached reads, mutation isolation, worker isolation, fresh dispatch permissions and exception cleanup, plus per-stage and snapshot timing diagnostics for live verification.

## 0.3.20 — 2026-11-03

- Move thermal searches and 3/6/9 kW advice into managed AppDaemon executor jobs with immutable input snapshots, single-flight dispatch and coroutine completion. Keep measurements, energy accounting, thermostat/fan guards, heartbeat and current status in the 15-second command loop. Reject superseded results; replay accepted sessions against current measurements and absolute timestamps before publication or execution.
- Ignore a delayed watchdog/own-command thermostat event only when it predates the latest command and the actual thermostat still matches that command. Preserve detection of genuine manual changes. Fix repeated power-confirmation loss after controller delays without extending the bounded energy hold or filling unknown intervals; checkpoint state during background planning.
- Give fresh measured boiler power priority in the main card and boiler card. Distinguish observation from automatic execution, show an ongoing physical heating session instead of a contradictory future start, and preserve unknown/stale values as unavailable. Add background-job and command-loop timing diagnostics.
- Publish a saved boiler profile immediately, including after restart; use the persisted selection in the card, invalidate sessions from the previous profile, and mark its displayed forecast as pending until the replacement plan arrives. A profile selection describes the boiler and does not physically switch its TENs.

## 0.3.19 — 2026-11-02

- Keep the 24-hour thermal forecast and future heating plan available when electrical measurements disappear during an outage. Separate forecast inputs from execution eligibility: unknown power remains unknown in accounting and learning, and off/unknown grid still forbids heating commands.
- Continue the existing stored-heat observer when the return sensor disappears during grid interruption, explicitly mark the estimated return basis, and require valid room/outdoor temperatures and a usable stored estimate. Recompute on actual grid-state transitions so a restored supply cannot reuse a pre-outage command without reassessment.
- Explain grid loss before the generic missing-measurements warning. Add regressions for off/unknown supply, missing watts, missing return probe, safe recovery, a full real-planner curve, and narrow-screen forecast/plan visibility.
- Preserve compact history array positions through AppDaemon serialization: use a non-null cycle placeholder and an explicit null sentinel, keeping gaps distinct from zero. Reproduce the actual HA payload and verify both decoders after transport sanitization.

## 0.3.18 — 2026-11-02

- Fix history dispatch on AppDaemon installations where all worker threads are pinned. Use coroutine entry/result callbacks and offload blocking read/publication work with asyncio.to_thread, preserving the immutable history snapshot and request lock. No AppDaemon thread configuration or heating-control changes are required.
- Reproduce the v0.3.17 failure against the exact AppDaemon 4.5.13 dispatcher; verify the async path with no unpinned workers. Add regressions for event-loop responsiveness, result publication from the immutable snapshot, and visible worker exceptions.

## 0.3.17 — 2026-11-02

- Classify the fresh, aligned household-group balance as measured balance instead of nominal fallback. Preserve independent=false, the derived-balance basis, and explicit estimates for held standby or appliance-stage reserve. Count measured and estimated increments together without losing prior counters or reconciled energy; allow fresh group measurements through existing calibration and validation gates.
- Complete 24-hour temperature checks at a curve endpoint using explicitly labelled interpolation between nearby real reports. Never extrapolate a frozen forecast or fill missing measurements. Keep prospective checks across reloads and queue 36/48-hour checks when the original calculation covers them, independently of power and unpublished outage schedules. Show pending checks separately from completed evidence.
- Expose the existing calculation beyond 24 hours through a compact separate outlook sensor and a dashboard horizon switch. Keep the control plan and the default 24-hour view; no additional planner search or invented future sessions.
- Serve history requests outside the controller thread using immutable read snapshots and a dedicated request lock. Add an opt-in, lossless compact page encoding with the existing request/range/result checks and compatibility for older open panels. On the same 3,003 archived items, reduce transfer from 59 to 28 pages and about 395 to 205 kB; benchmark results are local, not a production network-latency claim.

## 0.3.16 — 2026-11-02

- Add two non-overlapping inlet balances: household meter groups with local/cloud aliases as the primary source, and individually metered appliances as the reserve. The reserve supports up to eight consumers, including dishwasher, washer and dryer. Never subtract appliances twice inside their parent groups or Deye LOAD/GRID. Preserve signed-grid, actual supply and per-phase TEN capacity checks.
- Label both methods as derived estimates. Keep the original power timestamps for explicitly held quiet standby with fresh same-device voltage; unavailable readings remain unknown. Debounce by stage rather than changing residual watts. Preserve the thermal model and exclude estimated energy from exact-power learning.
- Add audited, idempotent Recorder energy reconciliation with evidence hashes, baseline/overlap/range checks, a state backup and atomic persistence. Restore dated rolling/monthly/cycle totals separately from measured energy without rewriting initial plans, telemetry or frozen forecasts. Failed saves roll back the correction.
- Keep existing boiler power/mode/energy helper IDs; document migration of their energy source to the canonical controller total without importing the old integrator offset. Update source diagnostics and calculated-power captions.

## 0.3.15 — 2026-11-02

- Show missing actual energy as unavailable when an incomplete cycle has no accounted energy and unknown measurement time. Do not present an unmeasured night as 0.00 kWh in current or completed cycle cards. Preserve genuine complete zero-consumption cycles and calculated nominal energy. Add a frontend regression; no controller or accounting algorithm change.

## 0.3.14 — 2026-11-02

- Keep a labelled control prior from fresh inlet total and signed GRID when quiet/stale input-phase watts prevent nominal accounting. Phase wattage and all consumer readings still gate accounted energy; actual grid voltage/permission guards stay authoritative. Missing or unverified signed GRID and contradictory totals remain unsafe. Reproduce the live post-heating quiet-phase case without generating watts or kWh.
- Prefer a time-aligned cloud alias over an asynchronous local value. Preserve phase zero and signed boolean flags through AppDaemon settings transport so the source configuration can be edited and saved again. Add live-fixture and adapter regressions.

## 0.3.13 — 2026-11-02

- Collect the inlet/return-temperature validation window for native thermostat AUTO demand as well as controller-owned heating. A validated physical AUTO profile can now flag a sustained electrical/model contradiction during native operation; provisional profiles remain explicitly provisional. Add an adapter regression without controller ownership. Preserve separate automatic/native/manual nominal subtotals in history and label nominal energy deviations as approximate, rather than measured lower bounds. Reject material GRID export while inlet export-sign semantics are unverified, preventing absolute phase readings from creating a false TEN stage.

## 0.3.12 — 2026-11-02

- Add inlet-based nominal boiler accounting independent of Faza 1/2/3. Validate active-power units, real report ages, alignment and aggregate/phase consistency. Subtract each configured physical consumer once, using fresh local/cloud aliases as alternatives; keep signed Deye GRID separate from LOAD/battery and reject conflicting aliases.
- Require a thermostat heat request or a setpoint gap of at least 0.5 °C. Match only the selected fixed 3/6/9 kW stage (or a genuine pause); AUTO identifies electrical stages and cross-checks a continuous return-temperature window against a validated profile. Uncalibrated modulation stays explicitly provisional.
- Enumerate unknown consumer phase allocations without assuming Faza names identify input phases. Reject more than one nominal 3 kW TEN per phase, unrelated residuals, stale/missing consumers and contradictory readings. A conservative control reserve can retain planning with fresh core data, but never creates accounted heat or energy.
- Persist a separate nominal-energy counter seeded once from the existing total. Preserve legacy entities/history, classify all nominal kWh as calculated, bound sample holds and omit restart gaps. Keep calculated cycle spend separate from measured balance energy, subtract it from the soft remaining plan and exclude it from coefficient/modulation learning and independent accuracy claims.
- Show calculated heating/energy explicitly in the panel; expose the active method and validated source configuration under expert settings. Preserve permissions, grid checks, thermostat command verification, morning request credit, native fallback and rollback-compatible equipment persistence.

## 0.3.11 — 2026-11-02

- Add an opt-in control reserve for one unavailable load-group meter in the documented total/three-groups/inverter-GRID balance. Keep healthy groups and signed GRID measurements; leave the unmetered remainder as an interval, not measured boiler watts. No physical A/B/C mapping or double subtraction of appliances is assumed.
- Continue an explicitly approximate plan with a configured fixed 3/6/9 kW profile and thermostat request. Actual watts, energy and heating markers remain unknown; these assumptions do not train the model, invent historical energy or add unobserved heat to the floor inventory. Preserve grid, permission, sensor, outage and upper-temperature guards; use the hotter return-water proxy for the reserve coast safety check.
- Keep estimated morning request minutes separate from physically confirmed heating and prevent repeated minimum sessions solely due to missing metering. Label a successful reserve response as estimated, retain the native fallback on missing core sources, two failed groups, stale/contradictory data, unknown profile or failed thermostat commands.
- Add regressions for each failed group, stale/skewed sources, W/kW and signed GRID, 3 kW household loads, unchanged physical safety vetoes, floor inventory, response confidence and morning completion. Show the reserve explicitly without presenting assumed power as confirmed heating.

## 0.3.10 — 2026-11-01

- Size the default private journal budget from observed daily archive volume, the existing retention window, two extra days and 25% headroom. Expand only with verified free disk space, in 64 MiB steps, up to 1 GiB and at most 10% of space above the disk reserve. Explicit custom budgets remain fixed.
- Reserve at least 2 GiB / 10% of the disk for other applications; refuse new diagnostic writes when that reserve is reached. Preserve existing lossless compaction, snapshot checksums, file format, retention and protected history; no data migration or reset. Recompute capacity safely after restart and expose its basis/ceiling in diagnostics.
- Add regressions for retention headroom, restart, fixed limits, low/unknown free space and the hard ceiling. This storage change does not modify thermal planning or allow unknown power to become measured.

## 0.3.9 — 2026-11-01

- Show measured archive-budget usage separately from free disk space in model quality diagnostics; preserve protected history and the existing retention policy.
- Separate audited grid presence from local load-meter availability. A live production check found all three input voltages and Deye confirming supply, while stale/unavailable local branch meters forced grid `unknown`. The audited policy now requires all three fresh input-voltage readings plus the existing Deye/connection guards. Missing/stale/low input voltage, incomplete phase bindings and actual grid loss remain unsafe; the legacy policy retains its prior behavior.
- Keep power-balance faults unknown: this correction does not substitute missing load measurements, certify boiler watts or invent consumed energy. Add red regressions for the false unknown, stale input with fresh local meters, missing phase bindings and unchanged outage/inverter/legacy guards. No Deye, Zigbee2MQTT, source entities or other application settings are changed.

## 0.3.8 — 2026-11-01

- Preserve immutable advance temperature forecasts independently of physical boiler starts. Select the latest original issue at least 12 hours before the checkpoint, disclose its real lead, and retain actual observations beyond the forecast tail through the next local window, including DST. Add prospective 1/3/6/12/24-hour checks using real sensor-report timestamps and persisted original curves.
- Replace oversized history attributes with authenticated, read-only, range/cursor pages; retain compact summaries and receipts in HA. Generate the static expert-settings schema with the frontend. Compact the current forecast without changing private planning inputs or archives: no repeated per-point sessions, session-average display power, one current observation and full technical evidence in the private journal.
- Keep the immutable initial cycle energy reference even when some measurements are unknown; subtract known spend before planning the remainder. Record plan increases, projected comfort changes and completed-cycle quality; distinguish partial fact from savings and preserve safety above the soft budget. Add observed execution timing/duration and source/coverage diagnostics without resetting counters or coefficients.
- Derive room-age warnings and rejection limits from observed reporting cadence within the owner's maximum; validate thermostat fallback age and bias. Reject a prepared heat command if its temperature source disappears before dispatch. Report audited source freshness from the same readings used for control, retain contradictory measurements, and distinguish a grid-confirmed inferred zero from an independent meter reading.
- Keep unhealthy/stale Svitlo data and unknown tomorrow coverage conditional. The old missing-schedule preference remains loadable but cannot certify an unhealthy source as normal; healthy, fresh no-restrictions mode remains available. Actual grid state, outage parsing and emergency protections stay authoritative.
- Simplify the overview around comfort, next heat, same-cycle initial plan/fact and the 24-hour forecast. Keep source details collapsed; put the forecast comparison first on Model/AI, with zoom, comfort/events, cycle details and separate analytical reports. Group basic/additional/expert settings, add staged Undo and transactional save rollback, and support HA themes, 320px screens, 44px targets and 200% text.
- Expand regression, generated, pairwise and two-client browser coverage. Full release evidence and physical limitations: [QA audit](docs/qa-audit-2026-11-01.md). Synthetic tests and offline counterfactuals do not establish real energy savings; verify new completed cycles separately.

## 0.3.7 — 2026-10-31

- Retry the normal full plan on the next controller tick when all required inputs recover from a missing-input temporary-auto fallback. Avoid an unnecessary five-minute native-auto hold after a probe/meter comes back. Healthy ticks retain the normal interval.
- Explain which inputs caused a new fallback. Preserve legacy fallback state, command/control-failure retry deadlines, manual pauses, actual-grid checks, measurement freshness/skew/confirmation, temperature limits and switching protections. Do not issue hardware commands from the recovery tracker.
- Add regressions for the real unknown-probe recovery path, partial recovery, asynchronous 63-second power reports and protected command/manual/grid failures. Model coefficients and energy history remain intact.

## 0.3.6 — 2026-10-31

- Recover the temperature comparison after a missed HA state push, panel detach/reconnect, WebView resume or network restoration. Read the completed result through a bounded REST fallback, accept matching shared-entity date ranges and redraw even when HA reuses its state object.
- Pin comparison dates while a request is in flight so a five-minute clock boundary cannot discard the response. Refresh rolling dates only when starting another request. Cancel pending timers on detach and retain valid comparison data.
- Add regression/browser checks with deliberately suppressed state pushes and an interrupted load; verify initial loading and recovery without manually injecting a hass acknowledgement. Heating control, model coefficients, counters and private history remain unchanged.

## 0.3.5 — 2026-10-31

- Preserve the frozen temperature forecast through the post-heating coast when an accounting window changes while the boiler is still heating. A continuous measured ON state is not another physical start, an unconfirmed later start cannot truncate an existing original forecast, and the heating tail cannot occupy tomorrow's forecast slot. Add backend/frontend regressions for the live07:00 boundary case; control and energy accounting remain unchanged.

## 0.3.4 — 2026-10-31

- Replace cycle-only temperature comparison with a calendar chart: actual and immutable advance forecasts on one timeline, default last seven days, 24-hour/30-day presets and custom dates in the HA timezone. Add touch/keyboard inspection and explicit forecast coverage/gaps.
- Read original, checksum-verified issued forecasts from the private decision archive and actual temperatures from telemetry, including evidence collected before 0.3.3. Never rerun the model to fill history, substitute a later forecast, or infer a physical heating start across a measurement gap. Preserve existing frozen comparisons and restart persistence.
- Load history through the AppDaemon executor without blocking heating ticks or changing plans, settings, model coefficients or energy counters. Retain the legacy cycle event contract.
- Fix delegated comparison-button clicks. Verify real range changes, custom dates across DST, touch controls, missing forecasts and observation gaps in regression/browser tests.

## 0.3.3 — 2026-10-31

- Preserve the energy-derived floor inventory during heating; a cooler return pipe no longer erases delivered heat. Bound counter deltas and do not invent heating across unobserved gaps. Model coefficients and canonical energy history remain intact.
- Execute future plan segments between full calculations and recheck their expiry at dispatch. Coalesce power callbacks and ignore unchanged outage metadata to reduce queued work. Actual grid, temperature limits, manual ownership and minimum on/off protections remain authoritative.
- Persist initial, current and observed cycle energy separately; subtract spent energy from the planning reference and prefer staying within it among equally safe candidates. Preserve legacy baselines, expose unknown intervals, counter resets and concrete deviation evidence.
- Record prospective temperature forecasts and freeze the one issued 12 hours before a confirmed heating start. Preserve a month of cycle comparisons through restart, expose period selection, break missing observations and explicitly mark absent advance data without reconstructing it.
- Simplify the overview, remove duplicate power advice and technical search text, consolidate warnings and show a compact plan/fact comparison. Put detailed execution and metering on the AI tab. Deficit messages use the same curve/limits and state their time, depth and relation to the visible horizon.
- Keep save acknowledgements through restart, reconcile persisted matching settings after a missed acknowledgement, allow bounded retries and retain visible success feedback. Disk failures do not report success. Add regression, incident replay and responsive browser checks. Physical consumption accuracy remains pending the next completed site cycle.

## 0.3.2 — 2026-10-30

- Save equipment changes without validating untouched, hidden model coefficients against HTML step increments. Keep the request pending through long controller computations so a delayed acknowledgement clears the saved draft.
- Read a bounded, batched HA power snapshot with real report timestamps instead of relying on AppDaemon's state-changed cache for unchanged meter readings. Preserve freshness, unit, skew, fault and actual-grid checks; failed reads remain unknown, not zero.
- Suppress duplicate diagnostic decisions caused solely by measurement timestamps and running energy-ledger updates. Losslessly compress closed-day diagnostic logs under disk pressure, verify their checksums and preserve protected evidence and retention times. Expose the diagnostic budget and persistent storage error.
- Add regressions for saving 6 kW with learned coefficients, delayed acknowledgement, fresh unchanged reports, stale reports and API failure, duplicate decisions, journal pressure and interrupted compression. Keep physical boiler modes, model coefficients, energy counters and telemetry unchanged by the hotfix.
- Pin the browser-test runner to the supported Ubuntu image and declare timezone data explicitly for reproducible schedule checks.

## 0.3.1 — 2026-10-30

- Show boiler advice on the comfort hero and boiler card, including a labelled best alternative when none can fully meet comfort. Keep conditional advice separate from a sufficient plan.
- Replace typed ventilation bindings with existing HA relay/fan choices and friendly names. Group settings by control, comfort, boiler, heating hours, circulation and ventilation, with critical controls first.
- Add an explicit persisted forecast policy for missing schedules: assume available grid without altering actual grid guards or emergency signals; published outage intervals remain authoritative. Remove generic missing/partial schedule notices from the overview.
- Keep capacity-advice caching through unchanged schedule refreshes; real interval/status changes still invalidate it. Publish a compact signature rather than the full slot tuple.
- Preserve explicit zero and false values in the new equipment/ventilation sensor attributes across AppDaemon transport. Zero measured/estimated energy and zero balance watts must remain distinguishable from unavailable data in the panel and Recorder.
- Encode only the published attributes; canonical energy counters, control, forecasts and saved history remain unchanged. Add a regression reproducing transport filtering and checking that counters are not mutated.

## 0.3.0 — 2026-10-30

- Describe the physical 3/6/9 kW or AUTO boiler mode in revisioned persistent settings, separately from HVAC and measured watts. Selecting a mode never sends a boiler command; old installations remain explicitly unconfirmed until selected.
- Extend return-band power learning with mode/configuration separation, completed-session holdouts, instantaneous power-band distributions and thermal non-regression gates. Compare three like-for-like capacity plans; distinguish conditional nominal advice, search failure and an optimistic model insufficiency bound.
- Add audited A/B residual measurement with per-input freshness, time alignment, units, duplicate checks and explicit phase-map verification. In audited mode unknown stays unknown; no phase-count or thermostat-only fallback. Preserve low-voltage heating and unrequested consumption.
- Keep a restart-safe measured/estimated/unknown W×seconds quality ledger without rewriting existing energy totals or telemetry. Back up state, telemetry and allowlisted effective configuration before the idempotent equipment migration.
- Observe recuperator/bathroom relays without controlling them. Fit joint base/ventilation losses only with identifiable independent days and held-out improvement; retain the existing thermal model while evidence is insufficient.
- Keep the compact three-tab panel; add physical-mode advice, quality/energy diagnostics and validated equipment settings. Preserve strict hours, grid guards, manual ownership, watchdog and HACS workflow, including unavailable-forecast release verification.
- Add mode, metering, migration and browser regressions plus a separate nonlinear-house closed-loop checker. Physical energy accuracy and new AUTO/ventilation coefficients remain unverified until sufficient newly tagged site sessions exist; see [validation and operating notes](docs/power-ventilation.md).

## 0.2.15 — 2026-10-29

- Validate persisted forecast records, including explicit null observations; quarantine malformed diagnostic records without certifying them or blocking heating control.
- Isolate optional validation/reporting failures, preserve the primary traceback with bounded repeat logging, and keep recovery retry deadlines stable.
- Separate calculated plans, dispatched commands and confirmed thermostat application; retain causal incident fields when Recorder attributes are offloaded.
- Report controller, validation and storage health independently.
- Check every configured residual-balance power source and all fallback phase powers for freshness; voltage or a different phase cannot refresh a stale power measurement. Preserve and label power without an automatic request.
- Show primary fallback errors, applied-plan age, and stale versus unavailable or true-zero power in the existing panel.
- Provide a tested watchdog alignment that rechecks permissions and heartbeat before each command and preserves strict windows, upper limits and scheduled outages.


## 0.2.14 — 2026-10-27

- Зовнішнє тепло: підтвердження стійкості й нового повідомлення датчика, перевірка другого виміру, швидке зняття кредиту після зворотного стрибка; неперевірений прогрів не замінює гарантований нагрів. Верхній сценарій збережено для захисту від перегріву.
- Узгоджені actuator/min-on/min-off у плануванні, replay та dispatch; фізична мережа, верхня межа й інерційний перегрів мають пріоритет. Лічильники, модель, thermal coefficients, HACS та planning policy 4 збережені.
- Окремі frozen-plan, measured-input thermal, power/energy та post-stop peak diagnostics; потрібний довгий горизонт, причини відсіву, N/різні дні/вік. Відомий grid-off з нульовою потужністю не вважається оцінкою; duty-off не скасовує запит. Старі підтверджені резерви збережені.
- Часове покриття, прогалини, вік датчика, частка оціненої потужності й градусо-години; енергія циклу «виміряно + залишок», baseline та причини зміни. Часткові дані не видаються за повний підсумок.
- Optional read-only telemetry вентиляції з окремими relay/supply/unknown fractions; коефіцієнти повітрообміну без незалежних вимірювань не активуються. Окремі налаштовувані cold/low-solar та blackout shadow-сценарії не змінюють команди.
- Компактні подробиці чинної панелі без редизайну. Знеособлені регресії, приватні exact before/after replay 25–27.10 і незалежні 23–24.10; докази та межі фізичної точності в docs/audit-2026-10-27.md.

## 0.2.13 — 2026-10-21

- Навчання: точна межа ±20% без хибного float-відхилення та абсолютного допуску 0,005; збережений сукупний добовий ліміт, строгі фізичні межі й нульовий коефіцієнт. Виключено непевні/нехронологічні дані та відсутність реального поліпшення holdout.
- Кандидат більше не витісняє чинну перевірену модель до підтвердження парними годинними вимірами. Окремі датовані спроби/успіхи та зрозумілий статус на панелі; похибки не вигадуються. Пошкоджений state зберігається без перезапису й блокує команди; перезапис навчальної телеметрії атомарний.
- Фінальний dispatch повторно перевіряє фізичну мережу, верхню межу й ручні дозволи; fallback враховує денну верхню межу та чинне відключення. Ручна пауза без температури термостата не переводиться в auto.
- Сумісний запит повних атрибутів Recorder без presence-based фільтрів. Додано наскрізні регресії, обмежену числову матрицю, браузерну перевірку статусів навчання та CI-артефакт вимірювань часу/журналу. Межі доказів і порядок встановлення описано в docs/comprehensive-validation.md; фізичний нагрів не тестувався.

## 0.2.12 — 2026-10-21

- Уточнено нову діагностику денного порушення: хвилини та градуси рахуються від фактичної денної нижньої межі, без додавання запасу невизначеності. Загальний дефіцит резерву й алгоритм вибору плану не змінено. Додано регресію для температури вище межі, але нижче резерву, та для реального порушення.

## 0.2.11 — 2026-10-21

- Ранній нічний нагрів більше не забороняється лише тому, що кімната дотягне до 05:00. Ранковий пріоритет залишається м’яким: спочатку запас до наступного доступного нагріву та відновлення, потім пізніший час і вартість. Температурні захисти, дозволені години й чинні outage exceptions не розширені.
- За дефіциту — обмежений розширений пошук, вибір найменшого дефіциту серед перевірених варіантів. Версія пошуку скидає старий reference; збережений ключ перевірки не допускає повторного дорогого пошуку на однакових входах, зокрема після reload.
- У резерві й історії рішень: версія/статус пошуку, перевірені альтернативи, дефіцит, енергія нічного циклу, остання доступна подача енергії та фактично відхилені обмеження. Залишковий дефіцит не видається за доказ фізичної неможливості; панель показує тривалість і глибину денного порушення.
- Контрольна синтетична регресія на коефіцієнтах і модуляції з огляду 21.10: 5,81345 → 0 °C·год зі збереженими верхніми межами. Додано перевірки короткого ранку, холодного дня, перезапуску/кешу, старої політики та умовної достатності. Повний приватний live snapshot через доступний транспорт не прочитано; точний replay ним не підміняється.

## 0.2.10 — 2026-10-21

- Ранковий резерв прив’язано до поточного дозволеного циклу в часовому поясі HA: завтрашній слот не витісняє сьогоднішній; враховано секунди, перезапуск і DST.
- Перевірка запасу до наступного доступного нагріву з тепловою затримкою, окремими денними/нічними межами та пріоритетом зменшення дефіциту перед тарифом. Збережено верхні захисти, фізичну мережу й межі чинних outage exceptions.
- Автоматично виконані хвилини та кВт·год визначаються за підтвердженими вимірюваннями; ручний нагрів, невідомі інтервали й downtime не зараховуються. На панелі план і виконання показано окремо.
- Стабілізатор зберігає прив’язаний резерв і повторно перевіряє комфорт; одногодинний MAE не підтверджує довгий горизонт. Додано окреме накопичення перевірених похибок потрібних горизонтів та явний unknown за браку доказів.
- Компактна історія рішень до 8 КіБ, приватні відтворювані знімки перед зміною плану, ротація нового журналу й видима діагностика збоїв. Асинхронне читання Recorder перевіряє збереження атрибутів за однакового state; налаштування Recorder не змінюються.

## 0.2.9 — 2026-10-19

- Виключно візуальне оновлення Heating AI: панель успадковує кольори, шрифт, радіуси та поверхні теми Home Assistant, включно зі світлим/темним Material You. Прибрано жорстко задане зелене оформлення, декоративні градієнти й сяйво карток.
- Узгоджено типографіку, відступи, навігацію та поля трьох вкладок. Збільшено дрібні підписи; на телефоні поля мають шрифт 16 px та збережені touch targets. SVG-іконки замінюють різностильні символи в заголовку й кнопці оновлення історії.
- Картки нагріву та горіння зберігають делікатне червоне тонування, розпалювання/згасання — жовте. Графіки й легенди використовують спільні семантичні кольори; геометрія, часові шкали, вимірювання та історичні інтервали залишені без змін.
- Короткий відгук натискання та переходу перемикача без анімації телеметрії/графіків. Враховано `prefers-reduced-motion`, клавіатурний фокус, disabled-стани та світлу тему полів. Додано браузерні перевірки чотирьох тем, контрасту основного/другорядного тексту, успадкування радіусів і єдиних кольорів графіків.
- Алгоритми опалення, прогноз, пороги, системні параметри, API/сутності та HACS-координатор не змінюються.

# 0.2.8 — 2026-10-18

- Svitlo має три режими: `scheduled` для реального графіка, `normal` для підтвердженої свіжої відсутності обмежень і `unknown` для недоступного, застарілого, некоректного або аварійного джерела без графіка. Враховано реальний стан `No data` разом із `now_status: nosched` з Svitlo v2.9.3; порожні масиви самі по собі не є підтвердженням.
- У `normal` планувальник і повторна симуляція отримують внутрішнє покриття всього поточного горизонту без умовних сеансів. Реальні HA-дані не підміняються. Перехід режимів і зміна інтервалів скидають чергу перерахунку; вчорашні відключення не утримуються. Фактичний grid залишається обов’язковою умовою нагріву.
- Діагностика графіка містить `mode`, `source_available`, `source_fresh`, `emergency`, `reason`, `warning`. У нормальному режимі панель нейтрально показує відсутність планових відключень; частковий графік, невідоме джерело й аварійний advisory зберігають попередження.
- Унизу налаштувань — згорнутий блок «Системні налаштування»: 25 розширених параметрів/коефіцієнтів, доступ до 24 чинних helpers та 8 захисних констант лише для читання. Біля кожного пункту є доступна з телефона підказка. Відкриття й чернетки нічого не змінюють; явне збереження перевіряє межі, сумісність порогів та актуальність ревізії. Коефіцієнти проходять чинну перевірку моделі зі збереженням відкату.
- Значення за замовчуванням і захисти обладнання збережені. Явні зміни параметрів відновлюються зі стану поза HACS-каталогом після reload/update; приватний YAML, telemetry та лічильники не перезаписуються. Додано регресії режимів/переходів графіка, фізичної втрати мережі, покриття обох шляхів планування, системних налаштувань і мобільного UI.

# 0.2.7 — 2026-10-16

- У режимі циркуляції «Авто» вентилятор вимикається без запиту каміна/ночі незалежно від `fan_owned`, зокрема після reload/update зі старим `state.json`. Застарілий ON більше не позначається ручним запуском; ручний режим, три швидкості, перевірки живлення, захист оновлення та повторення команд збережено.
- Новий і утримуваний 24-годинні прогнози допускають до одного погодинного інтервалу (3600 с) без погоди наприкінці горизонту. `weather_missing_minutes` показує реальний дефіцит, або `null` за відсутності forecast; короткий хвіст використовує чинний температурний fallback без постійного попередження. Попередження про більший дефіцит, неповний графік мережі й аварійні відключення збережено.

# 0.2.6 — 2026-10-16

- Циркуляція каміна запускається при `delta >= 5 °C`; розпалювання від `2 °C` з трендом понад `+0.8 °C/год` саме по собі її не вмикає. Від `10 °C` є фаза «Інтенсивно горить»; тренд нижче `−0.8 °C/год` позначає згасання.
- Автоматичний запит завершується після п’яти безперервних хвилин нижче `3 °C`, або одразу при `delta < 4 °C` та тренді `<= −2 °C/год`. Повернення до `3 °C` скидає таймер; недоступний датчик прибирає запит, а довгі прогалини вимірювань не зараховуються до витримки. Після короткого перезапуску збережений таймер продовжується.
- Три налаштовувані рівні швидкості: 1 — ніч 23:00–07:00, 2 — стандартний день, 3 — інтенсивний день. Після згасання/ослаблення рівень 3 повертається до 2; ніч завжди має пріоритет. Чинні денна швидкість і нічна межа збережені; додано `fan_intense_speed` із початковим резервом 60% та підтримкою локального entity mapping.
- Діагностика циркуляції розрізняє джерела запиту, ручний запуск, належність керування контролеру й обраний рівень. У подробицях панелі видно час до вимкнення. Захисти ручного ввімкнення, резервного живлення, оновлення HACS і обмеження повторних команд збережено.
- Картки активного котла й горіння мають червоне тонування; розпалювання та згасання — жовте, відповідно до наявних тем і стилю.
- Налаштування ущільнено: денні/нічні температури, часи й три швидкості згруповані в колонки. Вкладка «Модель і ШІ» має компактні метрики, зведення, дії поруч і розгортання параметрів/діагностики замість постійних пояснень. Усі 25 елементів керування доступні.
- Регресійні тести охоплюють пороги, затримку, прогалини/перезапуски, команду зупинки через fake HA I/O, рівні швидкості та нічні межі. Браузерні перевірки охоплюють п’ять фаз каміна, червоне/жовте тонування й компактні вкладки на вузьких екранах.

# 0.2.5 — 2026-10-16

- AI-звіт синхронізується одразу після зміни налаштованого `ai_report`: окремий listener перевидає лише `sensor.heating_ai_summary`, зберігаючи `model`, `usable_samples`, `last_24h` і `last_result`. Черга планування й `next_compute` не змінюються. Спільне блокування публікацій та читання актуального helper захищають від перезапису застарілим callback.
- Головна панель ущільнена зі збереженням кольорів, типографіки й радіусів. Температура надворі перенесена до hero комфорту. Замість окремих малих карток — одна картка котла з фактичною потужністю, обраткою та енергією за 24 години; активність визначається вимірюванням, а не командою.
- Камін і циркуляція згруповані компактно: реальні фази «Неактивний», «Розпалювання», «Горить», «Згасає» мають окремі делікатні акценти; температура, різниця з кімнатою, стан і швидкість вентилятора поруч. Подробиці залишаються доступними через розгортання.
- Картку історії електромережі прибрано лише з UI. Backend entities, Recorder, алгоритм керування, thermal model і механізм HACS не змінюються.
- У прогнозі чотири компактні метрики, на телефоні — сітка 2×2; відсутня енергія показується як `—`. Постійні пояснення перенесено під info icon. Попередження показуються за неповного графіка мережі/погоди, зниженої точності чи аварійного стану.
- «План нагріву» йде відразу після прогнозу в DOM на всіх екранах. Часи сеансів і ранковий прогрів видно одразу, детальний розподіл енергії та умовних інтервалів розгортається на вимогу.
- Додано тести миттєвої синхронізації та паралельної публікації summary, усіх станів котла/каміна, умовних попереджень і недоступної енергії, DOM-порядку, унікальності ID, щільності, вузьких екранів і живого відображення AI-звіту.

# 0.2.4 — 2026-10-16

- Відновлено минулі інтервали нагріву: до появи нового датчика «котел працює» графік використовує збережену історію підтверджених ступенів потужності. Історія Recorder не змінюється.
- Замість стандартної історичної картки — адаптивний графік у стилі панелі: кімната, обратка й вулиця, окремі смуги котла та корисного тепла каміна на спільній шкалі часу, короткі підписи й час роботи.
- Періоди 6/12/24 години, перемикання температурних кривих, перегляд значень і станів дотиком або клавіатурою. Світла й темна теми, читабельні підписи на телефоні й ПК.
- Прогалини історії відображаються явно: штрихування станів, розриви температур, позначення неповних підсумків. Кеш, повторне завантаження й повідомлення про помилку з’єднання.
- Додано регресійні перевірки історії, мобільного перегляду й помилок. Оновлення встановлюється звичайною кнопкою HACS із уже налаштованим автоматичним координатором; логіка фізичного керування не змінюється.

# 0.2.3 — 2026-10-16

- Для встановлення з підключеним координатором HA оновлення починається звичайною кнопкою HACS: дозволи зберігаються, команди призупиняються, AppDaemon зупиняється на заміну файлів і запускається автоматично. Дозволи повертаються після перевірки версії, дев’яти модулів, нового heartbeat і політики планування. Журнал переживає перезапуск HA; невдала перевірка залишає команди вимкненими й показує сповіщення.
- Контролер відкладає ініціалізацію та фізичні команди, поки HACS повідомляє про завантаження. Налаштування, модель і локальні дані не переносяться.
- Циркуляція використовує окреме підтвердження резервного живлення вентилятора; зовнішня мережа більше не є обов’язковою, якщо резерв підтверджений і інвертор доступний. Додано перевірки недоступного вентилятора, обмеження повторів, збереження ручного запуску, підтвердження зупинки та ізоляцію помилок вентилятора від котла.
- Блок каміна компактний: стан, температура й фактична циркуляція; додаткові дані розгортаються на вимогу й залишаються відкритими під час оновлень.
- На графіку прогнозу є окрема смуга оцінки залишкового тепла каміна за чинним двогодинним згасанням моделі. На добовому графіку — фактичні смуги котла й каміна на спільній часовій шкалі. Недоступний датчик каміна показується як відсутні дані.
- Тести сценаріїв оновлення, циркуляції й блокування команд доповнюють чинні перевірки планування, моделі, енергії та браузерної адаптивності.

# 0.2.2 — 2026-10-16

- Графік прогнозу перебудовує координати під реальну ширину картки: підписи та висота залишаються читабельними на телефоні, у браузері й на ПК.
- Адаптація працює за шириною самої панелі, зокрема при відкритті бічного меню, вбудовуванні картки й повороті телефона.
- Нативні історичні графіки зберігають підключення під час оновлення показників; обмежено ширину вкладених карток і canvas.
- У мобільному блоці комфорт і температура розміщені ліворуч, рішення контролера — праворуч; прибрано дублювання статусу й збільшено зони натискання.
- Камін і циркуляція винесені перед графіками: стан, датчик, різниця з кімнатою, динаміка, корисне тепло, фактичний стан вентилятора, план швидкості, нічне обмеження та причина очікування. Кнопка одразу відкриває налаштування циркуляції.
- Повернуто ручне оновлення погодинної погоди. Звірено всі 24 елементи керування зі старих кліматичних панелей.
- Браузерні перевірки охоплюють десять ширин, мобільну емуляцію, поворот, розміри підписів, стабільність графіків, стани каміна та всі початкові елементи керування. Логіка опалення й дані не змінюються.

# 0.2.1 — 2026-10-16

- Міграція створює звичайний каталог із посиланнями на окремі файли HACS: стандартний сканер AppDaemon бачить усі модулі, тоді як посилання на цілий каталог він не обходить.
- Додано регресійний тест видимості дев’яти модулів у сканері; відкат перевіряє кожне посилання. 125 Python-тестів.

# 0.2.0 — 2026-10-16

- Вбудована українська вебпанель: огляд, добовий прогноз, графіки, усі чинні налаштування, навчання та журнал рішень.
- Адаптивний інтерфейс для телефона, планшета й комп’ютера; використовує авторизацію Home Assistant.
- Інтерфейс доставляється тим самим HACS AppDaemon-застосунком. Стабільний завантажувач підтягує файл із хешем версії після оновлення сторінки.
- `sensor.heating_ui` повідомляє версію, шлях завантаженого коду, контрольні суми модулів і адресу панелі.
- Утиліта первинного переходу на HACS: перевірка дублікатів, резервування коду, незмінні локальні налаштування й дані, перевірений відкат.
- 124 Python-тести, тести JavaScript і браузерні перевірки трьох вкладок на чотирьох ширинах.
- Логіка планування, захисти, коефіцієнти моделі та облік енергії залишаються чинними. Завантаження HACS саме по собі ще не перемикає старий робочий шлях: потрібна первинна міграція.

# 0.1.0 — 2026-10-16

- Ранковий сеанс резервується перед розподілом вечірнього нагріву.
- Ранній нагрів у пріоритетному плані — підтримка до доступного ранкового вікна, коли пасивне охолодження загрожує нижній межі.
- Нічна перевага — нижня частина заданого діапазону; денна — між цільовою і верхньою межею з чинними захисними обмеженнями.
- Стабілізатор повторно перевіряє плани без ранку; зміна політики скидає утримання старих планів.
- Збережено захист верхньої температури, інерції, мережі, каміна, ручного керування та резерву.
- 114 офлайн-тестів. Облік енергії та коефіцієнти моделі не змінюються.
