# Big Bird first powered-bench procedure — DISARMED

## Purpose and stop boundary

This operator procedure collects the first real-hardware evidence for the reviewed Big
Bird profile. Its only acceptance items are barometer health, compass health, valid GPS
lock, and mission upload with complete exact readback through the existing SKYWriter
workflow.

Do not arm or force-arm. Do not test motors, propellers, takeoff, flight, AUTO execution,
Pause/Resume, Land, Land Here Now, or RTL. Do not weaken `ARMING_CHECK=4366` or the
`BATT_ARM_VOLT=19.7` gate. A below-minimum battery pre-arm message is compatible with this
disarmed scope; it is not permission to bypass the check.

## Required people, setup, and records

- Use the approved shop power procedure and qualified supervision. Remove propellers and
  keep propulsion physically incapable of producing thrust for the entire session.
- Record date/time, operator, observer, workstation, SKYWriter commit, Mission Planner
  version, vehicle system ID, and the dynamically discovered USB and SiK COM ports.
- Keep the supplied pre-change export unchanged as the backup. Record its SHA-256 and the
  passing `--stage pre-change` validator JSON.
- Select one complete SKYWriter mission and retain its canonical JSON/digest and compiled
  preview. Record the expected native item count, including native Home.
- Treat any unexplained hardware state, unexpected armed indication, port contention,
  wrong target, stale data, or parameter difference as **STOP/FAIL**.

## 1. Physical and identity hold point

With power removed, an operator must trace and initial all of these statements:

- flight controller marking/profile is Matek H7A3-SLIM and the approved target is
  `MatekH7A3`;
- the SiK air radio is the sole device on physical TX2/RX2;
- no RC receiver is attached;
- radio TX goes to FC RX2 and radio RX goes to FC TX2; and
- the GPS is not on TX2/RX2 and is assigned to `SERIAL3`.

Power using the approved procedure, remain disarmed, and use Mission Planner over the
dynamically discovered USB port. Capture the reported target/runtime version, frame, and
output summary. **PASS** requires MatekH7A3, official ArduCopter 4.6.3 with git identity
`3fc7011a`, QUAD/X, and the expected DShot300 1–4 / PWM 5–11 runtime summary. Any mismatch
is **STOP/FAIL**; do not flash firmware in this task.

## 2. Apply and prove the accepted vehicle-side stream rates

In Mission Planner, confirm the pre-change export is safely backed up. Change only:

| Parameter | Required value |
| --- | ---: |
| `SR2_EXT_STAT` | 2 |
| `SR2_POSITION` | 2 |
| `SR2_EXTRA3` | 1 |

Leave every other `SR2_*` group at zero. Do not change serial mapping, arming, battery,
sensor, firmware, radio, or mission parameters as part of this step. Reboot the flight
controller, reconnect on the dynamically discovered USB port, reload the parameters, and
visibly confirm persistence.

Export a new post-change `.param` file and retain it. Run the offline validator with
`--stage bench-ready`. **PASS** requires exit 0, the three exact rates above, all other
`SR2_*` groups zero, unchanged profile/sensor/safety values, a recorded post-change
SHA-256, and the retained validator JSON. A screenshot without the export is insufficient.

## 3. Native sensor and GPS evidence

Remain disarmed. Let the aircraft sit stationary in a location suitable for GPS reception.
Use native Mission Planner/SKYWriter status and `STATUSTEXT`; do not infer health from the
mere presence of a configured device ID.

| Gate | Exact PASS evidence | FAIL evidence |
| --- | --- | --- |
| Barometer | Selected-target `SYS_STATUS` shows absolute-pressure sensor present, enabled, and healthy; no current native barometer failure text. Retain timestamped status/log evidence. | Missing/stale bitfield, not present/enabled/healthy, barometer failure text, or wrong target. |
| Compass | Selected-target `SYS_STATUS` shows 3D magnetometer present, enabled, and healthy; the external compass remains enabled with its calibration values; no current native compass failure text. Retain timestamped status/log evidence. | Missing/stale bitfield, not present/enabled/healthy, lost/disabled calibration, compass failure text, or wrong target. |
| GPS | Fresh selected-target `GPS_RAW_INT` reports 3D fix or better (`fix_type >= 3`) and usable coordinates; Home is available for the same vehicle. Retain fix type, satellite count, timestamp, and status/log evidence. | No fix/2D fix, unavailable coordinates/Home, stale data, GPS/configuration failure text, or wrong target. |

The task does not invent HDOP, satellite-count, or wait-time limits. If the evidence is
ambiguous or never becomes fresh, record **FAIL/UNRESOLVED** and stop.

## 4. USB mission replacement, upload, and exact verification

Close Mission Planner completely and confirm it released every relevant COM port. Open
SKYWriter on the dynamically discovered USB port. Keep the vehicle disarmed.

1. Discover exactly one target and confirm system ID 20 plus the reviewed firmware/profile.
2. Download the onboard mission. The pre-change export recorded `MIS_TOTAL=14`; show it and
   deliberately confirm replacement. Never treat the count as semantic readback.
3. Upload the chosen complete mission through the existing SKYWriter workflow.
4. Require the matching accepted `MISSION_ACK`.
5. Download every native mission item and require SKYWriter **Verified** after its pinned
   normalization and field-by-field comparison.

**PASS** requires retained SKYWriter logs/result showing selected identity, transport USB,
disarmed state, explicit replacement confirmation, accepted ACK, expected/actual item
counts, separate Home verification, zero logical mismatches, mission digest/revision, and
final `USB_VERIFIED`. Timeout, rejection, armed state, partial download, wrong target,
count/field mismatch, or unclear logs is **STOP/FAIL**.

## 5. SiK ownership handoff and same-vehicle readback

Disconnect USB as required by the normal workflow. Keep Mission Planner closed. Discover
the current SiK ground-radio COM port at 57600; do not assume COM8. Confirm no other
program owns it, then connect SKYWriter.

**PASS** requires the same vehicle identity, fresh heartbeat, fresh sensor/GPS evidence,
a complete mission re-download, zero Home/logical mismatches, and final `SIK_VERIFIED`.
Any identity ambiguity, stale stream, port conflict, partial readback, or mismatch is
**STOP/FAIL**. No Arm or other flight command is requested in this procedure.

## 6. Session closeout and acceptance packet

Power down using the approved procedure. Do not declare the gate passed unless one packet
contains all of the following:

- completed hold-point checklist and dynamic COM observations;
- pre-change export hash and passing sanitized validator JSON;
- post-change export, SHA-256, and passing bench-ready validator JSON;
- runtime identity/frame/output evidence with raw board UID redacted;
- barometer, compass, and GPS pass evidence with timestamps and target identity;
- mission JSON/digest/compiled preview plus accepted ACK and complete exact USB readback;
- complete same-vehicle SiK readback and freshness evidence;
- SKYWriter/Mission Planner logs, deviations, failures, and operator/observer sign-off; and
- explicit declarations: disarmed throughout, propellers removed, motors not commanded,
  no firmware flashing, no safety-gate changes, and no flight.

Partial success is not acceptance. Preserve failed evidence and open a bounded follow-up;
do not fix wiring, firmware, parameters, or safety settings ad hoc under this procedure.
