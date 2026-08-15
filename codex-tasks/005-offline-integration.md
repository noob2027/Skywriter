# Task 005 — offline builder integration

## Goal

Integrate the accepted model, compiler, JSON repository, and map UI into a complete offline SKYWriter workflow.

## Base and ownership

Base: merged/rebased accepted Tasks 002–004.

Own: application mission use cases/state adapters, main-window wiring, integration tests, and only the smallest integration fixes in component files. Any frozen-contract change requires a separate PR first.

## Required work

- Connect typed UI intents to immutable mission edits and render snapshots.
- Implement new/save/load/review/compile-preview; loading never restores Verified state.
- Derive button gates from mission state; any edit invalidates compiled preview/version.
- Remove development mocks at production boundaries.
- Present validation findings without flight-safety claims.
- Provide sample JSON missions and deterministic native compiled preview.

## Tests and acceptance

End-to-end offline tests cover full creation/edit/save/load/compile flow, invalid JSON, cancellation, Land closure/reopen, state invalidation, all visuals, and exact mixed compiled sequence. Run full CI. Demonstrate no vehicle/MAVLink/parameter/command/RTL path exists.

## Handoff and stop

Report changed files, screenshots, sample mission, checks/results, remaining accessibility/map limitations, and compatibility assumptions. Open a PR and stop; do not begin pymavlink or SITL work.
