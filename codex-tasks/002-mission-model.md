# Task 002 — mission model, validation, and JSON

## Goal

Implement the pure beginner mission domain, structural validation, editing operations, and versioned JSON persistence against frozen contracts.

## Base and ownership

Base: accepted foundation/contract commit shared by Tasks 002–004.

Own: `src/skywriter/domain/mission.py`, `validation.py`, `policy.py`, `src/skywriter/infrastructure/json_repository.py`, and corresponding `tests/unit/domain/` and JSON tests. Do not edit UI, compiler, application state, or MAVLink modules.

## Required work

- Typed `GeoPoint`, settings, Proceed, Hold, Circle, Land, and Mission values with explicit SI-unit names.
- Draft editing: append/replace/delete/move coordinates, remove Land to reopen; preserve creation order.
- Validation modes for editable draft and complete/uploadable mission.
- Enforce Takeoff settings first/unique by model construction, warning acknowledgment, positive cruise speed, finite valid coordinates/altitudes, positive Hold time/Circle radius, one clockwise turn, and Land unique/last for complete missions.
- `MissionPolicy` port plus inert `NoOperationalPolicy`; do not create maximum/minimum operational bounds beyond structural positivity.
- Versioned strict JSON with atomic write, explicit action discriminators, load-time validation, and no persisted trust/connection/compiled state.

## Tests and acceptance

Cover each valid action, mixed mission, drafts, missing/duplicate/trailing Land attempts, invalid numeric/coordinates, unknown action/field/schema, editing after Land, remove-Land/reopen, round-trip equivalence, and failed atomic load/write behavior. Domain tests require no Qt, pymavlink, serial, map, or network.

## Handoff and stop

Report the schema with an example, changed files, checks/results, assumptions, and contract issues. Open a PR and stop; do not build UI/compiler or change frozen contracts.
