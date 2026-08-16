# SKYWriter Pre–Task 005 Remediation and Readiness Handoff

**Repository:** `noob2027/Skywriter`  
**Prepared:** 2026-08-15 (America/New_York)  
**Audience:** Coding agent, maintainer, and human reviewer  
**Purpose:** Correct and close all known blockers in the offline parallel wave before creating the Task 005 pull request.

## 1. Operating directive

Do not begin Task 005, create its branch, or open its pull request until every blocking gate in this document is satisfied.

Read these files in full before taking action:

- `AGENTS.md`
- `README.md`
- `docs/product-specification.md`
- `docs/architecture.md`
- `docs/development-plan.md`
- `codex-tasks/README.md`
- `codex-tasks/003-map-builder-ui.md`
- `codex-tasks/004-mission-compiler.md`
- `codex-tasks/005-offline-integration.md`

The repository rules remain authoritative. Work only on the currently assigned existing branch and pull request. Do not merge pull requests, tag releases, or start later work. Human review and acceptance remain mandatory.

## 2. Current verified state

| Work | Pull request | State | CI | Readiness |
|---|---:|---|---|---|
| Documentation import | #1 | Merged | Accepted | Complete |
| Task 001 — foundation | #2 | Merged | Accepted | Complete |
| Task 002 — mission model | #3 | Merged | Accepted | Complete |
| Task 003 — map-builder UI | #4 | Draft, open, mergeable | Green | **Blocked; corrections required** |
| Task 004 — mission compiler | #5 | Draft, open, mergeable | Green | **Technically strong; human review required** |
| Task 005 — offline integration | Not created | Not started | N/A | **Not authorized** |

Important numbering clarification: pull request #5 implements **Task 004**. Task 005 has not started.

Both open pull requests currently pass Windows CI for dependency installation, formatting, linting, static typing, tests, and smoke launch. Neither has a submitted review or review discussion. Green CI does not replace task acceptance.

## 3. Non-negotiable safety and scope boundaries

The remediation must not introduce any of the following:

- MAVLink transport or packets;
- serial-port access or `pymavlink`;
- mission upload, acknowledgments, readback, or verification;
- vehicle telemetry or vehicle identity;
- parameters or parameter writes;
- arm, mode, start, pause, resume, Land Here Now, or RTL controls;
- Guided setpoints, RC overrides, arbitrary commands, or raw command IDs;
- invented operational altitude, distance, speed, duration, radius, obstacle, terrain, or geofence limits;
- application integration assigned to Task 005.

The work must preserve the domain/UI separation, typed UI intents, immutable mission values, closed compiler whitelist, and inert operational-policy seam.

## 4. Required disposition of PR #5 — Task 004 compiler

PR #5 is the next candidate for acceptance because the development plan requires merge order: model, compiler, then UI.

### 4.1 Coding-agent duties

1. Re-read `codex-tasks/004-mission-compiler.md` and compare every acceptance item against the PR head.
2. Confirm that the PR changes only Task 004-owned compiler types, compiler code, compiler tests, and golden fixtures.
3. Confirm that compilation remains pure, deterministic, immutable, and independent of Qt, serial, and `pymavlink`.
4. Confirm that only the six approved command values are constructible through the normal compiled-command type:
   - `NAV_TAKEOFF`;
   - `DO_CHANGE_SPEED`;
   - `NAV_WAYPOINT`;
   - `NAV_LOITER_TIME`;
   - `NAV_LOITER_TURNS`;
   - `NAV_LAND`.
5. Confirm exact field-for-field fixture coverage for Proceed, Hold, Circle, Land, and a mixed mission, including sequence, frame, command, parameters, integer coordinates, altitude, `current`, `autocontinue`, and mission type.
6. Confirm draft and invalid-mission rejection, deterministic half-away-from-zero coordinate conversion, Land coordinate equality, and unsupported-command rejection.
7. Preserve every documented compatibility assumption for Task 005A. Do not “correct” frame or command semantics by guessing a firmware target.
8. Run the complete repository checks and update the PR handoff only if the recorded results or limitations changed.

