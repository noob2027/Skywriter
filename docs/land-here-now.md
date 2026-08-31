# Native Land Here Now

Task 104 adds one narrow emergency-style operator action: deliberately abandon the
remaining verified mission and ask stock ArduCopter to land at the aircraft's current
location. This is not the mission builder's planned **Land**, which remains attached to a
clicked map coordinate.

## Pinned native meaning

The compatibility target remains stock ArduCopter 4.6.3 at source commit
`92b0cd788ec29406f26c6f9c31d5ceedbd1cc538`. In that exact source,
[`MAV_CMD_NAV_LAND` command handling](https://github.com/ArduPilot/ardupilot/blob/92b0cd788ec29406f26c6f9c31d5ceedbd1cc538/ArduCopter/GCS_Mavlink.cpp)
changes Copter to native Land mode and reports the native command result. The matching
common MAVLink command conversion does not treat command 21 as a command-long location;
the fixed all-zero command therefore supplies no alternate landing coordinate. Stock
ArduCopter owns descent, horizontal behavior, checks, failsafes, and motor output.

SKYWriter's production link can emit only this action shape:

```text
target = current selected system/component
command = MAV_CMD_NAV_LAND (21)
confirmation = 0
param1..param7 = 0
```

No caller can select a command, mode, coordinate, or parameter. One additional fixed,
read-only `MAV_CMD_REQUEST_MESSAGE` (512) for `EXTENDED_SYS_STATE` (245) is allowed only
to obtain the post-ACK landing-state evidence.

## Deliberate two-step confirmation

The visible red panel says that Land Here Now is not the planned clicked Land point. The
first activation sends nothing. It opens a short-lived confirmation that explicitly says
the remaining mission will be abandoned and landing will occur at the aircraft's current
location. Cancel closes that state and sends nothing.

Confirmation is valid only while all of these remain unchanged and fresh:

- selected SiK target and same-vehicle identity;
- exact onboard mission verification digest and revision;
- Task 102 Running authorization and native mission progress;
- native Active or Paused mission state in verified bounds;
- armed AUTO heartbeat and native In Air extended state;
- idle command channel.

Mission completion, planned Land execution, existing Land mode, Landing/On Ground state,
disarm, non-AUTO mode, stale/link-lost telemetry, identity/mission mismatch, or another
command transaction blocks or clears confirmation.

## Honest completion states

Sending command 21 produces **Pending**, never success. A matching target and command-21
`COMMAND_ACK` must be accepted. **Landing** requires later selected-target telemetry that
shows both ArduCopter Land mode and `MAV_LANDED_STATE_LANDING`; later
`MAV_LANDED_STATE_ON_GROUND` is terminal **Landed** proof. Pre-ACK telemetry cannot prove
the command result.

Negative ACKs, unsupported command, timeout, cancellation, wrong ACK/target, native
rejection text, link loss, stale data, missing proof, disarm, unexpected mode, and
telemetry disagreement are separate visible outcomes. Repeated activation while Pending
is ignored. None of these paths sends RTL, Guided navigation, setpoints, parameter writes,
force arm, disarm, or a substitute control stream.

## Production-widget evidence

The Task 104 capture uses the real `FlightTelemetryWidget`, not a visual mock. It records
the separate confirmation, pending, accepted Landing, native rejection, timeout, link
loss, already Landing, already Landed, and telemetry-disagreement presentations under
[`screenshots/task-104/`](screenshots/task-104/).

## Zero-tolerance production audit

The Task 104 review confirms that the compiler/domain expose no RTL command and the UI
exposes no RTL intent or control. Read-only telemetry may still display ArduCopter's
native RTL-family mode names, explicitly labeled as native status only; this does not
emit a mode change. Production `command_long_send` calls remain confined to the dedicated
MAVLink connection adapter and its closed pre-arm, normal Arm, AUTO start, Pause, Resume,
Land Here Now, and two fixed read-only observation requests. There is no generic command
API, force-arm magic value, arming bypass, parameter write, Guided/setpoint stream, or
Disarm send path.

Within the new compartment, the only action packet is command 21 with confirmation zero
and seven zero parameters. The only additional packet is command 512 requesting message
245 with every remaining parameter zero. Architecture and concrete-link tests assert
those exact shapes and public surfaces.

## Evidence boundary

Fake-link tests cover confirmation/cancel-without-send, exact packet shapes, accepted and
negative ACKs, timeout, link loss, stale/wrong-link rejection, duplicate activation,
already Landing/Landed states, disarm, missing telemetry, and disagreement. The connected
stock-SITL scenario is run twice in fresh processes and records the fixed command, ACK,
later native landing telemetry, and final disarm/On Ground observation. No hardware or
real flight is authorized by this task.
