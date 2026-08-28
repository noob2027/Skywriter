# Read-only telemetry boundary

Task 008 adds a receive-only telemetry boundary for the selected MAVLink target. It is
presentation/evidence infrastructure, not a connection owner or command path. The
adapter consumes the transport-neutral messages supplied by a caller, produces immutable
application snapshots, and never exposes a send method. It does not request message
streams, upload or download missions, write parameters, or send vehicle commands.

## Accepted inputs

The closed parser whitelist is `HEARTBEAT`, `GLOBAL_POSITION_INT`, `BATTERY_STATUS`,
`SYS_STATUS`, `HOME_POSITION`, `MISSION_CURRENT`, `MISSION_ITEM_REACHED`, `GPS_RAW_INT`,
`EKF_STATUS_REPORT`, `EXTENDED_SYS_STATE`, and `STATUSTEXT`. Messages must come from the
selected system/component identity. Unsupported, wrong-target, malformed, and older
observations do not replace a newer valid signal.

Values use the units and unavailable sentinels defined by the MAVLink common message set.
Examples include degrees-times-10^7 and millimeters for global position, centimeters per
second for horizontal velocity, millivolts/centiamperes for battery state, `UINT16_MAX`
for unavailable heading or GPS dilution, and `-1` for unavailable battery current or
remaining percentage. Numeric validation happens before a snapshot is replaced.

The mode label is a display-only mapping for the pinned stock ArduCopter 4.6.3 target.
Unknown numeric modes remain visible as `Mode N`; they are not converted into commands.

Authoritative references:

- [MAVLink common messages](https://mavlink.io/en/messages/common.html)
- [MAVLink heartbeat protocol](https://mavlink.io/en/services/heartbeat.html)
- [ArduCopter MAVLink support](https://ardupilot.org/copter/docs/ArduCopter_MAVLink_Messages.html)

## Freshness and availability policy

Freshness is evaluated against an injected monotonic time. Defaults are deliberately
explicit and independently configurable:

| Signal | Fresh for |
|---|---:|
| heartbeat | 3 seconds |
| position | 2 seconds |
| battery | 10 seconds |
| home | 60 seconds |
| mission current/reached | 5 seconds |
| GPS, sensor flags, EKF, extended state | 5 seconds each |

These are SKYWriter presentation policies, not protocol guarantees. MAVLink does not
define one universal heartbeat-loss timeout. A caller that cannot supply the expected
cadence leaves that signal unavailable or stale; Task 008 does not send stream-rate
requests to manufacture a cadence. A disconnected link is distinct from a connected link
with a stale heartbeat. Missing data is always shown as unavailable, never healthy.

The immutable snapshot exposes a read-only heartbeat freshness fact suitable for later
application gates. Task 008 does not own or enable any command gate.

## Presentation and composition seam

The Preflight and Flight tabs display native observations through this receive-only
boundary. Later serial command tasks compose their own typed controls beside those views;
they do not add a send method to telemetry. The flight map draws aircraft, home, current
target, and completed/remaining route layers without a basemap. Route geometry is
provided separately by the caller through a typed application contract; the telemetry
adapter does not import or duplicate the logical compiler, compatibility envelope, or
mission transport. Tasks 009–102 compose these accepted pieces only through their
existing contracts.

The stock fixture represents ArduCopter 4.6.3 observations, but SITL is neither a runtime
dependency nor a prerequisite for offline SKYWriter. The intended Matek H7A3/H743 target,
USB-C connector/interface mapping, and exact Holybro or alternative SiK radio model,
firmware, region, and baud settings remain unverified hardware inputs. No hardware claim is
made by this adapter.

## Review evidence

- [Fresh preflight observations](screenshots/task-008/01-preflight-fresh.png)
- [Fresh flight and route layers](screenshots/task-008/02-flight-fresh.png)
- [Visible stale flight state](screenshots/task-008/03-flight-stale.png)

Reproduce the screenshots on Windows with:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:QT_OPENGL = "software"
python tests/ui/capture_task008_screenshots.py
```

The capture helper loads the installed Windows Arial font explicitly so offscreen output
remains readable even when Qt cannot enumerate system fonts automatically.
