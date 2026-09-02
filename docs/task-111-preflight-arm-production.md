# Task 111 installed Preflight and normal Arm production composition

## Outcome and safety boundary

Version 0.1.5 makes the already accepted Task 100 native Preflight request/review and
Task 101 normal Arm path available from a normal installed Windows launch. Stock
ArduCopter remains the only arming authority. An accepted pre-arm request means only that
the native checks ran, and an accepted normal-Arm acknowledgment is not shown as Armed
without a later fresh selected-target armed heartbeat.

Task 111 does not bind Flight and adds no Disarm, force-arm, AUTO, Pause/Resume, Land Here
Now, RTL, `PARAM_SET`, stream request, Guided/setpoint, generic command, firmware, driver,
motor, mission-execution, or flight path. Local and installed acceptance do not run a live
Arm.

## Installed composition

```text
explicit Task 110 Connected session
  -> current same-target SIK_VERIFIED mission and fresh disarmed telemetry
  -> typed native Preflight request
  -> exact command 401 gateway and correlated native result
  -> healthy, non-conflicting native sensor assessment
  -> explicit operator review of the current mission/target fingerprint
  -> typed normal Arm request
  -> exact command 400 [1, 0, 0, 0, 0, 0, 0] gateway
  -> correlated accepted ACK
  -> later fresh selected-target armed heartbeat
  -> Armed terminal interlock for the installed session
```

The Preflight widget remains an intent/snapshot view. A dedicated installed
`PreflightController` synchronizes the existing Task 100 and Task 101 services with the
authoritative Connected snapshot. Blocking protocol work is delegated to Task 110's
Connected controller, which retains one cancellable worker operation at a time.

One explicitly opened installed session owns three closed facets over one physical
connection: mission protocol, exact native pre-arm request, and exact normal Arm. The
facets share the same receiver but expose no command identifier or caller-supplied
parameter list. Connected mission/telemetry work and command work cannot run concurrently.
Shutdown, disconnect, cancellation, and link failure close the sole handle without a
cleanup command.

## Fail-closed readiness and command behavior

The existing services remain the authority for every gate and result. Native Preflight
sends nothing without current selected-target SiK verification, fresh matching disarmed
telemetry, and an idle shared transaction slot. Normal Arm additionally requires healthy
native evidence and the operator's review of that exact current mission/target fingerprint.

Mission edits, compiled revision or digest changes, an inspected/downloaded onboard
mission difference, target or identity changes, loss of exact SiK verification,
disconnection, stale telemetry, armed telemetry, and contradictory observations clear the
old readiness immediately. They never preserve `USB_VERIFIED`, `SIK_VERIFIED`, reviewed
readiness, or normal-Arm availability for a changed mission. Duplicate or overlapping
requests are rejected before a second transaction can start.

The installed Flight panel remains visibly unavailable. Its AUTO, Pause/Resume, and Land
Here Now controls are not connected to a controller or gateway. Repository confinement
tests continue to reject Disarm, force-arm, RTL, `PARAM_SET`, Guided/setpoint, and generic
command surfaces.

## Deterministic evidence

- Application/service: existing Task 100/101 gate, acknowledgment, telemetry-proof,
  duplicate, and invalidation matrices remain unchanged and passing. The focused Task 111
  service, gateway, controller, installed-shell, packaging, and architecture run passed 112
  tests.
- Infrastructure: the shared installed-session facets retain exact command 401 and exact
  command 400 normal parameters while one physical receiver is serialized. Tests also
  prove idempotent single-handle close and the absence of generic or prohibited surfaces.
- UI/controller: installed binding, off-thread ownership, busy exclusion, disconnected
  fail-closed state, review-to-Arm transition, Armed interlock, context invalidation, and
  Flight-unbound checks passed. The current tree collects 566 tests. A repository run that
  excluded the two WebEngine-containing modules passed 549 tests with the two Linux-only
  SITL cases skipped on Windows; those modules contain 15 tests. In the full run, this
  workstation's known GLES context failure prevented 12 of those tests from initializing,
  while the other three passed. Clean Windows CI remains authoritative for that boundary.
- Packaging: 0.1.5 frozen-runtime imports, installed Preflight gate, deterministic serial
  fixture, map pixels, shortcut launch, zero vehicle-I/O audit, and uninstall all passed.
  The acceptance produced 17 screenshots, recorded zero vehicle-I/O attempts and
  successes, showed Preflight controller-bound with readiness and normal Arm closed, never
  clicked either action, and showed Flight unbound with every Flight control disabled. The
  packaged map loaded 8 controlled tiles with a 0.99956 non-black pixel ratio. The payload
  includes the repository-pinned upstream pyserial 3.5 BSD license and provenance notice.
- Pinned SITL: the approved stock ArduCopter 4.6.3 workflow, including the existing
  production Task 100/101 positive and native-negative evidence, runs twice from fresh
  Ubuntu processes in required PR CI.

Final 0.1.5 installer evidence:

- artifact: `SKYWriter-Prototype-Setup-0.1.5.exe`
- size: **153,397,444 bytes**
- SHA-256: **`7178d90ed5f45eff13be4011febcbb4bb740e0ef922b5554005938082c9e1fae`**
- signing: **unsigned** (`build-metadata.json` records `false`; Authenticode reports
  `NotSigned`)

## Assumptions and limits

- The accepted Task 110 session, Task 100/101 services and gateways, stock ArduCopter
  4.6.3 pin, mission protocol, compatibility envelope, and telemetry contracts remain
  unchanged.
- Normal startup remains inert: no serial enumeration, port open, vehicle command, or
  network request occurs without the existing explicit operator actions.
- Windows packaging and Linux stock-SITL evidence are not real Matek/Holybro hardware,
  bench, motor, mission-execution, or flight evidence.
- A telemetry-confirmed Armed result is terminal for further Task 111 actions in that
  installed session; Task 111 adds no Disarm or alternate recovery command.

## Next safe task

After review and merge of the Task 111 pull request, the next safe activity is a separately
authorized, supervised, propellers-removed and disarmed Big Bird evidence procedure using
the existing C1 contract. It must stop before any live Arm, motor, mission-execution, or
flight action. Task 111 does not start or claim that work.
