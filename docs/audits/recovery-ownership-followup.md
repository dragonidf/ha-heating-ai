# Recovery ownership follow-up / candidate 0.5.4

Base main: `1ad4609a7df5eb6d5bb0992a71a397beeec726cb` (release 0.5.3).
UI redesign #44 remains untouched. This candidate is not installed or merged.

The approved 0.5.3 install completed. Its integration reload retained Python's
old imported modules and failed the mixed-version guard; an explicitly approved
HA restart loaded a verified 0.5.3 runtime. No OS/Proxmox/App restart was requested.
The persisted journal then reconciled without deletion or schema change, KWS
qualified, heartbeat/control health recovered, energy origin was preserved and
a new plan was published.

Production verification exposed a missed interaction with the legacy manual
thermostat listener. Reconciliation sent OFF without registering expected OFF;
an ordinary automatic low-target OFF could also supersede the pending recovery
reservation. A later controller-owned OFF echo selected Observation as if the
user changed the thermostat. The mode change's HA context matched the controller
and had no user context. The operator's preceding automatic mode was restored;
controller health stayed healthy and KWS ready. Positive physical heat is only
claimable after fresh independent phase-power evidence during normal plan demand.

Minimal fixes:

- Register expected OFF/setpoint, command time/generation and the normal 20s
  pending window before the journalled reconciliation STOP.
- While fenced in authorized automatic mode, let reconciliation own the command
  path. Do not replace its STOP with a normal low-target HEAT-mode command.
- Resolve a retained ledger-related `fallback_command` incident after verified
  safe STOP, keeping count/history. Do not clear unrelated diagnostics.

Regressions reproduce delayed own OFF without false manual pause, retain true
manual-change priority, reject normal OFF supersession and preserve old incident
counts. KWS/grid/manual gates and SQLite schema v1 are unchanged. WebSocket
contract remains [1,0]; frontend change is version metadata only.

Candidate package: 0.5.4, 60 files, 542702 bytes, SHA-256
`40f8cc6c12463d67555156827a7ff39667905001a20795cd49d3bbd23c16d41a`.
The complete local suite and CI results are recorded in the Draft PR. Installation
and any further HA restart require a new explicit operator decision. Until then,
production remains 0.5.3; its future fence recovery can still trigger the described
false manual pause. Existing full backup and 0.5.2 rollback package are retained.