### 4.2 Acceptance gate

PR #5 may be marked ready for review only when all checks pass and no unresolved review findings remain. A human must review and accept it. A human may then squash-merge it into `main`. The coding agent must stop and wait; it must not merge.

No Task 005 work may begin after PR #5 alone. Task 003 must also be corrected, reviewed, accepted, and merged.

## 5. Required disposition of PR #4 — Task 003 map-builder UI

PR #4 is not currently task-complete. Its native schematic canvas is a thoughtful prototype, but it substitutes for the authoritative Qt WebEngine + Leaflet production map architecture, while the included HTML/JavaScript bridge is not mounted by the production widget.

### 5.1 Architecture decision gate

Do not silently resolve this conflict inside unrelated code. The maintainer must explicitly choose one of these paths:

#### Path A — retain the authoritative WebEngine + Leaflet architecture (recommended)

Use the architecture described in `README.md` and `docs/architecture.md`. If adding or changing the exact Qt WebEngine/Leaflet dependency and lock files is outside Task 003 ownership, stop and request a dedicated dependency/architecture pull request. After that decision is accepted, rebase PR #4 and implement the approved map host.

#### Path B — approve a native schematic canvas

This requires a dedicated architecture decision that updates the authoritative README, architecture, task expectations, bridge requirements, dependency plan, and acceptance criteria. Do not treat the current PR description as approval. If this path is chosen, remove or reconcile dead production bridge assets and ensure the accepted architecture has one clear map implementation.

Until one path is formally accepted, PR #4 must remain draft and Task 005 remains blocked.

### 5.2 Required production-map behavior

Under the recommended Path A, the production mission-builder map must:

1. Mount the local HTML/JavaScript map through a Qt WebEngine host and a versioned `QWebChannel` bridge.
2. Use local, version-pinned Leaflet assets. Do not load executable code from a CDN.
3. Document the tile policy. A deterministic no-remote-tile Leaflet surface is acceptable unless the maintainer separately approves a provider, licensing/cache behavior, origin allowlist, and offline behavior.
4. Prevent navigation to arbitrary origins and prevent map content from obtaining references to application or vehicle services.
5. Accept only the four validated inbound map intents:
   - map clicked;
   - point selected;
   - point dragged;
   - viewport changed.
6. Send only sanitized, schema-versioned render snapshots from Python to JavaScript.
7. Render coordinates geographically rather than placing markers by array index.
8. Render a route polyline in creation order.
9. Render a pending, Mission Planner-style numbered point that is not committed until confirmation.
10. Render confirmed numbered points with explicit Above Home altitude labels.
11. Render Hold time, a distinct landing symbol, selection styling, and Land closure.
12. Render Circle geometry using the domain radius in meters, including perimeter, center-to-edge radius line, numeric radius label, and clockwise direction cue. Do not clamp unrelated radii to the same visual size.
13. Support actual point selection and dragging through the production bridge.
14. Remain usable without a vehicle, serial hardware, MAVLink, or Task 005 application wiring.

### 5.3 Pointer-selection and dragging correction

The current native canvas arms a drag on every point press and emits `ActionMoveRequested` on release even when the user only clicked to select. Correct the accepted production implementation so that:

- a click selects exactly one point and emits no move intent;
- dragging begins only after pointer movement exceeds the platform drag threshold;
- a completed drag emits exactly one move intent;
- releasing without movement does not alter coordinates;
- pointer actions outside the map drawing surface do not create or move points;
- cancellation or loss of capture clears drag state safely.

Add event-level tests using real mouse press/move/release behavior. Simulation helpers alone are insufficient for this requirement.

### 5.4 Land-closure correction

The accepted UI must make the dedicated “Remove Land and reopen” action the explicit way to reopen an existing closed mission for further appends.

