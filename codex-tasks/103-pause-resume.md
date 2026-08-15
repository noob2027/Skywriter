# Task 103 — mission Pause and Resume

## Goal

Add state-aware Pause and Resume for a native AUTO mission using the pinned supported command semantics.

## Base and ownership

Base: accepted Task 102. Own dedicated pause/resume methods, application transitions, two UI controls, and tests.

## Required work

- Pause is enabled only while mission-running telemetry is current; Resume only while the application has positively observed the pinned paused state.
- Send dedicated `MAV_CMD_DO_PAUSE_CONTINUE` semantics, correlate matching `COMMAND_ACK`, and confirm resulting telemetry state.
- Handle rejection, timeout, duplicate clicks, target/mode changes, completion, landing, disarm, and link loss.
- Never reinterpret Pause as RTL, Loiter command, Guided hold, or streamed position setpoint.

## Acceptance and stop

Fake-link and SITL tests pause/resume during multiple mission action types and cover all failure/state races. Review proves no generic command API, parameter write, RTL, or hidden navigation stream. Open a PR and stop; do not add Land Here Now.
