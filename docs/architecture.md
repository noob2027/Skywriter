# SKYWriter architecture

## 1. Design drivers

SKYWriter is a constrained ground-control application above stock ArduCopter. Its architecture is shaped by five rules: preserve flight-controller authority, make beginner actions impossible to confuse with raw commands, compile deterministically through a closed whitelist, prove mission storage through readback, and keep all vehicle I/O behind explicit state gates.

The exact ArduCopter release is intentionally not guessed in this package. Before MAVLink work, a compatibility PR must pin an approved stock version and record the firmware version/hash, MAVLink dialect/version, SITL artifact, board families tested, and any documented mission-item normalization.

## 2. Layered design

```text
PySide6 screens + Leaflet map
             |
       typed UI intents
             v
Application state / use-case services
       |                     |
       v                     v
Mission domain         Vehicle gateway interfaces
 model/validator/       mission protocol, telemetry,
 compiler/serializer   command acknowledgments
       |                     |
       v                     v
versioned JSON       pymavlink adapter -> USB / SiK -> stock ArduCopter
```

Dependencies point inward: UI and adapters depend on application/domain contracts. Domain code never imports Qt, WebEngine, Leaflet, serial libraries, or `pymavlink`.

## 3. Proposed source tree

```text
src/skywriter/
├── main.py
├── config.py
├── domain/
│   ├── mission.py
│   ├── validation.py
│   ├── compiled.py
│   └── policy.py
├── application/
│   ├── state.py
│   ├── mission_service.py
│   ├── readiness.py
│   └── ports.py
├── infrastructure/
│   ├── json_repository.py
│   └── mavlink/
│       ├── connection.py
│       ├── mission_protocol.py
│       ├── verification.py
│       ├── telemetry.py
│       └── commands.py
└── ui/
    ├── main_window.py
    ├── mission_builder.py
    ├── preflight.py
    ├── flight.py
    └── map/
        ├── bridge.py
        └── static/{map.html,map.js,map.css}
tests/
├── unit/
├── integration/
├── sitl/
└── fixtures/
```

## 4. Domain model

Illustrative typed shapes (names are normative; exact Python syntax is not):

```text
GeoPoint(latitude_deg, longitude_deg)
MissionSettings(takeoff_altitude_m, cruise_speed_m_s,
                obstacle_warning_acknowledged)
ProceedAction(point, altitude_m)
HoldAction(point, altitude_m, hold_time_s)
CircleAction(point, altitude_m, radius_m, turns=1,
             direction=CLOCKWISE)
LandAction(point, approach_altitude_m)
Mission(schema_version, id, settings, actions)
```

Takeoff is represented by `MissionSettings`, not by a clickable post-takeoff point. The flight controller's established home/launch location supplies Takeoff coordinates as required by the pinned compatibility behavior. `actions` contains zero or more Proceed/Hold/Circle and an optional final Land while drafting.

Raw MAVLink command IDs, frames, parameter slots, target IDs, ports, and verification flags do not belong in the domain mission.

### Structural validator

The pure validator returns typed findings with path/code/message/severity. It checks ordering, cardinality, required values, coordinate ranges, finite numeric inputs, positive cruise speed/hold time/radius, warning acknowledgment, and completed-mission Land. Draft validation may permit missing Land; compile/upload validation may not.

### Operational policy seam

`MissionPolicy.evaluate(mission, context) -> findings` is an application port. `NoOperationalPolicy` is the prototype implementation and produces no geofence or envelope approval. Future profiles may add limits through dedicated reviewed work; structural validation remains separate.

## 5. Compilation boundary

`MissionCompiler.compile(valid_complete_mission) -> CompiledMission` is pure and deterministic. `CompiledMission` contains immutable integer-coordinate items and no transport state.

Default mission frame is `MAV_FRAME_GLOBAL_RELATIVE_ALT_INT`; all displayed/stored altitudes mean meters **Above Home**. Latitude/longitude convert once at this boundary to signed integers in degrees × 10^7.