- When editing Land, lock the action kind to Land. Permit coordinate and approach-altitude edits only.
- Do not allow Land to be converted to Proceed, Hold, or Circle through the generic action selector.
- Disable generic Delete for the final Land action, or route removal through the explicit, deliberate `RemoveLandRequested` path.
- Ensure generic Undo cannot silently remove the final Land and reopen the mission.
- Clear may still reset the whole mission if its destructive meaning is explicit and tested; it must not preserve prior points while silently reopening them.
- After Land is committed, map clicks and append controls remain blocked until the explicit reopen action succeeds.

Add tests for every prohibited bypass and for the successful explicit reopen flow.

### 5.5 Bridge and test completeness

The tests must prove the behavior of the production map host, not merely parse standalone JSON or call simulation hooks.

Required coverage:

- production WebEngine host loads packaged local assets;
- Python-to-JavaScript render delivery and JavaScript-to-Python intents work through the mounted bridge;
- schema version mismatch, duplicate keys, unknown fields/types, non-finite values, invalid coordinates, invalid indices, and reversed viewport bounds fail closed;
- JavaScript emits selection, drag, click, and viewport intents;
- render snapshots display pending, confirmed, selected, mixed, Circle, and landed states;
- point click does not emit move;
- drag emits one coordinate move;
- Land conversion/delete/undo bypasses are unavailable;
- no remote script path, vehicle-service reference, raw command identifier, RTL, serial, or MAVLink dependency exists;
- headless tests have bounded timeouts and do not rely on a network connection.

### 5.6 Visual and accessibility evidence

Generate new screenshots from the exact final PR head and attach them to the PR handoff so reviewers can inspect them on GitHub:

1. empty/Takeoff state;
2. pending point and action editor;
3. mixed Proceed/Hold/Circle route;
4. selected Circle with perimeter, radius line/label, and clockwise cue;
5. landed/closed mission with the distinct landing marker.

Record the exact commit SHA used for the screenshots. The screenshots must be accessible to reviewers; a statement that they were generated is not sufficient.

Also report:

- keyboard traversal and focus behavior;
- keyboard limitations of coordinate placement/dragging;
- screen-reader limitations of map markers;
- high-contrast limitations;
- tile/network/offline assumptions.

Do not claim accessibility support that has not been demonstrated.

### 5.7 PR #4 acceptance gate

PR #4 may be marked ready only after:

- the architecture decision is accepted;
- the implementation matches that decision;
- pointer and Land-closure defects are corrected;
- production-boundary tests pass;
- all five screenshot states are attached;
- the PR description accurately names remaining limitations;
- full Windows CI is green;
- no unresolved review findings remain.

A human must review and accept PR #4. A human may then squash-merge it. The coding agent must not merge.

## 6. Required merge and rebase order

Follow this order exactly:

1. Complete human review of PR #5.
2. Human squash-merges accepted PR #5 into `main`.
3. Resolve and accept any required dedicated architecture/dependency decision before finishing PR #4.
4. Rebase the PR #4 branch onto the new accepted `main`; do not merge `main` into the task branch unless repository policy explicitly requires it.
5. Rerun all Task 003 and repository checks after the rebase.
6. Complete human review of PR #4.
7. Human squash-merges accepted PR #4 into `main`.
8. Run or confirm full CI on the resulting `main` containing Tasks 002, 003, and 004 together.

If a rebase exposes a frozen-contract mismatch, stop and report it. Do not hide integration work in PR #4.

## 7. Documentation and repository-name cleanup

The repository has been renamed to `Skywriter`, but `README.md` and `docs/development-plan.md` still contain the former repository target `305skylab-mission-console`.

Treat this as a documentation-only cleanup, not as a reason to broaden Tasks 003 or 004. Correct it only through an explicitly authorized documentation change or dedicated documentation PR. Preserve historical references when they describe an earlier artifact rather than the current repository.

