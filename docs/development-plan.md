# SKYWriter development plan

## 1. Delivery principles

- One repository: `Skywriter`.
- `main` is protected; Codex never commits directly to it.
- One short-lived branch and pull request per bounded handoff; squash merge after CI and human review.
- Foundation is serial. After shared contracts are accepted, at most three small non-overlapping tasks run in parallel.
- Integration is explicit. Flight-command work returns to serial, one command capability per PR.
- No real-flight behavior is accepted before unit/protocol tests, SITL, and props-off hardware gates.

Recommended tags:

```text
v0.1-foundation
v0.2-offline-builder
v0.3-compiled-missions
v0.4-sitl-connected
v0.5-hardware-candidate
```

Tags record achieved evidence; they are not schedules.

## 2. Phase and dependency map

```text
001 Foundation (serial)
        |
        v
 Freeze domain/application contracts
   /          |           \
002 Model   003 Map UI   004 Compiler       (parallel, max 3)
   \          |           /
        005 Offline integration (serial)
                    |
              005A Compatibility pin (serial)
          /         |          \
006 SITL     007 Transport   008 Telemetry   (parallel, max 3)
          \         |          /
        009 Connected integration (serial)
                    |
100 Pre-arm -> 101 Arm -> 102 AUTO Start
     -> 103 Pause/Resume -> 104 Land Here Now (all serial)
                    |
            Props-off hardware gate
                    |
       Separately approved staged flight tests
```

## 3. Phase 0 — repository setup

Create the GitHub repository, copy this documentation package to its root, enable branch protection and required CI, and create labels for `safety`, `architecture`, `compatibility`, `ui`, `protocol`, and `sitl`. The PR template should require scope, tests, screenshots where applicable, risk/safety impact, and rollback notes.

Do not add application code in this manual setup PR.

## 4. Phase 1 — foundation (serial)

Run Task 001 only. It creates the Python/PySide6 shell, package/test tooling, logging, error types, application-state skeleton, module boundaries, CI, and repeatable Windows developer instructions. It must not implement mission semantics, map interaction, MAVLink, telemetry, or commands.

Acceptance gate:

- clean checkout installs and launches on Windows;
- formatting, lint/type checks, and tests pass locally and in CI;
- placeholder navigation does not import vehicle libraries;
- domain/application/UI/infrastructure dependency rules are documented and testable;
- no prohibited behavior or later-phase implementation exists.

After review, squash merge and tag `v0.1-foundation`.

## 5. Contract freeze

Before parallel work, merge a small maintainer-reviewed contract PR if Task 001 did not already establish the exact contracts. Freeze names and signatures for mission/action values, validation findings, compiled items, repository, compiler, application events/snapshots, map bridge messages, mission transport, telemetry, and command gateway.

Contract changes during a parallel wave require a separate PR. Parallel agents report an insufficiency rather than editing shared contracts opportunistically.

## 6. Phase 2 — offline parallel wave

Launch Tasks 002–004 from the same foundation commit. File ownership may not overlap.

| Task | Owns | Produces |
|---|---|---|
| 002 Mission model | domain model, validation, JSON repository, domain tests | typed actions, draft/complete rules, versioned round trip |
| 003 Map builder UI | mission-builder widgets, map assets/bridge, UI tests | pending click/action flow and visual route editing using contract fakes |
| 004 Mission compiler | compiled types/compiler and fixtures/tests | exact whitelist translation, approach waypoint + Land |

Review each PR independently for scope and tests. Merge model, then compiler, then UI, rebasing as needed without smuggling integration work into those branches.

Task 005 connects the components, removes mocks at the application boundary, implements save/load/review, and adds end-to-end offline UI tests.

Offline acceptance:

- complete beginner workflow works without an aircraft;
- every action and visual requirement is present;
- JSON reload produces equivalent canonical mission;
- invalid/unsupported content fails clearly;
- compiled preview contains only whitelisted commands and deterministic values;
- RTL/parameters/vehicle commands do not exist.

Tag `v0.2-offline-builder`; tag `v0.3-compiled-missions` when the compiled representation and fixtures are independently accepted.

## 7. Compatibility pin gate

Run Task 005A to evaluate the maintainer-selected candidate, then approve one stock ArduCopter compatibility target. Record:

- exact release/version and source/firmware hash;
- MAVLink version/dialect and pymavlink lock;
- SITL acquisition/build instructions and artifact hash;
- mission-frame and parameter semantics for every whitelisted command;
- how home item, float precision, and readback normalization behave;
- supported USB and SiK connection assumptions;
- observed support for pre-arm request and pause/resume commands.

Run a minimal probe against SITL and preserve raw mission upload/download evidence. The task may recommend or reject the candidate but may not silently choose or upgrade flight firmware. A later pin change repeats connected/SITL acceptance.

