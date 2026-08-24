# Task 005A — stock ArduCopter 4.6.3 compatibility record

## Recommendation

**Reject stock ArduCopter 4.6.3 for the current SKYWriter compiler contract.** The
candidate accepts the upload, but its download is not field-for-field compatible.
Most importantly, ArduCopter consumes sequence zero as the home item and returns a
home waypoint instead of SKYWriter's `NAV_TAKEOFF`. Tasks 006–008 remain blocked
pending a separate, explicit architecture decision. This task does not propose or
make that decision.

The evidence PR itself may be accepted to preserve this compatibility result. That
does not constitute acceptance of ArduCopter 4.6.3 as a production target.

## Gate, scope, and safety

The accepted base is `ba1b266f52ae70e5318af05a49cc5ab39d1bcd32`. Its
[post-merge CI run](https://github.com/noob2027/Skywriter/actions/runs/32758289013)
was successful before Task 005A began.

The probe used only an official, unmodified stock SITL binary in a disposable
Ubuntu runner. It did not use real hardware, fly, arm, force-arm, change mode,
write a parameter, invoke RTL, modify or build firmware, or add a production
MAVLink/USB/SiK adapter. The mission upload changed only ephemeral SITL mission
storage and was required to obtain upload/readback evidence. The stock firmware
binary is not committed or included in the retained workflow artifact.

## Exact release and artifact identity

The official source tag and published stable SITL artifact are distinct and must
not be represented by one hash.

| Item | Immutable identity |
| --- | --- |
| Official tag | `Copter-4.6.3` = `92b0cd788ec29406f26c6f9c31d5ceedbd1cc538` |
| Published SITL source identity | `3fc7011a7d3dc047cbb17d8bd98ee94577d144c6` |
| Relationship | `3fc7011...` is the direct parent of `92b0cd7...` |
| Source delta | only `ArduCopter/version.h`: Beta to Official firmware type |
| Stable release | 2025-11-04; re-released 2025-11-20 after Beta/Stable metadata correction |
| Official Linux SITL binary | 7,023,152 bytes; SHA-256 `7862662092edc2861fc03da3d6fb2f0136d1670e563ca324eb52c1a324d1e14b` |
| Tag archive | 201,694,106 bytes; SHA-256 `e7876fb1801c35c3b0700f34715f3bb70de3e4c760933532555dfac5b295139b` |

The official SITL `git-version.txt` and live `AUTOPILOT_VERSION` custom-version
bytes both identify `3fc7011a`, while `flight_sw_version=0x04060380` reports
4.6.3 Official. The official directory's `firmware-version.txt` also reports
`4.6.3-FIRMWARE_VERSION_TYPE_OFFICIAL`. This is consistent with the documented
metadata correction but is not evidence that the published binary was rebuilt
from the later tag commit. The two official identities remain separately pinned.

Official sources:

- [ArduPilot releases](https://github.com/ArduPilot/ardupilot/releases)
- [Copter stable-4.6.3 firmware directory](https://firmware.ardupilot.org/Copter/stable-4.6.3/)
- [Pinned stock SITL directory](https://firmware.ardupilot.org/Copter/stable-4.6.3/SITL_x86_64_linux_gnu/)

Every downloaded or retained artifact and its hash is listed in `manifest.json` or
`evidence/SHA256SUMS`. The successful workflow artifact itself has SHA-256
`ad14eed70234bf2e063b916d7d90e6156e4b659c37c3d7a0b1707b0ea4f75d58`.

## MAVLink and probe dependency pin

The selected dialect is `ardupilotmega` over MAVLink 2. The Copter tag pins the
MAVLink repository at `bb87bc7390af7f21d9ad33a45c8be02997fecd24` and that
repository pins pymavlink source at
`8ba67079211a4315681bc84a44c37b383448d664`, whose declared version is 2.4.41.
The probe locks the official PyPI `pymavlink==2.4.41` wheel and all transitive
dependencies with hashes. The v2.4.41 release tag is
`4d8c4ff274d41b9bc8da1a411cb172d39786e46b`; it is not falsely equated with the
later source-submodule commit. Compatibility of this exact distribution is proven
by the live probe.

The exact `ardupilotmega.xml` and `common.xml` files at the MAVLink commit hash to
`dd9798d664d06e2b3c9115258ab30135e13f979826dd1de9774ed3ba848b5be1` and
`4563c1ca9d2461bec48f5efd844d142c14fea20464c318b6d5949f27cf0db0df`.
Every relevant received packet in the final trace used MAVLink 2 magic byte 253.

## Probe and raw evidence

The final [stock SITL run](https://github.com/noob2027/Skywriter/actions/runs/32761520693)
completed successfully in 54 seconds at probe commit
`ec0e545d4cd7b4dd4a778358fc45c75e3d105c32`. The preceding
[successful semantic run](https://github.com/noob2027/Skywriter/actions/runs/32761087476)
completed in 55 seconds. An initial
[diagnostic run](https://github.com/noob2027/Skywriter/actions/runs/32760709844)
failed closed when the target unexpectedly issued legacy mission requests; the
probe was then extended to retain that mismatch and continue to the next layer.

Raw evidence is retained under `evidence/`:

- `mavlink-messages.jsonl`: 297 timestamped sent/received records, including all
  seven upload items and all seven readback items;
- `probe-result.json`: structured identity, mission comparison, home behavior,
  native acknowledgements, safety declarations, and exact SITL invocation;
- `sitl.stdout.log` and `sitl.stderr.log`: stock-process output;
- `official/`: exact official metadata files;
- `SHA256SUMS`: hash ledger for every retained evidence file.

The TCP SITL console emits startup text on the same stream before MAVLink framing;
those bytes are retained as `BAD_DATA` records rather than suppressed.

## Mission protocol findings

The target reported capabilities `64495`, which includes
`MAV_PROTOCOL_CAPABILITY_MISSION_INT`, and every relevant packet used MAVLink 2.
Nevertheless, it issued seven `MISSION_REQUEST` messages rather than
`MISSION_REQUEST_INT`. The probe retained those requests and answered each with
the compiler boundary's `MISSION_ITEM_INT`. Stock ArduCopter accepted all seven
items with `MAV_MISSION_ACCEPTED` and returned all seven via
`MISSION_ITEM_INT`. This proves integer-item acceptance, while the request-format
mismatch remains an explicit future transport concern.

The upload contained the compiler whitelist in exact order:

| Seq | Upload command | Upload frame | Readback result |
| ---: | --- | ---: | --- |
| 0 | `NAV_TAKEOFF` (22) | 6 | replaced by home `NAV_WAYPOINT` (16), frame 0, `current=false`, home coordinates/altitude |
| 1 | `DO_CHANGE_SPEED` (178) | 6 | command/params preserved; frame normalized to 0 |
| 2 | `NAV_WAYPOINT` (16) | 6 | coordinates/params preserved; frame normalized to 3 |
| 3 | `NAV_LOITER_TIME` (19) | 6 | frame normalized to 3; `param3` changed from 0 to 1 |
| 4 | `NAV_LOITER_TURNS` (18) | 6 | values preserved except frame normalized to 3 |
| 5 | approach `NAV_WAYPOINT` (16) | 6 | coordinates/params preserved; frame normalized to 3 |
| 6 | `NAV_LAND` (21) | 6 | frame normalized to 3; `param4` changed from 0 to 1 |

Before upload, the mission count was zero. The independently requested home was
latitude `515007291`, longitude `-1246254`, altitude 15,100 mm. After upload,
sequence zero was that home point (altitude float32
`15.09999942779541`) rather than the takeoff. Every readback item had
`current=false`. Mission type remained 0 and `autocontinue=true` remained intact.
All non-home integer coordinates were exact. All other fixture floats were exactly
representable as float32 in this fixture; the comparison records compiler value,
wire-normalized value, readback value, and delta for every float field.

These are blocking architecture mismatches under Task 005A's acceptance rule.
They must not be hidden in a transport adapter or fixed by silently changing the
compiler fixture in this PR.

## Native compatibility acknowledgements

- `MAV_CMD_RUN_PREARM_CHECKS` (401): `MAV_RESULT_ACCEPTED`. This means the
  request was accepted; it does not mean the vehicle was armable. The accompanying
  text was `PreArm: Motors: Check frame class and type`.
- `MAV_CMD_DO_PAUSE_CONTINUE` (193), pause (`param1=0`):
  `MAV_RESULT_FAILED` while disarmed/not executing a mission.
- The same command, continue (`param1=1`): `MAV_RESULT_FAILED`, with
  `Failed to resume` status text.

These are compatibility observations only. No production command surface was
added.

## Windows reproduction and acquisition

The official stock SITL artifact is Linux x86_64. On Windows, use either the
manual GitHub workflow after this workflow exists on the default branch, or an
Ubuntu WSL environment following the official
[SITL landing page](https://ardupilot.org/dev/docs/SITL-setup-landingpage.html)
and [Windows WSL instructions](https://ardupilot.org/dev/docs/sitl-on-windows-wsl.html).
This workstation had no installed WSL distribution, so the live execution used
GitHub's `ubuntu-24.04` runner; acquisition and hashing were performed on Windows.

Windows acquisition verification:

```powershell
$artifactDirectory = 'C:\Skywriter-compat\Copter-4.6.3'
New-Item -ItemType Directory -Path $artifactDirectory -Force
Invoke-WebRequest `
  -Uri 'https://firmware.ardupilot.org/Copter/stable-4.6.3/SITL_x86_64_linux_gnu/arducopter' `
  -OutFile "$artifactDirectory\arducopter"
Get-FileHash -Algorithm SHA256 -LiteralPath "$artifactDirectory\arducopter"
```

Expected SHA-256:
`7862662092edc2861fc03da3d6fb2f0136d1670e563ca324eb52c1a324d1e14b`.

Ubuntu/WSL probe reproduction from the repository root:

```bash
python -m pip install --require-hashes \
  --requirement compatibility/arducopter-4.6.3/requirements-probe.lock
chmod 0700 /path/to/verified/arducopter
python tools/compatibility/arducopter_4_6_3_probe.py \
  --sitl /path/to/verified/arducopter \
  --fixture tests/fixtures/missions/mixed.json \
  --output compatibility-evidence
```

The probe's SITL process control, evidence normalization, compiler fixture,
production mission semantics, and future USB/SiK transports remain separate.
SITL is evidence infrastructure only; offline SKYWriter has no SITL or pymavlink
runtime dependency.

## Hardware-specific facts still unresolved

The intended family is Matek H7A3 or Matek H743. Official 4.6.3 directories named
`MatekH7A3`, `MatekH743`, and `MatekH743-bdshot` exist, but no exact board model,
revision, motor-output variant, or firmware file has been selected. No board file
hash is therefore claimed. The purchased board's official target mapping must be
verified before any future flashing work.

USB Type-C describes only the expected connector shape. It does not establish the
USB data interface, Windows driver, CDC serial endpoint, DFU/bootloader endpoint,
or board boot procedure.

A Holybro 933 MHz SiK radio is preferred but not purchased. Exact radio model,
firmware, region/legal frequency plan, paired-air/ground configuration, serial
port, voltage/pinout, and baud remain unresolved. A SpeedyFPV or other clone is
not assumed equivalent.

## Limitations and residual risk

- Evidence is Linux SITL, not H7 board/HAL, Windows USB, or SiK behavior.
- No real board firmware file was available to hash.
- The official firmware server provides the artifact but no adjacent published
  SHA-256 file; this record hashes the exact downloaded bytes and verifies them on
  each workflow run.
- GitHub Actions reported a platform warning that Node.js 20 actions were forced
  onto Node.js 24; the probe steps passed and the warning did not affect evidence.
- The source-tag/published-binary commit split remains a provenance risk even
  though both report official 4.6.3 metadata at runtime.

## Validation results

The exact local Python executable was
`C:\Users\Owner\Documents\Codex\venvs\swpa\Scripts\python.exe` (CPython
3.12.13). Commands that import SKYWriter used
`$env:PYTHONPATH=(Resolve-Path 'src').Path` so this worktree took precedence
over an older editable installation in that shared validation environment.

| Check | Exact command | Result | Wall duration |
| --- | --- | --- | ---: |
| Task evidence tests | `python -m pytest tests/compatibility/test_arducopter_4_6_3_evidence.py -q` | 5 passed (pytest 0.06s) | 0.797s |
| Full Windows/offscreen suite | `$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH=(Resolve-Path 'src').Path; python -m pytest` | 117 passed (pytest 10.69s) | 11.738s |
| Formatting | `python -m ruff format --check .` | 70 files already formatted | 0.249s |
| Lint | `python -m ruff check .` | passed | 0.179s |
| Static typing | `python -m mypy` | 46 source files, no issues | 0.668s |
| Probe syntax | `python -m py_compile tools/compatibility/arducopter_4_6_3_probe.py` | passed | 0.180s |
| Offscreen smoke | `$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH=(Resolve-Path 'src').Path; python -c "from skywriter.main import run; raise SystemExit(run(['skywriter-task005a'], close_after_ms=0))"` | exit 0 | 1.066s |

The stock SITL probe ran three times on GitHub `ubuntu-24.04`: one 49-second
fail-closed diagnostic that exposed legacy `MISSION_REQUEST`, followed by two
successful complete runs of 55 and 54 seconds. The final run is the retained
wire-level evidence. Thus the complete improved probe passed 2/2 repetitions;
the earlier failure is preserved as investigation history, not counted as a
passing repetition.

An initial local full-suite invocation without the `PYTHONPATH` override stopped
during collection because the shared venv referenced an older Skywriter
worktree. No tests executed in that invalid environment. The exact-current-tree
rerun above passed; repository CI installs the checked-out project editable and
does not use that shared local venv.

## Rollback

This change adds only compatibility evidence, a probe-only lock and script, tests,
and a manually triggered workflow. Roll back by reverting the Task 005A commit.
No firmware, production dependency, domain/compiler/UI behavior, saved user
mission, hardware state, or external vehicle state requires restoration.
