# Task 102 — native AUTO mission start

## Goal

Add only the acknowledged, pinned sequence for starting the already verified onboard mission under native AUTO execution.

## Base and ownership

Base: accepted Task 101. Own the dedicated start gateway method, state/gates, Start Mission UI, and tests.

## Required work

- Gate on fresh same-target link, armed telemetry, exact mission still verified/current, valid starting sequence, and idle command channel.
- Use the exact compatibility-tested ArduCopter AUTO/start mechanism through a narrow API.
- Correlate acknowledgment and confirm AUTO/mission progress telemetry before presenting Running.
- Handle rejection, timeout, unexpected mode, mission mismatch, disarm, and link loss without fallback setpoint streaming.

## Acceptance and stop

Fake-link and pinned SITL tests cover success and all failures above. Demonstrate ArduCopter executes the compiled mission natively after the desktop process is stopped/link interrupted according to configured onboard behavior. No Guided stream, parameter write, RTL, or generic mode/command API. Open a PR and stop.
