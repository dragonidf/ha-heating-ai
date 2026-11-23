# Adaptive learning shadow on Heating AI 0.5.17

`ready_for_review: true` · `ready_for_single_install: false`

`blocked_reasons: [real_telemetry_unavailable, real_frozen_holdout_unavailable, prospective_evidence_pending, stable_plan_p95_budget_unverified]`

## Baseline and isolation

- Base and `main` at branch creation were `e731883e96f8fc6756639425ee3b652aa15df15f` (tag `v0.5.17`, #76). The published release carries the Marketplace ZIP. The source commit and tree of this candidate are recorded in the Draft PR. The next version number can be reserved as 0.5.18 if still available at approval.
- Read-only live check on 2026-11-23 found `native_v1` 0.5.16, validated v6, and a fresh heartbeat. The live check provides context. All A/B comparisons here use the exact Git release v0.5.17.
- The native worker, planner, modulation and actuation algorithms are unchanged. The code for manual entry ID, 1–40 kWh range, confirmed KWS A/B/C fallback, 60-second phase-loss STOP, energy cursor, voltage/grid/outage/temperature interlocks and command ledger is inherited unchanged. The only package-integrity adjustment is the verified count and hash for the added module.
- Native options default to `disabled`. In that mode the three shadow hooks are never attached and no sidecar is read or written. Unknown options fail closed with a warning. `observe` attaches capture and private reporting. `candidate` additionally enables bounded scoring on the existing HA executor. The original sample, daily learning, plan issue and actuation calls run through the original controller. No shadow value is fed into them. The legacy 10:00 and 20:45 schedule remains authoritative.

## Qualification and historical replay

At each candidate physical interval the observer requires the primary device's numeric state change with a valid report timestamp. HA duplicate reports and value-format changes are insufficient. Reports keep their event time rather than the recorder sample time. Accepted report pairs do not share endpoints. They use 15–150 minute windows with measured KWS phase power, physical grid/voltage qualification, continuity, stable equipment/model/source/ventilation, and a measured return probe. A room temperature is evaluated only at the two observed endpoints. KWS power is integrated from qualified tick samples into full five-minute intervals. Partial edge intervals use the preceding measured power with qualified bracketing states. Changes in mode, known manual extra heat, uncertain physical inputs, outages, conflicting rooms, gaps and restarts break the evidence.

Solar uses the existing issue-time hourly cache and Astral geometry. It keeps a verified anchor across an unchanged primary reading, without treating asynchronous secondary reports as agreement. Solar comparison uses later independent days and the same stable daytime idle windows for zero, 0.283 commissioning and fitted gain. Comfort credit is always false. The thermal fit searches only `floor_air` and `room_loss` within ±1% against qualified data, with original model bounds. Boiler heat gain, floor decay and fireplace gain remain fixed in this candidate.

A small private, checksummed version-1 file retains at most 60 days, 500 completed windows, 4,800 physics steps, eight issue-time snapshots, and 512 KiB total. A damaged file is preserved and the shadow result is withheld. Per-boot evidence prevents incomplete windows and forecasts from being replayed through downtime. Finished forecasts retain paired error aggregates, energy and execution evidence. A prospective observation requires a source-qualified physical report within 60 seconds of the frozen horizon and measured execution consistent with the frozen plan. The candidate predicts a temperature adjustment on the identical frozen heat and weather inputs. Upper-risk, comfort and after-stop values are diagnostic model estimates. The candidate stays isolated even after prospective quality gates pass. Future review additionally requires ≥14 calendar days of qualified observations, both 1h and 3h gates, constrained strata, and explicit approval with the existing v6 and per-application/daily bounds.

### Identical synthetic input

| Metric | Legacy | Shadow windows |
|---|---:|---:|
| Eligible independent minutes | 80 | 4,800 |
| Old selected pairs / new disjoint windows | 16 | 64 |
| Verified primary reports present in input | 160 | 160 |
| Qualified window days | 16 | 16 |
| Measured heating / idle minutes in new windows | — | 1,920 / 2,880 |

The fixture contains deliberately spaced 60/120-minute primary measurements and short legacy pairs. The ratio tests execution on these inputs. Field eligibility remains unmeasured. Main legacy audit labels were 960 legacy-quality, 896 repeated, 896 stale, 416 daytime idle, 79 spacing/gaps and 1,055 secondary-unconfirmed. The sidecar rejected 15 expired anchors. Counts are multi-label. The tests additionally inject manual energy, power estimates, KWS loss, ventilation, fire, grid/voltage, duplicate data, mismatched equipment, forecast leakage and restart gaps. Source timestamps, off/missing evidence, and whole-day splits remain explicit.

The first 12 days trained the synthetic model and the last four were held out. The thermal endpoint MAE was 0.00283 °C → 0.00000 °C on eight windows generated by the known thermal model. The separate solar holdout was 0.10486 °C zero, 0.04138 °C commissioning and 0.00151 °C shadow on eight synthetic windows. These fixtures validate causality and selection.

| Frozen horizon on 16 later synthetic days | v6 MAE, °C | Shadow MAE, °C |
|---|---:|---:|
| 1 h | 0.00304 | 0.00241 |
| 3 h | 0.00609 | 0.00267 |
| 6 h | 0.01040 | 0.00242 |

The later fixture adds deterministic measurement perturbations. The cold/limited 1h stratum rejects the overall prospective gate, even though aggregate MAE improves. The replay command prints bias, median, p95, per-stratum counts, plan versus measured energy and runtime minutes, upper-risk/comfort event counts and after-stop shift. The owner's prior audit reported 0.283/0.732/1.127 °C for frozen 1/3/6h. That report used 0.5.16 and serves as historical context. Newly measured real frozen old/shadow, held-out bias, real eligibility and prospective accuracy are **unavailable**. The HA connector exposes state and Recorder views but the required private telemetry and exact issue-time snapshots were unavailable over the authorized channel.

## Regression and resource verification

- Exact packaged v0.5.17 outputs match in `scheduled`, `unknown`, `spring_dst`, and `autumn_dst`, including both 192-wide searches, fine trajectories and 3/6/9 kW advice. The runtime benchmark also compares the actual control action sequence across disabled, observe and candidate on the native fixture. The existing test suite has specific manual KWS, phase-loss, energy, handoff, outage, ledger, writer, stale-result and temperature protection cases.
- The isolated tests exercise shadow return values, private-file idempotence and corruption, forecast chronology, DST and observed numeric primary changes. Local checks passed 739 Python tests and 67 JavaScript tests. Both native smoke scripts passed with HA 2026.11.0 in a disposable environment. Package comparison confirmed byte identity for 16 core algorithm, worker, actuation and safety paths. The Draft PR links to the repository test matrix.

### Pinned worker, Xeon Platinum 8573C

Ten warm, serial, alternating runs after warm-up, CPU via `process_time` and wall via `perf_counter`. Times are seconds.

| Packaged operation | Base CPU median / p95 | Candidate CPU median / p95 | Base wall median / p95 | Candidate wall median / p95 |
|---|---:|---:|---:|---:|
| Stable plan | 1.2129 / 1.3254 | 1.2107 / 1.5679 | 1.2139 / 1.3255 | 1.2117 / 1.5681 |
| Power advice | 2.7298 / 4.6067 | 2.7692 / 2.9909 | 2.7301 / 4.6073 | 2.7705 / 2.9910 |
| Three plans plus one advice | 5.2703 / 7.4974 | 5.3232 / 5.7162 | 5.2724 / 7.5001 | 5.3301 / 5.7180 |

Worker peak RSS 29,280 → 29,284 KiB. The worker and its imported numerical modules are byte-identical. The unpinned repeat also missed the strict plan p95 threshold (1.2834 → 1.5023 s). The pinned plan p95 was +18.3%, despite similar medians. With ten observations p95 is the maximum. The follow-up below uses longer runs to reduce sensitivity to a single sample. The test workload and timing are separate from the live HA readings.

### Native 15-second control fixture and sidecar

Times below are milliseconds, with ten groups of twenty warm ticks, plus ten warm aggregation and scheduled shadow jobs. The fixture uses isolated HA-free plumbing and does not include live HA state transport.

| Operation | CPU median / p95 | Wall median / p95 |
|---|---:|---:|
| 0.5.17 tick | 2.007 / 3.441 | 2.009 / 3.599 |
| Shadow package, disabled tick | 2.004 / 2.610 | 2.006 / 2.613 |
| Observe tick | 2.053 / 2.824 | 2.055 / 2.827 |
| Candidate tick | 2.102 / 3.086 | 2.104 / 3.089 |
| Observe aggregation | 0.020 / 0.027 | 0.021 / 0.027 |
| Candidate aggregation | 0.020 / 0.109 | 0.021 / 0.110 |
| Observe scheduled job | 66.84 / 70.99 | 66.85 / 70.99 |
| Candidate scheduled job | 76.62 / 82.13 | 76.63 / 82.13 |

The largest within-process additional resident memory after ticks was 4,028 KiB and the measured peak increase was 4,232 KiB. The candidate sidecar used 159,708 bytes. The configured cap is 524,288 bytes. Earlier separate-process tick runs had materially noisier p95 values, so these isolated timing results should be repeated on a representative stable host before inferring a field overhead bound.

## Follow-up measurement

The benchmark now supports `--shared-worker` to alternate both packaged revisions
inside a single spawned process. The existing invocation still measures separate
workers with independent RSS. The shared peak RSS reflects both revisions loaded
in one process.

Twenty timed runs per revision after warm-up, pinned to CPU 2. The scheduled
fixture matched exactly, including both width-192 passes. Times are seconds.

| Operation | Base CPU median / p95 | Draft CPU median / p95 | CPU p95 change | Base wall p95 → draft |
|---|---:|---:|---:|---:|
| Stable plan | 1.1849 / 1.5714 | 1.1796 / 1.4255 | −9.3% | 1.5716 → 1.4256 |
| Power advice | 2.6545 / 3.2030 | 2.6502 / 3.3069 | +3.2% | 3.2039 → 3.3255 |
| Three plans plus one advice | 5.2951 / 6.2660 | 5.1840 / 6.3308 | +1.0% | 6.2674 → 6.3580 |

Twenty further timed runs per revision on CPU 2 used separate workers. With 20 samples, nearest-rank p95 is the second-highest measurement.

| Operation | Base CPU median / p95 | Draft CPU median / p95 | CPU p95 change | Base wall p95 → draft |
|---|---:|---:|---:|---:|
| Stable plan | 1.1621 / 1.2566 | 1.2062 / 1.3758 | +9.5% | 1.2583 → 1.3759 |
| Power advice | 2.6445 / 2.8562 | 2.6474 / 2.8677 | +0.4% | 2.8584 → 2.8678 |
| Three plans plus one advice | 5.1024 / 5.2813 | 5.1548 / 5.8226 | +10.2% | 5.2847 → 5.8304 |

Peak RSS in separate workers (KiB) was `{'baseline': 29280, 'candidate': 29280}`.

The shared-worker test met the threshold for both primary operations. The new separate-worker run exceeded the +5% threshold for stable plan by 4.5 percentage points. The resource gate remains unproven on this host.

The read-only follow-up confirmed the live fixed-6 v6 frozen baseline from the
controller's aggregate values. Recorder returned indoor temperature history but
no historical `sensor.heating_forecast` rows in the queried six-hour window,
even though the current forecast was available. The controller reported a
healthy archive. Field comparison still needs the private telemetry and
selected issue-time forecast snapshots from that archive. Aggregate controller
errors cannot reconstruct the paired old/shadow results.

## Reproduction and rollback

```sh
git worktree add --detach ../base517 v0.5.17
make check test
PYTHONPATH=tools python tools/test_production_ha2026.py
PYTHONPATH=tools python tools/test_native_v1_ha2026.py
python tools/replay_adaptive_learning.py
PYTHONHASHSEED=0 taskset -c 2 python tools/benchmark_planner.py ../base517 --repeat 10 --case scheduled
PYTHONHASHSEED=0 taskset -c 2 python tools/benchmark_planner.py ../base517 --case scheduled --repeat 20 --shared-worker
PYTHONHASHSEED=0 taskset -c 2 python tools/benchmark_planner.py ../base517 --case scheduled --repeat 20
python tools/benchmark_planner.py ../base517 --repeat 0 --case unknown spring_dst autumn_dst
PYTHONHASHSEED=0 taskset -c 2 python tools/benchmark_adaptive_runtime.py ../base517
```

Select an available equivalent CPU for affinity. HA smoke commands require the HA 2026.11.0 test environment. A local owner-approved telemetry JSONL can be audited with `tools/replay_adaptive_learning.py --telemetry PATH`. Its output is aggregate only. Legacy rows without independent provenance are deliberately excluded by the adaptive window builder.

Potential deployment needs a stable-host p95 recheck and owner review of read-only field evidence. An approved observation rollout would start disabled, then move to observe after smoke. Setting the option back to disabled cuts the sidecar hooks. Verify v6 and the original energy history immediately after an authorized change. If an approved package rollback is needed, reinstall the v0.5.17 Marketplace package in a safe window and restart only with separate authorization. Retain private evidence independently of production storage. Activation would need a future explicitly authorized change. The present branch has no promotion entry point.
