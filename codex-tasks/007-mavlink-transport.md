# Task 007 — mission transport and readback verification

## Goal

Implement USB-oriented target discovery, mission upload, download, and exact normalized verification using pymavlink, with no vehicle commands.

## Base and ownership

Base: accepted offline integration plus compatibility-pin PR, shared by Tasks 006–008.

Own: `src/skywriter/infrastructure/mavlink/connection.py`, `mission_protocol.py`, `verification.py`, corresponding adapter tests and protocol fixtures. Do not edit UI, domain compiler, telemetry presentation, or command modules.

## Required work

- Discover and explicitly select target identity from heartbeat; classify USB/SiK transport but permit upload only when application supplies a USB/disarmed gate.
- Implement bounded cancellable upload with `MISSION_COUNT`, requested `MISSION_ITEM_INT` responses, and terminal `MISSION_ACK`.
- Implement complete mission download with INT items.
- Compare count and every semantic field after only pinned/documented normalization; return a field-level mismatch report and verification evidence/digest.
- Handle retries, duplicates, wrong sequence, wrong target/mission type, negative ACK, timeout, cancellation, disconnect, and unexpected armed-state abort.
- Use injectable link and clock; never block the UI thread and never emit a success state early.

## Tests and acceptance

Scripted protocol tests cover every path above, including “ACK accepted but readback differs.” Tests prove there is no parameter write, arm, mode, pause, Land Now, generic command, or RTL emission path. SITL integration belongs to Task 009.

## Handoff and stop

Report state diagram, timeout/retry values and rationale, normalizations, changed files, checks/results, and unresolved pinned-target behavior. Open a PR and stop.
