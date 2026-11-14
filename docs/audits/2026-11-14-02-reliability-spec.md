# ТЗ — Heating AI V1: fault tolerance, safety and production reliability

**Owner:** independent reliability audit branch, 2026-11-14. **Status:** ready for implementation by a separate developer/agent. **Important:** the in-flight UI agent owns PR #44. Do not edit its branch, cherry-pick this audit, merge, deploy or restart HA until changes and ownership are coordinated. This branch must remain docs-only until it is explicitly designated for implementation. Use a new implementation branch from current `main` after UI integration/rebase. Never run physical fault-injection in production without a separate explicit approval.

**Goal:** achieve **100% coverage and passage of the defined safety/recovery acceptance scenarios** and measured high availability, **not claim literal 100% physical uptime** (grid, Wi-Fi, electronics, HA, maintenance and sensor power can fail). Hard constraint: no unsafe HEAT, no accidental repeated 9-kW energization, preserve manual overrides and physically independent voltage protection, never treat unknown as zero.

## P0-1 — Durable ledger fence (immediate functional blocker)
**Evidence:** installed 0.5.2 repeats `LedgerConflict: HEAT fenced; external reconciliation required` under confirmed grid, healthy KWS status and NORMAL voltage. Earlier `Outstanding reservation` and unhandled `Unconfirmed/stale transition` after state/echo callbacks, plus native AUTO recovery once followed by relapse. 13:34 live `writer_state=fallback` and command-error; heartbeat fresh.

**Implementation contract:**
1. Analyze `critical_ledger.py`, `production.py`, `native_bridge.py` and the migrated store in **read-only** mode first. Inventory `PREPARED/SENT/HA_ECHO/UNKNOWN`, owner epoch, revision, fence, pending in-memory echo, method origin and latest service response. **Never delete/reset the SQLite file, bypass revision CAS, forge HA_ECHO, or turn on heater to clear a fence.** Take backup via supported HA means before any production change.
2. State machine: if unresolved HEAT -> fence remains; safe STOP may supersede but **only independently verified thermostat off echo** clears the fence. Make double callback (blocking service + HA listener), out-of-order/late callback, and idempotent STOP handling race-safe. A stale/unconfirmed echo should be logged as bounded diagnostic, never an unhandled async task exception nor an unauthorized fence release.
3. Fix one-shot recovery dead-end: when safe STOP fails/transiently times out, retain pending state and retry with **bounded exponential backoff** on the existing control tick, only with current AUTO authority, voltage/grid confirmation, no manual override and no parallel in-flight STOP; cap event rate/command attempts. Recovery must resume when thermostat comes back, without HA restart or a GUI toggle. A permanent failure remains fenced with human-readable reason and optional manual reconciliation flow.
4. Explicit states for `safe_stop_required`, `reconciliation_waiting`, `reconciliation_verified`, `heat_fenced`; expose latest `last_attempt`, result and error **without journaling private command payload/identities in UI**. Separate control process liveness, permission, command journal, equipment-readiness, physical-demand from applied power.
5. Every real HEAT command must pass all existing grid/voltage/manual/ownership/thermostat gates **plus** the new KWS physical-readiness gate defined below. STOP remains fail-safe and may not be suppressed by a KWS outage.

**Acceptance:** for each pre-crash state (PREPARED HEAT, SENT HEAT, duplicate HA_ECHO, legacy fence, interrupted STOP, manual command concurrent, transient Tuya disconnect), zero HEAT before verified safe STOP, exactly one active writer, zero unhandled task exceptions, recovery without HA restart once valid conditions return, no endless 15-s command/error storm. Tests include 2+ reloads and process cancellation while protecting all immutable ledger evidence.

## P0-2 — KWS-306WF physical circuit breaker readiness
**Evidence:** on 2026-11-10 fault code 2 `undervoltage`, relay off/standby with KWS voltage as low as 177.32V and 180.41V. Grid return 2026-11-14 11:18:48; cloud returned 11:19:21 and local relay status returned 11:24:04. KWS local Tuya and cloud represent one device, not independent equipment. Thresholds: low 190 V, high 260 V, reset_delay 120 s. Integration device schema does not expose a `power_on_state` setting.

