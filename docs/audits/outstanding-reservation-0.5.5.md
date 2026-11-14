# Outstanding reservation — 0.5.5 candidate

Base main: `9874743cb0c72ab09c0abab2c6ccd0fe8f9c60d0` (merged PR #46).
Worktree/branch: `fix/outstanding-reservation-20261114`.

## Evidence and cause

Read-only production inspection confirmed 0.5.3, healthy KWS qualification,
a live 15s heartbeat, and active `LedgerConflict: Outstanding reservation`.
The stack ends in apply_action → set_hvac_mode → prepare_equipment_command.
PR #46 handles already-fenced reconciliation and controller-owned OFF echoes.
It does not serialize the unfenced setpoint/mode transition.

A blocking HA service return is not necessarily a device echo. If set_temperature
returns before its state update, its durable reservation remains SENT; the same
apply_action immediately prepares HEAT-mode/AUTO and conflicts with it. Repeated
ticks repeat the conflict and can provoke the secondary fallback interlock error.
The new regression failed on unmodified merged main with exactly Outstanding
reservation at set_hvac_mode (synthetic HA and real SQLite, no physical services).

## Minimal fix

- Gateway defers ordinary thermostat stages while evidence remains pending.
  It returns False explicitly for deferral; legacy apply_action waits rather
  than marking the full transition sent/owned or creating another reservation.
- Repeated apply_action ticks do not resend the setpoint or mode. A matching
  owned echo lets the existing next tick continue the remaining stage.
- Actual OFF can supersede pending HEAT for safety; repeated pending OFF waits.
  Existing voltage/KWS/manual checks still run before dispatch.
- PREPARED evidence now has a timestamp too. Missing PREPARED/SENT confirmation
  reaches the existing 30s fence → bounded safe STOP → fresh OFF reconciliation,
  without restart. UNKNOWN records, schema v1, owner epochs and lease remain.

## Validation and package

- Local make check test: 659 Python + 53 JavaScript passed.
- Final targeted suite: 28 passed, including delayed echoes for HEAT and AUTO,
  three repeated apply_action calls yielding one service, continuation after
  owned echo, PREPARED/SENT expiry and safe STOP, duplicate OFF suppression,
  stale superseded echo rejection, and existing manual/KWS/grid/voltage gates.
- Existing offline 403MiB history/busy-executor test: heartbeat p99 56.84ms,
  Python peak 52.08MiB; these are synthetic measurements, not production load.
- CI repeats full tests on Python 3.12/3.13, HA 2026.11 on Python 3.14 and browsers.

Marketplace candidate 0.5.5: 60 files, 542986 bytes; SHA-256
`08ae493edbdb28438ca37969e89d37e83d435187c9bd3d29d1444888405b389d`.
Manifests, nine-module fingerprints and reproducible ZIP checked.
Frontend change is version metadata only; WebSocket API remains [1,0].
UI GPT should rebase its branch and preserve this backend/release manifest;
no UI redesign branch, helper/grid automation or production setting was changed.

## Deployment boundary

No installation, reload, restart, boiler/KWS command or journal mutation was
performed during this investigation. Production restoration is NOT confirmed
until an approved deployment and fresh runtime/ledger/power checks succeed.
Use Marketplace package only after CI, retain the private directory/journal,
make a fresh backup, retain the last package for code rollback. Never restore
an older journal over new commands. Installation and any HA restart require
an explicit operator decision for this candidate.
