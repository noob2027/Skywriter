# Task 109 installed-control triage

## Scope and evidence key

This is the durable control inventory for the installed Windows 0.1.3 prototype. The audit
used the production widgets in the exact packaged executable, launched through its installed
Start-menu shortcut from an arbitrary working directory. Routine acceptance used only the
offline grid and local fixtures. The MAVLink open boundary was hard-blocked and the audit
recorded zero vehicle-I/O attempts and zero successful opens.

Evidence keys: **PA** means packaged installed UI acceptance; **UT/IT** means automated
regression evidence; **SRC** means source/controller audit without hardware; **LG** means
deliberately deferred to the later supervised hardware gate. Hardware controls are not
represented as live-tested merely because their fail-closed presentation was verified.

Representative captures are in [`docs/screenshots/task-109/`](screenshots/task-109/):

- `01-installed-0.1.2-confirm-no-visible-error.png` — Confirm is visible, but the validation
  label is below the sidebar viewport;
- `02-installed-0.1.3-visible-validation.png` — specific feedback is adjacent, focused, and
  inside the viewport;
- `03-installed-0.1.3-confirm-success.png` — list, summary, route, and marker reflect success;
- `04-installed-0.1.3-rejection-retained.png` — downstream refusal leaves the editor and
  entered values intact;
- `05-installed-0.1.3-connected-gate.png` — unbound hardware controls are visibly unavailable.

## Builder

| Control | Classification | Expected effect and visible feedback | Evidence | Severity | Next action |
|---|---|---|---|---:|---|
| New | Works | Reset mission, summary, route, selection, and transient editor. | PA, IT | — | Add destructive confirmation as P2 follow-up. |
| Edit settings | Works | Reopen Takeoff settings with saved values; Cancel preserves them. | PA, UT | — | None. |
| Save | Works | Use the safe picker, write versioned JSON, and show saved status. | PA, IT | — | Acceptance stays inside its temp seam. |
| Load | Works | Load JSON and replace mission, route, list, summary, and transient state. | PA, IT | — | Add destructive confirmation as P2 follow-up. |
| Review & Compile | Works-with-gate | Compile a valid mission; show a specific refusal otherwise. | PA, IT | — | Upload remains separately gated. |
| Basemap selector | Works-with-gate | Offline grid is deterministic; explicit OSM selection reports network state. | PA, map tests | — | Public OSM stays outside routine acceptance. |
| Retry | Works-with-gate | Retry the selected network provider and report its result. | fixture tests, SRC | — | Retain bounded local-fixture coverage. |
| Latitude / Longitude | Works | Accept finite in-range decimals; invalid input is field-specific. | PA, UT | — | None. |
| Go / recenter | Works | Recenter the rendered map and show accepted coordinates. | PA, map tests | — | None. |
| Center Home | Disabled-by-design | Explain that isolated Builder has no authoritative Home. | PA, UT | — | Enable only with trusted connected state. |
| Center Vehicle | Disabled-by-design | Explain that isolated Builder has no current vehicle position. | PA, UT | — | Enable only with fresh trusted telemetry. |
| Takeoff | Works-with-gate | Focus altitude, require a positive value and warning acknowledgement. | PA, UT | — | None. |
| Warning acknowledgement | Works-with-gate | Explicitly acknowledge warning before committing mission points. | PA, UT | — | None. |
| Rendered-map click | Works-with-gate | With Takeoff confirmed, create a pending marker and visible editor. | PA, map tests | — | None. |
| Proceed editor | Works | Visible focused error on rejection; success updates all mission views. | PA, UT/IT | P1 fixed | None. |
| Hold editor | Works | Validate altitude/time visibly; commit Hold detail everywhere. | PA, UT/IT | P1 fixed | None. |
| Circle editor | Works | Validate altitude/radius visibly; commit Circle geometry/detail. | PA, UT/IT | P1 fixed | None. |
| Land editor | Works-with-gate | Validate visibly; preserve values when non-final Land is rejected. | PA, UT/IT | P1 fixed | None. |
| Confirm point | Works | Mouse/keyboard validate visibly, commit once, and retain edits on refusal. | PA, UT/IT | P1 fixed | None. |
| Cancel point | Works | Remove only pending editor/marker, preserving committed state. | PA, UT | — | None. |
| Mission selection | Works | Select row/marker and open its populated editor. | PA, UT/IT | — | None. |
| Delete | Works | Remove selection and update list, summary, route, and markers. | PA, IT | — | Add destructive confirmation as P2 follow-up. |
| Undo | Works-with-gate | Restore prior mutation when history exists; otherwise stay disabled. | PA, IT | — | None. |
| Clear | Works-with-gate | Clear actions and update every rendered representation. | PA, IT | P2 | Add explicit confirmation later. |
| Remove Land / reopen | Works | Remove final Land and reopen route authoring. | PA, IT | — | None. |

