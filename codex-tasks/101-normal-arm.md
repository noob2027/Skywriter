# Task 101 — normal acknowledged arm

## Goal

Add one normal Arm control that preserves native ArduCopter pre-arm authority.

## Base and ownership

Base: accepted Task 100. Own only the dedicated arm gateway method, application state/gates, Arm UI, and tests.

## Required work

- Gate on fresh same-target SiK link, disarmed state, verified unchanged mission, completed readiness review, and idle command channel.
- Send only normal arm semantics; the force-arm value/flag must be impossible to supply through the API.
- Wait for matching `COMMAND_ACK`, then confirm armed telemetry before presenting Armed. Show rejection/timeout/disconnect honestly with native text.
- Disable duplicate activation while pending; never add Disarm or mid-air disarm.

## Acceptance and stop

Fake-link and SITL tests cover accepted arm, native pre-arm rejection, ACK accepted without armed telemetry, timeout, wrong ACK/target, link loss, double-click, and gate invalidation. Static/API review proves no force, bypass, parameter write, generic command, or RTL. Open a PR and stop; do not add AUTO start.
