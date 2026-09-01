# Windows prototype installer

## What this proves

Tasks 106 and 107 package the accepted SKYWriter Python, PySide6/Qt WebEngine, pymavlink, map
assets, Python runtime, Qt plugins/resources, and dependency notices as a PyInstaller
`onedir` payload. Inno Setup wraps that payload in one per-user Setup executable. Version
0.1.1 is the Task 107 map-usability prototype.

Successful installation and launch prove desktop deployment mechanics only. They do not
prove vehicle compatibility, bench readiness, arming, motor, mission-execution, or flight
readiness. Normal startup remains offline and disconnected. The installer does not open a
COM port, contact Mission Planner, change parameters, request streams, send vehicle
commands, or make a network request. No port, board identity, SiK Net ID, or
aircraft-specific setting is embedded in startup behavior.

## Install, launch, and uninstall

The expected files are:

- `SKYWriter-Prototype-Setup-0.1.1.exe`
- `SHA256SUMS.txt`
- `build-metadata.json`

Before installing, compare the Setup file's SHA-256 value with `SHA256SUMS.txt`. In
PowerShell:

```powershell
Get-FileHash .\SKYWriter-Prototype-Setup-0.1.1.exe -Algorithm SHA256
```

Double-click Setup and follow the prompts. It installs for the current Windows user under
local application data, so administrator rights are not expected. Setup always creates a
Start-menu entry. The desktop shortcut is selected by default and may be cleared in the
wizard. After installation, launch **SKYWriter Prototype** from either shortcut.

Uninstall from **Windows Settings → Apps → Installed apps → SKYWriter Prototype**, or use
the standard uninstall entry Windows exposes for the application. Uninstall removes the
installed payload and shortcuts. Mission files are user-selected documents outside the
installation directory and are not deleted.

## Reproducible local build

Use 64-bit Windows and exact CPython 3.12.10. From a clean checkout:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build-installer.ps1 `
  -PythonPath C:\Path\To\Python312\python.exe `
  -OutputDirectory .\artifacts\windows
```

The entry point creates an isolated short-path virtual environment, installs the existing
application lock plus `packaging/requirements-build.lock`, collects declared runtime
license files, generates the provisional icon, builds the `onedir` payload, acquires
Inno Setup 6.7.3 from its official release, verifies the pinned download SHA-256, compiles
the installer, and runs a silent install/launch/uninstall smoke test. `-BuildRoot` may set
another dedicated short path whose final directory name contains `skywriter`, `sw106`, or
`sw107`.

The packaged launch smoke starts from an arbitrary working directory, blocks the MAVLink
open boundary, waits up to 15 seconds for the real local map page, Qt WebChannel bridge, and
pinned Leaflet 1.9.4 surface to report positive dimensions, writes deterministic JSON
evidence, and exits success or failure. It remains on the offline provider and therefore
makes no tile-network request. Use
`-SkipInstallerSmoke` only while diagnosing a build; CI does not skip it.

Pinned build inputs:

- CPython 3.12.10 x64
- PyInstaller 6.22.2
- pyinstaller-hooks-contrib 2026.7
- Inno Setup 6.7.3, installer SHA-256
  `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`
- application/runtime versions in `requirements.lock`

Generated installers, payloads, certificates, and private keys are ignored and must not
be committed.

Task 107's verified unsigned local artifact is
`SKYWriter-Prototype-Setup-0.1.1.exe`, 147,313,409 bytes, SHA-256
`c293846ead84224cdaf0f260c5bf43361f4f30a9ad342b8005e4c7b24135f5b2`. Its clean smoke
mounted packaged Leaflet 1.9.4 at 608 × 358 CSS pixels from `C:\Windows` with the offline
provider and vehicle-I/O guard active, then uninstalled successfully.

## Optional signing seam

Unsigned builds are fully functional and record `"signed": false` in
`build-metadata.json`. They may trigger Microsoft Defender SmartScreen because neither a
publisher certificate nor reputation is configured.

The build signs `SKYWriter.exe` before installer compilation and signs the final Setup
file only when both variables are explicitly present:

- `SKYWRITER_SIGN_CERTIFICATE_FILE`: path to a temporary PFX certificate
- `SKYWRITER_SIGN_CERTIFICATE_PASSWORD`: its password

`SKYWRITER_SIGNTOOL` may select `signtool.exe`, and
`SKYWRITER_SIGNING_TIMESTAMP_URL` may explicitly select a timestamp service. CI accepts
the base64 PFX and password through the `WINDOWS_SIGNING_CERTIFICATE_BASE64` and
`WINDOWS_SIGNING_CERTIFICATE_PASSWORD` secrets, creates the temporary PFX only for that
job, and removes it at job cleanup. No certificate or key belongs in Git.

Before any public release, the owner must choose the final legal publisher identity and
obtain/configure its matching Windows code-signing certificate. A later release task must
then verify signatures, decide release retention/versioning, review bundled-license
obligations, and deliberately publish a GitHub Release. Task 106 does none of those.

## Known limitations

- Builds are unsigned unless signing secrets are supplied; SmartScreen warnings are
  expected for unsigned downloads.
- The repository-owned document icon is deliberately provisional and replaceable when
  final artwork is approved.
- Only 64-bit Windows is supported by this installer.
- There is no updater, GitHub Release publication, certificate, reputation claim, or
  automatic network check.
- SKYWriter remains a prototype/hardware-candidate precursor, not a flight-validated or
  production-certified product.
