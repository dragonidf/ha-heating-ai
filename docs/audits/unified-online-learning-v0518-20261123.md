# Unified scheduled learning, proposed 0.5.18

Baseline is `v0.5.17` at `e731883e96f8fc6756639425ee3b652aa15df15f`. The starting point for the review branch was Draft PR #77 at `ed7bf3dc9bb1e5f8ba5fe998cd245e74dd82365a`. The proposed number is reserved for the later authorized release. This branch does not publish a Marketplace release.

## Runtime

Native startup installs one learner by method dispatch on the controller. The prior draft had two definitions of `NativeController.initialize`, so its initialization hook was unreachable. The new branch uses the existing 10:00 and 20:45 scheduling and the existing HA executor. The scheduled worker can calculate a bounded proposal using historical inputs and cannot issue commands or edit the active model. No candidate runs alongside the active planner. Historical issue records contain the production forecast and known weather as issued, together with subsequently validated physical input and endpoint evidence.

The active model starts from the actual stored coefficients, normally v6. Upgrades require independent primary changes, nonoverlapping 15–150 minute windows, minimum time/state/day/return coverage, later-day holdout and both 1 h and 3 h forecast checks. The day separation, 12×1h / 6×3h minima, 2% improvement per horizon, no worse signed bias and trajectory guards all apply. The evaluator fits only `floor_air` and `room_loss` for this review. Return response and heat gain are held constant. Solar candidate estimates are diagnostics and add no new comfort credit. The existing real-time gain observation can still inform upper-temperature risk. Legacy proposals remain visible for diagnosis, while scheduled promotion is the sole automatic application route.

The runtime rejects overlapping, missing or unsafe evidence, candidate results after a timeout, and results whose original model, equipment or safety generation changed. The current model and rollback checkpoint are replaced atomically in the normal state file. The active version changes only on a successful commit, and neither the observer nor model commit writes the command or energy ledger. Rollback can use comparable newly observed 1 h / 3 h forecasts without running a second model. It restores the preceding validated coefficients and retains a seven-day freeze and evidence cutoff. The manual rollback button also uses the atomic path. The normal planner performs any subsequent recalculation.

The only additional durable file is `online_learning_v1.json` with a checksum, a 512 KiB cap, at most 60 days and at most 500 completed windows. A process epoch rejects incomplete evidence across restarts. The native controller leaves the existing telemetry history append-only. Removing the older daily learner also removes its periodic compaction of that file. Startup reads the last 4,032 records from the tail instead of scanning the accumulated archive. Its disk growth should be observed independently of the bounded learning file.

## Field evidence

The read-only live runtime reported 0.5.16 with validated model v6, owned writer, healthy control and storage and the primary living-room source. The last successful promotion was v6. The latest legacy attempt rejected its data, reporting 22 train and 8 holdout pairs. Compared with a 3–7-minute adjacent-pair filter, prior Recorder inspection found typical numeric room changes roughly an hour apart. Existing synthetic tests verify observed numeric changes, KWS quality, unknown measurements, grid, manual heat, DST and lack of issue-time evidence. Recorder alone lacks enough independent and time-frozen evidence to calculate a paired comparison on this house. Historical live v6 frozen MAE from the preceding audit follow-up was 0.284 / 0.732 / 1.127 °C for 1 h / 3 h / 6 h. A new real MAE and a qualifying real automatic update are unmeasured.

## Measured verification

Thirty timed samples per variant after warm-up on CPU 2, with alternating
revisions in one spawned worker. Times are seconds.

| Test | Operation | A CPU median / p95 | B CPU median / p95 | Wall p95 A → B |
|---|---|---:|---:|---:|
| A/A | Stable plan | 1.1614 / 1.5735 | 1.1692 / 1.2426 | 1.5737 → 1.2427 |
| A/A | Power advice | 2.6335 / 3.8431 | 2.6167 / 3.2151 | 3.8446 → 3.2158 |
| A/B | Stable plan | 1.3414 / 1.7496 | 1.3399 / 1.5249 | 1.7497 → 1.5249 |
| A/B | Power advice | 2.9772 / 3.8344 | 2.9923 / 3.4694 | 3.8395 → 3.4740 |

The A/A p95 spread exceeds 5% for both operations, so the A/B resource gate is
inconclusive. Both revisions load the same optimizer and thermal step. The
added solar-fit switch affects the existing native observer. Shared-worker
peak RSS was 29,412 KiB for A/A and 29,492 KiB for A/B.

All four planner goldens matched exactly: scheduled, unknown, spring DST,
autumn DST. The isolated 15-second control fixture preserved its command trace.
Tick CPU p95 on that fixture was 4.766 → 3.987 ms. Native online collection
CPU p95 was 0.0935 ms and its scheduled job CPU p95 was 51.0 ms. The measured
separate-process peak RSS was 38,820 → 38,808 KiB. The candidate process held
240 KiB more resident memory after fixture training than before capturing its
ring. The evidence file used 216,914 bytes on the fixture.

The controlled synthetic replay retained 4,800 independent minutes versus 80
for the legacy selector. Its frozen 1 h / 3 h / 6 h MAE was 0.00304 / 0.00609 /
0.01040 °C for v6 and 0.00241 / 0.00267 / 0.00242 °C for the proposed coefficients.
A separate clean synthetic archive produced a bounded two-coefficient proposal
through the actual scheduled job. The accepted, disk-failure and automatic
rollback paths were exercised without a device service call.

## Review status

The production configuration stays on the old values unless the above evidence gates pass. The synthetic archive is a policy test. The branch remains draft until resource evidence is reliable and the remaining release conditions are reviewed. No authorization for merge, release, installation or restart was exercised.

## Reproduce

```sh
git worktree add --detach ../base517 v0.5.17
make check test
python tools/replay_adaptive_learning.py
PYTHONHASHSEED=0 taskset -c 2 python tools/benchmark_planner.py ../base517 --case scheduled --repeat 30 --a-a --primary-only --shared-worker
PYTHONHASHSEED=0 taskset -c 2 python tools/benchmark_planner.py ../base517 --case scheduled --repeat 30 --primary-only --shared-worker
python tools/benchmark_planner.py ../base517 --case scheduled unknown spring_dst autumn_dst --repeat 0 --shared-worker
python tools/benchmark_adaptive_runtime.py ../base517
PYTHONPATH=tools python tools/test_production_ha2026.py
PYTHONPATH=tools python tools/test_native_v1_ha2026.py
```

Use an available CPU and a controlled host. The HA smoke commands use a disposable HA 2026.11.0 Python environment. If a later authorized release needs a package rollback, restore v0.5.17 through Marketplace after preserving the current local state and command/energy evidence. Coefficient rollback uses the verified checkpoint and leaves the installed package in place.
