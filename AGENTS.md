# Working on Heating AI

Answer the owner in Ukrainian. GitHub is the source of controller code; live HA is the source of current settings and measurements.

Run `make check test` before committing. Add a failing regression test before a behavioral fix. Keep configuration, thermal coefficients, telemetry, energy counters and manual-control protections intact unless explicitly in scope. Never publish state.json, telemetry, credentials, full site configuration or Recorder databases.

Production runs inside HA Core as the `heating_ai_legacy` native integration (`custom_components/heating_ai_legacy/production.py`). The nine original V1 algorithm/controller modules are maintained under `apps/heating_ai/` and adapted into `native_controller/` at Marketplace package build time. Edit `frontend/src/*`, then run `python tools/build_ui.py`; never hand-edit generated `frontend/heating-panel.js` or `apps/heating_ai/heating_ui_assets.py`. Fake HA I/O and numerical tests do not prove physical relay operation.

`tools/build_ui.py` also generates `release.json` hashes for all nine V1 modules; keep generated artifacts synchronized after Python or frontend changes. Use `python -m pip install -r tests/requirements.txt` for development tests, plus the HA-native smoke and browser CI gates.

The old HACS AppDaemon update coordinator and its generator were retired after native migration. Do not generate or re-enable AppDaemon heating update automations. The native Marketplace installation is updated through Marketplace and an HA Core restart only after the owner's separate authorization. Do not stop shared AppDaemon: it still serves unrelated apps.

For production work read live HA states and GitHub code, keep a rollback path, and verify mode/armed, heartbeat, safety guards, model and plan after an explicitly approved installation. Never restart HA Core, HA OS, Proxmox or shared apps without the owner's separate permission. Never execute code on HA through File editor's command endpoint.

Do not edit Sky Radar, inverter/grid detection, energy history or unrelated automations. Return-water temperature is a stored-heat proxy, not floor-surface temperature. Do not simulate grid availability in production.

Cut a release by increasing VERSION and updating CHANGELOG.md in a tested commit. The release workflow publishes only after the main-branch push passes CI. Check the exact released commit. Do not overwrite an existing release/tag.

On 2026-11-18 live HA reported native V1 v0.5.11 loaded, fresh heartbeat, and Marketplace installed/latest v0.5.11; this is a dated checkpoint, not a substitute for a fresh read. `docs/installation.md` and `docs/automatic-updates.md` describe archived AppDaemon deployment only; use `docs/marketplace-v1-install.md` for native installation and rollback.