This cleanup is recommended before launching Task 005 so task instructions, developer setup, and repository references are unambiguous.

## 8. Full pre–Task 005 readiness checklist

Every item below must be true before creating the Task 005 branch:

### Repository state

- [ ] PR #5 is reviewed, accepted, and squash-merged.
- [ ] The map architecture decision is recorded and accepted.
- [ ] PR #4 implements the accepted architecture.
- [ ] PR #4 is reviewed, accepted, and squash-merged.
- [ ] `main` contains accepted Tasks 002, 003, and 004.
- [ ] Full Windows CI is green on the combined `main` state.
- [ ] No unresolved review threads or requested changes remain on the accepted work.
- [ ] The working tree used to launch Task 005 is clean and based on the accepted `main` head.

### Task 003 behavior

- [ ] Production map host and bridge are mounted and tested.
- [ ] Pending/confirmed/selected/closed visual states are correct.
- [ ] Click selection never mutates coordinates.
- [ ] Dragging emits one intentional coordinate edit.
- [ ] Circle radius geometry represents the domain radius in meters.
- [ ] Land cannot be converted or generically removed to bypass explicit reopen.
- [ ] Five required screenshots are visible to reviewers.
- [ ] Accessibility and tile/offline limitations are reported honestly.

### Task 004 behavior

- [ ] Compiler output is deterministic and immutable.
- [ ] All exact golden fixtures pass.
- [ ] Only approved command values are present.
- [ ] Drafts and invalid missions are rejected.
- [ ] Compatibility assumptions remain explicit and deferred to Task 005A.

### Safety and scope

- [ ] No vehicle, MAVLink, serial, parameter, command, RTL, telemetry, or upload path exists.
- [ ] No invented operational or obstacle/geofence limits were introduced.
- [ ] No Task 005 application integration was smuggled into prior PRs.
- [ ] Documentation uses the current repository name or has an accepted cleanup plan.

## 9. Authorization to begin Task 005

Task 005 becomes authorized only when the entire checklist in Section 8 is complete and a human maintainer explicitly accepts the resulting `main` base.

At that point:

1. create one new short-lived branch from the exact accepted `main` head;
2. attach only `codex-tasks/005-offline-integration.md` as the assigned task file;
3. use the repository launch prompt from `codex-tasks/README.md`;
4. implement only Task 005-owned application use cases/state adapters, main-window wiring, integration tests, and the smallest approved component fixes;
5. open one draft Task 005 pull request and stop.

Task 005 must not include compatibility pinning, SITL, `pymavlink`, transport, telemetry, connected behavior, or flight controls.

## 10. Required completion report from the coding agent

Return a concise report containing:

- the PR and exact head SHA inspected or changed;
- files changed, grouped by owned scope;
- each review finding and its disposition;
- the accepted map architecture decision;
- test commands and exact results;
- CI run link and conclusion;
- screenshot links and the commit SHA represented;
- accessibility, map/tile, and compatibility limitations;
- safety-rule impact;
- unresolved blockers requiring human action;
- a final statement of either:
  - `TASK 005 READY — all gates satisfied; waiting for maintainer authorization`, or
  - `TASK 005 BLOCKED — <specific unsatisfied gates>`.

Never claim Task 005 readiness while any gate is incomplete.

## 11. Short launch prompt for the remediation agent

```text
Read AGENTS.md, README.md, docs/product-specification.md,
docs/architecture.md, docs/development-plan.md, codex-tasks/README.md,
codex-tasks/003-map-builder-ui.md, codex-tasks/004-mission-compiler.md,
and this remediation handoff in full.

Do not start Task 005 or open its PR. First satisfy the existing Task 004 and
Task 003 acceptance gates in the required merge order. Preserve task ownership
and all safety boundaries. Stop for a maintainer decision if the map dependency
or architecture requires a dedicated PR. Run the complete checks and return the
required evidence and readiness statement. Do not merge any pull request.
```

