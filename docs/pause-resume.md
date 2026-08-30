# Task 103 — native mission Pause and Resume

## Scope and authority

Task 103 adds two production vehicle actions: request stock ArduCopter 4.6.3 to Pause
or Resume the current native AUTO mission. ArduCopter remains the position-hold,
navigation, mission, command-acceptance, and failsafe authority. SKYWriter neither
reinterprets Pause as another mode nor streams a substitute position target.

There is no generic command or mode API, RTL, Loiter command, Guided hold, setpoint
stream, Land Here Now, Disarm, parameter write, mission rewrite, or hardware behavior in
this compartment. Task 102 AUTO start and every earlier compartment remain independently
testable.

## Exact pinned semantics

The official `Copter-4.6.3` tag is
`92b0cd788ec29406f26c6f9c31d5ceedbd1cc538`. Its
[`handle_command_pause_continue`](https://github.com/ArduPilot/ardupilot/blob/92b0cd788ec29406f26c6f9c31d5ceedbd1cc538/ArduCopter/GCS_Mavlink.cpp#L1204-L1225)
handler accepts command 193 with `param1=0` only when the active flight mode can pause,
and `param1=1` only when it can resume. Other selectors are denied. The pinned Copter
AUTO implementation pauses only while its waypoint submode is still progressing toward
the destination; a reached destination or incompatible submode returns native failure.

The same pinned Copter MAVLink implementation reports `MISSION_STATE_PAUSED` from
`MISSION_CURRENT` while the AUTO navigation controller is paused. SKYWriter therefore
does not use reduced ground speed, a stationary position, an accepted ACK, or mode name
alone as Paused proof.

`PymavlinkNativePauseResumeLink.send_native_pause()` emits command 193 with confirmation
zero and parameters `[0, 0, 0, 0, 0, 0, 0]`.
`send_native_resume()` emits `[1, 0, 0, 0, 0, 0, 0]`. No command identifier, mode,
coordinate, speed, hold radius, or parameter array crosses the application API.

## Application gates

Pause sends nothing unless all of these facts are current together:

- an explicitly classified connected SiK link;
- selected target, expected native package, and typed telemetry identify the same
  vehicle;
- selected-target and typed heartbeats are fresh, agree that the vehicle is armed, and
  report Copter AUTO mode 3;
- exact same-vehicle `SIK_VERIFIED` mission evidence is still current;
- Task 102 reports telemetry-confirmed Running for the same mission digest and target;
- fresh `MISSION_CURRENT` reports state Active, the pinned ArduCopter execution count
  (the verified package excluding sequence-zero Home) when available, and an in-bounds
  executable sequence that is not native Land; and
- the command channel is idle.

Resume additionally requires the Task 103 service to have positively observed the pinned
Paused state. A Paused-looking UI toggle or caller-supplied Boolean cannot open the gate.

Mission completion, native Land execution, Land mode, landing/on-ground extended state,
disarm, mission or target change, stale/lost link, non-AUTO mode, out-of-bounds progress,
armed-state disagreement, or another command owner closes both gates before
transmission. Repeated activation while either transaction is pending is recorded and
ignored without a second command.

## Acknowledgment and state proof

The gateway correlates only command-193 `COMMAND_ACK` from the selected target and
addressed to the local GCS. Wrong source, command, address, missing result, negative ACK,
unsupported result, timeout, cancellation, link loss, and native `STATUSTEXT` remain
distinct evidence.

An accepted ACK opens a bounded observation phase:

- **Paused** requires a later selected-target `MISSION_CURRENT` with pinned state Paused,
  an in-bounds sequence, and the exact verified execution count when present. Pinned
  ArduCopter deliberately excludes sequence-zero Home from that `total` field.
- **Running** after Resume requires the same later evidence with pinned state Active.
- later heartbeats must remain armed and in AUTO; later Land, landing, on-ground,
  Complete, disarm, wrong sequence/count, or a different mode fails closed.

Because pinned Copter does not publish a fresh `MISSION_CURRENT` merely because command
193 changed its pause state, the dedicated transport requests message 42 read-only after
acceptance. This is fixed `MAV_CMD_REQUEST_MESSAGE` command 512 with all reserved fields
zero; it cannot select a mode, rewrite a mission, or steer the vehicle.

Pre-ACK mission-state telemetry cannot satisfy either proof. An ACK without the expected
later state remains explicitly uncertain. Losing the desktop link after either confirmed
state disables the controls without sending fallback navigation.

## UI and worker boundary

The Flight panel exposes separate **Pause native mission** and **Resume native mission**
buttons and emits only immutable `NativePauseRequested` or `NativeResumeRequested`
intents. Exactly one is enabled for the positively observed state. Both lock
synchronously on activation and share one exclusive worker handoff, so overlapping
clicks cannot create parallel command transactions.

Reviewed states are captured under `docs/screenshots/task-103/`: Pause pending, Paused,
Resume pending, Running, native rejection, timeout, link loss, mission completion, and
landing.

## Stock-SITL evidence

The twice-fresh connected workflow uses the production Task 103 service, gateway, and
fixed concrete link. Each isolated stock process must retain two positive cycles:

1. Pause and Resume while the Proceed action owns the active waypoint leg.
2. Pause and Resume while the Hold action owns its waypoint-approach leg.

Each half-cycle requires command 193 with the exact selector/reserved fields, a matching
addressed accepted ACK, and later pinned Paused or Active `MISSION_CURRENT` telemetry at
the expected verified sequence. The test then interrupts the desktop link and observes
the remaining mission complete and native Land auto-disarm under stock onboard
execution. Evidence retains raw command/ACK/state traces, exact stock identities, later
mission progress, teardown, zero parameter writes, and hashes.

## Limits and rollback

Evidence is Linux stock SITL, not real hardware or field readiness. The pinned AUTO pause
handler can natively reject an action after its waypoint destination is reached or in an
incompatible submode; SKYWriter reports that result and does not substitute another hold
mechanism. Hardware/radio timing and aircraft-specific configured failsafes remain
props-off validation inputs.

Revert the Task 103 PR to remove the Pause/Resume application, gateway, connection, UI,
tests, screenshots, documentation, and evidence updates. Task 102 native AUTO start and
all earlier compartments remain intact, while Task 104 returns to blocked status.
