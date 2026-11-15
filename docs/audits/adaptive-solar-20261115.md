# Adaptive model / solar — review candidate 0.5.8

Base: `46948c6864091377103bf4ad86131021f44fc3f2` (main, V1 0.5.7).
Scope: preparation only. No HA access, production file/configuration/state
changes, installation, restart, merge or release. No private telemetry was
supplied or retrieved. Current v6 coefficients are not replaced by this work.

## Implemented and bounded

| Requirement | Implementation / evidence |
|---|---|
| Slow physical adaptation | `learn`: alpha .20 after a bounded raw proposal; first extreme step is 4%, cumulative local-day limit 5%. `validate_candidate` uses the same daily baseline and retains 2% holdout improvement, physical bounds, insufficient-data rejection, prospective hourly confirmation and rollback. No recency reweighting without evidence that it improves independent days. |
| Fast response | `observe_residual` / `residual_at`: signed diagnostic rate after two independent fresh reports spanning 5–90 minutes, ±.4 C/h, half-life 90 min, zero on another local day. Known measured OFF/KWS, fresh chosen outdoor source, room cross-check, inactive fireplace and known quiet ventilation are required. Gaps/quality loss clear the measurement anchor and let the old rate decay. Model changes clear the old correction. |
| No unverified free heat | **Residual comfort credit is not activated.** The signed rate is diagnostic; a positive rate can contribute to the separate upper-risk estimate. Real withheld-day 1h/3h validation remains necessary before enabling an adaptive comfort correction. Negative residual cannot manufacture solar credit. |
| Seasonal sun | `solar_context` prepares five small Astral day records once per local day/location. The numerical loop uses solar-noon declination/height geometry and actual sunrise/sunset, not HA API or fixed 07:00–18:00. No geometry means no solar credit and no new physical-model training with unknown daylight. Astral is already a HA dependency; no new runtime requirement/integration. |
| Cloud / weather freshness | `weather_event` retains valid hourly cloud coverage. Smooth attenuation with condition fallback; solar credit only inside that row's one-hour interval. Current cloudiness is used only by the current observer, never copied to future rows. Stale weather gives no guaranteed solar credit. |
| Solar fitting | Existing multiday/holdout gates retained. Old fixed-hour observations remain stored but do not certify the new geometric factor. At least 3 eligible days / 12 stable observations; a commissioned coefficient moves by at most 10% per local day. Evaluate the actual bounded proposal against the current coefficient on holdout, not only against zero solar. No increase of .8 C/h external-gain cap. Validated ventilation loss is subtracted before classifying unexplained gains. |
| Temperature disagreement | `room_source_check`: configured existing primary/fireplace/thermostat values only; age <=5 min, time spread <=3 min, other sources agree within .5 C, primary deviation >1.2 C, persistent independent reports >=3 min. Pending suspicion and confirmed disagreement are distinct. Both prevent training/solar certification; neither averages or replaces the controlling temperature. |
| STOP / retained plan | `coast_trajectory`, `_plan`, and `replay` use the same interval-start forecast inputs and separate upper trajectory. Upper risk is checked during heat and for 3h afterward; comfort output stays separate. First-block STOP and later retained-session overshoot both reject unsafe candidates. Hypothetical unvalidated tomorrow sunshine receives no credit. |
| Frozen post-STOP validation | Save the comfort and risk peaks at STOP, measure the subsequent 3h maximum without rewriting the saved forecast. A stale primary report, gap, reheating, changed model or source disagreement invalidates scoring. Preserve observed peak, frozen prediction and reasons for diagnosis. |

`heating_stability.py` needs a small `replay` change: otherwise an old held
session would bypass the new planner's upper-risk check. `heating_recovery.py`
changes only post-STOP records/quality, not commands, reservations or counters.
No ledger, safety interlock, voltage/grid policy, 3/6/9 configuration, fan or
thermostat control, energy integration, helper/entity creation, dependency list
or polling changes. Frontend changes are release-version metadata only.

## Accuracy: facts and limits

The supplied incident description is not a complete replay dataset. The 21.03 C
saved peak versus the 10:00 observation 22.4 C establishes at least 1.37 C
underprediction of the primary sensor's 3h peak. It is **not** a 3h MAE, nor
proof that the whole house reached that temperature. The 24.9 C value at 11:34
is outside the 07:00–10:00 window; nearby fireplace/thermostat values suggest a
possible local source effect, not proven solar-only or slab-only heat.

| Comparison | 1h | 3h |
|---|---|---|
| Real withheld-day old/new MAE | Not available | Not available |
| Isolated synthetic +/- .2 C/h drift, unchanged comfort credit | .20 C both | .60 C both |

The three synthetic held-out days test sign, decay, bounds and the deliberate
absence of unverified comfort credit; they do not establish household forecast
improvement. The reported v6 rejection (.01660 old vs .01760 candidate MAE)
remains rejected; a dedicated test preserves that rule even for a smoothed
proposal. Existing coefficients remain the fallback when there is no proven
improvement. Do not claim the 15 November overheating incident is fixed.

## Verification

- RED regressions reproduced missing season/cloud/source/bias safeguards and
  20% physical steps before the changes. An additional first-block STOP case
  reproduced acceptance of an unsafe 1-minute held heat interval before its fix.
- Old solar fit counterexample: .30 -> .36 despite a better .30 current holdout;
  revised fit keeps .30. Freshness, restart/quality/model changes, local-day
  expiry, source disagreement and frozen STOP history are covered.
- Targeted tests: 88 PASS. Final `make check test`: 684 Python + 58 JS PASS, no skips.
  A final outdoor-freshness RED test also showed that stale outdoor values
  could enter thermal samples; sample quality now excludes those intervals.
- Existing Astral 2.2 development check: a synthetic Boryspil-area location gives
  15 November sunset 16:10:44 Europe/Kyiv; 17:00 solar factor exactly zero.
- Marketplace build: 60 files, 570629 bytes; source/native numerical logic
  verified by the standard builder and package tests. SHA256:
  `e8eddf6b9aec954eb0eb287388446690fbc34debe2c4f6cdcf5f77a3cc759f52`.
- CI: recorded in the Draft PR. No manual browser tests or HA tools were used.
  Existing repository workflows run normally when the PR is opened.

Remaining acceptance blocker: real timestamped telemetry and saved issue-time
forecasts for independent 1h/3h days. Keep this as a Draft PR; do not install,
publish or enable residual comfort credit on the strength of synthetic tests.
