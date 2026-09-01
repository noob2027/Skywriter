# Task 109 — installed UI triage and Confirm point repair

## Goal

Treat SKYWriter as an installed Windows application: reproduce and repair the owner-visible
**Confirm point** failure, audit every visible control, and require real packaged screen
evidence without touching hardware or widening flight authority. Deliver unsigned 0.1.3.

## Base and safety boundary

Base: merged Task 108 on `main` (`4be19c80c20436c767e824e08521a643b6111716`).
Run serially. No COM discovery/open, vehicle connection, upload, pre-arm request, arming,
AUTO, Pause/Resume, Land command, motor, parameter, firmware, or flight action is permitted.
The acceptance mode hard-blocks the MAVLink open boundary and reports attempted and
successful opens. It is explicit test mode, not a hidden production bypass.

## Reproduced root causes

The exact accepted 0.1.2 merge-build installer was installed and launched on the owner-like
Windows desktop. With a rendered-map point pending, the Confirm button was visible at the
bottom of the sidebar. Blank altitude made its connected handler reject the request, but
the only error label was laid out *after* the pending card below the scroll viewport. The
click and parser worked; the human saw no changed list, route, marker, focus, or message.

A second P1 defect cleared the pending editor before synchronously emitting append/replace.
If the application service rejected a mutation—for example replacing a non-final point with
Land—the editable point and entered values were discarded or repopulated with stale state.
There was no explicit re-entry lock.

Installed source audit also found enabled Discover USB/SiK and native pre-arm buttons with
no controller bound in `MainWindow`; these emitted unconsumed intents and looked like no-ops.

## Implemented outcome

- Validation is field-specific, adjacent, accessible, focused, and deterministically
  scrolled into view for Takeoff, Proceed, Hold, Circle, and Land.
- Confirm uses an explicit in-flight transaction. It ignores re-entry, disables Confirm and
  Cancel while resolving, preserves the complete form on rejection, and clears only after
  the requested snapshot mutation is visible.
- Success updates list, summary, route, markers, and an accessible message; no stale editor
  remains. New and successful Load also clear transient state.
- Builder/map minima and scroll behavior support 1498×758 and 1366×768; keyboard order,
  button activation, label buddies, and accessible names are covered.
- The installed shell shows an explicit no-controller explanation and disables all
  Connected, Preflight, and Flight commands. No vehicle architecture or gate was changed.
- Packaged acceptance installs the exact Setup, resolves and activates its Start-menu
  shortcut from an arbitrary working directory, uses production widgets plus native Qt
  mouse/keyboard hit paths, writes only to its test temp root, records screenshots/JSON,
  proves offline provider use and zero vehicle I/O, closes, and uninstalls in `finally`.

The exhaustive inventory is in
[`docs/task-109-control-triage.md`](../docs/task-109-control-triage.md). Hardware controls
were presentation-audited only and are not claimed as live-tested.

Representative installed evidence is under `docs/screenshots/task-109/`: the exact 0.1.2
offscreen-error reproduction, 0.1.3 visible validation, successful commit, retained
downstream rejection, and the honest Connected-tab gate. The accepted 0.1.2 merge-build
baseline was 149,194,983 bytes with SHA-256
`edf98519b341a0fd424a14527cfd64d7be33fa950c3f6c901305688eb3d9a684`.

## Windows installer evidence

- Installer: `SKYWriter-Prototype-Setup-0.1.3.exe`
- Size: 152,286,232 bytes
- SHA-256: `b91aeba6b18f788e85104ca25566d458b1d12225e2615a86545f0621820a7aca`
- Authenticode: unsigned (`signed: false`); SmartScreen warnings remain expected
- Runtime: CPython 3.12.10, PyInstaller 6.22.2, Inno Setup 6.7.3

The exact final Setup passed silent per-user install, resolved Start-menu shortcut launch
from an arbitrary directory, the pre-existing controlled-tile pixel smoke, production-widget
acceptance at 1498×758 and 1366×768, 17 full-window screenshots, all offline tab gates, zero
MAVLink-open attempts, zero successful opens, deterministic close, and clean uninstall. The
available desktop could not truthfully provide a 1920×1080 run, so that optional layout is
recorded as skipped rather than inferred.

## Required verification

- formatting, lint, strict typing, full tests, and repository-pinned SITL policy checks;
- exact CPython 3.12.10 installer build;
- silent install, exact Start-menu shortcut activation, normal production-widget acceptance,
  arbitrary working directory, deterministic close, and clean uninstall;
- offline/local map fixture only, zero hardware-I/O attempts, exact artifact hash/size, and
  unsigned status;
- final diff scan for temp outputs, personal paths, board identity, COM references, and
  unrelated line-ending churn.

## Stop

Open a pull request and stop. Do not merge, tag, publish a GitHub Release, connect hardware,
or claim bench, motor, flight, or hardware-control readiness.
