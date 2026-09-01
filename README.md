# SKYWriter

## High-technical overview

SKYWriter is a beginner-oriented Windows desktop ground-control application for building, validating, uploading, verifying, and monitoring a deliberately small class of ArduCopter missions. It is not custom flight firmware and it is not an ArduCopter fork. A stock, explicitly pinned and flight-tested ArduCopter release remains the sole flight authority for estimation, stabilization, navigation, mission execution, arming checks, failsafes, and motor control.

The application reduces operator complexity by translating five beginner concepts—**Takeoff, Proceed, Hold, Circle, and Land**—into a whitelist of native ArduCopter mission commands. The UI never exposes a raw mission table, arbitrary MAVLink commands, vehicle-parameter editing, forced arming, arming-check bypasses, or an RTL control.

> SKYWriter is a prototype mission console, not a substitute for trained flight operations, regulatory compliance, obstacle surveys, or native ArduCopter safety configuration.

## Authority boundary

| SKYWriter owns | Stock ArduCopter owns |
|---|---|
| Beginner mission authoring | EKF, position and attitude estimation |
| Structural validation | Stabilization and motor output |
| Deterministic command compilation | AUTO mission execution |
| Mission-protocol transaction state | Native pre-arm checks and arming denial |
| Upload/readback comparison | Navigation-command semantics |
| Read-only telemetry presentation | Battery, radio and other configured failsafes |
| Approved, acknowledged operator commands | Final acceptance/rejection of vehicle commands |

SKYWriter must fail closed. A UI click, packet transmission, or upload acknowledgment alone is never treated as proof that the aircraft accepted or stored the intended state.

## Beginner mission workflow

1. **Takeoff is first.** Selecting Takeoff opens setup for takeoff altitude, one mission-wide cruise speed, and a required obstacle-warning acknowledgment.
2. **Land replaces Takeoff.** After Takeoff is committed, the primary action space permanently exposes Land until the Land action is added.
3. **Each map click creates a pending point.** It is rendered as a Mission Planner-style numbered dot but is not committed until the operator chooses Proceed, Hold, Circle, or Land and supplies required values.
4. **Every post-takeoff point has altitude.** Altitude is displayed explicitly as **Above Home**.
5. **Action-specific fields stay small.** Hold requires time; Circle requires radius and draws a perimeter, radius line/label, and direction cue.
6. **Land closes the mission.** Land is attached to the selected clicked location and compiles to an approach waypoint followed by native Land at the same coordinates. Removing Land reopens editing.

There is one cruise speed for the whole mission. The prototype uses one clockwise Circle turn. The mission sequence follows point creation order; arbitrary drag-to-reorder is deferred.

## Deterministic compilation

SKYWriter compiles only the following mission commands in `MAV_FRAME_GLOBAL_RELATIVE_ALT_INT` (or the equivalent pinned compatibility representation):

| Beginner concept | Native mission output |
|---|---|
| Takeoff | `MAV_CMD_NAV_TAKEOFF` |
| Mission cruise speed | one `MAV_CMD_DO_CHANGE_SPEED` immediately after Takeoff |
| Proceed | `MAV_CMD_NAV_WAYPOINT` |
| Hold | `MAV_CMD_NAV_LOITER_TIME` |
| Circle | `MAV_CMD_NAV_LOITER_TURNS` |
| Land | approach `MAV_CMD_NAV_WAYPOINT`, then `MAV_CMD_NAV_LAND` at the same latitude/longitude |

No model field may contain a raw command identifier. The compiler owns the closed mapping, and tests must prove that unsupported commands cannot be emitted.

## Pinned compatibility boundary

Stock ArduCopter 4.6.3 is the recommended compatibility pin for the next connected
development wave. The logical compiler remains unchanged. A pure version-specific
envelope supplies a caller-owned, authoritative, fresh, same-vehicle home waypoint at
native sequence zero and shifts the compiled Takeoff and later items by one. Missing,
stale, invalid, or wrong-vehicle home is a typed non-uploadable state, never numeric
`0,0,0`.

Readback verifies native home separately and then compares every shifted logical field
after only the documented closed normalization whitelist. The retained stock-SITL
evidence and remaining platform limits are in
[`compatibility/arducopter-4.6.3/README.md`](compatibility/arducopter-4.6.3/README.md).
This boundary is not a production MAVLink transport and does not make offline SKYWriter
depend on SITL, a connection, USB, or SiK.

The isolated pinned-target test workflow and practical Windows limitations are
documented in [`docs/sitl-harness.md`](docs/sitl-harness.md).

The receive-only telemetry whitelist, explicit freshness/availability policy, presentation
seam, screenshots, and unresolved hardware assumptions are documented in
[`docs/telemetry.md`](docs/telemetry.md). This boundary emits no MAVLink traffic and does
not make offline SKYWriter depend on a vehicle link or SITL.

The connected mission composition, fail-closed USB/SiK lifecycle, pinned mixed-mission
evidence, and remaining hardware limits are documented in
[`docs/connected-integration.md`](docs/connected-integration.md). Task 009 adds no
production flight-command or parameter-write surface.

