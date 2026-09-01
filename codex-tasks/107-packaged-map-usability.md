# Task 107 — packaged mission-map usability

## Goal

Make the installed mission-authoring map understandable, navigable, observable, and
verifiably mounted while preserving the accepted Leaflet/Qt isolation and every vehicle
safety boundary. Deliver the next unsigned Windows prototype installer as version 0.1.1.

## Base and ownership

Base: accepted Task 106 on `main` (`86449c6`). Run serially.

Own: mission Builder map host/bridge/static assets, the smallest Builder controls and status
presentation, map-specific unit/mounted UI tests, packaged map readiness smoke, version and
installer metadata, representative screenshots, and directly affected documentation. Do not
change mission compilation, persistence semantics, vehicle connection/identity, MAVLink,
telemetry, parameters, firmware, arming, commands, motors, or flight behavior.

## Reproduction and demonstrated causes

Accepted 0.1.0 was reproduced with the closest faithful packaged Qt WebEngine runtime from an
arbitrary working directory. The local page, Qt WebChannel, pinned Leaflet 1.9.4 assets, zoom
controls, fixed OSM request allowlist, CSP, and existing `SKYWriter/0.1.0` user agent all mount.
A bounded live probe received 16 of 16 world-view OSM tiles, proving there is no universal
packaging, TLS, CSP, interceptor, or asset-path failure. A deliberately blocked probe produced
zero visible tiles while the graph-paper CSS remained visible.

The installed-product defect was the combination of:

- a selector label that claimed only “network” and never proved Loading, received tiles,
  partial delivery, or unavailable delivery;
- a single vague error with no correlated counts or deliberate Retry;
- an unexplained Washington, D.C. initial viewport and no way to reach an operating area by
  coordinates;
- every mission render removing and recreating the tile layer, causing avoidable requests;
- session-only `MemoryHttpCache` rather than a bounded disk cache honoring HTTP cache headers;
- no settled-size synchronization, allowing the Leaflet viewport/tile surface to retain an
  earlier WebEngine size until a later interaction;
- a 250 ms packaged smoke that proved process launch/assets, not mounted map/bridge readiness.

Diagnostic interpretation is intentionally split: absent Leaflet zoom controls indicate local
page/mount failure; controls plus graph paper and an OSM selection require provider/interceptor/
CSP/transport evidence; painting only after resize indicates the size lifecycle. The Flight tab
is separately documented and intentionally has no basemap.

One sanitized blocked diagnostic is retained conceptually as:

```text
provider=openstreetmap attempt=1 state=unavailable loaded=0 failed>0 pending>=0 host=tile.openstreetmap.org
```

No query, credential, arbitrary URL, mission content, or vehicle identifier is logged.

## Implemented behavior

- Offline is the no-network default and begins at a neutral world viewport `(0, 0)`, zoom 2.
- Strict decimal latitude/longitude validation accepts only finite values within `[-90, 90]`
  and `[-180, 180]`; **Go / recenter** never creates or changes a mission point.
- The fixed OSM provider reports Offline, Loading, Online, Partial, and Unavailable with one
  attempt identifier and balanced requested/loaded/error/pending counts. Retry is explicit;
  provider selection never changes silently.
- Tile events are correlated to the active layer and attempt. Cancelled/unloaded elements no
  longer remain as phantom pending requests, and late events from replaced attempts are ignored.
- Ordinary resize settles through the Qt host and Leaflet `invalidateSize`/visible-tile redraw.
- The fixed endpoint, strict typed bridge, navigation block, CSP, visible attribution,
  application-identifying user agent, and interactive-only request behavior remain intact.
- A 128 MiB disk HTTP cache honors normal response headers. This is not an offline map cache and
  there is no bulk download, prefetch, or scraping feature.
- **Center Home / Vehicle** is visibly disabled: the current mission Builder contract has no
  authoritative current same-vehicle point. Later integration must supply a typed, fresh,
  identity-bound point from Python; the map must not access MAVLink.
- No geocoder, satellite/commercial provider, key storage, provider account, or arbitrary URL
  extension was added.

## Verification requirements

- Bridge/model/unit tests cover all provider states, counter invariants, attempt staleness, and
  strict coordinate validation.
- Mounted Qt WebEngine tests cover local Leaflet readiness, neutral viewport, no-network default,
  fixed-provider switching, attribution, identifying user agent, controlled success/failure,
  Retry recovery, resizing, recentering, clicks, selection/dragging, route lines, Circle, Land,
  and viewport round trips.
- Routine CI uses an interceptor-gated loopback tile fixture; it never calls public OSM. A bounded
  optional manual live probe may confirm the public endpoint without prefetch or bulk access.
- The packaged Windows smoke installs 0.1.1, launches from an arbitrary working directory with
  hardware access blocked, waits for the real map page/bridge/Leaflet surface and positive
  dimensions, records JSON evidence, and uninstalls.
- Full format, lint, strict typing, unit/integration/UI/packaging, and existing pinned SITL policy
  checks remain green. Generated installers are not committed.

Representative Windows captures are stored in `docs/screenshots/task-107/`: neutral offline,
loading, controlled deterministic tile success, and actionable provider failure. The success
image is explicitly a loopback fixture, not a claim about public OSM availability; the bounded
live probe is recorded separately in the reproduction evidence.

## Windows installer evidence

The accepted Task 106 pipeline was run with verified CPython 3.12.10 x64, PyInstaller 6.22.2,
and Inno Setup 6.7.3. The clean installer smoke passed install, Start-menu shortcut creation,
launch from `C:\Windows`, mounted local map/WebChannel/Leaflet readiness, hardware-I/O blocking,
and uninstall. Recorded readiness was Leaflet 1.9.4, 608 × 358 CSS pixels, offline provider,
existing packaged `map.html`, and `vehicle_io_blocked=true`.

- Installer: `SKYWriter-Prototype-Setup-0.1.1.exe`
- Size: `147313409` bytes
- SHA-256: `c293846ead84224cdaf0f260c5bf43361f4f30a9ad342b8005e4c7b24135f5b2`
- Authenticode: unsigned (`NotSigned`); SmartScreen/reputation warnings remain expected

## Remaining limitations and later owner decision

OpenStreetMap Standard is a community best-effort service with no production SLA. SKYWriter has
no address search, satellite imagery, licensed commercial provider, offline tile distribution,
stored operator viewport, or authoritative Home/Vehicle centering. Browser HTTP cache entries may
help normal browsing but are not promised offline coverage.

The one later owner decision is which production street/satellite provider, commercial terms,
licensing/attribution, credentials, cache policy, and service expectations to approve. The closed
provider-enum/interceptor seam is retained for that separately reviewed task.

## Stop

Open a PR and stop. Do not merge, tag, publish a GitHub Release, operate hardware, or claim bench,
motor, flight, production-provider, or public-OSM-SLA readiness.
