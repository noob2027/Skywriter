# Task 101 — normal acknowledged Arm

## Scope and authority

Task 101 adds one production vehicle action: request a normal Arm from the selected
stock ArduCopter 4.6.3 target. ArduCopter remains the arming authority. SKYWriter cannot
override a native denial and never treats a click, packet transmission, or accepted
acknowledgment as proof that the vehicle is armed.

There is no Disarm, mid-air disarm, AUTO start, mode change, Pause/Resume, Land Here
Now, RTL, parameter write, arming-check bypass, Guided setpoint, or generic command API.
The mission compiler, compatibility envelope, mission protocol, and telemetry parser are
unchanged.

## Exact pinned semantics

The immutable published-SITL source identity remains
`3fc7011a7d3dc047cbb17d8bd98ee94577d144c6`. Its
[`handle_command_component_arm_disarm`](https://github.com/ArduPilot/ardupilot/blob/3fc7011a7d3dc047cbb17d8bd98ee94577d144c6/libraries/GCS_MAVLink/GCS_Common.cpp#L4671-L4696)
accepts the normal arm selector, runs native arming checks, returns accepted only if
ArduPilot arms, and otherwise returns failed. The production link emits command 400
with confirmation zero and parameters `[1, 0, 0, 0, 0, 0, 0]`. Callers cannot provide
or alter any parameter.

`NativeNormalArmGateway` has one public method, `request_normal_arm()`, and its closed
link has one send method, `send_normal_arm()`. The concrete link contains no alternative
arming values. Repository confinement tests reject known bypass values anywhere in
production source and reject generic, Disarm, mode, parameter, RTL, landing, or setpoint
surfaces.

## Application gate

`NormalArmService` sends nothing unless all of these are current together:

- an explicitly classified, connected SiK link;
- selected target, expected mission vehicle, and telemetry identify the same system and
  component;
- selected-target heartbeat and command-gate telemetry are fresh and agree that the
  vehicle is disarmed;
- the current mission has exact same-vehicle SiK readback verification;
- Task 100's native request is accepted, its sensor assessment is healthy, and the
  operator has reviewed that exact mission/target fingerprint;
- the caller confirms the command channel is idle.

Mission edits, digest changes, target changes, link loss/staleness, telemetry conflict,
armed state, or any Task 100 review revision close the gate. Repeated activation while a
request is pending is retained as an ignored duplicate and never sends twice.

## Acknowledgment and telemetry proof

The gateway correlates only command 400 `COMMAND_ACK` from the selected target and
addressed to the local GCS. Wrong source, command, address, missing result, timeout,
cancellation, and link loss are terminal fail-closed states. Native `STATUSTEXT` from
that target is retained for the operator.

An accepted ACK opens only a bounded telemetry-confirmation phase. `Armed` requires a
later, fresh heartbeat from the selected target with the armed bit set. An accepted ACK
with no fresh armed heartbeat is presented as uncertain; fresh heartbeats that remain
disarmed are presented as telemetry disagreement. A heartbeat observed before the ACK
cannot satisfy the post-ACK proof.

## UI and worker boundary

The Preflight panel emits immutable `NormalArmRequested` intents. The button locks
synchronously and the worker handoff rejects another active operation. The injected
application operation runs on a Qt thread-pool worker; only immutable snapshots return
to the UI thread. The panel distinctly renders pending, telemetry-confirmed Armed,
native rejection text, unsupported, timeout, cancellation, wrong ACK/target, stale or
lost link, missing armed telemetry, telemetry disagreement, and every blocked gate.

Reviewed screenshots:

- [pending](screenshots/task-101/01-pending.png)
- [telemetry-confirmed Armed](screenshots/task-101/02-armed-confirmed.png)
- [native rejection](screenshots/task-101/03-native-rejected.png)
- [timeout](screenshots/task-101/04-timeout.png)
- [link loss](screenshots/task-101/05-link-loss.png)
- [telemetry disagreement](screenshots/task-101/06-telemetry-disagreement.png)

## Installed composition

Task 111 binds the unchanged Task 101 service and gateway to the installed Preflight
panel. The typed `NormalArmRequested` intent may enter the shared Connected controller's
single worker only while the exact Task 100 readiness fingerprint and every Task 101 gate
remain current. The installed session exposes a dedicated normal-Arm facet over the same
physical link as mission and native-prearm work; it adds no caller-provided command or
parameter surface.

The button remains unavailable without the exact reviewed same-mission/same-target SiK
state. After a telemetry-confirmed Armed result, the installed session interlocks further
Preflight and Arm requests. A mission edit, onboard mission difference, target or identity
change, disconnection, stale telemetry, or loss of verification invalidates the prior
readiness rather than retaining authorization. This composition does not add Disarm,
force-arm, AUTO, Pause/Resume, Land Here Now, RTL, `PARAM_SET`, or a generic command API,
and Flight remains deliberately unbound.

## Stock-SITL evidence

The existing twice-fresh connected workflow now routes its positive normal Arm through
the production Task 101 application service, gateway, and concrete link. Each isolated
stock process must retain the exact command parameters, matching ACK, and a later armed
heartbeat before mission execution can continue.

After native Land completes and Copter auto-disarms, pinned stock Copter remains in
AUTO. A direct normal-only gateway request is then expected to receive native failure;
this is isolated negative evidence because the production Task 100 review gate is no
longer current and would send nothing in that condition. It does not add a reusable
mode or mission-start action. Safe cleanup remains native Land followed by bounded
stock-SITL process teardown; SKYWriter does not send Disarm as cleanup.

## Limits and rollback

Evidence is Linux stock SITL, not real hardware. Matek H7A3/H743 target/revision,
USB-C interface mapping, and Holybro/alternative SiK model, firmware, region, and baud
remain unresolved hardware validation inputs. Task 101 does not authorize props-on or
real-flight use.

Revert the Task 101 PR to remove the normal-arm application/gateway/UI compartments,
tests, screenshots, and evidence updates. Task 100 native readiness review remains
intact, while Task 102 and every later command compartment return to blocked status.
