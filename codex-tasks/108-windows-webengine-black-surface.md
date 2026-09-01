# Task 108 — Windows WebEngine black-surface hotfix

## Goal

Repair the installed Windows 0.1.1 mission Builder whose Qt WebEngine child surface can
composite as solid black even while the local page, bridge, and some OSM tile requests are
alive. Deliver the unsigned 0.1.2 prototype installer without widening map or vehicle
authority.

## Base and ownership

Base: merged Task 107 on `main` (`b01e6b9`). Run serially.

Own: process-level Windows WebEngine renderer selection, bounded renderer diagnostics,
pixel-level map smoke evidence, map-specific tests, Windows packaging/version metadata,
representative screenshots, and directly affected documentation. Do not change mission
semantics, MAVLink, connection/identity, telemetry, parameters, firmware, arming, commands,
motors, or flight behavior.

## Reproduction and root cause evidence

The owner-installed CI 0.1.1 payload was reproduced on the same Windows host. The owner
capture shows the native Builder and provider banner working, OSM selected, `8 tiles
received, 4 failed, 0 unanswered`, and the entire WebEngine rectangle solid black with no
visible Leaflet zoom controls. The accepted 0.1.1 readiness smoke still reported the local
page, bridge, Leaflet 1.9.4, and positive 606 × 358 CSS dimensions as ready.

The exact installed executable produced this default-renderer diagnostic repeatedly:

```text
Failed to create shared context for virtualization.
```

Desktop captures separated the rendering paths:

- default Qt/Chromium acceleration: black WebEngine rectangle;
- `QT_OPENGL=software` alone: still black;
- `QT_QUICK_BACKEND=software` alone: still black;
- Qt WebEngine's documented Chromium `--disable-gpu` software path: the local grid,
  Leaflet zoom controls, attribution, and tiles visibly render.

This is a failed Chromium/Qt GPU-compositor handoff, not a missing local page, bridge, GPS,
or mission location. The 8/4 OSM delivery is separate: one bounded manual world-view probe
using the identifying SKYWriter user agent received all eight expected tiles with HTTP 200
and cache headers. That does not erase the observed partial attempt or claim an OSM SLA;
the existing Partial/Unavailable/Retry behavior remains the honest response to transient
provider, DNS, TLS, firewall, or network failures.

The current official OpenStreetMap tile policy still requires the fixed HTTPS endpoint,
visible attribution, identifiable user agent, normal cache handling, and no bulk download
or prefetch. Task 108 preserves each of those constraints.

## Implemented behavior

- Windows selects Chromium software rendering with the single documented `--disable-gpu`
  switch before `QApplication` construction. It does not set Qt software OpenGL because
  that path did not repair this host, and it does not set the broader
  `--disable-gpu-compositing` switch in the installed product.
- SKYWriter never sets `--no-sandbox`, `QTWEBENGINE_DISABLE_SANDBOX`, TLS bypasses, CSP
  bypasses, or request-interceptor bypasses. Safe diagnostics explicitly distinguish the
  SKYWriter-selected renderer from any sandbox override inherited from the environment.
- A bounded, non-identifying `map-renderer.json` records renderer mode, Qt platform/version,
  whether Chromium GPU acceleration is disabled, and sandbox facts. It contains no command
  line, device identity, arbitrary environment values, URLs, coordinates, mission data, or
  vehicle identity.
- The hardware-blocked packaged visual smoke starts from `C:\Windows`, uses the existing
  loopback-only controlled tile seam, waits for balanced Online counters, captures the real
  `QWebEngineView` surface, and fails unless the controlled green tile pixels, non-black
  content, and both DOM-present and pixel-visible Leaflet zoom controls are proven.
- Routine tests and packaging never contact public OSM. The loopback seam is accepted only
  while explicit packaged smoke mode is active and continues through the same image-only
  interceptor classification.

## Verification requirements

- Pure tests prove the Windows renderer adds `--disable-gpu` without adding a sandbox
  bypass, diagnostics remain bounded, and an all-black image fails even if DOM/tile counters
  claim success.
- Mounted tests prove controlled tiles and zoom controls exist in captured WebEngine pixels,
  in addition to the existing bridge/provider/retry/recenter/click/drag/route/Circle/Land
  checks.
- The installer smoke proves install, Start-menu shortcut, arbitrary-working-directory
  launch, hardware-I/O blocking, local controlled tiles, non-black map pixels, visible
  controls, renderer diagnostics, and uninstall.
- Full formatting, lint, strict typing, unit/integration/UI/packaging, and pinned SITL policy
  checks remain required. No public OSM request is part of CI.

Representative before/after and controlled-tile captures are stored under
`docs/screenshots/task-108/`. The controlled tile is intentionally synthetic and is not a
claim about public OSM availability.

## Windows installer evidence

- Installer: `SKYWriter-Prototype-Setup-0.1.2.exe`
- Size: 147,330,245 bytes
- SHA-256: `fe35b9d49842939ec3e302ab87fb3628b9baa5234130dd51cd4745d9876f6800`
- Authenticode: unsigned (`NotSigned`); SmartScreen/reputation warnings remain expected

The final exact artifact passed silent per-user install, Start-menu shortcut, hardware-I/O
blocking, launch from `C:\Windows`, deterministic eight-tile rendering, pixel-visible
Leaflet controls and attribution, and uninstall. Its captured 634 × 440 map surface was
99.9771% non-black, contained the controlled tile signature in 66.4382% of sampled pixels,
and reported eight loaded, zero failed, and zero pending tiles. Renderer evidence recorded
Chromium software mode with GPU acceleration disabled and both environment- and
SKYWriter-originated sandbox bypasses false.

## Remaining limitations

OpenStreetMap Standard remains a community best-effort service with no production SLA.
SKYWriter still has no satellite/commercial provider, provider account, geocoder, offline
tile distribution, or authoritative Home/Vehicle point in the isolated mission Builder.
Software composition is deliberately preferred for this lightweight 2D Windows map; a later
performance-oriented renderer change requires new visual acceptance on representative GPUs.

## Stop

Open a PR and stop. Do not merge, tag, publish a GitHub Release, operate hardware, or claim
bench, motor, flight, production-provider, or public-OSM-SLA readiness.
