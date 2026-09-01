# Task 106 — production-grade Windows prototype installer

## Goal

Deliver durable Windows installer infrastructure so a 64-bit Windows user without Python
can install SKYWriter from one Setup executable, launch it from standard shortcuts, and
uninstall it cleanly. Label every artifact as a prototype/hardware-candidate precursor,
not as flight-validated.

## Base and ownership

Base: accepted Task 105 on `main` (`97c76dd`). Run serially.

Own: Windows packaging dependencies and scripts, repository-owned provisional artwork,
one Windows installer CI workflow, packaging/path/smoke tests, this handoff, installer
instructions, and the smallest frozen-runtime safety seam. Do not change mission,
telemetry, vehicle-command, parameter, connection, firmware, or SITL behavior.

## Acceptance criteria

- A clean 64-bit Windows build uses CPython 3.12.13, exact application locks,
  PyInstaller 6.22.2, hooks 2026.7, and verified Inno Setup 6.7.3.
- PyInstaller emits a reliable `onedir` payload containing Python, PySide6/Qt WebEngine,
  Qt plugins/resources, pymavlink, local map/static assets, required dynamic dialect
  resources, documentation, and declared dependency license files.
- Inno Setup emits `SKYWriter-Prototype-Setup-0.1.0.exe` and installs per-user without
  required elevation under local application data.
- Setup creates a Start-menu shortcut, offers a default-selected desktop shortcut,
  registers standard uninstall metadata, and cleanly removes the payload and shortcuts.
- Metadata uses `SKYWriter Prototype`, version `0.1.0`, and the repository-authoritative
  `305 Skylab` organization. It makes no copyright, signing, production, certification,
  bench, motor, or flight claim.
- Normal shortcut startup remains offline and inert. It contains no hardcoded COM port,
  UID, Net ID, parameter behavior, stream request, auto-connect, update, or command path.
- Map/static paths resolve relative to the installed package rather than the shortcut
  working directory. User-selected mission documents remain outside the install tree.
- A deterministic `--packaged-smoke-test` loads the frozen Qt/WebEngine shell, blocks the
  MAVLink open boundary, and exits successfully from an arbitrary working directory.
- Automated tests cover version/metadata consistency, per-user install settings,
  shortcuts, uninstall, artifact names, provisional icon validity, forbidden constants,
  and smoke-mode vehicle-I/O blocking.
- A Windows CI workflow builds and uploads the installer, `SHA256SUMS.txt`, and build
  metadata without creating a GitHub Release. Its install/launch/uninstall smoke passes.
- Optional signing occurs only when explicit certificate/password secrets are present.
  Unsigned output remains functional and is marked unsigned. No key or certificate is
  created or committed.
- README, development plan, task index, and `docs/windows-installer.md` describe install,
  launch, uninstall, local build, exact artifacts, limitations, and future release steps.
- Full formatting, lint, type, unit/integration/UI, Windows packaging, and existing pinned
  SITL checks remain green. Generated binaries are not committed.

## Build and verification

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build-installer.ps1 `
  -PythonPath C:\Path\To\Python312\python.exe `
  -OutputDirectory .\artifacts\windows
```

The build prints and records the exact byte size and SHA-256. CI uploads the directory as
`skywriter-prototype-windows-unsigned-0.1.0` unless signing secrets are configured.

## Known limitations and future release steps

Task 106 output is unsigned by default and may trigger SmartScreen. The repository icon is
neutral and provisional. There is no updater or release publication. The owner must later
approve a final legal publisher identity and matching paid/trusted code-signing
certificate. A separate release task must configure and verify timestamped signing,
review bundled-license obligations, replace artwork if desired, choose public release
retention/versioning, and publish deliberately.

## Stop

Open a PR and stop. Do not merge, tag, publish a GitHub Release, install on an aircraft
computer, operate hardware, or claim vehicle/bench/arming/motor/flight readiness.
