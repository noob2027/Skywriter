# SKYWriter repository instructions

These rules bind every Codex task and every contributor. If a task conflicts with this file, stop and report the conflict. A task grants permission only for its explicit scope and files.

## Non-negotiable product rules

1. Stock, pinned ArduCopter is the flight authority. Do not fork, patch, build, flash, or redistribute ArduCopter.
2. Do not implement RTL in the beginner UI, mission compiler, command service, shortcuts, menus, or hidden actions. Do not change the aircraft's separately configured native failsafes.
3. Do not force arm, bypass or suppress pre-arm checks, change `ARMING_*` parameters, or offer mid-air disarm.
4. Do not write vehicle parameters. `PARAM_SET` and equivalent write paths are forbidden. Read-only parameter access requires explicit task scope.
5. Do not send raw/unapproved mission commands. The compiler whitelist is closed to `NAV_TAKEOFF`, `DO_CHANGE_SPEED`, `NAV_WAYPOINT`, `NAV_LOITER_TIME`, `NAV_LOITER_TURNS`, and `NAV_LAND`.
6. Do not add Guided setpoint streaming, RC overrides, servo/relay commands, scripting, payload control, mission jumps, spline waypoints, unlimited loiter, or arbitrary MAVLink consoles.
7. Do not invent operational flight-envelope limits or claim obstacle/geofence protection. The prototype has structural validation only; policy extension points must remain inert until a later approved task.
8. Do not claim upload success without accepted `MISSION_ACK`. Do not claim **Verified** without a full download and field-by-field comparison after documented normalization.
9. Flight behavior is not accepted on unit tests alone. SITL must pass before props-off hardware; staged field testing comes last.

## Required mission behavior

- Takeoff is unique and first.
- Takeoff setup captures relative altitude, one positive mission cruise speed, and obstacle-warning acknowledgment.
- Land remains available after Takeoff.
- Every clicked post-takeoff point remains pending until Proceed, Hold, Circle, or Land is confirmed.
- Every post-takeoff action has coordinates and relative altitude.
- Hold adds positive time in seconds.
- Circle adds positive radius in meters, one clockwise turn, and a visual circle/radius/direction cue.
- Land is unique and last. It compiles to an approach waypoint at the entered altitude and native Land at the same selected coordinates.
- Removing Land is the only way to append more points to a closed mission.
- JSON uses an explicit schema version; SI units are canonical internally.

## Architecture boundaries

- Domain code is independent of Qt, WebEngine, Leaflet, serial ports, and `pymavlink`.
- UI code emits typed user intents; it never constructs MAVLink packets.
- The compiler maps typed actions to a closed, deterministic intermediate mission representation.
- MAVLink adapters own protocol packets, target identity, timeouts, retries, acknowledgments, and telemetry parsing.
- Application state owns readiness gates. Widgets may render gates but may not duplicate or bypass them.
- All I/O uses injectable interfaces so unit tests can run without Qt, serial hardware, or SITL.
- Keep policy extension interfaces for future geofence/limits, but their prototype implementation is pass-through and makes no safety claim.

## Connection and command rules

- Prototype mission upload occurs over direct USB while disarmed.
- SiK is used after USB disconnect for flight telemetry and explicitly approved acknowledged controls.
- On reconnection, match vehicle identity and read back the mission before readiness can be restored.
- Every command waits for the matching `COMMAND_ACK` and presents rejection/timeout honestly.
- Native pre-arm requests and `STATUSTEXT` are displayed; absence of a message is not treated as readiness.
- Disconnection or stale heartbeat invalidates command readiness. Do not stream fallback navigation commands.

## Scope and file ownership

- Perform only the named task. Do not begin later phases.
- Edit only task-owned files plus the smallest necessary test/config files explicitly allowed by the handoff.
- No opportunistic refactors, dependency upgrades, or shared-interface changes.
- If an interface is insufficient, document the issue and stop the affected portion. Propose a separate interface PR.
- Preserve unrelated user changes and keep commits reviewable.

## Engineering requirements

- Prefer typed Python, immutable domain values where practical, and explicit state transitions.
- Use integer latitude/longitude (`degrees * 1e7`) at the compiled protocol boundary; use decimal degrees in the domain/UI.
- Make units explicit in names (`altitude_m`, `hold_time_s`, `radius_m`, `cruise_speed_m_s`).
- Make timeouts, retries, and clocks injectable; no unbounded blocking calls on the Qt UI thread.
- Log state transitions and protocol outcomes without secrets or misleading success messages.
- Reject unknown JSON fields/actions when they could change mission meaning. Migrations must be explicit and tested.
- New runtime dependencies require justification in the PR and exact version locking.

## Tests and completion report

Run the task-specified checks plus the repository's formatter, linter/type checker, and relevant `pytest` suites. Protocol tests must cover success, rejection, timeout, wrong sequence, duplicate messages, and connection loss where applicable.

Every handoff reports:

- changed files;
- tests and commands run with results;
- assumptions and unresolved limitations;
- safety-rule impact;
- screenshots for visible UI changes;
- the next safe task, without starting it.

Never commit directly to `main`. Use one short-lived branch per task, open a pull request, require CI and review, and squash merge only after acceptance.
