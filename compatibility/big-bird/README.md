# Big Bird compatibility and disarmed-bench record

## Decision and boundary

Task 105 establishes a real, reviewable Big Bird vehicle profile and prepares the first
powered-bench gate. It does **not** report that the bench ran, certify the aircraft, or
authorize arming, motors, takeoff, Land, RTL, or flight. SKYWriter remains receive-only
for telemetry and retains its existing mission-only and fixed-command compartments.

The exact machine-readable evidence classification is in [`manifest.json`](manifest.json).
The raw parameter export and screenshots are not committed: one source image exposes a
board UID, and local workstation paths are not product evidence. Hashes and sanitized
findings preserve provenance without publishing either.

## Evidence classification

| Classification | What Task 105 can say |
| --- | --- |
| Independently inspected | The supplied export has SHA-256 `EF8F...1020`, 1,269 lines, and passes the pre-change validator. The official stable APJ downloaded during this task has Board ID 1149, target `MatekH7A3`, git identity `3fc7011a`, and SHA-256 `6CBE...0956`. Supplied captures show the runtime/version/output and matched SiK settings recorded in the manifest. |
| Operator-reported physical configuration | Matek H7A3-SLIM; the radio is the only device on TX2/RX2; no RC receiver is attached; the GPS is a u-blox SAM-M10Q. These facts require physical confirmation where the procedure says so. |
| Accepted operator change | In Mission Planner, set only `SR2_EXT_STAT=2`, `SR2_POSITION=2`, and `SR2_EXTRA3=1`; leave every other `SR2_*` group at zero. SKYWriter never applies these values. |
| Not yet verified live | Cross wiring, persisted stream rates, fresh sensors/GPS, same-vehicle SiK reconciliation, and mission upload plus exact readback. These remain blocked on the supervised session. |

At 57,600 baud, the accepted modest-rate proposal is intended to make the existing
receive-only parser see the following required inputs without adding a stream request:

| Vehicle stream group | Rate | Required SKYWriter inputs covered |
| --- | ---: | --- |
| `SR2_EXT_STAT` | 2 Hz | `SYS_STATUS`, `MISSION_CURRENT`, `GPS_RAW_INT` |
| `SR2_POSITION` | 2 Hz | `GLOBAL_POSITION_INT` location |
| `SR2_EXTRA3` | 1 Hz | `BATTERY_STATUS`, `EKF_STATUS_REPORT` |

ArduCopter may include additional messages in those native groups. Live cadence and link
freshness are bench evidence, not facts inferred from the configured numbers.

## Exact profile

The flight controller is mapped to the official `MatekH7A3` target running stock official
ArduCopter 4.6.3. The APJ downloaded from the
[official stable directory](https://firmware.ardupilot.org/Copter/stable-4.6.3/MatekH7A3/arducopter.apj)
is 1,389,681 bytes and hashes to
`6CBEB3E1E109072963929EE582D4B0624E23ACB964C581F000881488F10E0956`.
Its metadata reports `APJFWv1`, Board ID 1149, `MatekH7A3`, and git identity `3fc7011a`.
The local APJ named in the handoff was unavailable, so this is independent verification
of the official artifact, not a claim that two files were byte-compared.

The runtime capture reports ArduCopter 4.6.3 (`3fc7011a`), ChibiOS `88b84600`, QUAD/X,
and DShot300 on outputs 1–4 with PWM on 5–11. It also contains a raw board UID that is
deliberately omitted here.

The pinned ArduPilot
[MatekH7A3 hardware definition](https://github.com/ArduPilot/ardupilot/blob/3fc7011a7d3dc047cbb17d8bd98ee94577d144c6/libraries/AP_HAL_ChibiOS/hwdef/MatekH7A3/Readme.md)
maps USB to `SERIAL0`, USART2 to `SERIAL2`, and the GPS USART3 to `SERIAL3`. Therefore
physical TX2/RX2 is `SERIAL2`, not `SERIAL3`. Before a live test, the operator must trace
both directions: radio TX to FC RX2 and radio RX to FC TX2.

The validated pre-change export contains:

- `SYSID_THISMAV=20`, `SYSID_MYGCS=255`, `FRAME_CLASS=1`, `FRAME_TYPE=1`;
- USB `SERIAL0_PROTOCOL=2`, `SERIAL0_BAUD=115`;
- unused RC assignment `SERIAL1_PROTOCOL=23`, `SERIAL1_BAUD=115`,
  `SERIAL1_OPTIONS=7` (no RC is attached);
- SiK on `SERIAL2_PROTOCOL=2`, `SERIAL2_BAUD=57`, `SERIAL2_OPTIONS=0`;
- GPS on `SERIAL3_PROTOCOL=5`, `SERIAL3_BAUD=115`, `SERIAL3_OPTIONS=0`, with
  `GPS1_TYPE=1` and `GPS1_RATE_MS=200`;
- nonzero primary barometer ID 816641 and one enabled external compass ID 855297 with
  nonzero calibration offsets;
- `ARMING_CHECK=4366` and `BATT_ARM_VOLT=19.7`; neither may be relaxed;
- `MIS_TOTAL=14`, proving that replacement confirmation is mandatory; and
- every `SR2_*` group at zero before the accepted operator change.

The validator intentionally does not interpret a `MIS_TOTAL` match as mission
verification. Only accepted `MISSION_ACK` followed by SKYWriter's complete download and
field-by-field normalized comparison can produce **Verified**.

## SiK evidence, without retail assumptions

The supplied configuration reports `RFD SiK 2.0 on HM-TRP`, `FREQ_915`, format 26,
57,600 UART baud, air speed 64, Net ID 27, TX power 20, 915000–928000 kHz, 50 channels,
duty cycle 100, LBT 0, MAVLink enabled, and max window 131 ms. ECC, RTS/CTS, opportunistic
resend, and AES are off. Both ends appeared matched. This is firmware/device evidence;
it is not a retail manufacturer/model claim.

One USB observation used COM19 at 115200 and one SiK-ground observation used COM8 at
57600. Windows COM assignments are dynamic. Discover and record the actual ports in each
session; never copy COM19 or COM8 into product behavior. Mission Planner must fully
release the SiK COM port before SKYWriter opens it.

## Offline validator

The validator is read-only and consumes an existing Mission Planner export:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
python tools/compatibility/big_bird_params.py `
  C:\path\to\pre-change.param --stage pre-change

python tools/compatibility/big_bird_params.py `
  C:\path\to\post-change.param --stage bench-ready
```

The second command may add `--expected-mission-count N` as a count cross-check, but that
still cannot replace mission readback. Missing, duplicate, malformed, non-finite, wrong,
or extra enabled `SR2_*` profile values fail closed.

## Bench gate and progression

Use [`bench-procedure.md`](bench-procedure.md) for the first supervised session. That gate
is intentionally limited to disarmed barometer health, compass health, valid GPS lock,
and mission upload plus exact readback. Passing it advances the evidence ladder; it does
not skip the later props-off command/link gates or authorize flight.

The durable progression is:

```text
Task 105 repository/profile readiness
  -> supervised disarmed profile + sensor + mission bench evidence
  -> broader USB and SiK props-off integrated-prototype validation
  -> reviewed aircraft-specific readiness and staged field plan
  -> incremental Takeoff/Land, Proceed, Hold, Circle, and representative-mission gates
  -> finished-product acceptance only after all required evidence is reviewed
```

Every arrow is a separate approval gate. No later result is implemented or claimed here.
