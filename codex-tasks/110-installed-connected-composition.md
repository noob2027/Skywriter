# Task 110 — installed explicit serial and Connected composition

## Goal

Make the accepted mission-only and receive-only connected stack usable from a normal 0.1.4
Windows install through explicit current-port, link-kind, baud, and vehicle selection.

## Boundary

- No serial enumeration or I/O at startup; refresh is an explicit operator action.
- No hardcoded or automatically selected port or vehicle.
- USB defaults to 115200 and SiK to 57600; the chosen baud crosses the transport boundary.
- Only `ConnectedMissionService`, `ConnectedMavlinkPort`, mission protocol, exact readback,
  and receive-only telemetry are composed.
- Preflight, Arm, AUTO, Pause/Resume, Land Here Now, RTL, parameter read/write, stream
  request, firmware, driver, and flight behavior remain outside this task.

## Acceptance

- Focused application, infrastructure, UI, packaging, and negative-path tests.
- Direct exact `pyserial==3.5` pin with Windows PyInstaller inclusion and notices.
- Installed Start-menu acceptance displays a deterministic human serial description and
  both baud defaults without opening hardware; the hard MAVLink-open guard and zero-attempt
  assertion remain intact.
- Full repository checks, the pinned stock ArduCopter 4.6.3 SITL workflow on its approved
  Linux runner, and exact Windows installer build/smoke evidence.
- Real Big Bird remains a separate supervised C1 disarmed bench step.

## Stop

Open the Task 110 PR with software/package evidence and the exact C1 next step. Do not arm,
run motors, execute a mission, change firmware/parameters/drivers, weaken safety gates, or
claim hardware acceptance.
