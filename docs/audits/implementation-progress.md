# Backend reliability implementation — 2026-11-14

Base: `origin/main` **9fcc5c05c8ff0a962651cf0c661500a6851ed3d6**.
Branch: `fix/controller-reliability-20261114`; separate fresh checkout. Audit source:
`audit/controller-reliability-20261114`, all three audit documents read.
No production access/mutations, installation, releases, merges or restarts.

## Implemented checkpoint

- Recovery now uses exactly one gateway reservation per STOP, existing 15s tick,
  exponential backoff (30–300s), eight failed attempts per continuously online
  incident. Thermostat reconnect restores the retry budget. Permanent rejection
  remains fenced for manual reconciliation; no endless journal growth.
- Superseded callback evidence cannot release the fence; callback exceptions are
  consumed with bounded diagnostics. Only fresh OFF evidence can complete STOP.
  Unknown journal records, CAS, owner epochs, checksum and writer lease retained.
- Fan commands no longer use the boiler's critical command ledger. Unknown grid
  cannot use a permission helper as proof of fan backup availability.
- Read-only KWS contactor/fault/voltage qualification, 120s minimum reset,
  30s stable voltage and post-return relay report; final dispatch rechecks live
  equipment, Deye and thermal input state. No breaker toggles.
- Optional `fallback_bias_enabled=true` (off by default in imported config)
  uses fresh validated bias, room comfort bounds and thermostat limits.
  Safety lock publishes comfort unavailable; cold lock alerts after 15min.
- KWS power response uses the fresh measured phase sum instead of legacy power;
  energy migration origin/deltas unchanged. Four independent health grades in
  existing diagnostics; transition-only archive evidence, no new HA entities.

## Coordination

PR #31 and #37 are already non-mergeable against main; do not merge them wholesale.
#31's one-shot recovery is superseded here. #37's 16MiB bounded history tail is
already in main (`DecisionJournal.read_telemetry`); retained unchanged.
#44 is a mergeable UI draft: no frontend edits here. Shared `release.json` has
only two backend checksum updates; UI author must preserve these when rebasing.

## Validation (in progress)

Targeted native production, persisted-ledger, KWS and reliability injections run
locally. Full Python/JS and CI gates follow. Accelerated 72h test currently covers
the breaker qualification state machine at 15s steps; it is not a production soak
or a full model/worker/resource endurance certification.

## Remaining evidence requirements

Production private journal forensic IDs/config, actual relay behavior, 7-day
post-release observation and real CPU/RAM/Recorder telemetry require a separately
authorized rollout. Raw voltage Recorder absence and utility-meter origin offset
cannot be repaired by inventing missing history or rewriting migrated balances.
