# Task 005A — stock ArduCopter 4.6.3 compatibility record

## Recommendation

**Pin stock ArduCopter 4.6.3 for the next connected-development wave.** The approved
pure compatibility envelope preserves the unchanged logical compiler, supplies an
authoritative same-vehicle home at native sequence zero, shifts Takeoff and every later
logical item by one, and verifies the downloaded native home separately. The translated
eight-item upload and canonicalized readback matched cleanly in stock SITL.

This is a compatibility recommendation, not hardware or flight acceptance. Tasks 006–008
may consume the accepted boundary after human review and merge; they remain outside this
PR. Props-off board, Windows USB, SiK, mission execution, and staged-flight gates remain.

## Gate, scope, and safety

The accepted base is `fd670c107f3c9ec9ed9569ceb494ff2bd0692af3`. Its
[post-merge CI run](https://github.com/noob2027/Skywriter/actions/runs/32766426099)
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
`evidence/SHA256SUMS`. The final successful workflow artifact itself has GitHub-recorded
SHA-256 `44d05a1fe8e92a72a94fd92932fc1d5d2f22d6a5d4fa97252e590a40c0f36dde`.

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

The final [stock SITL run](https://github.com/noob2027/Skywriter/actions/runs/32771294947)
completed successfully in 51 seconds at completed-boundary commit
`723dc1c6608adc0dadd3adf367c0b4020d6af5b5`. The preceding
[successful boundary run](https://github.com/noob2027/Skywriter/actions/runs/32767813658)
completed in 49 seconds at `576f9f0d221bbca00d6218ca22b65387f37aa0ad`.
The first
[diagnostic run](https://github.com/noob2027/Skywriter/actions/runs/32767540511)
failed closed in 42 seconds because the separately verified native home altitude was
one binary32 step below ordinary float32 packing. The exact value reproduced
ArduPilot's integer-centimeter multiplication by binary32 `0.01f`; that narrow,
home-only rule was added to the closed whitelist and the complete probe then passed.

The earlier Task 005A runs remain relevant history: one diagnostic exposed legacy
`MISSION_REQUEST`, and two complete unremediated probes established the original
sequence-zero loss and field normalizations. Their evidence was not rewritten.

Raw evidence is retained under `evidence/`:

- `mavlink-messages.jsonl`: 302 timestamped sent/received records, including all
  eight translated upload items and all eight readback items;
- `probe-result.json`: structured identity, mission comparison, home behavior,
  native acknowledgements, safety declarations, and exact SITL invocation;
- `sitl.stdout.log` and `sitl.stderr.log`: stock-process output;
- `official/`: exact official metadata files;
- `SHA256SUMS`: hash ledger for every retained evidence file.

The TCP SITL console emits startup text on the same stream before MAVLink framing;
those bytes are retained as `BAD_DATA` records rather than suppressed.

## Compatibility boundary

`skywriter.compatibility.arducopter_4_6_3` is pure Python with no connection,
transport, SITL, serial, USB, SiK, telemetry, parameter, or command dependency. It
accepts the unchanged `CompiledMission`, an opaque target identity, a caller-supplied
`HomeSnapshot`, and caller-supplied time. A home must be authoritative, fresh,
geographically valid, centimeter-preserving, and owned by the same vehicle.

`HomeUnresolved` is a typed, non-uploadable state for unconnected, unavailable, stale,
invalid, or wrong-vehicle home. Numeric `0,0,0` cannot stand in for missing home. No
`NativeMissionPackage` is returned on any failed home gate.

The exact normalization whitelist is:

- all MAVLink float payloads compare after binary32 packing;
- sequence-zero home remains frame 0 and is verified separately;
- home altitude uses integer centimeters multiplied by binary32 `0.01f`;
- `DO_CHANGE_SPEED` frame 6 reads back as frame 0;
- navigation frame 6 reads back as frame 3;
- `NAV_LOITER_TIME.param3` zero reads back as one;
- `NAV_LAND.param4` zero reads back as one.

Every other command, count, sequence, frame, current/autocontinue flag, parameter,
coordinate, altitude, or mission-type difference fails closed. Lossy normalizations are
not reversed or presented as compiler values.

## Mission protocol findings

The target reported capabilities `64495`, which includes
`MAV_PROTOCOL_CAPABILITY_MISSION_INT`, and every relevant packet used MAVLink 2.
Nevertheless, it issued eight `MISSION_REQUEST` messages rather than
`MISSION_REQUEST_INT`. The probe retained those requests and answered each with the
compatibility package's `MISSION_ITEM_INT`. Stock ArduCopter accepted all eight items
with `MAV_MISSION_ACCEPTED` and returned all eight via `MISSION_ITEM_INT`. This proves
integer-item acceptance through the new boundary, while the request-format mismatch
remains an explicit future Task 007 transport concern.

The native upload contained home followed by the unchanged compiler whitelist:

| Seq | Upload command | Upload frame | Readback result |
| ---: | --- | ---: | --- |
| 0 | native home `NAV_WAYPOINT` (16) | 0 | home coordinates preserved; altitude matched exact home-only normalization |
| 1 | `NAV_TAKEOFF` (22) | 6 | preserved; frame normalized to 3 |
| 2 | `DO_CHANGE_SPEED` (178) | 6 | command/params preserved; frame normalized to 0 |
| 3 | `NAV_WAYPOINT` (16) | 6 | coordinates/params preserved; frame normalized to 3 |
| 4 | `NAV_LOITER_TIME` (19) | 6 | frame normalized to 3; `param3` changed from 0 to 1 |
| 5 | `NAV_LOITER_TURNS` (18) | 6 | values preserved except frame normalized to 3 |
| 6 | approach `NAV_WAYPOINT` (16) | 6 | coordinates/params preserved; frame normalized to 3 |
| 7 | `NAV_LAND` (21) | 6 | frame normalized to 3; `param4` changed from 0 to 1 |

Before upload, the mission count was zero. The independently requested home was
latitude `515007291`, longitude `-1246254`, altitude 15,100 mm. The boundary placed
that home at sequence zero and shifted Takeoff to sequence one. Every readback item had
`current=false`; mission type remained 0 and `autocontinue=true` remained intact. All
non-home integer coordinates were exact. Native home and the seven shifted logical items
both verified with zero mismatches after the closed whitelist.

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

The probe's SITL process control, the pure compatibility envelope, the logical compiler,
and future USB/SiK transports remain separate. The probe imports the same pure envelope
that future transport work must consume; it does not duplicate its normalization rules.
SITL is evidence infrastructure only; offline SKYWriter has no SITL or pymavlink runtime
dependency.

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

## Deferred adaptation and refinement points

- Task 007 must define how a connected session establishes the opaque vehicle identity,
  obtains `HOME_POSITION`, marks it authoritative, selects a bounded freshness lifetime,
  and rechecks package expiry immediately before upload. This PR supplies no connection.
- Task 007 must handle the observed legacy `MISSION_REQUEST` sequence, target/mission-type
  routing, retries, timeouts, disconnects, negative acknowledgements, and full downloads.
  None of those transaction responsibilities move into the pure envelope.
- Task 006 may reuse the probe boundary in a repeatable harness but must keep SITL process
  control optional and outside offline/runtime mission semantics.
- Exact Matek target/firmware mapping, Windows USB endpoints, and SiK configuration remain
  hardware facts for later separately authorized gates.
- A future ArduCopter pin change must provide a new version-specific envelope/evidence
  decision or prove that this exact closed normalization contract remains valid.

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
- The successful result proves mission storage/readback compatibility in Linux SITL. It
  does not yet prove execution behavior, connection-state correctness, or hardware I/O.

## Validation results

The exact local Python executable was
`C:\Users\Owner\Documents\Codex\venvs\swpa\Scripts\python.exe` (CPython
3.12.13). Commands that import SKYWriter used
`$env:PYTHONPATH=(Resolve-Path 'src').Path` so this worktree took precedence
over an older editable installation in that shared validation environment.

| Check | Exact command | Result | Wall duration |
| --- | --- | --- | ---: |
| Boundary and evidence tests | `$env:PYTHONPATH=(Resolve-Path 'src').Path; python -m pytest tests/unit/compatibility/test_arducopter_4_6_3.py tests/compatibility/test_arducopter_4_6_3_evidence.py -q` | 35 passed (pytest 0.22s) | 0.990s |
| Full Windows/offscreen suite | `$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH=(Resolve-Path 'src').Path; python -m pytest` | 148 passed (pytest 7.78s) | 8.618s |
| Formatting | `python -m ruff format --check .` | 73 files already formatted | 0.168s |
| Lint | `python -m ruff check .` | passed | 0.142s |
| Static typing | `$env:PYTHONPATH=(Resolve-Path 'src').Path; python -m mypy` | 49 source files, no issues | 0.662s |
| Probe syntax | `python -m py_compile tools/compatibility/arducopter_4_6_3_probe.py` | passed | 0.174s |
| Offscreen smoke | `$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH=(Resolve-Path 'src').Path; python -c "from skywriter.main import run; raise SystemExit(run(['skywriter-task005a-remediation'], close_after_ms=0))"` | exit 0 | 0.975s |

The remediated stock-SITL boundary passed 2/2 complete GitHub `ubuntu-24.04` runs:
49 seconds ([run 32767813658](https://github.com/noob2027/Skywriter/actions/runs/32767813658))
and 51 seconds ([final retained run 32771294947](https://github.com/noob2027/Skywriter/actions/runs/32771294947)).
The preceding 42-second diagnostic run failed closed on the exact home-altitude
normalization and led to the narrow evidence-backed whitelist entry; it was not retried
unchanged or hidden as a passing run.

## Rollback

Revert this remediation PR to remove the pure compatibility package, its tests, and the
updated evidence/recommendation. That restores the accepted Task 005A rejection record
and re-blocks Tasks 006–008; it does not require changing the logical compiler. No
firmware, runtime dependency, UI behavior, saved mission, hardware state, or external
vehicle state requires restoration because this work performed no production I/O.