**Implementation contract:**
1. Model separately `grid_confirmed`, `voltage_qualified` (input-220 multi-phase), `kws_link_fresh`, `kws_contactor_on`, `kws_fault`, `kws_reset_pending`, `thermostat_ready`, `heating_physically_confirmed`. Do not infer relay state from 0 W or from the cloud duplicate.
2. Subscribe to KWS Local status `off/standby/on`, switch, fault bitfield, all three local voltages, three powers and the grid-return epoch. During `standby` or active fault, **reject new HEAT** and report `KWS: захист/очікування відновлення`, without forcing the KWS output ON.
3. Re-qualification after outage: at least 120s configured delay when relevant, current fault cleared, appropriately fresh per-phase voltage, post-return contactor confirmation, and at least 30s stable grid/voltage; avoid mixing stale pre-outage switch status with post-outage fresh voltage. Use conservative UNKNOWN if definitive local relay acknowledgment unavailable. Read status only; do not add a KWS off/on watchdog.
4. If grid good but KWS stays `standby/off/unknown` after a bounded diagnosis window (~5m after nominal reset), raise **one** actionable notification (local fault/voltage/communication) and do not repeatedly pulse 9kW contactor or thermostat. Distinguish manual KWS off from protection fault when evidence permits; do not override intentional off.
5. On HEAT demand, require service-level thermostat acceptance plus independent KWS measured nonzero power within existing 180-s verification window; distinguish `KWS open`, `KWS stale`, `thermostat no demand`, and `boiler no power`. Do not treat successfully delivered thermostat service as physical heat ACK. Preserve 3/6/9kW stage detection and 20s stabilization.
6. Add optional diagnostics for discrepancies between input-220 and KWS voltages, with verified phase map: KWS A/B/C = boiler 1/2/3 and input voltage phase_c/phase_b/main = physical 1/2/3. Alert only on sustained, qualified differences; never use instantaneous cross-device voltage differences to change phase topology.

**Acceptance:** under/over-voltage trip, power cycling, intermittent Tuya Local disconnect and stable return, zero HEAT sent while breaker protected, automatic return to model only when verified and authorized, no unsafe switch toggles, no duplicate alarm flood. When no physical data exists, report lack of verification; do not fabricate it.

## P0-3 — Safe comfort fallback while model/control failed
**Evidence:** 13:34 living room 20.3°C below configured daytime lower 21.0°C, yet thermostat AUTO temperature and setpoint both 21.5°C. A confirmed `temporary_native_auto` target may therefore not call for heat because thermostat/room bias is ~1.0–1.2°C. Ledger fence makes HEAT unavailable independently.

**Contract:** expose honest **comfort unavailable due safety lock** rather than pretending fallback maintains temperature. In automatic fallback *after all independent safety gates and healthy journal are satisfied*, permit a **bounded, explicitly enabled** target correction from a recently validated room/thermostat bias; clamp to thermostat limits, user comfort bounds, hysteresis and existing power/temperature limits. Never compensate for a stale room sensor or manually chosen thermostat mode; no silent force-on during ledger fence. Escalate to owner when room is below lower bound and safe control unavailable for >15min.

**Acceptance:** under valid room + bias, fallback can achieve requested room comfort without overshoot; under stale/inconsistent room or fenced ledger, no blind HEAT, correct alert and safe hold. Simulated cold-day and 3/6/9kW tests.

## P1-1 — Inputs: freshness, outage and source provenance
- Verify all required entities from imported configuration, not only `sensor.heating_ui.entities`: grid helper, three input voltage channels, Deye connection/heartbeat, room (Tuya cloud), return probe (Zigbee2MQTT), outside (Z2M), fireplace (Tuya cloud), thermostat (Tuya Local), KWS (Tuya Local), weather, Svitlo calendar/emergency, fan/recuperator/bath and setpoint/manual helpers.
- Differentiate a **state that did not change** from a **device that stopped reporting**; validate real HA Core `last_reported` semantics, cross-check local recorder/tool values and controller's `source_quality.value_reported_at`. A/C KWS 0 readings showed old externally reported timestamps yet internal controller quality had current timestamps: investigate before changing max-age or claiming stale.
- Cloud Tuya room reports may have 4h between **changed values**; test actual report cadence before setting freshness alarms. The dynamic current model's primary `room_reporting_limits` and 24h thermostat bias are acceptable only if proven against outages, abrupt cold shifts, room sensor failure, manual changes and spurious stale recovery. Never infer actual missing telemetry from significant-change history alone.
- Zigbee probe and outdoor sensors have recorder `unknown/unavailable` entries: model temporary loss and recovery without zero conversion. For return probe use external `...probe_temperature` only, not device-body temperature.
- Physical grid must be proved by independent input voltage and Deye link; regional Svitlo calendar/emergency is scheduling advice, not electrical proof. Preserve existing grid automations and TG messaging untouched.
- Calendar `emergency_outages=on` while scheduled forecast is valid: validate advisory fallback, timestamp age, timezone/DST and 3.1 queue; avoid treating region-wide emergency as a precise local blackout.
- Weather provider `weather.lozivka_google_weather`: stale hourly feed, API offline 3h, discontinuous forecast, partial horizon; publish bounded 24h **informational** plan with `grid!=on`, but never actuate while grid absent. Integrate merged PR #43 only through normal release pipeline after testing; currently installed 0.5.2 lacks it.