| Order/source | Command | Required semantics |
|---|---|---|
| 0 / settings | `MAV_CMD_NAV_TAKEOFF` | requested relative takeoff altitude; pinned/default unused parameters |
| 1 / settings | `MAV_CMD_DO_CHANGE_SPEED` | ground-speed type; mission cruise speed m/s; no parameter write |
| Proceed | `MAV_CMD_NAV_WAYPOINT` | clicked coordinates and relative altitude; zero/default delay/radii/yaw |
| Hold | `MAV_CMD_NAV_LOITER_TIME` | clicked coordinates/altitude; `param1 = hold_time_s`; other behavior pinned and tested |
| Circle | `MAV_CMD_NAV_LOITER_TURNS` | clicked center/altitude; `param1 = 1`; `param3 = positive radius_m` for clockwise behavior; draw equivalent geometry |
| Land approach | `MAV_CMD_NAV_WAYPOINT` | selected landing coordinates at approach altitude |
| Land | `MAV_CMD_NAV_LAND` | same selected coordinates; native landing semantics, with unused/default fields pinned and tested |

The compatibility suite must assert every frame, parameter, coordinate, altitude, `current`, `autocontinue`, and `mission_type` value against the pinned ArduCopter/SITL target. Any required deviation from this table needs a dedicated architecture/compatibility PR. Compiler construction rejects command values outside the whitelist by type, not only by a final runtime check.

### ArduCopter 4.6.3 compatibility envelope

The accepted adaptation seam is a pure, version-specific boundary after
`MissionCompiler` and before the future MAVLink transport. The logical compiler and
its fixtures remain unchanged and unaware of vehicle identity, connections, home,
SITL, USB, SiK, or live coordinates.

`skywriter.compatibility.arducopter_4_6_3` accepts a `CompiledMission`, an opaque
caller-established `VehicleIdentity`, a caller-supplied home state, and a caller-supplied
time value. A usable `HomeSnapshot` must be authoritative, fresh, geographically valid,
centimeter-preserving, and owned by the same vehicle. Missing or unconnected home is a
typed `HomeUnresolved`; stale, invalid, non-authoritative, future-dated, or wrong-vehicle
snapshots are converted to the same non-uploadable state. Numeric `0,0,0` is never used as
a missing-home substitute.

On success, the boundary produces an immutable native package with the vehicle's home
waypoint at wire sequence zero and every logical compiler item shifted by one. The shifted
Takeoff remains present at sequence one. All native `current` flags are false, all approved
`autocontinue` values remain true, and the compiler's integer-coordinate frame and mission
meaning remain unchanged in the upload package.

Readback verification separates native home from the shifted logical mission. Both must
verify before the combined result can be true. Expected and downloaded fields are compared
exactly after this closed ArduCopter 4.6.3 whitelist:

| Observed native normalization | Exact treatment |
|---|---|
| MAVLink float payloads | pack and compare as IEEE-754 binary32 |
| sequence-zero home | retain `MAV_FRAME_GLOBAL` (0) and verify separately |
| home altitude | native integer centimeters multiplied by binary32 `0.01f` |
| `DO_CHANGE_SPEED` frame | frame 6 reads back as frame 0 |
| navigation frames | frame 6 reads back as frame 3 |
| `NAV_LOITER_TIME.param3` | compiler zero reads back as one |
| `NAV_LAND.param4` | compiler zero reads back as one |

No tolerance, reversible-transform claim, or open-ended field rewrite is permitted. Any
other command, count, sequence, frame, flag, parameter, coordinate, altitude, or mission-type
difference fails closed with an exact field mismatch.

This envelope is not a MAVLink transport. The observed 4.6.3 behavior of requesting each
integer upload item with legacy `MISSION_REQUEST` is retained only in compatibility evidence.
Task 007 still owns target routing, request handling, retries, acknowledgements, download,
timeouts, disconnect behavior, and transaction state. That future adapter must consume the
native package without moving normalization, home authority, or mission semantics into the
transport.

## 6. Application state

Use explicit immutable snapshots and reducer/use-case transitions. Key orthogonal state:

```text
Mission: EMPTY | DRAFT | VALID | COMPILED | UPLOAD_PENDING |
         UPLOAD_ACKED | READBACK_PENDING | VERIFIED | MISMATCH
Link:    DISCONNECTED | USB_CONNECTING | USB_READY |
         SIK_CONNECTING | SIK_READY | STALE | ERROR
Vehicle: UNKNOWN | DISARMED | ARM_PENDING | ARMED |
         AUTO_RUNNING | PAUSED | LANDING
Command: IDLE | PENDING(kind, token, deadline) | ACCEPTED | REJECTED | TIMED_OUT
```