The accepted ArduCopter 4.6.3 remediation seam is a pure compatibility envelope after the
logical compiler and before transport. It requires caller-owned authoritative, fresh,
same-vehicle home, inserts native home at sequence zero, shifts the unchanged logical
mission, and verifies home separately from canonicalized logical readback. Tasks 006–008
may depend on that boundary after its PR is accepted, but may not duplicate its
normalization or make SITL/transport a prerequisite for offline SKYWriter.

## 8. Phase 3 — connected parallel wave

Launch Tasks 006–008 from the same accepted offline/compatibility commit.

| Task | Owns | Produces |
|---|---|---|
| 006 SITL harness | scripts/test fixtures/SITL CI docs | repeatable pinned simulator and evidence capture |
| 007 MAVLink mission transport | connection/mission protocol/verification adapter tests | USB upload, ACK, download, exact normalized comparison; no commands |
| 008 Telemetry presentation | typed telemetry adapter and display | read-only SiK/transport-agnostic status; no vehicle commands |

Task 009 integrates target identity, USB upload/readback, SiK reconnection/readback, telemetry, state invalidation, and mixed-mission SITL execution.

The Task 009 implementation retains the existing compartments behind injected
contracts. Its stock-SITL acceptance harness may supply the normal arm/AUTO stimulus
required to execute the test mission, but those test-only actions must not create a
production API or UI control. Production pre-arm, Arm, and AUTO remain serial Tasks
100–102.

Connected acceptance:

- upload state machine passes loss/duplicate/wrong-sequence/negative-ACK tests;
- the application cannot display Verified without complete matching readback;
- different-vehicle and stale-link cases fail closed;
- SITL completes representative Takeoff–Proceed–Hold–Circle–Land missions;
- Mission Planner independently reads the expected sequence;
- no arm, mode, pause, land-now, parameter-write, or generic command path exists yet.

Tag `v0.4-sitl-connected`.

## 9. Phase 4 — flight controls (strictly serial)

Run one task and merge it before starting the next:

1. Task 100: request/display native pre-arm checks and readiness review.
2. Task 101: normal arm only, accepted/rejected/timeout states, no force path.
3. Task 102: native AUTO mission start with verified-mission gate.
4. Task 103: Pause and Resume with state-aware acknowledgments.
5. Task 104: deliberately confirmed native Land Here Now.

Each PR needs positive and rejected-command SITL tests, timeout/link-loss tests, UI state tests, exact acknowledgment handling, and a review confirming no generic command API, force arm, bypass, parameter write, RTL, or mid-air disarm.

## 10. Validation ladder

### Gate A — automated offline

Unit, property/boundary where useful, serializer, compiler golden fixtures, application-state, UI bridge, and headless UI tests.

### Gate B — protocol simulation

Deterministic fake-clock/fake-link tests for timeouts, retries, duplicates, reordering, wrong target, negative ACK, cancellation, disconnect, and stale telemetry.

### Gate C — pinned SITL

Every action alone, mixed mission, upload/readback, execution progress, native rejection paths, link interruption, restart/reconnect, and each later approved vehicle command.

### Gate D — USB props-off hardware

Confirm exact vehicle/firmware identity, disarmed upload/readback, Mission Planner independent comparison, no parameter changes, and logs. Propulsion remains physically unable to produce thrust under the approved procedure.

### Gate E — SiK props-off hardware

Confirm same-vehicle reconciliation, mission re-verification, telemetry freshness/staleness, command ACKs where the approved procedure safely permits, radio interruption behavior, and zero hidden writes.

### Gate F — staged field validation

Requires a separate approved test plan, qualified supervision, legal site/conditions, aircraft-specific risk controls, and go/no-go authority. Progress from Takeoff–Land to Proceed–Land, then Hold, then Circle, then a representative mission. This repository plan does not itself authorize flight.

After all hardware-candidate gates, tag `v0.5-hardware-candidate`.

## 11. Pull-request acceptance checklist

- Scope matches one handoff and owned files.
- Safety invariants in `AGENTS.md` remain intact.
- Shared contracts changed only in an approved dedicated PR.
- Tests are meaningful and pass; skipped tests are justified.
- UI changes include screenshots and keyboard/error-state review.
- Protocol changes include raw/equivalent fixture evidence and negative paths.
- No dependency or compatibility pin changed incidentally.
- Documentation reflects user-visible or architectural changes.
- Handoff lists files, tests, assumptions, limitations, and next safe task.

## 12. Deferred extension roadmap

Future dedicated designs may implement geofences, launch-distance bounds, aircraft profiles, maximum altitude/speed, minimum Circle radius, hold-time bounds, battery-based mission rejection, duration/distance estimates, terrain/obstacle data, offline maps, signed MAVLink, roles, and mission/log history. These are not prototype features and must not be implied by the obstacle acknowledgment or structural validator.
