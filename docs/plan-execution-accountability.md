# Plan, execution and temperature evidence

The initial cycle estimate is stored once before heating. Subsequent calculations have a separate current estimate; measured consumption is never added to the initial number. Legacy three-hour baselines retain their provenance. An installation started during heating cannot claim a prestart estimate.

The planner subtracts recorded consumption from this reference. Among trajectories with the same comfort deficit it prefers less reference overrun, before tariff and timing preferences. This is not a hard energy shutoff: an actual forecast temperature violation remains higher priority. Diagnostics retain its time/temperature/limit, observed supply interruptions and mean requested power. These observations do not independently identify causal kWh shares.

The floor observer integrates credible counter deltas or contiguous measured power. It treats return water as a proxy and cannot replace a warmer integrated inventory with a colder pipe. Gaps, resets and implausible counter jumps do not become heating energy. Coefficients and existing counters are preserved. This model estimate is not a floor-surface temperature or an independent energy measurement.

The normal 15-second loop starts scheduled segments without waiting for a five-minute recalculation. Dispatch checks a segment against the current clock after calculation; grid, upper limits, ownership, outage masks and minimum switching times still apply. Repeated unchanged outage metadata no longer triggers new planning.

Temperature comparisons are prospective. The first confirmed heating edge of a cycle selects the last forecast issued at least 12 hours earlier, with at most ten minutes of issue-cadence tolerance. Its values and issue time are copied once. Later plans cannot change it. An unknown start, restart gap or missing advance forecast is explicit missing evidence. Observation gaps break lines; neither series is extended beyond its available coverage. Periods and observations survive reload in private controller state, with a rolling month of comparisons. Only a selected period is published to HA.

The overview shows one priority warning. A comfort deficit references the same calculated trajectory and day/sleep bounds as the chart, separately from statistical reserve uncertainty. A point beyond 24 hours is labelled; incomplete or stale data is not a current definitive temperature conclusion. Technical search evidence stays on the AI tab.

Settings receipts and the persisted revision survive restart. The panel can reconcile a missed acknowledgement against matching persisted values; a disk failure remains a failed save. Equipment mode describes the mode on the boiler and sends no physical stage command.

Offline replays and browser checks establish software behaviour. A change in actual daily consumption must be assessed after a completed heating cycle, including its initial/current plans, known versus unclassified energy and delayed temperature peak. No simulated savings certify the physical installation.
