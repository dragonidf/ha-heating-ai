# Heating AI V1 planner CPU benchmark

The read-only Home Assistant check on 2026-11-23 confirmed `sensor.heating_ui` = `0.5.15`, runtime `native_v1`, and a fresh controller heartbeat. The GitHub v0.5.15 baseline is `921b091bde23bb055d830bc9fdf9305647e532ae`.

The native integration dispatches through `async_setup_production` → `production.NativeController` → `native_controller.heating_climate.tick_inner`. `background_plan` and `update_power_recommendation` enter `poll_calculation`. `native_bridge.Hass` routes those jobs through its existing spawn pool to `calculation_worker.calculate`, which loads `stable_plan` or `recommend_power` from `native_engine.heating_stability`. Both packaged copies come from the same verified V1 sources under `apps/heating_ai`. The version check establishes the live runtime and version; benchmark input and worker packages are disposable.

The read-only Recorder sample had recomputes at 13:00:56, 13:06:26, 13:12:11, 13:18:11 and 13:23:56 Kyiv time, with a power-advice completion during that interval. These are control telemetry and wall durations. The controlled budget compares three 5-minute plan calls (initial search followed by reference updates) plus one three-mode recommendation for the same fixed inputs. The advice cache, invalidation, worker dispatch and 15-second tick use their existing code.

## Changes

- Resolve weather, solar and upper-risk gains by exact time in a job-local context. The index preserves the last eligible forecast row, including ties and unordered input. Keep separate DST folds and the original wall-clock comparisons at hold deadlines and window cuts.
- Prepare beam substeps, weather, comfort, tariffs and masks before iterating candidates. The minimum-start memo is scoped to one search and its outage/extension masks. Accumulate evening/morning energy in segment order for deduplication.
- Use precomputed ventilation coefficients with the original temperature-dependent risk branch and arithmetic order. Replay retains its original cuts and exact interval tests while using sorted boundaries and a segment coverage index. The three independent power searches share weather/solar/ventilation inputs.

## Reproduction

```sh
git worktree add --detach ../heating-ai-v0515 v0.5.15
python tools/benchmark_planner.py ../heating-ai-v0515 --case scheduled --repeat 5 \
  --profile stable_plan plan replay stress_scenarios recommend_power
python tools/benchmark_planner.py ../heating-ai-v0515 --case unknown spring_dst autumn_dst --repeat 0
make check test
```

The harness builds each revision with the Marketplace builder. It runs one task at a time, using separate spawned processes for baseline and candidate, a warm-up per fixture, and alternating execution order. CPU is `process_time` within the process, and wall is `perf_counter`. With five measured samples, the nearest-rank p95 is the maximum. Profile runs use `cProfile` with a CPU timer and print the top 20 entries by cumulative time, including call counts, separately from timings. Linux worker peak RSS is a process high-water mark.

The fixture uses fixed synthetic weather, outages, solar, fireplace, ventilation and boiler calibration, with representative comfort limits and rounded model parameters based on the read-only check. Outputs are compared in full, including all internal trajectory points, segment boundaries, raw floats, search evidence, reserve/deficit, and power alternatives. Only generated plan UUIDs are made deterministic.

## Measured results

CPython 3.12.14 on Linux x86-64 (Xeon Platinum 8573C). All durations are seconds.

| Operation | CPU baseline median / p95 | CPU candidate median / p95 | Median CPU reduction | Wall baseline median / p95 | Wall candidate median / p95 |
|---|---:|---:|---:|---:|---:|
| Stable plan, initial worker call | 11.7111 / 12.1854 | 1.1250 / 1.1469 | 90.4% | 11.7141 / 12.1896 | 1.1251 / 1.1542 |
| Plan refresh at +5 min | 12.4664 / 13.2588 | 1.1831 / 1.2332 | 90.5% | 12.4690 / 13.2735 | 1.1832 / 1.2355 |
| Plan refresh at +10 min | 0.0450 / 0.0480 | 0.0189 / 0.0238 | 58.0% | 0.0450 / 0.0480 | 0.0189 / 0.0238 |
| Direct planner | 11.9974 / 12.6662 | 1.0831 / 1.1397 | 91.0% | 12.0053 / 12.6714 | 1.0831 / 1.1423 |
| Replay | 0.0443 / 0.0459 | 0.0181 / 0.0208 | 59.1% | 0.0443 / 0.0459 | 0.0181 / 0.0208 |
| Stress scenarios | 0.0469 / 0.0504 | 0.0175 / 0.0198 | 62.7% | 0.0469 / 0.0504 | 0.0175 / 0.0198 |
| Power advice, 3/6/9 kW | 35.0417 / 40.5999 | 2.4552 / 2.5835 | 93.0% | 35.0690 / 40.6431 | 2.4600 / 2.5856 |
| **Worker budget, 3 plans + 1 advice** | 59.7114 / 64.2883 | 4.8030 / 4.9827 | 92.0% | 59.7577 / 64.3542 | 4.8163 / 4.9872 |

The candidate uses 8.0% of the baseline CPU budget at the median (12.4× speedup). The combined wall p95 is 4.99 s versus 64.35 s. Peak worker RSS was 29,456 KiB baseline and 29,276 KiB candidate. The worker pool size is unchanged.

These figures cover the calculation worker on the fixed replay fixture. Whole-controller and whole-host CPU on the Home Assistant mini-PC were outside the measured budget.

## Verification

The scheduled fixture matched the baseline in every run, including both width-192 searches. The +5 min refresh replans and the +10 min refresh retains the previous plan. Three additional golden comparisons matched exactly:

- Unknown schedule and missing weather, with the emergency flag, safety stop, and unavailable expected ventilation duty. This exercised cold/low-solar and two curtailed-power stress cases.
- Spring DST in Europe/Kyiv, with allowed hours and sleep limits around the clock change.
- Autumn DST in Europe/Kyiv, with duplicate and unordered weather rows and independent calibrated 3, 6, and 9 kW profiles.

The exact comparisons include discrete candidate counts, half-hour curves, all fine-grained temperatures, reserve and outage evidence, and the mode selection. The repository's control tests also exercise stale worker results, changed inputs, voltage and grid recovery, sensor loss, manual heating, and the 30-minute morning minimum.

Local `make check test` passed 717 Python tests and 66 JavaScript tests. Both native smoke scripts passed against a disposable Home Assistant 2026.11.0 installation (CPython 3.14.10), using synthetic services.

## Rollback

Close the PR before merging, or revert its commit on the main branch and re-run verification. A production rollback would use the last approved Marketplace package under the owner's separate deployment authorization. The previous package can use the existing storage.
