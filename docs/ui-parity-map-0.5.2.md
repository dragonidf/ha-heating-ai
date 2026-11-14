# Heating AI UI parity map — v0.5.2 redesign baseline

Updated: 2026-11-14. This is the regression contract for the 0.5.x redesign.

## Baseline and non-negotiable behavior

- Source commit: `9fcc5c05c8ff0a962651cf0c661500a6851ed3d6`.
- The browser receives one generated local JavaScript asset. There are no
  external fonts, UI libraries, CDNs or extra runtime module requests.
- Baseline asset size: 203,049 bytes before source modularization.
- Baseline scheduling: one 60-second presentation refresh interval, one
  `ResizeObserver`, no high-frequency UI polling. History is cached for at
  least 60 seconds. Comparison retries are bounded by the existing 25-second
  request deadline and resume only after visibility/network recovery.
- HA writes remain allow-listed by entity domain and service. Settings use the
  authenticated versioned API with revision conflict handling, staged drafts,
  explicit save, acknowledgement and undo.
- Offline plans are informational. The UI must never bypass controller safety,
  grid/voltage checks, manual AUTO, command ownership or ledger fencing.

## Shared shell

- Heating AI identity, controller heartbeat, controller mode and HA menu.
- Three destinations remain available: Overview, History and Settings.
- Human-readable controller warning/error area with status semantics.
- Light/dark HA theme variables, container-query layout and 44 px controls.
- No sensor-provided string may become executable HTML.

## Overview

- Current room temperature, comfort zone, target and outdoor temperature.
- Current decision, controller/observation mode, next heat and expected minimum.
- Future-only forecast graph with 24-hour and extended horizons, comfort band,
  plan identity, weather completeness and conditional reliability warnings.
- Plan energy versus measured/known energy; unknown values stay unknown.
- Planned heating sessions, conditional sessions and absolute dates.
- Boiler: physical mode, measured/calculated power semantics, request/state,
  selected profile, pending profile and measurement diagnostics.
- Fireplace and circulation: five fireplace phases, temperatures, trend,
  actual/requested fan state, level and human-readable reason.
- Recorder history chart: 6/12/24 hour ranges, room/return/outdoor visibility,
  boiler/fireplace lanes, pointer/keyboard inspection and empty/error states.
- Native HA energy and weather cards, monthly energy/budget and weather refresh.

## History

- Plan-versus-fact temperature comparison for 1/7/30 days and a custom
  timezone-correct date range.
- Zoom in/out/reset, pointer and keyboard inspection, comfort envelope,
  immutable advance forecast, actual series and plan/boiler/fireplace/grid
  evidence lanes.
- Missing advance forecasts, partial coverage, archive warnings and source age
  remain explicit; gaps must never be interpolated into false facts.
- Model version/validation, temperature and energy errors, measurement coverage.
- Controller quality horizons, daily report and archive/storage health.
- Heating-cycle history with initial plan, actual/partial energy, execution and
  unexplained excess.
- AI report, learning result, model actions, modulation, floor/weather facts,
  plan execution, power/ventilation diagnostics, decision journal and advanced
  parameters.

## Settings

- Basic controls: operating mode, command arming, one-time restore request,
  day/night comfort, sleep window, heat strategy/window, circulation mode and
  levels.
- Equipment: boiler profile, native fallback setpoint, monthly budget, session
  limit, ventilation bindings and device-friendly entity choices.
- All 46 expert schema parameters remain available with current min/max/step,
  units, options, help, JSON validation and model/config targets.
- Existing confirmation dialogs for command arming, restore and model rollback
  remain mandatory.
- Draft, save, persisted revision, conflict, delayed acknowledgement and undo
  behavior remain intact.
- Non-admin/read-only states must stay understandable and must not create writes.

## Required verification

- Pure frontend tests cover formatting, null/unknown semantics, safety wording,
  history decoding, DST, comparison transport and settings revisions.
- Chromium and WebKit cover 320, 360, 390, 430, 600, 620, 768, 1024, 1440 and
  1920 px, touch rotation, light/dark themes and container width changes.
- Accessibility covers 100/125/200% text scale, keyboard controls, focus,
  44 px targets, no horizontal page overflow and semantic live status.
- Screenshots are required for Overview, History, Settings, light/dark,
  blocked/error state and admin settings at 390 and 1440 px where applicable.
- Final evidence must compare asset size, recurring timers, HA request counts
  and render behavior against this baseline.
