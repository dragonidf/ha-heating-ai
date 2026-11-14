# Heating AI V1 — production reliability audit (2026-11-14)

**Branch purpose:** isolated, documentation-only audit. Do not merge, install, release, restart Home Assistant or change entities/automations as part of this branch. The other GPT owns active UI/production development. Starting SHA: `9fcc5c05c8ff0a962651cf0c661500a6851ed3d6`.

## Scope
1. Read live Home Assistant states/configuration for all direct and secondary Heating AI dependencies.
2. Correlate recorder timelines for loss/restoration of grid, KWS-306WF, thermostat, room/return/outside/fireplace probes, ventilation, outage calendar and controller incidents.
3. Classify each signal: command authority, hard interlock, diagnostic, independent evidence, or advisory.
4. Inspect installed `0.5.2` against GitHub `main`, with no assumption that merged/unreleased code is deployed.
5. Deliver ranked findings with precise reproduction criteria, fail-safe remediation/test specification and acceptance evidence.

## Live observations at ~13:34 Kyiv
- Controller `sensor.heating_controller`: temporary AUTO, `LedgerConflict: HEAT fenced; external reconciliation required` despite heartbeat fresh and `sensor.heating_voltage_guard=NORMAL`.
- `sensor.heating_ui=0.5.2`; GitHub release `v0.5.2`, main additionally contains merged offline planner fix #43. UI PR #44 remains separate draft.
- Grid confirmed, KWS local relay/status `on`, problem `off`, local per-phase power ~1 W; thermostat in AUTO.
- Model validated, but forecast `validated=false`; temporary fallback command confirmed is not equivalent to model control success.
- KWS independently documented `undervoltage` fault_code=2 on 2026-11-10, with 177–181 V measurements, switch `off` and status `standby`.
- 2026-11-14 return of grid ~11:18:48; Tuya cloud KWS ~11:19:21; local Tuya KWS `on` only ~11:24:04, leaving device actuation state unknown in that interval.

**Audit status:** IN PROGRESS. This document preserves evidence only; detailed final findings and implementation checklist are added separately. Do not copy auth tokens, local keys, IPs, installation IDs or other secrets into this public repo.
