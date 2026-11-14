# Reliability audit progress and handoff (2026-11-14)

**13:34 Kyiv — started.** Scope isolated to read-only HA MCP, repository source reads and documentation commits on branch `audit/controller-reliability-20261114` from main SHA `9fcc5c05`. No HA service calls or entity changes, no code changes to main or UI PR #44; no runtime restart, no Git merge/release.

**13:36 — direct production state.** Controller release 0.5.2, fallback despite healthy input, diagnosed persistent ledger fence; probe vs enclosure temperature correctly identified; ~63 live entity/helper states checked, including safety, weather, power, temperature, fan, ventilation, outage and utility meters.

**Historical analysis.** Sampled most recent 18–48h critical streams and 7d KWS fault/state transitions. Found physical KWS undervoltage and exact relay state correspondence 2026-11-10; measured return-to-KWS observation delays ~40–316 s; KWS power/energy matches controller hardware delta and phase sum. Tuya cloud/Local duplicate identified as same KWS hardware. Other observations: sparse Tuya temperature changes, Zigbee probe/outdoor transient availability, Tuya fan and recuperator transient unavailable, full weather/outage advisory with emergency flag.

**13:40 — system overload and recorder audit.** Controller state ~8,529 recorder events / last 24h; Deye AC voltage ~13,321. Raw input 220 phase A/B/C voltage returned 0 recorder history rows / 24h even while live readings were fresh. Archive disk pressure false, archive ~403MB; planner ~54.5s, power advisor ~173s seen in diagnostics. Diagnose before optimizing; do not add uncontrolled high-rate sensors.

**13:43 — upstream grid source resolved.** The actual authoritative user-facing helper is a chain:
`binary_sensor.detektor_220_z2m_contact` (Zigbee Tuya door/window style, **off=grid present, on=grid absent**) -> `automation.sinkhronizatsiia_statusu_220v_z2m_boolean` -> `input_boolean.status_merezhi_220_new` -> `automation.220_on_new / 220_off_new` -> `input_select.indikator_merezhi_stan` -> controller, plus an on/off template display helper.

The detector showed multiple `unavailable/unknown` intervals (especially 2026-11-13), while the derived boolean/selector preserved their last known values. **These helpers are not independent evidence**. Native controller also insists on Deye connection/inverter heartbeat and three fresh input-220 phase voltages before HEAT. Keep this multi-source guard. No changes to grid automation, telegram logic or detector are in scope.

**Code-level ledger evidence.** Source `v0.5.2`: `CriticalLedger.prepare` blocks HEAT on outstanding PREPARED/SENT or fence; STOP remains valid and needs confirmed HA echo to complete safe stop. `NativeController.tick` and `_reconcile_writer_fence` use one-shot recovery; if attempt fails on transient I/O, running process may not retry until re-initialization or manual transition. `command_echo_changed` may raise `Unconfirmed/stale transition` via async dispatch, seen as a live unhandled exception in HA logs. Root-cause command ID/revision remains unproven because the private ledger DB was not opened/read via supported MCP.

**Dependency files:** see `01-dependency-matrix.md`. **Implementation requirements and tests:** see `02-reliability-spec.md`.

## Done / not done
- [x] Created isolated git branch without modifying UI agent's draft.
- [x] Inspected installed version and current controller error without restarting anything.
- [x] Mapped physical KWS, Zigbee, Tuya, Solarman, Svitlo, Google Weather, settings/helpers and manual mode dependencies.
- [x] Cross-checked alarm and voltage, relay and outage chronology, energy accumulation and public heartbeat.
- [x] Drafted ranked remediation work and independent safety acceptance tests.
- [ ] Exact inspection of private ledger journal and imported config using a safe approved read channel; not available in current HA MCP read surface.
- [ ] Controlled production-equivalent integration fault injection; **do not run on live boiler**.
- [ ] Separate feature implementation, PR review, CI/soak tests and safe rollout — **not performed**.
- [ ] Measured 7-day post-rollout reliability and physical failover; no guarantee of 100% real-world uptime.

## Coordination rule
Another GPT is implementing UI in draft PR #44. This branch contains **documentation only** and must not be merged/rebased into its branch or directly deployed. Once its UI work is finished, create independent backend fix PR(s), pin deployed SHA and coordinate release and HA integration reload with the user. Do not use HA Core, OS, Proxmox or other app restart as a reliability test without separate user permission. Never clear KWS breaker via off/on automatically as a way to mask voltage faults.