The first serial flight-control compartment is the dedicated native pre-arm request and
operator readiness review documented in [`docs/native-prearm.md`](docs/native-prearm.md).
It exposes only command 401, preserves native ACK/`STATUSTEXT` meaning, and never treats
an accepted request or silence as proof that ArduCopter will arm.

The second serial compartment is the normal-only Arm path documented in
[`docs/normal-arm.md`](docs/normal-arm.md). It is bound to the current reviewed readiness
fingerprint, exposes no caller-supplied command parameters, and requires fresh
selected-target armed telemetry after the exact acknowledgment before showing Armed.

The third serial compartment is the fixed native AUTO mission start documented in
[`docs/auto-start.md`](docs/auto-start.md). It exposes no mode or sequence arguments and
requires both a later armed AUTO heartbeat and in-bounds native mission progress after
the exact command-300 acknowledgment before showing Running.

The fourth serial compartment is the state-aware native Pause/Resume path documented in
[`docs/pause-resume.md`](docs/pause-resume.md). Its two dedicated command-193 actions use
fixed selectors, and neither Paused nor resumed Running is shown without later pinned
`MISSION_CURRENT` state telemetry from the same verified mission and target. After an
accepted ACK, one fixed read-only request obtains that state without exposing a generic
command surface.

The fifth serial compartment is the deliberately confirmed native Land Here Now path
documented in [`docs/land-here-now.md`](docs/land-here-now.md). It is visibly separate
from the planned clicked Land point, emits only fixed command 21 with all-zero parameters,
and requires the matching accepted ACK plus later native Land-mode and landing-state
telemetry before showing Landing. It never substitutes RTL, Guided control, setpoints,
disarm, or parameter changes.

The real Big Bird vehicle profile and first disarmed powered-bench evidence contract are
documented in [`compatibility/big-bird/README.md`](compatibility/big-bird/README.md).
Task 105 adds only a pure offline parameter-export validator and repository evidence. It
does not add live parameter access, stream requests, vehicle setup automation, or a bench,
arming, motor, or flight claim.

## Safety invariants

- The repository never builds, patches, or distributes ArduCopter firmware.
- The exact supported stock ArduCopter version is pinned before MAVLink integration and changed only by a compatibility pull request with SITL recertification.
- Mission upload is allowed only while connected over USB and the aircraft reports disarmed.
- Successful upload requires an accepted `MISSION_ACK`; **Verified** additionally requires a complete mission download and field-by-field comparison after documented protocol normalization.
- ArduCopter's pre-arm system remains authoritative. SKYWriter may request native checks and show native status, but cannot override the result.
- No `PARAM_SET`, force-arm magic value, arming-check bypass, hidden Guided setpoint stream, mid-air disarm, or RTL UI/mission command is permitted.
- The prototype has structural validation but no invented operational altitude, distance, speed, radius, duration, or geofence limits. Extension interfaces exist for reviewed future policies.
- Map imagery does not establish obstacle clearance. The setup warning explicitly calls out power lines, rooftops, trees, cables, poles, and other obstacles.

## Mission and connection lifecycle

```text
Draft -> Structurally Valid -> Compiled -> USB Uploading
      -> Upload Acknowledged -> Read Back -> Verified
      -> SiK Reconnected/Same Vehicle -> Native Preflight Review
      -> Armed -> AUTO Running -> Paused/Running
      -> Planned Land or deliberately confirmed Land Here Now -> Landing -> Complete
```

Any mission edit invalidates compilation and verification. Any identity mismatch, readback mismatch, disconnection, timeout, negative acknowledgment, or unexpected armed state moves the application to a non-ready state. The vehicle continues under native ArduCopter behavior if the desktop application or SiK link is lost; SKYWriter does not stream substitute navigation setpoints.

## Technical stack

- Python 3 and PySide6 for the Windows desktop application
- Qt WebEngine with Leaflet for the embedded interactive map
- `pymavlink` for MAVLink 2 framing, mission transactions, commands, and telemetry
- Versioned JSON for local mission documents
- `pytest` for unit, integration, protocol-state-machine, and SITL tests
- ArduPilot SITL as the mandatory flight-behavior validation environment
- GitHub feature branches, required CI, reviewed pull requests, and squash merges

## Repository target

```text
Skywriter/
├── README.md
├── AGENTS.md
├── docs/
│   ├── product-specification.md
│   ├── architecture.md
│   └── development-plan.md
├── codex-tasks/
│   ├── README.md
│   └── staged task handoffs...
├── pyproject.toml                 # Foundation Task 001
├── src/skywriter/                 # Foundation Task 001
└── tests/                         # Foundation Task 001
```

The documents in this starter package are the implementation source of truth. Begin with [`codex-tasks/001-foundation.md`](codex-tasks/001-foundation.md); do not ask Codex to implement the entire plan at once.

## Documentation map

