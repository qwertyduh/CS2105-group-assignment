# CS2105

## Problem statement

We are replacing spreadsheet based shift rostering for a college fest with an optimisation engine that produces a conflict free schedule, explains any compromise it had to make, and can repair itself after a volunteer is sick without reshuffling everyone.

The festival runs on four student groups: **MPR, CND, Hospitality and Spons**. Manual rostering today causes three recurring problems:

- compliance errors (rest, hour caps and consecutive-day rules broken unnoticed)
- uneven workloads between students of the same group
- disruption when someone drops out, because a manual fix ripples through the whole sheet