# Task 102 — native AUTO mission start

## Scope and authority

Task 102 adds one production vehicle action: request stock ArduCopter 4.6.3 to start
the already verified onboard mission through its native AUTO handler. ArduCopter remains
the mode, navigation, mission, and failsafe authority. SKYWriter does not stream Guided
setpoints and never treats a click, packet transmission, or accepted acknowledgment as
proof that the mission is running.

There is no generic command or mode API, sequence selector, Pause/Resume, Land Here Now,
RTL, Disarm, parameter write, mission rewrite, setpoint stream, or hardware behavior in
this compartment. Compiler, compatibility-envelope, mission-protocol, telemetry-parser,
Task 100 readiness, and Task 101 normal Arm semantics remain independently testable.

## Exact pinned semantics

The official `Copter-4.6.3` tag is
`92b0cd788ec29406f26c6f9c31d5ceedbd1cc538`. Its
[`handle_MAV_CMD_MISSION_START`](https://github.com/ArduPilot/ardupilot/blob/92b0cd788ec29406f26c6f9c31d5ceedbd1cc538/ArduCopter/GCS_Mavlink.cpp#L958-L972)
handler denies any nonzero first/last selector. With both values zero it asks Copter to
enter AUTO, sets its native auto-armed state, starts or resumes the onboard mission when
needed, and returns accepted only if the AUTO transition succeeds.

The published stable SITL binary remains identified by its direct-parent source commit
`3fc7011a7d3dc047cbb17d8bd98ee94577d144c6`; the accepted compatibility evidence proves
the only tag delta is `ArduCopter/version.h` firmware metadata. The distinct official
identities are not collapsed.

`PymavlinkNativeAutoStartLink.send_native_auto_start()` emits command 300 with
confirmation zero and parameters `[0, 0, 0, 0, 0, 0, 0]`. No command identifier, mode,
first item, last item, or parameter array crosses the application API.

## Application gate

`NativeAutoStartService` sends nothing unless all of these facts are current together:

- an explicitly classified, connected SiK link;
- selected target, expected package, and typed telemetry identify the same vehicle;
- selected-target and typed heartbeats are fresh and agree that the vehicle is armed;
- exact same-vehicle `SIK_VERIFIED` mission evidence is still current;
- Task 101 reports telemetry-confirmed Armed for the same Task 100 review fingerprint;
- the native package is contiguous with authoritative Home at sequence 0 and Takeoff at
  the first executable sequence 1; and
- the command channel is idle and the vehicle is not already in AUTO.

Mission edit/digest change, target mismatch, stale or lost link, disarm, armed-state
disagreement, invalid Home/Takeoff sequence, invalid Task 101 fingerprint, or another
command owner closes the gate before transmission. Repeated activation while pending is
recorded and ignored without a second command.

## Acknowledgment and Running proof

The gateway correlates only command-300 `COMMAND_ACK` from the selected target and
addressed to the local GCS. Wrong source, command, address, missing result, negative ACK,
unsupported result, timeout, cancellation, link loss, and native `STATUSTEXT` remain
distinct evidence.

An accepted ACK opens a bounded observation phase. **Running** requires both:

1. a later selected-target heartbeat that remains armed and reports Copter AUTO mode 3;
2. a later `MISSION_CURRENT` or `MISSION_ITEM_REACHED` sequence inside the exact verified
   native mission bounds and mission type 0 when that field is present.

Pre-ACK telemetry cannot satisfy either proof. AUTO without progress, progress without
AUTO, unexpected mode, disarm, stale telemetry, or an out-of-range/wrong-type progress
event remains uncertain or failed closed. Losing the desktop link after Running
immediately invalidates the application state; SKYWriter sends no fallback navigation.

## UI and worker boundary

The Flight panel emits only immutable `NativeAutoStartRequested`. Its button locks
synchronously, and the worker handoff rejects another active operation. Blocking gateway
work runs on a Qt thread-pool worker; only immutable snapshots return to the UI thread.
Telemetry cards and parsing remain receive-only.

Reviewed states are captured under `docs/screenshots/task-102/`: pending,
telemetry-confirmed Running, native rejection, timeout, link loss, unexpected mode, and
mission mismatch.

## Stock-SITL evidence

The twice-fresh connected workflow replaces the former test-only positive AUTO helper
with the production Task 102 service, gateway, and fixed concrete link. Each isolated
stock process must retain command 300 with all-zero parameters, its matching addressed
accepted ACK, a later armed AUTO heartbeat, and a later in-bounds mission-progress event.

Immediately after Running, the test closes the desktop SiK-classified connection and
asserts that the application state fails closed. It reconnects only to observe that stock
Copter advanced beyond the start-confirming sequence and completed the verified
Takeoff–Proceed–Hold–Circle–Land mission under native onboard execution. Native Land
auto-disarms; no Disarm cleanup command is sent.

The pinned handler's selector-denial path is captured separately with a test-only
`param1=1` packet after landing. Production cannot supply that value. Evidence retains
the raw ACK/telemetry trace, exact stock binary/default identities, link interruption,
later mission progress, native rejection, teardown, zero parameter writes, and hashes.

## Limits and rollback

Evidence is Linux stock SITL, not real hardware or field readiness. Matek H7A3/H743
target/revision, USB-C interface mapping, and Holybro/alternative SiK model, firmware,
region, and baud remain unresolved props-off validation inputs. Link-loss behavior is the
observed stock-default SITL behavior; SKYWriter does not configure or claim aircraft-
specific failsafe behavior.

Revert the Task 102 PR to remove the native AUTO-start application/gateway/UI
compartments, tests, screenshots, and evidence updates. Task 101 normal Arm and all prior
mission/readiness compartments remain intact, while Task 103 returns to blocked status.