- [`docs/product-specification.md`](docs/product-specification.md): user behavior, requirements, exclusions, and acceptance criteria
- [`docs/architecture.md`](docs/architecture.md): domain model, state machines, interfaces, protocol boundaries, and test seams
- [`docs/connected-integration.md`](docs/connected-integration.md): USB/SiK composition, invalidation, and stock-SITL evidence
- [`docs/native-prearm.md`](docs/native-prearm.md): exact native pre-arm request, review gate, and SITL evidence boundary
- [`docs/normal-arm.md`](docs/normal-arm.md): gated normal Arm, exact ACK/telemetry proof, and stock-SITL evidence
- [`docs/auto-start.md`](docs/auto-start.md): fixed native AUTO start, Running proof, and link-interruption evidence
- [`docs/pause-resume.md`](docs/pause-resume.md): native Pause/Resume gates, exact state proof, and SITL evidence
- [`docs/land-here-now.md`](docs/land-here-now.md): deliberate native landing-at-current-location gate, confirmation, proof, and SITL evidence
- [`docs/development-plan.md`](docs/development-plan.md): phases, branch/PR workflow, gates, and validation ladder
- [`compatibility/big-bird/README.md`](compatibility/big-bird/README.md): exact vehicle profile, sanitized evidence, disarmed bench boundary, and later-gate progression
- [`docs/windows-installer.md`](docs/windows-installer.md): prototype Setup, shortcuts, uninstall, reproducible build, signing seam, and limitations
- [`AGENTS.md`](AGENTS.md): binding repository rules for Codex and human contributors
- [`codex-tasks/README.md`](codex-tasks/README.md): launch order and bounded handoffs

## Windows developer setup

The project supports CPython 3.11 through 3.14 on 64-bit Windows. The exact lock uses
PySide6 Addons and Essentials, including Qt WebEngine for the accepted Path A map host.

From a clean PowerShell checkout:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --requirement requirements.lock
python -m pip install --no-build-isolation --no-deps --editable .
```

Run the desktop shell:

```powershell
python -m skywriter
```

Run the same checks as CI:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest
```

To apply formatting during development, use `python -m ruff format .` and then rerun
the checks. If PowerShell blocks virtual-environment activation, keep the scoped
`Set-ExecutionPolicy` command above or invoke `.\.venv\Scripts\python.exe` directly.
If Qt cannot initialize a display in an automated session, set
`$env:QT_QPA_PLATFORM = "offscreen"`; do not set it for normal interactive use.

## Windows prototype installer

A Windows user without Python can install the prototype from
`SKYWriter-Prototype-Setup-0.1.3.exe`. Setup installs per-user, creates a Start-menu entry,
offers a default desktop shortcut, and registers a standard uninstaller. The application
still starts offline and disconnected; packaging is not vehicle, bench, motor, or flight
validation.

The mission Builder starts on an honest neutral world viewport with the local offline
planning grid selected, so startup makes no map-network request. An operator may enter
validated decimal latitude/longitude and choose **Go / recenter**, then deliberately select
**OpenStreetMap Standard (network)**. The Builder reports Offline, Loading, Online, Partial,
or Unavailable with tile counts and a deliberate Retry action; it never silently changes
providers. OpenStreetMap is a best-effort prototype provider, not a production SLA. There
is no address search, satellite provider, offline tile download, or map-derived Home/Vehicle
position.

Version 0.1.3 retains Qt WebEngine's Chromium software-rendering path on Windows because the
accepted 0.1.1 GPU-compositor path was reproduced painting the entire embedded map black.
The installed smoke now inspects captured map pixels, controlled local tiles, and visible
Leaflet controls; page/bridge readiness alone is no longer sufficient.

The 0.1.3 installed acceptance also drives the real Builder through its installed Start-menu
shortcut at 1498×758 and 1366×768. Confirm-point validation is adjacent, focused, and
scrolled into view; downstream rejection preserves entered values, while success updates
the list, summary, route, and marker exactly once. Connected, Preflight, and Flight controls
remain visibly disabled in this unbound shell and acceptance records zero vehicle-I/O
attempts. See the complete
[`installed-control triage`](docs/task-109-control-triage.md).

The verified Task 109 artifact is `SKYWriter-Prototype-Setup-0.1.3.exe`, 152,301,811
bytes, SHA-256
`7e0f4a96e18b0f1f07c3d4c4f3ded1c9405bfabbb1551ba08c784873a52f28a5`. It is unsigned,
so SmartScreen/reputation warnings remain expected.

Builds are unsigned unless explicit signing secrets are configured and may trigger
SmartScreen. Verify `SHA256SUMS.txt` before running Setup. Nontechnical install, launch,
uninstall, exact build, CI artifact, signing, and limitation instructions are in
[`docs/windows-installer.md`](docs/windows-installer.md).

## Authoritative implementation references

- [MAVLink Mission Protocol](https://mavlink.io/en/services/mission.html)
- [MAVLink Common Message Set](https://mavlink.io/en/messages/common.html)
- [ArduPilot Copter mission commands](https://ardupilot.org/copter/docs/common-mavlink-mission-command-messages-mav_cmd.html)
- [ArduPilot MAVLink messages supported by Copter](https://ardupilot.org/copter/docs/ArduCopter_MAVLink_Messages.html)
- [ArduPilot mission upload/download development guide](https://ardupilot.org/dev/docs/mavlink-mission-upload-download.html)
