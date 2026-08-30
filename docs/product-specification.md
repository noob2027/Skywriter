# SKYWriter product specification

## 1. Product definition

SKYWriter is a Windows desktop mission console that lets a beginner create a limited ArduCopter mission by clicking a map and choosing plain-language actions. It compiles those actions into a small set of native mission items, uploads them to a stock pinned ArduCopter vehicle over USB, reads them back, and presents flight progress over SiK telemetry.

### Product promise

A beginner can author and review a simple mission without seeing raw MAVLink fields while the aircraft retains native ArduCopter navigation, pre-arm, command-acceptance, and failsafe authority.

### Prototype status

The prototype is an engineering and training tool. It does not certify terrain clearance, obstacle clearance, regulatory compliance, airworthiness, or operational flight limits.

## 2. Users and operating context

**Primary operator:** a beginner working under an established safe operating procedure and supervised hardware/flight validation.

**Platform:** Windows desktop, initially configured and connected to one ArduCopter vehicle at a time.

**Connection sequence:** direct USB for identity/setup/mission upload and verification; disconnect USB; reconnect the same vehicle over SiK for flight telemetry and approved commands.

## 3. Core user journey

### 3.1 Create

1. Start a new mission. The only primary mission action is **Takeoff**.
2. Press Takeoff and enter:
   - takeoff altitude Above Home;
   - one cruise speed for the entire mission;
   - acknowledgment of: “Verify clearance from power lines, rooftops, trees, cables, poles, and other obstacles. The map does not guarantee obstacle clearance.”
3. Confirm Takeoff. The same primary action space becomes a persistent **Land** control.
4. Click the map. A pending Mission Planner-style numbered point and compact action editor appear.
5. Choose one action:
   - **Proceed:** enter altitude;
   - **Hold:** enter altitude and hold time;
   - **Circle:** enter altitude and radius;
   - **Land:** enter approach altitude.
6. Confirm or cancel the pending point. Confirmed points connect in creation order.
7. Continue adding points or select Land for a clicked location. Land closes the mission.

### 3.2 Edit and review

The operator can select and edit a point, drag it to update coordinates, delete it, undo the last addition, clear the mission, save/load JSON, and read a plain-language summary. Removing Land reopens the route. Arbitrary reordering is excluded from the prototype.

The map displays numbered markers, route lines, Above Home altitude labels, Hold time badges, Circle perimeter/radius/direction, and a distinct landing symbol. During flight it additionally displays aircraft/home position, heading, current target, completed route, and remaining route.

### 3.3 Upload and verify

1. Connect over USB and identify the vehicle and pinned ArduCopter compatibility target.
2. Require disarmed state and valid compiled mission.
3. If an onboard mission exists, show it and require explicit replacement confirmation.
4. Run the MAVLink mission upload transaction using integer-coordinate items.
5. Require accepted `MISSION_ACK`.
6. Download the complete onboard mission.
7. Normalize only documented representation differences and compare sequence, command, frame, coordinates, altitude, parameters, current/autocontinue state, and mission type.
8. Display **Verified** only on a complete match. Any edit or mismatch invalidates verification.

### 3.4 Preflight and flight

After reconnecting by SiK, SKYWriter confirms the same vehicle identity and repeats mission readback comparison. The readiness view presents connection freshness, disarmed/armed state, mode, mission verification, GPS/home/EKF/battery/safety information when available, requested native pre-arm checks, and native `STATUSTEXT`.

Flight controls are introduced only in the later serial phase:

- Arm (normal acknowledged path only)
- Start Mission (native AUTO execution)
- Pause
- Resume
- Land Here Now, with deliberate confirmation, requesting native landing at the current location

RTL is not present. There is no mid-air disarm or flight-termination control.

## 4. Functional requirements

### FR-1 Mission structure

- Exactly one Takeoff; it is first.
- Exactly one mission-wide positive cruise speed compiled immediately after Takeoff.
- Zero or more Proceed/Hold/Circle actions.
- Exactly one Land for a complete/uploadable mission; it is last.
- No action follows Land.

### FR-2 Point data

- All post-takeoff actions contain valid latitude, longitude, and numeric relative altitude.
- Hold contains positive duration seconds.
- Circle contains positive radius meters and compiles as one clockwise turn.
- Land contains approach altitude and selected landing coordinates.

### FR-3 Native command whitelist

The compiler can emit only:

```text
MAV_CMD_NAV_TAKEOFF
MAV_CMD_DO_CHANGE_SPEED
MAV_CMD_NAV_WAYPOINT
MAV_CMD_NAV_LOITER_TIME
MAV_CMD_NAV_LOITER_TURNS
MAV_CMD_NAV_LAND
```

Land emits two items: approach waypoint at the selected location/approach altitude, then Land at the same location. RTL cannot be represented in the domain or compiler.

### FR-4 Local persistence

- Save and load human-readable, versioned JSON.
- Canonical stored units are meters, meters/second, seconds, and decimal-degree coordinates.
- Reject malformed, unsupported, or semantically unknown actions.
- Revalidate and recompile after load; do not persist a trusted verification state.