Verification is tied to a digest of the canonical compiled mission, target identity, mission type, and readback. Editing the mission, changing target, detecting a newer onboard mission, or losing transaction integrity clears it. Reconnection alone never restores it.

Task 009 implements this as a presentation-neutral `ConnectedMissionService` behind an
injected high-level port. The concrete MAVLink port composes, rather than duplicates, the
accepted target discovery, mission protocol, compatibility verification, and telemetry
adapters. Its public send surface remains mission-only. The Qt panel emits immutable
intents and leaves every blocking operation to a caller-owned worker.

The implemented verification states are `UNVERIFIED`, `USB_VERIFIED`,
`REVERIFY_REQUIRED`, `SIK_VERIFIED`, and `MISMATCH`. USB verification requires explicit
onboard-replacement approval plus fresh same-target heartbeat and Home. Disconnect or an
edit clears readiness; a fresh same-vehicle SiK full readback is required to restore it.
No state transition in this compartment arms, changes mode, starts flight, writes a
parameter, or sends a generic command.

Task 100 adds a separate `PrearmReadinessService` and `NativePrearmGateway`. The
application service consumes the current immutable connected snapshot and owns the
SiK/same-target/disarmed/exact-mission/freshness/idle gates. The gateway exposes only
`request_prearm_checks()` and its link exposes only `send_prearm_checks()` for pinned
command 401. Neither broadens the mission-only `ConnectedMavlinkPort` or the receive-only
telemetry adapter.

The result retains ACK classification and associated native text separately from the
typed telemetry review. An accepted request never means armable. Explicit review can
produce an application gate only with current healthy native sensor evidence, and any
mission, target, link, or armed-state change invalidates it. Qt emits typed request and
review intents; the blocking transaction remains worker-owned.

Task 101 adds a separate `NormalArmService` and `NativeNormalArmGateway`. The service
reuses Task 100's exact reviewed context instead of duplicating native readiness logic,
then revalidates same-target SiK identity, disarmed/fresh telemetry, verified mission,
and idle command ownership. The gateway and concrete link expose only the normal Arm
operation with fixed parameters; no generic or Disarm surface exists.

An accepted command-400 ACK begins a second bounded state, not success. Only a later
selected-target heartbeat with the armed bit can produce `ARMED`. Missing telemetry,
fresh disarmed telemetry, wrong ACK/target, cancellation, and link loss remain distinct
fail-closed or uncertain states. Qt hands the blocking application callable to a real
thread-pool worker and receives immutable snapshots back on the UI thread.

Task 102 adds a separate `NativeAutoStartService` and `NativeAutoStartGateway`. Its
application gate consumes the current Task 101 Armed snapshot plus the unchanged exact
mission/target evidence. The concrete link emits only command 300 with the pinned
supported zero first/last selectors; callers cannot provide a mode, sequence, command,
or parameter array.

An accepted ACK opens a second bounded observation state. Running requires both a later
selected-target armed AUTO heartbeat and later in-bounds native mission progress. Link
loss invalidates the application state without sending substitute navigation, while the
stock flight controller continues according to its configured onboard behavior.

Task 103 adds a separate `NativePauseResumeService` and `NativePauseResumeGateway`.
The application service consumes Task 102's exact Running authorization plus current
same-target mission-state telemetry. Its two gateway action methods expose only fixed
command-193 Pause (`param1=0`) and Resume (`param1=1`) actions with reserved zeros. The
link adds one fixed read-only request for message 42 after acceptance; no command, mode,
coordinate, or parameter array is caller supplied.

An accepted command-193 ACK is not Paused or resumed Running proof. Pause requires a
later in-bounds `MISSION_CURRENT` with the pinned Paused state, while Resume requires a
later Active state. Resume is unavailable until the application has positively observed
Paused. Mission completion, landing, disarm, non-AUTO mode, target/mission mismatch,
stale telemetry, and link loss disable both actions without fallback control.