## Connected

The installed shell has no production vehicle controller binding. Task 109 fixes the prior
enabled no-op presentation by showing an adjacent explanation and disabling every command.
Underlying hardware behavior is **not** live-tested here.

| Control | Classification | Expected effect and visible feedback | Evidence | Severity | Next action |
|---|---|---|---|---:|---|
| Discover USB | Disabled-by-design | Disabled with no-controller explanation; no COM enumeration/open. | PA, SRC; 0 I/O | P1 fixed | LG: supervised connection audit. |
| Discover SiK | Disabled-by-design | Disabled with explanation; no radio or COM access. | PA, SRC; 0 I/O | P1 fixed | LG: supervised SiK audit. |
| Inspect onboard mission | Disabled-by-design | Disabled until a verified controller exists. | PA, SRC | — | LG. |
| Upload and verify | Disabled-by-design | Disabled; cannot upload from offline installed shell. | PA, SRC | — | LG; preserve all safety gates. |
| Refresh telemetry | Disabled-by-design | Disabled; no stream request is sent. | PA, SRC | — | LG. |
| Re-download and compare | Disabled-by-design | Disabled until a verified transaction exists. | PA, SRC | — | LG. |
| Disconnect | Disabled-by-design | Disabled because no connection can be created. | PA, SRC | — | LG. |

## Preflight

| Control | Classification | Expected effect and visible feedback | Evidence | Severity | Next action |
|---|---|---|---|---:|---|
| Request native pre-arm checks | Disabled-by-design | Disabled with explanation; emits no command. | PA, SRC; 0 I/O | P1 fixed | LG: verified identity and supervised hardware. |
| Review native pre-arm result | Disabled-by-design | Disabled until a fresh native result exists. | PA, SRC | — | LG. |
| Arm | Disabled-by-design | Disabled; headline and detail state the block consistently. | PA, SRC | P2 fixed | LG: preserve native/application gates. |

## Flight

| Control | Classification | Expected effect and visible feedback | Evidence | Severity | Next action |
|---|---|---|---|---:|---|
| Start AUTO | Disabled-by-design | Disabled with explanation; sends no command. | PA, SRC; 0 I/O | P2 fixed | LG only. |
| Pause | Disabled-by-design | Disabled and unable to emit a pause command. | PA, SRC; 0 I/O | — | LG. |
| Resume | Disabled-by-design | Disabled and unable to emit a resume command. | PA, SRC; 0 I/O | — | LG. |
| Land Here Now / confirm | Disabled-by-design | Primary/confirmation path inaccessible; sends no command. | PA, SRC; 0 I/O | — | LG: verify confirm/cancel separately. |
| Land Here Now cancel | Disabled-by-design | No confirmation dialog opens during offline acceptance. | PA, SRC | — | LG. |

## Ranked follow-up list

1. **Hardware gate, safety-critical:** bind and exercise Connected, Preflight, and Flight
   only in the existing staged supervised-hardware process.
2. **P2 offline usability:** add confirmation before New, Load, Clear, and destructive
   multi-action edits where accidental data loss is plausible.
3. **P2 platform coverage:** add a 1920×1080 scaled installed run on a worker whose real
   desktop supports it. The harness executes 1498×758 and 1366×768 and records an explicit
   skip instead of fabricating a large-screen result.
4. **P2 accessibility depth:** retain keyboard/accessibility regressions, then add a later
   Windows Narrator/manual screen-reader pass.