## P1-2 — Fan, fireplace and ventilation isolation
- Fan Tuya cloud: 10 unavailable entries in sampled 48h; recuperator 7, bath 1. Test intermittent cloud unavailability, manual ON/OFF, schedule night boost, fireplace rise/fall, cooldown, daytime/night speed, backup flag and grid-off.
- A manual `input_boolean.heating_fan_backup_verified=on` is operator permission, **not measured independent electrical backup**. Treat grid unknown conservatively, preserve manual intent and the Deye connection guard.
- A fan/recuperator failure must remain a warning, **never fence the boiler's critical ledger**. No retry burst; maintain 30s throttling and user intent precedence.
- No recent significant fireplace ignition in sampled 48h: do not label this scenario production-proven until a controlled replay or safe real event.

## P1-3 — Observability, runtime and release validation
- Three physical input voltage entities returned 0 recorder history rows in a 24h query despite live fresh values. Check Recorder include/exclude and entity statistics support; add **bounded event snapshots on actual voltage/outage/fault transitions** (before/after, KWS code, grid/Deye state and provenance), not high-rate raw Recorder spam.
- HEAT/STOP evidence diagnostics: command requested, service accepted, echo observed, electrical power confirmed, manual override, recovery reason; distinguish each stage and surface it in user UI. Do not erase old diagnostic incidents; set `active=false` with recovery timestamp when genuinely resolved, retain counts for auditing.
- Controller state ~8,529 recorder rows/24h, Deye voltage ~13,321; measure recorder writes, CPU/RAM/event loop, high-latency state snapshots and UI requests. Current worker durations observed: plan ~54.5s; power advice ~173s, but 15-s tick heartbeat may remain healthy. Keep expensive work off event loop; cap/skip overlapping jobs; conserve one writer and no unbounded background loops. Use realistic archive size (~403MB observed) to load-test tail, compaction, restart and history/UI responsiveness.
- Add health dashboard with **four separate** grades: computation alive, grid/KWS equipment ready, authorized command, physical heating confirmed. Set simple pass/fail alerts and deduplicate per incident.
- Do **not** infer latest deployed code from git `main`; verify `sensor.heating_ui` and installed component against release assets and exact SHA. UI PR #44 must be tested with existing backend and rebase before any merge.

## P2 — KWS energy/accounting and UX invariants
- KWS `total_energy` and phase A+B+C matched 233.83 kWh at 13:09; cloud duplicate agrees; controller day=12.00 kWh equals KWS day increment (221.83 to 233.83). Preserve *hardware counter deltas* and migrated controller origin=597.53734, measured=70.87, total=668.40734.
- Reconcile yearly utility-meter baseline discrepancy ~0.19928 kWh without rewriting historic records and without assuming the counters share absolute origins. Test counter reset, rollover, missing grid period, no power reporting during zero-state and Tuya Local reconnect.
- Keep status displays honest: model `validated=true` does not imply forecast `validated=true`, service AUTO echo doesn't prove physical heat, time-to-recovery is conditional, emergency outage warning is advisory.

