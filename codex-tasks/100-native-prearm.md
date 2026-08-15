# Task 100 — native pre-arm request and readiness review

## Goal

Add only the ability to request ArduCopter's native pre-arm checks and present their acknowledged/native results in a deliberate readiness review.

## Base and ownership

Base: accepted Task 009. This and every later flight-control task is serial.

Own: narrow typed command gateway method, readiness use case/state, preflight UI, and related unit/SITL tests. Do not add Arm or other commands.

## Required work

- Implement a dedicated `request_prearm_checks()` path using pinned supported semantics; no generic command API.
- Gate on fresh same-target SiK link, disarmed state, verified current mission, and idle command channel.
- Correlate matching `COMMAND_ACK`; display accepted/rejected/unsupported/timeout distinctly.
- Capture/display associated native `STATUSTEXT` and available GPS/home/EKF/battery/safety state. Never infer readiness from silence or override native results.
- Require operator review acknowledgment; this is an application gate, not proof that ArduCopter will arm.

## Acceptance and stop

Unit/fake-link and SITL tests cover success, native failure text, rejection, unsupported, timeout, wrong target, stale/lost link, and repeated request. Prove no force/bypass/parameter write/Arm/RTL path. Report evidence, open a PR, and stop.
