# Connected mission integration

Task 009 composes the accepted offline compiler, ArduCopter 4.6.3 compatibility
envelope, mission-only MAVLink adapter, read-only telemetry adapter, and pinned SITL
harness. It does not merge those compartments or make the offline Builder depend on
a connection or simulator.

## Production boundary

`ConnectedMissionService` owns immutable application state and accepts an injected
`ConnectedVehiclePort`. `ConnectedMavlinkPort` delegates discovery to the accepted
heartbeat selector, upload/download to `MissionProtocol`, success to exact
compatibility-envelope verification, and telemetry to
`TelemetryAdapter`/`TelemetryPoller`. It contains no protocol normalization and no
generic send method.

The connected UI emits typed intents only. Blocking discovery and mission transactions
remain caller-worker responsibilities; Qt callbacks do not perform serial or MAVLink I/O.
The visible surface contains mission inspection, explicit replacement approval,
upload/readback verification, disconnect, telemetry refresh, and same-vehicle SiK
re-verification. It contains no Arm, mode, AUTO, Pause/Resume, Land Here Now, RTL,
parameter, or generic command control.

## Fail-closed lifecycle

```text
compiled
  -> USB discovered + exactly one target selected
  -> existing mission downloaded and shown
  -> operator explicitly confirms replacement
  -> fresh selected-target heartbeat and HOME_POSITION
  -> disarmed USB upload
  -> accepted ACK + complete INT readback + normalized exact match
  -> USB_VERIFIED
  -> disconnect => REVERIFY_REQUIRED
  -> SiK discovers the same vehicle identity
  -> fresh telemetry + complete download + exact match
  -> SIK_VERIFIED
```

The upload preparation path uses an explicit receive-side readiness handshake: it exits
only after both the selected-target heartbeat and `HOME_POSITION` have crossed the real
telemetry adapter. Reaching the bounded deadline without Home returns typed
`HOME_UNRESOLVED`; a partial snapshot is never treated as uploadable.

An edit clears the translated package and verification. Cancellation, disconnect,
stale identity, wrong identity, missing/stale/wrong-vehicle Home, unexpected armed state,
negative acknowledgment, retry exhaustion, protocol error, or any unapproved readback
difference cannot produce readiness. Reconnection by itself never restores readiness.

## UI evidence

![USB replacement review](screenshots/task-009/01-usb-replacement-review.png)

![Same-vehicle SiK verification](screenshots/task-009/02-sik-verified.png)

## Pinned SITL evidence

The Ubuntu workflow runs the genuine connected test twice in fresh stock ArduCopter
4.6.3 processes on isolated port blocks 26200 and 26300. Each run proves USB discovery,
live Home translation, mission upload and exact readback, disconnect/restart over an
explicitly classified SiK link, same-vehicle comparison, read-only telemetry, execution
of Takeoff–Proceed–Hold–Circle–Land, and a protocol-independent raw reference readback.
Task 104 keeps that complete first sortie, including Task 103 Pause/Resume and the
desktop link interruption, then starts a second verified stock process for a production
USB/SiK lifecycle and safe simulated Land Here Now sortie. The fresh process is necessary
because pinned Copter intentionally remains in unarmable AUTO after completed native Land;
the test does not invent a mode-reset command.

Stock Copter cannot execute a mission without normal arming and native mission start.
Task 101 routes normal Arm through its production application/gateway boundary with the
fixed normal value, exact ACK correlation, and later armed-heartbeat proof. Task 102 now
routes `MAV_CMD_MISSION_START` through its separate production boundary with fixed
first/last-item parameters both zero. The pinned 4.6.3 handler enters AUTO, sets the
internal auto-armed state, and starts or resumes the uploaded mission. Running additionally
requires a later armed AUTO heartbeat and in-bounds native mission progress. This order
is required because stock `AUTO_OPTIONS=0` deliberately rejects a separate normal arm
request made after AUTO is selected. Before either stimulus, the test requires a fresh
same-connection
`SYS_STATUS` proving ArduPilot's enabled pre-arm bit is healthy. The harness launches
with the exact official Copter defaults selected by the pinned source's `quad` mapping;
their hash and effective `FRAME_CLASS=1` / `FRAME_TYPE=0` are retained in evidence.
This is stock startup initialization through `--defaults`, not a MAVLink parameter
write. Task 102 still owns the production AUTO-start compartment.

The direct stock-binary TCP session does not assume an ambient `SYS_STATUS` stream.
The same-connection check immediately before the normal Arm uses a bounded read-only
`MAV_CMD_REQUEST_MESSAGE(SYS_STATUS)` handshake. It never invokes
`MAV_CMD_RUN_PREARM_CHECKS`; an accepted request-message command is not treated as
readiness unless the returned health bitmap itself passes. Reusable Task 006 readiness
does not claim arm readiness while GPS/EKF state is still settling.

