# 0.5.3 backend release / UI handoff

Release scope: PR #45 only. UI redesign PR #44 and its branch are untouched.
The deployed 0.5.2 manifest and native release fingerprints were read directly;
installed critical_ledger.py matches the v0.5.2 implementation (trailing newline
normalization only in the local read copy). SQLite schema stays v1.

Final review found two installation-specific defects and fixed them before release:

- KWS selection restored from persisted system settings must be read from runtime
  `cfg`, not original import `args` (which has no KWS setting on this installation).
- On integration reload, current HA grid-return transition timestamps can qualify
  an already acknowledged post-return KWS contactor. A subsequently observed
  outage/unknown grid still requires new post-return evidence. No relay toggles.

Validation: 654 Python tests, 53 JS tests, package round-trip, existing installed
0.5.2 journal implementation -> new safe STOP recovery -> old reader compatibility,
SQLite quick_check and preserved UNKNOWN evidence. Exact final CI and merged main
SHA are recorded in PR #45. Local deterministic package:

- name: `heating_ai_v1_marketplace.zip`
- version: `0.5.3`; 60 files; 542583 bytes
- SHA-256: `34b65c02fbf3f7eac72855ea24d80a1d2f38974acf2990e933e82785adc78ef1`

The main release workflow publishes that immutable version only after Tests pass.
Compare the published asset digest to the above before installation.

## Approval / recovery plan

A fresh full HA backup completed successfully with HA configuration/database and
no failed agents; its identifier is kept in the operator handoff, not public git.
It includes the controller data outside the code directory. Protected backup
restoration needs its encryption key and separate approval. No restore was run.

Primary code rollback: Marketplace reinstall the existing v0.5.2 release package
SHA-256 `f5e4cdc57239c8892b91574662ab93e780210cf5448cc59d69542000c90c05ad`.
Keep the live private data, learned model, energy anchors, history and command
journal. Do not replace the journal with an older backup or erase its evidence.
The old reader accepts the new schema-v1 journal; old fence-recovery defects may
return, so rollback is containment, not proof of restored automatic control.

The native Marketplace installer writes code and reloads integrations. Ask for
permission to install AND explicit permission for Heating AI integration reload
before calling it. Do not restart HA Core, OS, Proxmox or any App. If reload is
unsupported or asks for a Core restart, stop and seek that separate decision.

After approved installation: verify the running manifest/module hashes, fresh
success heartbeat, journal reconciliation stages, KWS readiness, valid plan and
manual/safety gates. Physical heating requires fresh measured KWS phase watts,
not an accepted service or thermostat mode. Allow only normal authorized demand;
no fault injection on the live boiler. Do not declare recovery before those
checks. Never force KWS OFF/ON or alter grid/TG/third-party automations.

## For the UI agent (PR #44)

1. Fetch and rebase `redesign/modern-ui-ux` onto the merged main SHA recorded in
   PR #45. Do not cherry-pick old #31/#37 or overwrite backend files.
2. Keep 0.5.3 backend and manifest hashes. If publishing UI as another release,
   use the next version and regenerate assets/hashes with the UI branch's builder.
3. WebSocket/settings/history contract remains `[1, 0]`. No required request field,
   entity, settings or array position was removed. Existing 0.5.2 UI behavior is
   preserved; the frontend diff here is only the 0.5.3 version marker plus its
   generated Python bundle/digest.
4. Additive diagnostics: `reliability` computation/equipment/authority/power grades,
   `ledger_recovery`, `equipment_readiness`, `comfort_available`. `control_state`
   may be `blocked`; display its human reason. Model validation, permission,
   thermostat echo and measured heat remain distinct.
5. KWS/ledger gates, manual priority, cumulative energy accounting and recovery
   are backend authority. UI must not bypass them or reset the journal. Bounded
   comfort bias remains opt-in and disabled by default.
6. Run full Python/JS + HA 2026 + browser tests after rebase; coordinate any later
   installation and reload separately with the operator.