### FR-5 Protocol correctness

- Identify target system/component from heartbeat; never assume an identity silently.
- Support bounded timeouts/retries and sequence-specific responses.
- Upload through `MISSION_COUNT` / `MISSION_REQUEST_INT` / `MISSION_ITEM_INT` / `MISSION_ACK`.
- Download through the corresponding request-list/count/item-int flow.
- Associate acknowledgments with the active transaction and mission type.
- Surface negative acknowledgment, timeout, link loss, target mismatch, storage error, unsupported command, and readback mismatch.

### FR-6 Telemetry

Show connection state, vehicle identity, armed state, flight mode, position, altitude, ground speed, battery when available, current mission sequence, reached items, and native status text. Telemetry is stale after a defined heartbeat timeout and must not leave commands enabled.

### FR-7 Command acknowledgments

Every vehicle command has explicit prerequisites, pending state, bounded timeout, matching `COMMAND_ACK`, accepted/rejected presentation, and an audit log entry. A transmitted command is not a successful command.

For normal Arm specifically, an accepted acknowledgment is still not success. The UI
may present **Armed** only after a later fresh armed heartbeat from the same selected
target. Missing or conflicting telemetry is an explicit uncertain state, never an
optimistic transition.

For Start Mission, an accepted acknowledgment is also not success. The UI may present
**Running** only after later fresh selected-target telemetry confirms armed AUTO mode and
native mission progress within the exact verified onboard mission. No fallback Guided
setpoint stream is sent when confirmation or the command link is lost.

For Pause and Resume, an accepted acknowledgment is likewise not the resulting state.
The UI may present **Paused** or resumed **Running** only after later fresh selected-target
`MISSION_CURRENT` telemetry reports the pinned Paused or Active mission state inside the
exact verified mission. Resume remains unavailable until Paused has been positively
observed. Landing, completion, disarm, mode/target change, or link loss disables both
controls without a substitute hold or navigation stream.

## 5. Structural validation versus operational policy

Prototype validation prevents malformed data: missing fields, non-numeric values, invalid coordinates, non-positive speed/time/radius, illegal ordering, unknown actions, or missing Land. It intentionally does not impose maximum altitude, maximum range, maximum speed, minimum radius, maximum hold time, a geofence, terrain clearance, or aircraft-specific capability rules.

The architecture includes a `MissionPolicy` interface and typed policy findings so future reviewed profiles can add those constraints without contaminating the core model. The prototype implementation returns no operational findings and the UI makes no claim that the mission is safe merely because it is structurally valid.

## 6. State-dependent gates

| Operation | Minimum gate |
|---|---|
| Compile | structurally complete mission |
| Upload | USB, same target, disarmed, compiled, replacement confirmed |
| Verify | accepted upload plus full successful readback comparison |
| Arm | SiK connection fresh, same target, disarmed, verified mission, readiness reviewed, native checks requested |
| Start | armed, verified mission still current, command link fresh |
| Pause/Resume | mission running/paused respectively, command link fresh |
| Land Here Now | airborne/armed state appropriate, command link fresh, deliberate confirmation |

ArduCopter can still reject any command. These gates do not replace native checks.

## 7. Non-functional requirements

- UI remains responsive during serial, protocol, and SITL operations.
- Mission compilation is deterministic and pure.
- Protocol state machines are testable with a fake clock and fake transport.
- Important state transitions and acknowledgments are logged with timestamps and vehicle identity.
- Connection loss fails closed in the UI and does not generate substitute flight setpoints.
- Windows setup is repeatable from a clean checkout.
- Dependencies are pinned and CI runs formatting, lint/type checks, and tests.
- Map/JS bridge messages use a versioned, validated schema; untrusted web content cannot issue vehicle commands.

## 8. Explicit exclusions

- ArduCopter firmware changes or automatic firmware updates
- RTL control or RTL mission item (without altering separately configured onboard failsafe behavior)
- parameter writes, calibration, firmware flashing, arming-check changes
- force arming, mid-air disarm, motor/servo/relay/payload controls
- raw mission editor, arbitrary MAVLink console, mission import from arbitrary native files
- Guided navigation/setpoint streaming, follow-me, mission jumps, scripting, spline paths
- operational geofence/limits, terrain following, obstacle detection/avoidance, route feasibility claims
- multi-vehicle operations, cloud accounts/sync, collaborative editing, offline map cache

## 9. Prototype acceptance

The prototype passes only when:

- a beginner can build, edit, save, load, and review a Takeoff–Proceed–Hold–Circle–Land mission without raw MAVLink fields;
- all required labels and Circle cues are visible and correct;
- Land uses the clicked location and creates approach waypoint plus native Land;
- unsupported or RTL commands cannot be represented or emitted;
- upload failure cannot appear as success, and Verified requires a complete matching readback;
- native pre-arm failures are visible and cannot be bypassed;
- representative and negative-path missions pass automated SITL tests;
- a Mission Planner readback of the pinned target shows the expected mission sequence;
- USB props-off hardware tests pass before the SiK props-off tests;
- no flight test begins until its separately approved staged test procedure is satisfied.