## Mandatory acceptance test matrix
| Test ID | Situation / injection (offline in test harness only) | Expected invariant |
|---|---|---|
| L01 | Crash after PREPARED HEAT | Remains fenced, never duplicate HEAT, safe STOP + echo gates recovery |
| L02 | Crash after SENT HEAT without HA echo | Unknown evidence retained, no false confirmation or HEAT |
| L03 | Duplicate/out-of-order state callback vs service return | No unhandled `LedgerConflict`, no false fence release |
| L04 | STOP service timeout then later thermostat reply | Backoff retry and valid echo clear fence, no restart |
| L05 | Manual mode changed during STOP/HEAT prepare | Controller does not override manual intent; owner CAS respected |
| L06 | Restart/reload 2x with old journal and current activity | Single writer, ledger integrity, bounded shutdown |
| K01 | KWS undervoltage 177–181V / fault 2 | KWS off/standby means no HEAT; notify once |
| K02 | Grid returns, KWS relay status unavailable 5min | Unknown != on, no auto toggles, clear diagnosis |
| K03 | KWS protection clears after reset_delay 120s | Stable, post-return confirmed relay required to resume |
| K04 | Power=0 or sensor A/C unchanged but voltage fresh | Honest quality label, no false physical HEAT ACK |
| K05 | Grid 220 on and Deye off, then inconsistent voltage phases | Conservative grid unknown; never start heat |
| T01 | Room Tuya cloud missing 4h but thermostat fresh | Valid bias backup only within documented age, alert if no trustworthy temp |
| T02 | Return probe invalid, outdoor unavailable | Keep informational forecast where valid, reject unsafe HEAT |
| T03 | Room 20.3, thermostat 21.5, fallback setpoint 21.5 | No claim of maintained comfort; permitted bounded bias if opted in and authorized |
| F01 | Fire ignition, slope, cooldown, cloud fan unavailable | No boiler failure, manual fan precedence preserved |
| O01 | Emergency flag on + scheduled 3.1, blackout/offline Google weather | No false grid authority; 24h advice if valid inputs, no physical HEAT off-grid |
| E01 | KWS cumulative reset / delayed readings | Monotonic migrated total; no double count and correct quality |
| U01 | UI rendering stale active error vs recovered hardware | Distinguish incident resolved, fenced and pending, no deceptive green badge |
| P01 | Large archive, 15s tick, 2 workers, slow HA UI/history | Heartbeat p99 < 45s; no event-loop lockup, no growing worker queues |
| P02 | Complete unattended 72h soak + scripted outages and mixed faults | No crashes/unhandled errors, no unsafe HEAT, no memory/file growth without bound |

## Evidence and SLO / release gate
1. **100% passing safety-critical L/K/T/O tests**, 0 HEAT commands under blocked/fenced/unknown critical inputs; manual controls never silently overwritten.
2. Demonstrate recovery **without HA restart** after confirmed stable grid, clear KWS, and service echo, with bounded attempts and target **<=5 min from all prerequisites becoming true** (unless hardware reset delay, no temperature permission or manual override requires more).
3. 72h representative offline soak, 7 days observation after release, no unhandled ledger task exceptions; warnings may occur as expected on actual outages and remain distinguishable from software faults.
4. Runtime telemetry: 15s baseline control scheduling preserved, heartbeat below 45s age under eligible operation, no >60s event-loop stall, bounded worker backlog and archive/Recorder usage; quantify against pre-fix baseline. P95/P99 metrics from measurements rather than assumptions.
5. Independent model decision vs actual thermostat/power evidence; no fabricated power on stale zero, no wrong KWS phase map or double-counted kWh, no mixed UTC/Kyiv timestamps.
6. Staged implementation: separate PR for ledger/recovery (P0-1), KWS readiness (P0-2), comfort fallback (P0-3), then diagnostics/other improvements. Keep PRs small and reversible, with unit/integration/HA-2026/browser tests and a clear recovery plan. **Do not merge/deploy/restart** automatically or touch PR #44. Backup first, verify version, and obtain a separate user decision before any production application or Core reload.

## Minimal next actions, in order
1. Have current UI GPT finish its work. Keep this audit branch independent and docs-only.
2. Assign ledger root-cause fix to another implementation branch, reproduce with persisted journal fixtures and prove safe STOP race handling. No database deletion or restart workaround.
3. Add KWS readiness and fault-state tests/guard; no auto off/on recovery toggle.
4. Verify deployed code/version and only then apply release under operator control; use staged 72h monitoring and rollback on first unsafe or recurring fence case.