AUTO also requires the estimator position that pinned Copter checks after arming. The
execution boundary therefore uses the same read-only request mechanism for
`EKF_STATUS_REPORT` and requires `EKF_POS_HORIZ_ABS` without
`EKF_CONST_POS_MODE` before the normal arm. Those are the pinned source's armed
absolute-position conditions. Position and pre-arm snapshots share one 30-second
readiness deadline; neither the arm nor AUTO stimulus is sent if either condition is
unresolved.

The direct binary also does not stream `GLOBAL_POSITION_INT` or `EXTENDED_SYS_STATE` by
default. During execution the test sends bounded read-only `MAV_CMD_REQUEST_MESSAGE`
requests for those snapshots before each telemetry refresh; both responses must cross
the accepted telemetry adapter. Completion requires a climb above 2 m, the navigation
item immediately before Land to be reached, and a final typed on-ground plus disarmed
state. The pinned 4.6.3 `NAV_LAND` verifier disarms on landing and deliberately returns
incomplete, leaving the state machine on the Land item; it therefore does not emit
`MISSION_ITEM_REACHED` for that final item. The test records this stock behavior instead
of demanding an event the pin does not produce.

The protocol-independent exact readback runs on the same SiK-classified connection after
same-vehicle re-verification and before the flight stimulus. This placement is
intentional: pinned Copter constructs sequence 0 from live `AP::ahrs().get_home()` on
every request, so normal estimator refinement during flight can change its reported
altitude by a centimetre even though stored mission items are unchanged. Moving the
readback preserves exact fail-closed Home verification; no tolerance or post-flight Home
normalization is introduced.

Each evidence directory retains the verified binary identity, exact process command,
SITL stdout/stderr, readiness protocol trace, `connected-integration.json`, teardown
result, and `SHA256SUMS`, including on failure. The workflow uploads the complete tree
for 30 days.

Task 100 extends this same twice-fresh connected evidence path only after same-vehicle
SiK verification. Task 101 then consumes its current reviewed fingerprint and runs the
positive normal Arm through the production boundary. After native Land auto-disarms
while Copter remains in AUTO, the same closed gateway captures a native rejected normal
Arm as isolated test evidence; the now-invalid production review gate would send
nothing. See [`native-prearm.md`](native-prearm.md) and
[`normal-arm.md`](normal-arm.md).

Task 102 consumes the current Task 101 Armed fingerprint and replaces the former
test-only positive mission-start stimulus with its fixed production boundary. Immediately
after Running proof, the test closes the desktop link and confirms the application state
fails closed. A new observation-only connection later sees mission progress advance and
native Land complete, demonstrating onboard execution without fallback streaming. A
nonzero first-item selector is retained only as test-only native-denial evidence because
the production API cannot supply it. See [`auto-start.md`](auto-start.md).

Task 103 preserves two production Pause/Resume cycles during different first-sortie
mission actions before that link interruption. Task 104 then starts a second wiped,
identity-verified stock process, uploads and exactly verifies the same logical mission
through the production USB boundary, re-verifies it on SiK, repeats native readiness
review, normal non-forced Arm, and fixed AUTO start, and
waits for current armed AUTO/In Air mission evidence. The initial Land Here Now activation
sends nothing; only the deliberate confirmation emits fixed command 21 with seven zero
parameters. A matching accepted ACK plus later Land mode and native Landing telemetry is
required before the result is Landing, and the run separately observes final disarm plus
On Ground. Only fixed request-message 512 for `EXTENDED_SYS_STATE` 245 is added to that
command transaction. See [`pause-resume.md`](pause-resume.md) and
[`land-here-now.md`](land-here-now.md).

## Platform and hardware limits

The official pinned SITL artifact is Linux x86_64. Windows developers can run all
deterministic application, protocol, UI, and harness-contract tests locally, but genuine
stock-SITL execution occurs on the approved Ubuntu GitHub runner unless an equivalent
Linux environment is installed. SITL is evidence infrastructure, never a runtime
dependency.

No real-hardware claim is made. The explicit pre-hardware compatibility-profile gate in
[`development-plan.md`](development-plan.md) blocks props-off and flight work until the
exact Matek H743 revision/firmware artifact, FC telemetry-port observations, matched
Holybro SiK identities/settings, and same-vehicle MAVLink identity are recorded and
reviewed. SKYWriter does not hardcode or write those parameters, and Mission Planner and
SKYWriter may not compete for one COM port without a separately reviewed router design.
