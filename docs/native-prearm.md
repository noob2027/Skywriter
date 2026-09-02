# Native pre-arm request and readiness review

Task 100 adds one vehicle command capability: a dedicated request for stock
ArduCopter 4.6.3 to run its native pre-arm checks. It does not add Arm, disarm,
mode selection, AUTO start, Pause/Resume, Land Here Now, RTL, parameter writes,
or a generic MAVLink command channel.

## Exact pinned semantics

The immutable published-SITL source commit is
`3fc7011a7d3dc047cbb17d8bd98ee94577d144c6`. Its
[`GCS_MAVLINK::handle_command_run_prearm_checks`](https://github.com/ArduPilot/ardupilot/blob/3fc7011a7d3dc047cbb17d8bd98ee94577d144c6/libraries/GCS_MAVLink/GCS_Common.cpp#L4445-L4452)
handler implements these exact semantics:

1. if the vehicle is soft-armed, return `MAV_RESULT_TEMPORARILY_REJECTED`;
2. otherwise call `AP::arming().pre_arm_checks(true)`; and
3. return `MAV_RESULT_ACCEPTED` regardless of whether those checks reported a
   native failure.

The same pinned source routes only command 401 to that handler and emits a
`COMMAND_ACK` addressed to the requesting GCS. Consequently, an accepted ACK
means only that ArduCopter accepted and ran the request. It is not evidence that
the aircraft is armable. Native `STATUSTEXT` and the `SYS_STATUS` pre-arm bit
remain separate observations, and silence is never converted into readiness.

SKYWriter sends `MAV_CMD_RUN_PREARM_CHECKS` (`401`) as `COMMAND_LONG` with
confirmation zero and all seven reserved parameters zero. The concrete link has
only `send_prearm_checks()`; no command identifier or parameter array crosses the
application boundary.

## Application gate and state

`PrearmReadinessService` requires all of the following before it invokes the
gateway:

- an explicitly classified, connected SiK link;
- a fresh heartbeat from the selected same-vehicle target;
- disarmed state in both selected-target and typed heartbeat observations;
- `SIK_VERIFIED` for the current mission, with its exact readback digest still
  current; and
- an idle pre-arm command channel.

The service distinguishes accepted, rejected, unsupported, timed out,
wrong-target ACK, wrong-command or misaddressed ACK, stale link, lost link,
cancelled, wrong-link, armed, unverified-mission, and wrong-identity states. A
second request while one is pending is ignored and visibly recorded without a
second transmission.

Associated native `STATUSTEXT` is retained. The current typed telemetry snapshot
shows GPS, Home, EKF, battery, sensor flags, and any available native hardware-
safety message. Hardware safety is explicitly shown as unavailable when no
dedicated native observation was received; motor or sensor flags are not relabeled
as a safety-switch fact.

The review assessment is fail-closed:

- a present, enabled, healthy `MAV_SYS_STATUS_PREARM_CHECK` bit with no
  contradictory native failure text is shown as healthy native evidence;
- a missing/unhealthy bit or native `PreArm:` failure is shown as failed;
- healthy telemetry plus native failure text is shown as conflicting; and
- missing/stale sensor evidence remains unavailable.

The operator must explicitly acknowledge review of the result and available
observations. Even the resulting application gate is labelled as an application
readiness gate, not proof that ArduCopter will accept a later arm request. Mission
edit/digest change, identity change, stale/lost link, armed state, or loss of exact
verification invalidates the acknowledgment.

## UI and worker boundary

The Preflight widget emits only `NativePrearmChecksRequested` and
`PrearmReviewAcknowledgmentRequested` typed intents. It renders immutable
application snapshots and performs no MAVLink or blocking I/O. A caller-owned
worker must run `PrearmReadinessService.request_prearm_checks()` and return its
snapshot to Qt.

Meaningful reviewed states are retained as screenshots:

- [blocked gate](screenshots/task-100/01-gate-blocked.png)
- [pending and repeated request](screenshots/task-100/02-pending-repeated-request.png)
- [accepted, awaiting review](screenshots/task-100/03-accepted-awaiting-review.png)
- [explicitly reviewed application gate](screenshots/task-100/04-reviewed-application-gate.png)
- [native/telemetry conflict](screenshots/task-100/05-native-failure-conflict.png)
- [unsupported](screenshots/task-100/06-unsupported.png)
- [timeout](screenshots/task-100/07-timeout.png)
- [wrong acknowledgment](screenshots/task-100/08-wrong-ack.png)

## Installed composition

Task 111 supplies the previously deferred caller-owned composition. The installed
Preflight controller consumes only the widget's typed intents and delegates blocking work
to the existing Connected controller's single operation slot. One explicitly opened
installed session owns the mission, native-prearm, and normal-Arm typed facets over one
physical link; there is no second receiver or concurrent serial transaction.

The controller invokes the unchanged `PrearmReadinessService` and
`NativePrearmGateway`. Without a current selected SiK session, the application service
closes the gate before its defensive no-I/O gateway can be reached. Connected busy state,
mission revision changes, reinspection/readback differences, identity changes,
disconnection, stale telemetry, and armed telemetry immediately remove reviewed
readiness. The installed composition adds no new pre-arm semantics and no generic command
surface. Flight remains unbound.

## Stock-SITL evidence

The existing genuine connected mixed-mission test now exercises the production
Task 100 service and gateway after the accepted USB upload, exact readback, and
same-vehicle SiK verification. It preserves the exact command fields, matching
ACK, associated `STATUSTEXT`, typed assessment, operator-review state, and safety
declarations in the existing hashed evidence tree.

The pinned handler's only negative return is its armed case. The Task 100 evidence
calls the narrow pre-arm gateway directly while stock SITL is armed, while the
production application service is separately proven to block before transmission
for an armed snapshot. Task 101 subsequently replaces the old test-only positive
arming stimulus with its dedicated normal-only production boundary; it does not
change this pre-arm request/review behavior. See [`normal-arm.md`](normal-arm.md).
The simulator still terminates without a Disarm command.

The Ubuntu workflow runs this positive and rejected-command path twice in fresh,
isolated stock processes. The same evidence artifact retains the verified binary
and startup-default identities, effective frame configuration, process logs,
protocol trace, structured result documents, teardown proof, and `SHA256SUMS`.

## Limitations and rollback

This is Linux stock-SITL evidence, not Matek H7A3/H743, Windows USB, physical USB-C,
or SiK-radio evidence. Board target/revision, USB interfaces, radio model/firmware,
regulatory region, and baud plan remain later props-off inputs. No hardware or
flight claim is made.

Reverting Task 100 removes the pre-arm application state, dedicated gateway/link,
Preflight request/review UI, tests, screenshots, and documentation. Task 009's
mission verification, telemetry, mixed-mission execution, compiler, compatibility
envelope, and stock pin remain independently intact.
