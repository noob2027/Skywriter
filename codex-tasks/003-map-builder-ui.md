# Task 003 — offline map mission-builder UI

## Goal

Implement the beginner map interaction and mission-builder presentation using frozen typed contracts and fakes, with no vehicle integration.

## Base and ownership

Base: accepted foundation/contract commit shared by Tasks 002–004.

Own: `src/skywriter/ui/mission_builder.py`, `src/skywriter/ui/map/`, UI resources, and corresponding UI tests. Use contract fakes/adapters under test-owned paths. Do not edit domain/compiler/MAVLink modules or shared contracts.

## Required behavior

- Initially show Takeoff. Its panel captures altitude Above Home, mission cruise speed, and required exact obstacle warning acknowledgment.
- After confirmation, replace that action space with persistent Land.
- A map click creates a pending Mission Planner-style numbered point and action editor; cancel leaves no committed point.
- Proceed requires altitude; Hold altitude/time; Circle altitude/radius; Land approach altitude.
- Confirmed points show sequence, route, altitude, Hold badge, Circle perimeter/radius line/label/clockwise cue, and landing symbol.
- Support selection/edit, coordinate drag, delete, undo, clear, plain-language summary surface, and remove Land/reopen. Block appending after Land.
- Keep the UI responsive and use a versioned validated Python↔JS bridge. Map content cannot call vehicle services.

## Explicit exclusions

No pymavlink, serial ports, mission upload, telemetry, vehicle icon requirement beyond a harmless placeholder, commands, geofence, terrain/obstacle claims, native mission table, arbitrary reorder, or operational limits.

## Tests and acceptance

Headless UI/bridge tests cover the complete flow, required fields, pending cancel, labels/cues, edit/delete/undo/clear, Land persistence/closure/reopen, malformed bridge messages, and absence of raw commands/RTL. Provide screenshots of empty, pending, mixed mission, Circle, and landed states.

## Handoff and stop

Report changed files, screenshots, test results, accessibility/keyboard limitations, map tile assumption, and contract issues. Open a PR and stop; do not integrate the real model or begin connected work.
