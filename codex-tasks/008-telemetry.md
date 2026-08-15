# Task 008 — read-only telemetry adapter and presentation

## Goal

Present typed, freshness-aware vehicle and mission telemetry without sending vehicle commands.

## Base and ownership

Base: accepted offline integration plus compatibility-pin PR, shared by Tasks 006–008.

Own: `src/skywriter/infrastructure/mavlink/telemetry.py`, typed telemetry application adapter at the frozen path, preflight/flight read-only widgets, and their tests. Do not edit mission transport/compiler/model or create command modules.

## Required work

- Parse target-scoped heartbeat/mode/armed state, position, relative altitude, ground speed, battery when available, home, mission current/reached, GPS/EKF/readiness indicators when available, extended state, and native `STATUSTEXT`.
- Produce immutable snapshots with per-signal timestamps/availability and a heartbeat-stale connection state.
- Show vehicle identity, link type, state/mode, aircraft/home/current-target map layers, completed/remaining route, altitude/speed/battery, and native messages.
- Treat missing data as unavailable, not acceptable/healthy. Staleness is visible and suitable for application command gates.

## Exclusions

No outgoing MAVLink messages, stream-rate requests unless separately approved by contract, upload/download, pre-arm request, arm, AUTO, pause/resume, Land Here Now, RTL, or parameter writes.

## Acceptance and handoff

Fixture tests cover valid/missing/malformed/wrong-target/out-of-order/stale data and UI recovery. A test-spy proves zero outbound frames. Provide screenshots, changed files, checks/results, availability assumptions, and stop with a PR.
