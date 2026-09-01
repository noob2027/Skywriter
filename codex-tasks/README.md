# Codex staged handoffs

## How to launch a task

Create a branch from the exact accepted base named by the current phase, paste only the short launch prompt below, and attach the matching task file. Do not paste the full development plan as the assignment.

```text
Read AGENTS.md, README.md, docs/product-specification.md,
docs/architecture.md, and the assigned codex-tasks file.
Perform only that task. Respect its owned files and stop condition.
Run the required tests and return the requested handoff report.
```

## Execution order

| Stage | Tasks | Concurrency | Start gate |
|---|---|---:|---|
| Foundation | 001 | 1 | documentation on protected repository |
| Offline wave | 002, 003, 004 | up to 3 | foundation accepted; contracts frozen |
| Offline integration | 005 | 1 | 002–004 accepted |
| Compatibility | 005A | 1 | offline compiler accepted; maintainer chooses candidate |
| Connected wave | 006, 007, 008 | up to 3 | pin and offline integration accepted |
| Connected integration | 009 | 1 | 006–008 accepted |
| Flight controls | 100, 101, 102, 103, 104 | exactly 1, in order | prior task merged and SITL green |
| Vehicle profile | 105 | 1 | Task 104 merged; Big Bird evidence supplied |

Parallel means separate branches with non-overlapping ownership, not multiple tasks in one Codex prompt. Tasks 002–004 and 006–008 are the only planned parallel waves. Stop a parallel task and raise a dedicated contract PR if shared interfaces must change.

## Remediation handoffs

- [`pre-task-005-remediation-readiness.md`](pre-task-005-remediation-readiness.md): required review, correction, merge-order, and stop gates before Task 005 may begin

## Task index

- [`001-foundation.md`](001-foundation.md)
- [`002-mission-model.md`](002-mission-model.md)
- [`003-map-builder-ui.md`](003-map-builder-ui.md)
- [`004-mission-compiler.md`](004-mission-compiler.md)
- [`005-offline-integration.md`](005-offline-integration.md)
- [`005a-compatibility-pin.md`](005a-compatibility-pin.md)
- [`006-sitl-harness.md`](006-sitl-harness.md)
- [`007-mavlink-transport.md`](007-mavlink-transport.md)
- [`008-telemetry.md`](008-telemetry.md)
- [`009-connected-integration.md`](009-connected-integration.md)
- [`100-native-prearm.md`](100-native-prearm.md)
- [`101-normal-arm.md`](101-normal-arm.md)
- [`102-auto-start.md`](102-auto-start.md)
- [`103-pause-resume.md`](103-pause-resume.md)
- [`104-land-here-now.md`](104-land-here-now.md)
- [`105-big-bird-bench-readiness.md`](105-big-bird-bench-readiness.md)

Every task ends with a pull request; Codex must not merge it, create tags, or begin the next handoff.
