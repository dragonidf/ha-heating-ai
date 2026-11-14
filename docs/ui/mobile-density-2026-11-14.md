# Mobile Heating AI layout — review candidate

Base: main `5706409034c69afa603e6aeef92b2b9d4d9d6173`, installed 0.5.6 verified read-only.
Own worktree: `ui/mobile-density-20261114`. No production write, install, reload or restart.

## Design contract

A phone overview answers: current comfort, next heat, future temperature, and
which source is actually working. Keep HA theme tokens, calm flat surfaces,
44px touch targets, keyboard access, and honest unknown/stale/conditional states.
References: [HA sections](https://www.home-assistant.io/dashboards/sections/)
and [Android compact list/detail layouts](https://developer.android.com/design/ui/mobile/guides/layout-and-content/common-layouts).
Use their density and progressive-disclosure principles, not their branding.

- Two balanced columns in the current-state card; room temperature, target,
  forecast minimum, controller state, mode, outside temperature and comfort.
- One pair for actual past-24h energy and planned next-24h energy. The latter
  uses the main 24h forecast even when the chart displays a longer horizon.
- One network value in the header. Remove the four redundant metric tiles.
- The nearest heat remains visible. Native keyboard-accessible “Більше” expands
  the whole plan in place; open state survives a data update.
- Shorter forecast geometry; restore residual fireplace heat as its own lane.
  No duplicated legend, minimum/energy blocks or schedule-until footer.
- Boiler and fireplace/circulation share one row. Actual power/return and selected
  vs recommended profile stay on the boiler; deficit/quality detail is collapsed.
  Measured heating remains separate from calculated power. Recommendations do
  not operate equipment. Fireplace phase, temperature and fan state stay visible.
- Shorter temperature/history graphs and tighter history statistics. Monthly
  consumption/budget becomes one inline line rather than a standalone card.
- Displayed entity values use native HA more-info events. Historical graph
  samples remain chart inspection, not invented per-sample HA entities.

## Scope and validation

Only UI sources, generated JS/Python UI bundle/fingerprints, UI tests and docs.
No backend algorithms, command gateway, journal, protection, energy accounting,
settings schema, WebSocket API or polling/subscriptions are changed. Version
metadata stays 0.5.6 for this review; release numbering follows design approval.

Local full `make check test` and CI cover existing backend checks as regression
protection. Browser checks cover 320–1440px, 100/125/200% text, 44px controls,
keyboard plan disclosure, residual-fireplace lane, daily horizon distinction,
configured entity links and absence of equipment calls. Screenshots use synthetic
HA fixtures, never production claims or private configuration. CI screenshots
are review artifacts. Installation requires the owner's approval after preview.
