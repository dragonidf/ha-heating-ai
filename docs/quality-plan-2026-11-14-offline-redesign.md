# Quality plan — offline plan recovery and UI redesign

Updated: 2026-11-14, Europe/Kyiv.

## Product goal

Deliver a working Heating AI controller with a reliable planner and all current
features preserved. The controller must not delay Home Assistant startup or
shutdown, create avoidable polling/process load, or degrade UI responsiveness.
The final release must combine a verified offline-plan recovery with a modern,
compact design and a frontend structure that can evolve without editing the
controller's physical-control code.

Production Home Assistant is read-only during development. Installation,
reloads and restarts are forbidden until both work streams are complete,
GitHub CI is green and the owner explicitly proceeds with installation.

## Ordered work streams

### 1. Offline Plan Recovery — first, isolated PR

- [x] Reproduce the missing native-background forecast with grid off/unknown,
  unavailable KWS and valid thermal inputs.
- [x] Distinguish worker failure, timeout rejection, changed inputs,
  replay rejection and publication failure with tests.
- [x] Make the 60-second threshold diagnostic rather than an unconditional
  rejection of a completed result.
- [x] Accept a late result only when its exact input key still matches and a
  fresh replay with current thermal inputs and policy succeeds.
- [x] Preserve zero HEAT commands while grid/voltage safety is not confirmed.
- [x] Keep worker concurrency bounded and avoid duplicate jobs or polling.
- [x] Publish bounded rejection diagnostics without new Recorder entities.
- [x] Run focused tests and full GitHub CI on the implementation commit.
- [x] Merge the focused PR after its documentation-only head is green.

### 2. Modern UI/UX Redesign — after recovery is green

- [x] Record a UI parity map for every current control, chart, setting and
  administrative function before moving components.
- [x] Split the monolithic frontend into presentation/view-model modules and a
  thin HA transport/controller shell; preserve the generated single asset.
- [x] Rebuild Overview, History and Settings according to the attached TZ,
  without changing planner, model, safety, energy or actuator semantics.
- [x] Preserve all history ranges, forecast interactions, 46 schema settings,
  helpers, permissions and confirmation/revision behavior.
- [x] Add semantic statuses, compact heat-source cards, responsive layouts,
  keyboard/accessibility support and local settings search.
- [ ] Add Chromium/WebKit coverage at required widths, long-open tests,
  light/dark screenshots and performance comparison.
- [ ] Regenerate UI/release assets and run full GitHub CI.

## Release gates

- No HA writes, installs, reloads or restarts during development.
- All behavioral changes start with a failing regression test.
- `tools/build_ui.py` remains the only generator of bundled UI assets and the
  nine-module release manifest.
- No new periodic HA polling, Recorder entities, unbounded retries or worker
  processes.
- Offline planning must remain informational; physical HEAT stays blocked by
  existing grid, voltage, ownership, manual-mode and ledger protections.
- A release is ready only after both PRs are merged, exact-commit GitHub CI is
  green, assets are synchronized and the final parity/performance checklist is
  documented.

## Current checkpoint

- Backend follow-up PR #46 passed both GitHub workflows (Python 3.12/3.13,
  native HA 2026.11, package, Chromium/WebKit and browser interaction jobs) and
  was squash-merged as `9874743cb0c72ab09c0abab2c6ccd0fe8f9c60d0`
  (candidate 0.5.4). Production HA remains on its separately installed 0.5.3;
  this work did not install, reload or restart anything.
- Base for the redesign is now `main` at
  `9874743cb0c72ab09c0abab2c6ccd0fe8f9c60d0`. The UI checkout was reconciled
  with every changed backend file before regenerating the bundle, so the 0.5.4
  command-ledger, KWS and package hashes are not replaced by older versions.
- Confirmed root cause: `poll_calculation` marked a plan timed out after
  60 seconds and then discards its completed result solely because
  `timed_out_at` exists. `background_plan` already performs exact-key checks,
  current-input drift checks and a fresh replay, so the unconditional drop can
  cause repeated expensive jobs and no published forecast during blackout.
- Red test: GitHub run `43575118302` failed exactly on late-result acceptance,
  offline publication and missing rejection classification before the fix.
- Green implementation: PR #43 head `dd7ef0d896bb95966cddccc2a8a345161c10b2de`,
  GitHub run `43575757402` passed Python 3.12/3.13, native HA 2026.11,
  generated manifest checks, Chromium/WebKit, accessibility and responsive UI.
- HA remained read-only; no installation, reload or restart was performed.
- PR #43 was squash-merged as
  `9fcc5c05c8ff0a962651cf0c661500a6851ed3d6`.
- UI work continues on `redesign/modern-ui-ux`. The maintainable sources are
  split under `frontend/src/`; `tools/build_ui.py` still emits one local,
  dependency-free `frontend/heating-panel.js` and its Python asset.
- Overview now has a single truthful primary status, a compact next-action
  summary, a 2x2 mobile metric grid, semantic forecast colors, explicit
  conditional-plan presentation and compact side-by-side heat sources.
- History retains 24-hour/7-day/30-day/custom comparison, frozen advance
  forecasts, actual temperature, plan/fact markers, boiler/fireplace/grid
  evidence, zoom and source-quality gaps without adding Recorder load.
- Settings retains all 46 schema fields, HA helpers, revision/conflict checks,
  staged saving and undo; expert search is local and creates no polling.
- KWS readiness, recovered ledger state and physical heating are presented as
  separate concepts. A historical recovered `LedgerConflict` cannot turn a
  currently healthy controller into an active emergency banner.
- Local deterministic bundle check and 56 frontend regressions pass. The
  remaining gates are GitHub full CI, responsive/light/dark screenshot review,
  long-open/performance evidence and final Draft PR documentation.
