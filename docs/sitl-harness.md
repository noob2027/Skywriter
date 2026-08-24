# Pinned ArduCopter SITL harness

Task 006 provides test and evidence infrastructure for the exact approved stock
ArduCopter 4.6.3 SITL artifact. It is not a production runtime dependency, transport,
or vehicle-control path. Offline SKYWriter, future USB transport, and future SiK
transport do not import or require this harness.

## Immutable target

| Property | Pinned value |
| --- | --- |
| Official artifact | `Copter/stable-4.6.3/SITL_x86_64_linux_gnu/arducopter` |
| Artifact size | `7,023,152` bytes |
| Artifact SHA-256 | `7862662092edc2861fc03da3d6fb2f0136d1670e563ca324eb52c1a324d1e14b` |
| Official release tag commit | `92b0cd788ec29406f26c6f9c31d5ceedbd1cc538` |
| Published stable SITL source commit | `3fc7011a7d3dc047cbb17d8bd98ee94577d144c6` |
| Runtime `flight_sw_version` | `0x04060380` |
| Runtime custom version | `3fc7011a` |
| MAVLink | version 2, `ardupilotmega` dialect |
| Probe library | `pymavlink==2.4.41` |

The release-tag commit and published-binary commit are distinct official identities.
The harness preserves both and verifies the downloaded executable before it is ever
passed to the operating system for execution. A cached file is verified again on
every run. No firmware source is built, patched, flashed, or redistributed.

## What one smoke run proves

The harness starts the stock binary with `--wipe` in a unique working directory and
uses explicit loopback TCP/UDP ports, the `quad` model, home
`51.5007292,-0.1246254,15,0`, system ID 1, speedup 1, and a fixed simulation start
time. A cross-process lock prevents two harnesses from sharing a port block. Automatic
allocation can support parallel invocations; CI uses explicit non-overlapping blocks
so its endpoints are reproducible.

Readiness is an observed MAVLink handshake rather than a sleep. The fixture:

1. receives a vehicle heartbeat from ArduPilot;
2. confirms the heartbeat is disarmed;
3. makes the native read-only `MAV_CMD_REQUEST_MESSAGE` request for
   `AUTOPILOT_VERSION`;
4. verifies the exact firmware and MAVLink identities above; and
5. sends `MISSION_REQUEST_LIST` and requires mission type 0 with count 0.

The request-message command is used only as a read-only identity probe. The harness
has no parameter write, mission upload, arm, mode, telemetry-presentation, or flight
control behavior.

Each fixture yields the isolated endpoint, exact target identity, and clean mission
state. Teardown closes MAVLink, terminates the entire SITL process group within a
bounded deadline, escalates to a bounded kill only if necessary, confirms all ports
are released, and releases the process lock. Normal and failing runs retain
`result.json`, protocol JSON Lines, process stdout/stderr, the isolated working files,
and `SHA256SUMS`. Acquisition produces its own JSON record and SHA-256 sidecar.

## Windows developer use

The approved official binary is Linux x86_64. This Windows workstation has no
installed WSL, Docker, or Podman, and Task 006 does not build firmware or invent a
native Windows substitute. Windows developers can run the platform-independent
contract and all ordinary repository gates:

```powershell
python -m pytest tests/sitl/test_harness_contract.py -q
python -m pytest
```

The exact smoke execution runs on the repository's `ubuntu-24.04` GitHub Actions job.
On a trusted Linux x86_64 machine, the same acquisition and smoke commands are:

```bash
python -m pip install --require-hashes \
  --requirement compatibility/arducopter-4.6.3/requirements-probe.lock
python -m pip install pytest==9.1.1
python -m scripts.sitl.acquire \
  --destination .cache/skywriter-sitl/arducopter \
  --record .cache/sitl-evidence/acquisition.json
MAVLINK20=1 \
SKYWRITER_SITL_BINARY=.cache/skywriter-sitl/arducopter \
SKYWRITER_SITL_EVIDENCE=.cache/sitl-evidence/run-1 \
SKYWRITER_SITL_BASE_PORT=26000 \
python -m pytest tests/sitl/test_pinned_sitl_smoke.py -q --durations=10
```

Use a different explicit block for a concurrent run, or omit
`SKYWRITER_SITL_BASE_PORT` to lease the first free managed block. A requested block
already leased or occupied fails closed instead of falling back silently.

## Resource cost and limitations

The CI job downloads about 7 MB on a cold artifact cache and installs the isolated
probe dependency set. It runs one platform-independent contract suite and two fresh
SITL processes. Exact observed durations and artifact sizes are recorded by each CI
run and its uploaded `result.json` files.

Task 006 acceptance run
[`32775510581`](https://github.com/noob2027/Skywriter/actions/runs/32775510581)
completed the Ubuntu job in 30 seconds on branch commit
`d24e1832758c0c4f848b6a45f6ae3d0edad1a419`. The contract suite passed 6 tests in
0.05 seconds. Fresh SITL run 1 passed in 1.12 seconds (1.101 seconds recorded by the
harness) on base port 26000; fresh run 2 passed in 1.11 seconds (1.097 seconds recorded
by the harness) on base port 26100. Both reported the exact target above, disarmed
`base_mode=89`, mission count 0/type 0, bounded SIGTERM exit `-15`, and
`ports_released=true`.

The uncompressed retained evidence is 106,510 bytes. Uploaded artifact
`pinned-sitl-32775510581-1` is 9,793 bytes with archive SHA-256
`53127403ffae99ec1d2a02cfd6b8239e6c25a4dbbcf1d41b1c0415db62c58539`.
The acquisition record, both result documents, protocol logs, process logs, and both
generated EEPROM files passed their recorded SHA-256 checks.

The matching Windows run
[`32775510477`](https://github.com/noob2027/Skywriter/actions/runs/32775510477)
completed in 73 seconds: formatting checked 80 files, lint passed, strict typing passed
55 source files, the full suite passed 154 tests in 6.83 seconds with the one explicit
Linux-artifact smoke skip, and the offscreen application smoke exited successfully.
The skip is replaced, not bypassed, by the dedicated Ubuntu job's two real executions.

This is startup/readiness evidence, not mission execution or production transport
evidence. It does not cover Windows execution, physical USB, Matek board-target
mapping, SiK radios, telemetry, uploads, commands, parameters, arming, modes, or real
flight. Those remain deferred to their separately authorized tasks and gates.
