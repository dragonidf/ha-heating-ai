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

## Validation

Local `make check test`: 652 Python + 53 JS passed on the complete acceptance set.
The final shutdown service-cancellation change also passed all 42 targeted
native-production/reliability tests; CI repeats the complete suite.
Full Python/JS/HA/browser CI gates run on the Draft PR. Exact latest CI status is
reported in the PR description. No frontend change or API removal.
Accelerated 72h replay covers KWS state and the native dispatch gateway with a
real journal at 15s steps; it is not a production soak or a full numerical-worker
and hardware endurance certification.

## Remaining evidence requirements

Production private journal forensic IDs/config, actual relay behavior, 7-day
post-release observation and real CPU/RAM/Recorder telemetry require a separately
authorized rollout. Raw voltage Recorder absence and utility-meter origin offset
cannot be repaired by inventing missing history or rewriting migrated balances.

## Acceptance mapping — offline, synthetic equipment only

All listed suites are part of `make check test`; native HA smoke is CI-only
(Python 3.14, HA 2026.11). Existing passing behavior was preserved, not rewritten.

| Scenario | Evidence / remaining qualification |
|---|---|
| L01 | Real persisted PREPARED HEAT → boot fence → one echoed OFF; original UNKNOWN retained |
| L02 | Same injection after SENT; no duplicate HEAT |
| L03 | Superseded/duplicate callback; stale evidence cannot release fence |
| L04 | Transient timeout, 30s backoff, successful OFF without restart; old OFF display rejected; concurrent service guard |
| L05 | Manual change between PREPARED and send rejects dispatch; existing manual round-trip tests |
| L06 | Two boot claims reject stale owner; native shutdown/lease tests now actually await async methods |
| K01 | Fault, standby, relay off and 177.32V injections reject readiness |
| K02 | Five-minute missing relay report remains unknown, diagnosis overdue; zero switch-toggle API |
| K03 | 120s reset, 30s stability, post-return contactor evidence; unchanged status uses same-device live voltage reports |
| K04 | Zero/stale/estimated watts never physical confirmation; measured 3/6/9kW phase-sum tests |
| K05 | Deye disconnect / phase safety; final gateway rejects inconsistent supply evidence |
| T01 | Four-hour missing room uses dated bias; fresh disagreement invalidates backup; >24h/undated bias rejected |
| T02 | Invalid probe/missing room block final gateway; outage forecast continuity suite preserves advisory planning |
| T03 | Exact room 20.3 / thermostat 21.5 case; opt-in correction within comfort/thermostat limits; fence/stale input suppress correction |
| F01 | Circulation ignition/cooldown/manual/night/cloud-failure suite; fan error cannot create boiler reservation |
| O01 | Existing outage safety, forecast continuity and weather coverage suites; emergency advice never grants grid authority |
| E01 | Existing KWS reset, delayed counter, restart, duplicate, tariff split and migration-origin tests |
| U01 | Recovered incident retains count with recovery timestamp; independent grades; fresh OFF required |
| P01 | 403MiB sparse tail read off-loop with two busy executor jobs; measured Python peak ≈52MiB, heartbeat p99 ≈63ms; real HA CPU/Recorder baseline still unmeasured |
| P02 | 17,280 accelerated 15s steps: KWS faults/outages/manual periods + real native gateway and bounded SQLite history; separate breaker-state replay; not 72h production or full numerical-worker soak |

New scenario tests: `tests/test_controller_reliability.py`. Source/energy/fan
regressions: `test_qa_truthfulness.py`, `test_kws.py`, `test_circulation.py`.
Core regression coverage: native production, critical ledger, voltage, outage,
weather, power provenance, archive capacity and temperature-history suites.

## Review and rollout limits

- No missing Recorder history was fabricated. Existing 16MiB history-tail bound,
  cache/queue limits, process workers and energy accounting remain intact.
- The yearly utility-meter ≈0.19928kWh discrepancy is an **unresolved origin
  reconciliation**, not evidence for changing controller totals. Need the meter's
  initialization/period records before any correction. No historical rewrite.
- Fresh OFF is HA equipment evidence, not a mechanical contact measurement;
  physical heating is a separate fresh measured KWS power grade.
- Eight continuously online failed reconciliation attempts require manual review;
  an observed thermostat disconnect/reconnect permits a fresh bounded retry budget.
  This bounds UNKNOWN journal growth without deleting unresolved evidence.
- Bias correction is deliberately opt-in through imported configuration; no
  production option or helper was changed. Production calibration must be reviewed
  before enabling it.
- Optional cross-device phase-voltage discrepancy alarms were not added: no
  confirmed defect or calibrated sustained threshold justified another alarm path.
- Final release gate still requires review, actual installed-config/journal
  inspection via an approved read channel, staged hardware validation, real
  CPU/RAM/Recorder measurements and seven-day post-release observation. No claim
  of literal 100% uptime or full production certification.
