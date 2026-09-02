# Task 111 — installed Preflight and normal Arm composition

## Goal

Make the accepted Task 100 native Preflight request/review and Task 101 normal Arm path
usable from a normal 0.1.5 Windows install by composing them onto Task 110's explicit
Connected session and controller lifecycle.

## Boundary

- Reuse the existing `PrearmReadinessService`, `NativePrearmGateway`, `NormalArmService`,
  and `NativeNormalArmGateway`; do not redefine their gates or proof semantics.
- Mirror Task 110's controller pattern: typed UI intents, immutable snapshots, one
  explicitly opened physical link, one cancellable worker transaction at a time, and no
  blocking I/O on the Qt thread.
- Keep the installed Flight panel unavailable.
- Add no Disarm, force-arm, AUTO, Pause/Resume, Land Here Now, RTL, `PARAM_SET`, stream
  request, Guided/setpoint, generic `send_command`, firmware, driver, motor, mission-
  execution, or flight behavior.
- Never retain reviewed readiness after the mission revision/digest, selected identity,
  exact SiK verification, link freshness, or disarmed evidence stops matching.
- Do not run a live Arm during local or installed acceptance.

## Acceptance

- Focused application, infrastructure, UI/controller, packaging, and negative-path tests.
- Installed-session tests prove that mission, native-prearm, and normal-Arm typed facets
  share one physical link and cannot overlap transactions.
- UI tests prove natural fail-closed Preflight gating without a link, explicit review before
  normal Arm, duplicate suppression, and immediate invalidation on mission/context change.
- The Flight panel remains visibly unavailable and no prohibited command surface appears.
- The 0.1.5 installer passes the exact Windows build, smoke, hardware-blocked installed UI,
  map-pixel, zero-I/O, shortcut, and uninstall gates.
- Full repository checks and the approved Ubuntu stock ArduCopter 4.6.3 workflow run twice
  from fresh processes.

## Stop

Open the Task 111 pull request and report its URL plus required Windows and twice-fresh
Ubuntu stock-SITL status. Do not merge, tag, publish a GitHub Release, run a live Arm,
perform C1/bench/flight work, or begin another command compartment.
