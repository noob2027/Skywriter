# Task 009 — connected USB/SiK and SITL integration

## Goal

Integrate the accepted SITL harness, mission transport, verification, telemetry, and application gates without adding flight commands.

## Base and ownership

Base: merged/rebased accepted Tasks 006–008.

Own: application connection/upload/readback use cases, UI wiring, integration/SITL tests, and minimal integration fixes. Shared-contract changes require a separate PR.

## Required work

- USB flow: discover/select identity, require disarmed state, display existing mission, explicit replacement confirmation, upload, accepted ACK, full readback, Verified/mismatch evidence.
- Disconnect/reconnect flow: reconcile the same vehicle over SiK, re-download/compare mission, and restore readiness only after success.
- Invalidate readiness on edit, target mismatch, stale heartbeat, onboard mismatch, protocol error, or connection loss.
- Wire read-only telemetry and mission progress without commands.
- Execute representative Takeoff–Proceed–Hold–Circle–Land in pinned SITL and preserve logs/readback artifacts.

## Tests and acceptance

Pass full CI plus SITL for each action/mixed mission, negative ACK, readback mismatch, existing-mission cancel, wrong vehicle, stale/disconnected link, and restart/reconnect. Mission Planner or an independent reference read confirms expected items. Prove no command/parameter-write/RTL path exists.

## Handoff and stop

Report evidence paths, screenshots, state transitions, changed files, checks/results, known compatibility limits, and props-off prerequisites. Open a PR and stop; do not request pre-arm checks or add Arm/AUTO controls.