Task 104 adds a separate `NativeLandHereNowService` and
`NativeLandHereNowGateway`. Its application gate binds the current Task 102 Running
authorization to the exact target, mission digest/revision, progress sequence, native
Active/Paused state, armed AUTO heartbeat, and fresh native In Air state. The first UI
activation creates application-owned confirmation state and emits no MAVLink. A later
confirmation is accepted only while that complete fingerprint is unchanged.

The gateway exposes one action: fixed command 21 (`MAV_CMD_NAV_LAND`) with all seven
parameters zero. Callers cannot supply a command, coordinate, mode, or parameter array.
After a matching accepted ACK, the gateway uses one fixed read-only request for message
245 (`EXTENDED_SYS_STATE`). Landing requires both later Land-mode heartbeat and native
Landing state; On Ground is terminal Landed proof. Rejection, timeout, wrong ACK/target,
telemetry disagreement, cancellation, stale telemetry, and link loss stay explicit. No
RTL, Guided mode, setpoint, parameter, disarm, or fallback command is sent.

Readiness is derived, never toggled directly by a widget. Example predicates:

```text
can_upload = usb_ready && disarmed && compiled && replacement_confirmed
can_arm = sik_ready && same_target && verified && disarmed && preflight_reviewed
can_start = sik_ready && verified && armed && command_idle
```

These application gates are necessary but not sufficient; native ArduCopter may reject commands.

## 7. MAVLink gateway

### Connection identity

The connection service discovers heartbeat, records target system/component, vehicle/autopilot type, firmware identity when available, transport kind, and last-seen time. USB and SiK sessions must reconcile to the same configured identity. Ambiguity or multiple candidate vehicles requires operator selection; do not take the first heartbeat silently.

### Mission upload state machine

```text
IDLE -> SEND_COUNT -> WAIT_REQUEST(seq)
     -> SEND_ITEM_INT(seq) -> ... -> WAIT_ACK
     -> ACKED -> DOWNLOAD_FOR_VERIFY -> VERIFIED | MISMATCH
```

Handle requested sequence explicitly. Bound total transaction time and per-message retries. Ignore or log unrelated traffic; fail on wrong target/mission type, terminal negative acknowledgment, exhausted retries, disconnection, unexpected armed state, or protocol inconsistency. A retry restarts from a known state and first re-reads the onboard mission.

### Readback verification

Request the complete mission list and reconstruct every `MISSION_ITEM_INT`. Compare count and every semantic field. A compatibility-specific normalizer may account only for documented, tested representation changes made by the pinned flight controller (for example canonical float precision or home-item handling); it must preserve mission meaning and produce an audit record. Tolerances must derive from wire representation, not broad “close enough” values.

### Telemetry

Telemetry parsing produces typed snapshots for heartbeat/mode/arming, global position, relative altitude, ground speed, battery, home, mission current/reached, extended system state, GPS/EKF indicators when available, and `STATUSTEXT`. Presentation is read-only. Freshness is measured per signal and a stale heartbeat closes every command gate.

### Commands

Commands are separate from the mission compiler. Each command service method has preconditions and waits for the matching target/command `COMMAND_ACK`:

- request native pre-arm checks;
- normal arm (force flag/value prohibited);
- enter/start native AUTO mission through the pinned, tested sequence;
- pause/resume with `MAV_CMD_DO_PAUSE_CONTINUE` where supported by the pinned target;
- request native Land Here Now at current location with deliberate UI confirmation.

No generic `send_command(command_id, params)` API may be exposed to UI/application code.

The first implemented method is the exact pinned `request_prearm_checks()` path. Stock
4.6.3 returns temporarily rejected while armed; while disarmed it runs native checks and
returns accepted even when a check reports failure. SKYWriter therefore correlates ACK,
preserves `STATUSTEXT` and `SYS_STATUS` evidence, and requires deliberate review without
claiming native arm readiness.

The second implemented method is the exact normal-only `request_normal_arm()` path.
It accepts no command ID or parameter arguments, requires the current reviewed Task 100
fingerprint, and cannot present Armed until selected-target telemetry confirms it.

The third implemented method is `request_native_auto_start()`. It sends only the pinned
command-300 all-zero shape, requires current Task 101 Armed plus exact mission evidence,
and cannot present Running until post-ACK AUTO and mission-progress telemetry both match.

