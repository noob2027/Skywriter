# Task 005A — stock ArduCopter compatibility pin

## Goal

Evaluate a maintainer-selected stock ArduCopter release against SKYWriter's compiled mission semantics and produce an explicit, reviewable compatibility pin before production MAVLink work.

## Base and ownership

Base: accepted Task 005. Run serially before Tasks 006–008.

May create/edit: a compatibility manifest under `compatibility/`, read-only probe scripts/tests isolated from production adapters, mission/readback evidence fixtures, dependency lock entries needed for the probe, and compatibility documentation. Do not modify/fork ArduCopter or change the domain/UI/compiler semantics without a separate architecture decision.

## Required work

- Verify exact official release/version, firmware or source hash, Copter SITL artifact, MAVLink dialect/version, and pymavlink version.
- Probe every whitelisted mission item and record exact frame/parameter/current/autocontinue/mission-type upload and readback behavior, integer coordinates, home-item handling, and wire-precision normalization.
- Probe native support/acknowledgment behavior for pre-arm request and Pause/Resume without adding production flight controls.
- Record supported Windows/SITL acquisition steps and USB/SiK assumptions.
- Compare evidence with compiler fixtures and identify any discrepancy as a blocking architecture issue.

## Safety and exclusions

The maintainer selects the candidate; Codex does not automatically choose “latest.” No firmware patch/build modification, flashing, parameter write, force arm, real hardware, flight, RTL addition, or production command implementation is allowed.

## Acceptance and handoff

All evidence is reproducible, hashed, and sufficient for a human accept/reject decision. Report candidate, artifacts, exact findings, compiler mismatches, changed files, and checks. Open a compatibility PR and stop. Tasks 006–008 may start only after human acceptance.
