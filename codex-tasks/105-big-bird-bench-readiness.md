# Task 105 — Big Bird profile and disarmed powered-bench readiness

## Goal

Establish the real Big Bird vehicle profile, retain sanitized traceable evidence, and
prepare a fail-closed first powered-bench procedure that advances SKYWriter toward a
working integrated prototype without creating throwaway bench architecture.

## Base and ownership

Base: accepted Task 104 on `main` (`0518667`). Run serially.

Own: one pure offline Big Bird parameter-export validator and tests, the Big Bird
compatibility/evidence record, this handoff, the disarmed bench procedure, and the
smallest documentation updates needed to place the new gate in the product roadmap.
Do not change the mission, telemetry, command, connection, UI, or firmware architecture.

## Required work

- Pin the exact Matek H7A3-SLIM / `MatekH7A3` stock official ArduCopter 4.6.3 artifact
  identity, runtime evidence, serial mapping, sensor configuration, output summary, SiK
  evidence, and dynamic COM observations without retaining the raw board UID.
- Independently validate the supplied pre-change Mission Planner export and retain only
  its hash, counts, and sanitized decision.
- Provide a pure offline validator for pre-change and post-change `.param` exports. It
  must fail closed on malformed/missing/duplicate values, wrong TX2/RX2 mapping, relaxed
  safety values, missing sensor evidence, or any stream-rate difference.
- Record the accepted operator-only changes: `SR2_EXT_STAT=2`, `SR2_POSITION=2`, and
  `SR2_EXTRA3=1`, with every other `SR2_*` group left at zero. SKYWriter must not read or
  write parameters or request streams in this task.
- Require Mission Planner to apply the rates, reboot, prove persistence, and export a
  post-change `.param`; preserve the pre-change backup.
- Provide an operator procedure whose first live acceptance is limited to disarmed
  barometer health, compass health, valid GPS lock, and mission upload with accepted ACK
  plus complete exact readback through the existing SKYWriter workflow.
- Keep observed evidence, operator-reported/configured facts, and unverified live facts
  visibly separate. State the subsequent integrated-prototype and staged-flight gates
  without implementing or claiming them.

## Safety and exclusions

No firmware flashing/build/modification, live parameter access, parameter writes, stream
requests, new transports, Mission Planner automation, raw UID publication, hardcoded COM
ports, arming, force-arm, motor/propeller operation, mode change, mission execution, Land,
RTL, takeoff, or flight. Do not relax `ARMING_CHECK=4366` or the battery arm threshold.
Mission Planner and SKYWriter must never own the SiK COM port simultaneously.

## Acceptance and stop

The supplied pre-change export passes the offline validator with its expected hash,
1,269 lines, exact profile values, and explicit `MIS_TOTAL=14` replacement notice. Tests
cover both stages and negative paths. The record pins the official APJ and source mapping,
contains no raw UID or local path, and claims no live result. The procedure defines exact
pass/fail evidence and a complete session packet. Full repository checks pass.

Open a PR and stop. Do not run the physical bench, merge the PR, or begin later gates.
The next safe task is the separately supervised execution and review of the disarmed
procedure; broader props-off commands and flight remain later approvals.
