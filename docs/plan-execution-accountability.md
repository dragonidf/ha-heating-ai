# Plan, execution and temperature evidence

The initial cycle estimate is stored once before heating. Subsequent calculations have a separate current estimate; measured consumption is never added to the initial number. Legacy three-hour baselines retain their provenance. An installation started during heating cannot claim a prestart estimate.

The planner subtracts recorded consumption from this reference. Among trajectories with the same comfort deficit it prefers less reference overrun, before tariff and timing preferences. This is not a hard energy shutoff: an actual forecast temperature violation remains higher priority. Diagnostics retain its time/temperature/limit, observed supply interruptions and mean requested power. These observations do not independently identify causal kWh shares.

The floor observer integrates credible counter deltas or contiguous measured power. It treats return water as a proxy and cannot replace a warmer integrated inventory with a colder pipe. Gaps, resets and implausible counter jumps do not become heating energy. Coefficients and existing counters are preserved. This model estimate is not a floor-surface temperature or an independent energy measurement.

The normal 15-second loop starts scheduled segments without waiting for a five-minute recalculation. Dispatch checks a segment against the current clock after calculation; grid, upper limits, ownership, outage masks and minimum switching times still apply. Repeated unchanged outage metadata no longer triggers new planning.

Temperature comparisons freeze the last forecast issued at least 12 hours before a confirmed heating edge, with at most ten minutes of issue-cadence tolerance. Its values and issue time are copied once. Later plans cannot change it. An unknown start, restart gap or missing advance forecast is explicit missing evidence. Observation gaps break lines; neither series is extended beyond its available coverage. Periods and observations survive reload in private controller state, with a rolling month of comparisons.

The AI tab defaults to a calendar week on one temperature chart, with 24-hour/30-day presets and custom inclusive dates interpreted in the HA timezone (including DST). It shows actual telemetry even when no heating cycle or advance forecast exists. Forecast coverage and each original issue time are explicit. Touch and arrow keys inspect the timeline.

On request the AppDaemon executor also reads retained daily JSONL/gzip decisions and checksum-verified original plan snapshots from before the feature was installed. This is retrieval of already-issued evidence, never a model replay. A known physical start requires adjacent measured power observations at most45 seconds apart. Only a forecast actually recorded at least12 hours before that start is eligible; missing/corrupt evidence stays missing. The archive's existing retention and disk budget remain unchanged, so a30-day view may contain gaps. No raw snapshots, configuration or model internals are published to the browser. Heating ticks do not wait for archive reads; the event's legacy cycle selection remains compatible.

The overview shows one priority warning. A comfort deficit references the same calculated trajectory and day/sleep bounds as the chart, separately from statistical reserve uncertainty. A point beyond 24 hours is labelled; incomplete or stale data is not a current definitive temperature conclusion. Technical search evidence stays on the AI tab.

Settings receipts and the persisted revision survive restart. The panel can reconcile a missed acknowledgement against matching persisted values; a disk failure remains a failed save. Equipment mode describes the mode on the boiler and sends no physical stage command.

Offline replays and browser checks establish software behaviour. A change in actual daily consumption must be assessed after a completed heating cycle, including its initial/current plans, known versus unclassified energy and delayed temperature peak. No simulated savings certify the physical installation.
