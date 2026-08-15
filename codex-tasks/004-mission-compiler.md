# Task 004 — deterministic mission compiler

## Goal

Compile a valid complete beginner mission into an immutable, deterministic representation of only approved native ArduCopter mission items.

## Base and ownership

Base: accepted foundation/contract commit shared by Tasks 002–004.

Own: `src/skywriter/domain/compiled.py`, compiler module at the frozen path, `tests/unit/compiler/`, and `tests/fixtures/missions/`. Do not edit model/UI/application/MAVLink code or shared contracts.

## Required compilation

Use `MAV_FRAME_GLOBAL_RELATIVE_ALT_INT` semantics and integer latitude/longitude at the compiled boundary. Output:

1. Takeoff.
2. One mission-wide ground-speed change immediately after Takeoff.
3. Proceed → Waypoint.
4. Hold → timed Loiter at clicked point/altitude.
5. Circle → one clockwise Loiter Turns with positive entered radius and matching center/altitude.
6. Land → approach Waypoint at entered altitude, then native Land at identical selected coordinates.

The command type must be closed so unsupported IDs cannot be constructed through normal application APIs. Reject drafts/invalid missions. Do not silently clamp or add limits.

## Tests and acceptance

Golden fixtures cover each action, mixed actions, exact order/count/frame/command/coordinates/altitudes/parameters/current/autocontinue/mission type, coordinate conversion boundaries, deterministic repeated output, invalid input, Land coordinate equality, and proof that RTL/arbitrary commands cannot be emitted. Encode compatibility assumptions explicitly for later pin verification.

## Handoff and stop

Report exact parameter mapping, fixtures, changed files, checks/results, and every assumption requiring pinned SITL confirmation. Open a PR and stop; do not import pymavlink, send messages, or begin Task 005.