The fourth implemented compartment exposes only `request_native_pause()` and
`request_native_resume()`. Both send command 193 through fixed dedicated link methods,
then use only a fixed read-only message-42 request to obtain the later pinned mission-state
telemetry required before presenting Paused or Running.

The fifth implemented compartment exposes only
`request_native_land_here_now()`. The concrete link sends fixed command 21 with all
parameters zero, then may issue only the fixed message-245 read request needed for later
landing proof. The two-step confirmation remains in the application layer and is cleared
when its bound flight context changes.

## 8. Map isolation

Leaflet runs inside Qt WebEngine and communicates through a narrow versioned bridge. JS may emit only map intents (clicked, point dragged, selected, viewport changed). Python sends only sanitized render models (markers, polylines, circles, labels, vehicle pose). The web view has no reference to the MAVLink gateway or command service and cannot navigate to arbitrary origins in production packaging.

Circle geometry uses the same normalized radius as the domain/compiler. The UI shows a center marker, perimeter, center-to-edge radius line, numeric radius label, and clockwise cue. Rendering tests verify pending/confirmed/selected/complete states.

### Accepted production map decision

The accepted Task 003 remediation architecture is **Path A: Qt WebEngine plus Leaflet**.
The production widget hosts a packaged local page in `QWebEngineView`, and the local page uses a
version-pinned Leaflet 1.9.4 distribution. Executable map assets must not load from a CDN. The Qt
WebEngine dependency is supplied by the exact `PySide6-Addons` version matching
`PySide6-Essentials`.

The mission builder exposes a small basemap provider selector. Its initial choices are:

- **No basemap (offline)**, which is the deterministic default and performs no network requests.
- **OpenStreetMap Standard**, using only the documented
  `https://tile.openstreetmap.org/{z}/{x}/{y}.png` endpoint with visible OpenStreetMap attribution,
  an application-identifying user agent, normal interactive viewing, honored cache headers, and no
  bulk download, prefetch, or offline-tile feature.

Additional providers require a separately reviewed, documented endpoint and licensing/cache policy.
Providers that require credentials may use a key supplied at runtime by the user; keys must never be
committed, persisted in mission JSON, exposed to bridge messages, or written to logs. The selector
must not accept arbitrary URL templates. Navigation remains restricted to the packaged local page,
and tile requests are limited to the selected provider's allowlisted origins. A provider failure must
leave mission editing usable and must not silently switch providers.

## 9. Persistence

JSON includes `schema_version`, stable mission ID, settings, and discriminated action objects. It excludes ports, target IDs, connection state, compiled bytes, acknowledgment history, and trusted verification. Writes are atomic (temporary file plus replace); loads are parsed, migrated only through explicit migrations, structurally validated, and recompiled.

## 10. Concurrency and failure handling

Serial reads and protocol transactions run off the Qt UI thread. Thread/async boundaries send immutable events into the application reducer. There is one mission transaction and one vehicle command in flight at a time. Shutdown cancels work, closes transport, and never sends a navigation or disarm command as cleanup.

Errors are classified as validation, compatibility, identity, connection, protocol, acknowledgment, verification, or internal. User messages state what is known (“Upload acknowledged; readback mismatched item 4”) rather than collapsing states into “Failed” or “Ready.” Logs retain technical detail and correlation IDs.

## 11. Test architecture

- **Unit:** model invariants, JSON round trips, compiler exact sequences, state reducer/gates, geometry, normalization.
- **Protocol simulation:** scripted fake transport/clock for request order, duplicate/lost messages, retries, wrong target, negative ACK, stale link, and cancellation.
- **UI:** mission flow, pending-point behavior, Land persistence/closure, field validation, command enablement, bridge schema.
- **SITL:** upload/readback, each action, mixed mission execution, command ACKs, native pre-arm rejection, pause/resume, Land, and reconnect identity.
- **Hardware:** USB props-off, Mission Planner independent readback, then SiK props-off; staged flight only under a separately approved procedure.

Every supported ArduCopter pin has a compatibility fixture and SITL evidence. Changing the pin is a recertification event, not a dependency bump.
