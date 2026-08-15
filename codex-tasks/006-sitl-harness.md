# Task 006 — pinned ArduCopter SITL harness

## Goal

Provide a repeatable, evidence-producing test harness for the exact approved stock ArduCopter compatibility pin.

## Base and ownership

Base: accepted offline integration plus compatibility-pin PR, shared by Tasks 006–008.

Own: `scripts/sitl/`, `tests/sitl/conftest.py`, SITL fixtures/helpers, CI job definition, and SITL setup documentation. Do not edit application/domain/UI/MAVLink production modules.

## Required work

- Acquire/build and start the exact pinned Copter SITL target without modifying its source.
- Use known isolated endpoints, deterministic location/vehicle defaults, readiness detection, bounded startup/shutdown, and artifact/log capture.
- Expose pytest fixtures for target endpoint/identity and clean mission state.
- Document Windows developer use and CI caching; verify artifact hash/version at startup.
- Add a smoke test for heartbeat, firmware identity, disarmed state, and clean shutdown.

## Exclusions

No production transport, mission compiler edits, arm/flight commands, parameter writes, firmware fork/patch, or real hardware.

## Acceptance and handoff

Two clean consecutive runs pass without orphaned processes or shared-port collisions. Failure preserves useful logs. Report pin/hash, commands, files, checks/results, limitations, and resource cost. Open a PR and stop.
