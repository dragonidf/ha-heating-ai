# Working on Heating AI

Answer the owner in Ukrainian. GitHub is the source of controller code; live HA is the source of current settings and measurements.

Run `make check test` before committing. Add a failing regression test before a behavioral fix. Keep configuration, thermal coefficients, telemetry, energy counters and manual-control protections intact unless explicitly in scope. Never publish state.json, telemetry, credentials, full site configuration or Recorder databases.

The app is `apps/heating_ai/`; it is deployed on AppDaemon, not in HA Core. All nine Python modules are one HACS app. Keep the local app YAML outside the HACS-managed directory. Edit frontend/heating-panel.js, then run python tools/build_ui.py; do not hand-edit heating_ui_assets.py. Tests use fake HA I/O and numerical fixtures; they do not prove physical relay operation.

For production work use the owner's ha-climate-controller skill when available. Read fresh modules and states, back up code before replacing it, pause model commands for a multi-file rollout, restore the original armed/mode state, verify a fresh cycle and planning_policy_version. Do not restart HA for a Python-only update. If File editor access is expanded, restore enforce_basepath=true and stop it afterward. Never execute code on HA through File editor's command endpoint.

Do not edit Sky Radar, inverter/grid detection, energy history or unrelated automations. Return-water temperature is a stored-heat proxy, not floor-surface temperature. Do not simulate grid availability in production.

Cut a release by increasing VERSION and updating CHANGELOG.md in a tested commit. The release workflow publishes only after the main-branch push passes CI. Check the exact released commit. Do not overwrite an existing release/tag.

The initial production patch was installed directly on 2026-10-16, before HACS path migration. Until that migration is verified, a HACS download alone does not update the running app. See docs/installation.md.
