# Task 110 installed Connected production composition

## Outcome and safety boundary

Version 0.1.4 makes the accepted Connected mission and receive-only telemetry workflow
available from a normal installed Windows launch. It does not bind Preflight, Arm, AUTO,
Pause/Resume, or Land Here Now and adds no generic command, RTL, parameter, stream-request,
firmware, driver, motor, mission-execution, or flight path.

Normal startup remains inert: it neither enumerates nor opens a serial port. The operator
must click **Refresh ports**, inspect the current human-readable Windows descriptions,
select one port, select USB or SiK, review the baud, and click Open. No first port or first
vehicle is selected automatically. USB defaults to 115200; SiK defaults to 57600. Windows
COM assignments remain dynamic and no Big Bird historical port is a product default.

## Production flow

```text
current Builder compile/revision
  -> explicit port refresh and selection
  -> explicit USB + baud selection
  -> worker-owned open/discovery
  -> explicit fresh vehicle selection
  -> complete onboard mission inspection
  -> explicit replacement approval
  -> fresh disarmed heartbeat + authoritative Home
  -> upload + accepted MISSION_ACK + complete exact readback
  -> reliable disconnect
  -> explicit SiK + baud selection
  -> same-vehicle discovery and explicit selection
  -> fresh receive-only telemetry
  -> complete mission re-download and exact comparison
  -> SIK_VERIFIED only while identity/mission/telemetry remain current
```

One Qt controller owns one link and one cancellable worker at a time. Port refresh, open,
discovery, mission inspection/transfer/readback, telemetry collection, re-verification, and
disconnect run outside the Qt thread. Discovery is bounded to 3 seconds, telemetry/Home
collection to 5 seconds, and mission transactions retain the accepted 30-second overall
deadline, bounded response/item waits, and five retries. Shutdown cancels and closes the
sole handle; no cleanup command is sent.

Busy ports tell the operator to close Mission Planner. Missing/disappeared ports,
no-heartbeat/wrong-baud conditions, cancellation, stale/wrong identity, armed state,
unresolved Home, protocol/acknowledgment failure, and incomplete or mismatched readback are
typed and visible. Any Builder edit clears the compiled input and readiness evidence; an
edit during work cancels the stale transaction before the new revision is applied.

## Dependency and packaging decision

`pyserial==3.5` is a direct exact runtime dependency rather than an undeclared pymavlink
transitive assumption. It is the current stable pyserial release and supplies
`serial.tools.list_ports` plus the Windows `list_ports_windows` implementation used for
human port discovery. The PyInstaller spec names that platform module explicitly. The
installed smoke imports it from the frozen application before the UI run. The pyserial 3.5
wheel has no `License-File` metadata and contains no license file, so the collector uses a
repository-pinned fallback only for that exact version: the verbatim upstream v3.5 BSD
notice plus its source provenance are included in the installed notices.

The bundled workspace runtime also exposes Poppler on `PATH`. PyInstaller can otherwise
mistake Poppler's private `icuuc.dll`/`icudt78.dll` for Qt dependencies and shadow
Windows' system ICU, causing a frozen `QtCore` missing-procedure failure. The spec rejects
only those Poppler-sourced shadow binaries; the retained Task 109 package, a reduced frozen
probe, and the final installed acceptance establish the expected system-ICU behavior.

The hardware-blocked installed acceptance injects one deterministic `COM42` fixture with a
human description. It clicks Refresh, proves no auto-selection, explicitly selects the
fixture and link kind, verifies 115200/57600 defaults, and never clicks Open. The existing
MAVLink-open guard remains active and requires zero attempted and zero successful opens.

## Deterministic evidence

- Unit/application: revision invalidation, prior-verification invalidation on protocol
  failure, typed connection failures, and existing identity/readback gates.
- Infrastructure: natural port sorting/descriptions, exact pyserial pin enforcement, baud
  forwarding into pymavlink, busy/disappeared classification, and existing protocol paths.
- UI/controller: no startup enumeration/open, off-thread ownership, complete USB-to-SiK
  fake-hardware flow, cancellation/close, no heartbeat, busy port, and disappeared port.
- Packaging: direct lock, Windows hidden import, frozen-runtime import smoke, deterministic
  installed serial surface, map pixel gate, shortcut launch, zero-I/O audit, and uninstall.
- Pinned SITL: the unchanged stock ArduCopter 4.6.3 connected workflow remains the required
  Linux evidence; Task 110 adds no protocol or flight-command behavior.

Final local verification used the exact locked dependencies on Python 3.12.13:

- `ruff check .` passed, `ruff format --check .` passed, and mypy passed across 128
  source files.
- The final tree collects 559 tests. The current workstation split passed 545 tests with
  2 expected platform skips; its 12 QtWebEngine-dependent cases failed uniformly before
  page readiness because Chromium could not create a GLES context, so no SKYWriter page
  assertion ran. The focused 41-test Task 110 non-WebEngine suite passed after the final
  fail-closed and pyserial-notice fixes. The pull-request clean Windows runner is the final
  WebEngine arbiter. The two local skips are the approved Linux-x86_64 stock-SITL binary
  tests.
- The Windows installer build, packaged-runtime smoke, shortcut launch, installed UI/map
  acceptance, zero-I/O audit, and uninstall all passed. The unsigned artifact is
  `SKYWriter-Prototype-Setup-0.1.4.exe`, 153,400,474 bytes, SHA-256
  `cfedb3d455aad55973b4ddb9e2da8860fd6ff801814abfe8fd71231e8a42cbed`.
- No approved stock-SITL binary is available on the Windows workstation. The pull-request
  `Pinned SITL harness` therefore remains the authoritative Linux evidence and runs the
  stock ArduCopter 4.6.3 workflow twice from fresh processes.

## Supervised C1 next step

Real hardware is intentionally not auto-detected or claimed. The next step is the existing
Big Bird C1 disarmed, propellers-removed bench procedure: record the current USB and SiK
ports; keep Mission Planner and SKYWriter mutually exclusive; confirm system 20 and stock
ArduCopter 4.6.3 git `3fc7011a`; inspect the onboard mission; deliberately approve USB
replacement; require accepted ACK plus exact complete readback; disconnect; select the
current SiK port at 57600; explicitly select the same vehicle; collect fresh telemetry; and
require the complete exact mission comparison. Stop on any identity, freshness, port,
sensor, protocol, or readback ambiguity. Do not arm, run motors, execute the mission, change
firmware/parameters/drivers, or weaken `ARMING_CHECK=4366` / `BATT_ARM_VOLT=19.7`.
