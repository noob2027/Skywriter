# Task 104 — deliberately confirmed Land Here Now

## Goal

Add the final beginner intervention: request native landing at the aircraft's current location with strong accidental-activation protection.

## Base and ownership

Base: accepted Task 103. Own the dedicated native-land gateway method, confirmation/state UI, and tests.

## Required work

- Present **Land Here Now** separately from the planned clicked Land action.
- Gate on fresh same-target link and appropriate armed/airborne/mission state.
- Require a deliberate confirmation interaction that clearly states it abandons remaining mission progress and lands at the current location.
- Use only the pinned native Land mechanism through a narrow API; correlate acknowledgment and confirm landing mode/state telemetry.
- On rejection/timeout/link loss, report uncertainty honestly; do not substitute RTL, Guided descent/setpoints, disarm, or parameter changes.

## Acceptance and stop

Fake-link and SITL tests cover confirm/cancel, accepted/rejected/timeout, stale link, duplicate activation, already landing/landed, disarm, and telemetry disagreement. Complete a final audit for zero RTL UI/compiler path, force arm, arming bypass, parameter writes, generic commands, and setpoint streaming. Open a PR and stop; do not authorize hardware or flight testing.
