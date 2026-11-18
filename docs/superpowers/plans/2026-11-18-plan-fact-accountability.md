# Plan vs fact — evidence-preserving repair (2026-11-18)

> **For agentic workers:** Apply superpowers systematic debugging, test-driven development, verification-before-completion and Ponytail. Execute only on this isolated branch, never in live HA.

**Goal:** Make the already-calculated plan's initial energy and session reference observable and persistent; label actual KWS energy and unknown attribution truthfully without changing heat control or meter totals.

**Spec:** `docs/plan-execution-accountability.md` and current HA read-only findings, 2026-11-18.

**Root causes confirmed:**
- Background planner calls `stable_plan` (includes `apply_search_diagnostics`), then creates an accepted fresh `replay`. The replay only calls `attach_reserve`, so `morning_reserve.end_cycle_planned_kwh` is absent. `cycle_budget` returns `{}` before saving `initial_plan`/`initial_sessions`/`latest_plan`. The current HA forecast has `cycle_energy={}`; three history cycles have no baseline.
- KWS counter increments span longer than 45 s. `observe_cycle_energy` conservatively places those increments in `transition_kwh` (cannot assign them to AUTO/manual), while the UI misleadingly says no independent boiler meter.
- The quality summary hardcodes `energy_basis=calculated_meter_balance`, even when ledger says `kws_counter_delta`.

## Chosen scope (smallest safe solution)
- Restore existing `apply_search_diagnostics` contract only for the accepted fresh background replay, before `cycle_budget`; never accept stale worker output directly.
- Preserve historical missing baselines as **missing**. Do not reconstruct or change private archives, controller commands, guards, model, or meter counters.
- Keep all KWS measured kWh. Keep the unclassified `transition_kwh` unless evidence truly supports attribution; explain it as *unknown mode*, not automatic vs manual.
- Report meter provenance from existing ledger fields; make UI wording depend on evidence. No new dependencies or configuration.

## Task 1 — lossless accepted replay
- [ ] Add RED test in `tests/test_background_control.py`: accepted replay contains cycle energy reference and `cycle_budget` persists the first immutable plan, including a later refreshed plan.
- [ ] Run test RED in GitHub Actions on the isolated branch and inspect the failing assertion.
- [ ] One minimal change in `apps/heating_ai/heating_climate.py`: invoke existing diagnostic helper on the accepted fresh replay.
- [ ] Run Python and native HA smoke tests GREEN; verify no heat decisions change.

## Task 2 — measured energy source and UI
- [ ] Add RED report test in `tests/test_qa_energy_quality.py` for `kws_counter_delta`; keep `independent_energy_kwh=null` absent a *second* meter.
- [ ] Amend only summary reporting; no change in cumulative energy allocation.
- [ ] Add UI test: KWS is named; unclassified `transition_kwh` is visible; absent baseline remains unknown, never zero.
- [ ] Update `frontend/src` and regenerate exact bundle, Python assets and release hashes using `tools/build_ui.py`.

## Task 3 — verification and handoff
- [ ] Full `make check test`, CI Python 3.12/3.13, HA 2026.11 native smoke, browser Chromium/WebKit, ZIP parity all GREEN.
- [ ] Separate diff review: no changes to safety, decisions, KWS energy sums, leases, config, control ownership.
- [ ] Create PR for review, **do not install or restart HA**, do not backfill missing past evidence.
- [ ] Recheck live HA read-only after GitHub work.

## Two-step rollout boundary (owner decision, 2026-11-18)

**Step 1 — ANALYTICS ONLY:** accepted background replay regains the missing cycle-energy estimate and first immutable reference, while tagging newly captured baseline as `report_only`. Such a baseline remains visible in reports but is not passed to the planner via `cycle_energy_budget` and does not force a recompute on budget exceedance. Previously persisted baselines without this marker retain their existing planner behavior. Preserve KWS counter totals and genuinely unclassified energy. Tests explicitly pin both the old baseline behavior and the reporting-only boundary.

**Step 1 verification gate:** reproduce RED failures first; pass 3 new regression assertions, existing Python suites on 3.12/3.13, HA native smoke, browser UI/UX, ZIP/hash integrity; review diff for zero actuation/physical command, safety, model, writer, and meter-total changes. Commit/merge code only after verification; no VERSION change, Marketplace release, HA installation or restart. Check actual HA read-only.

**Step 2 — PLANNER POLICY REVIEW, SEPARATE APPROVAL:** after Step 1 is verified and frozen, evaluate whether/how a new cycle energy budget could improve schedule selection, using separate tests and a separate branch/PR. Do not turn the reporting-only baseline into a live energy constraint without a new explicit owner decision, regression proof and deployment consent. Historic missing plans stay unknown.

**High-risk exclusions:** no inferred AUTO/manual energy splits across sparse samples; no rewriting old historical records; no version bump or release/install.
