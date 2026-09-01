(() => {
  "use strict";

  const SCHEMA_VERSION = 2;
  const DEFAULT_CENTER = [0, 0];
  const DEFAULT_ZOOM = 2;
  const OSM_TILE_URL =
    typeof window.__skywriterControlledTileTemplate === "string"
      ? window.__skywriterControlledTileTemplate
      : "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const TILE_TIMEOUT_MS = Number.isInteger(window.__skywriterControlledTileTimeoutMs)
    ? window.__skywriterControlledTileTimeoutMs
    : 8000;
  const EARTH_RADIUS_M = 6371008.8;
  const mapElement = document.getElementById("mission-map");
  const status = document.getElementById("status");
  const providerStatus = document.getElementById("provider-status");
  const map = L.map(mapElement, {
    attributionControl: true,
    boxZoom: false,
    doubleClickZoom: true,
    dragging: true,
    keyboard: true,
    scrollWheelZoom: true,
    zoomControl: true,
  }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);

  let resizeFrame = null;
  let lastSettledMapSize = {
    height: mapElement.clientHeight,
    width: mapElement.clientWidth,
  };
  const resizeObserver = new ResizeObserver(() => {
    if (resizeFrame !== null) {
      window.cancelAnimationFrame(resizeFrame);
    }
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = null;
      map.invalidateSize({ animate: false, debounceMoveend: true, pan: false });
    });
  });
  resizeObserver.observe(mapElement);

  function synchronizeSize() {
    const nextSize = {
      height: mapElement.clientHeight,
      width: mapElement.clientWidth,
    };
    const changed =
      nextSize.height !== lastSettledMapSize.height ||
      nextSize.width !== lastSettledMapSize.width;
    map.invalidateSize({ animate: false, debounceMoveend: false, pan: false });
    lastSettledMapSize = nextSize;
    if (changed && tileLayer) {
      tileLayer.redraw();
    }
    return changed;
  }

  let bridge = null;
  let tileLayer = null;
  let tileTimeout = null;
  let tileStatus = emptyTileStatus();
  let renderModel = emptyRenderModel();
  let actionMarkers = [];
  let renderLayers = [];
  let activeDrag = null;
  let pointerInsideMap = true;
  let suppressViewportIntent = false;
  let viewportRenderSequence = 0;
  let viewportSettledRenderSequence = 0;
  let viewportMoveendSequence = 0;
  let viewportPanRequestSequence = 0;
  let pendingViewportPan = null;
  let lastGeometrySignature = null;
  let mapReadyReported = false;
  const viewportPanStates = new Map();
  const viewportPanCompletions = new Map();

  function emptyRenderModel() {
    return {
      schema_version: SCHEMA_VERSION,
      type: "render_mission",
      actions: [],
      pending_point: null,
      tile_provider: "offline",
      tile_attempt_id: 0,
      drag_threshold_px: 10,
    };
  }

  function emptyTileStatus() {
    return {
      provider: "offline",
      attempt_id: 0,
      state: "offline",
      requested_tiles: 0,
      loaded_tiles: 0,
      error_tiles: 0,
      pending_tiles: 0,
    };
  }

  function sendIntent(type, fields, onResult = null) {
    if (!bridge || typeof bridge.receive_message !== "function") {
      status.textContent = "Map bridge is not connected.";
      return false;
    }
    const payload = JSON.stringify({ schema_version: SCHEMA_VERSION, type, ...fields });
    if (onResult === null) {
      bridge.receive_message(payload);
    } else {
      bridge.receive_message(payload, onResult);
    }
    return true;
  }

  function pointValue(latLng) {
    return { latitude_deg: latLng.lat, longitude_deg: latLng.lng };
  }

  function pointLatLng(point) {
    return L.latLng(point.latitude_deg, point.longitude_deg);
  }

  function addRenderLayer(layer) {
    layer.addTo(map);
    renderLayers.push(layer);
    return layer;
  }

  function clearRenderLayers() {
    renderLayers.forEach((layer) => layer.removeFrom(map));
    renderLayers = [];
    actionMarkers = [];
    activeDrag = null;
  }

  function providerMessage(value) {
    if (value.state === "offline") {
      return "Offline · local planning grid";
    }
    if (value.state === "loading") {
      return `Loading OpenStreetMap · ${value.loaded_tiles}/${value.requested_tiles} tiles`;
    }
    if (value.state === "online") {
      return `Online · ${value.loaded_tiles} OpenStreetMap tiles received`;
    }
    if (value.state === "partial") {
      return `Partial · ${value.loaded_tiles} received, ${value.error_tiles} failed`;
    }
    return `Unavailable · ${value.error_tiles} tile request(s) failed`;
  }

  function publishTileStatus(state) {
    tileStatus.state = state;
    providerStatus.dataset.state = state;
    providerStatus.textContent = providerMessage(tileStatus);
    sendIntent("provider_status_changed", { ...tileStatus });
  }

  function clearTileTimeout() {
    if (tileTimeout !== null) {
      window.clearTimeout(tileTimeout);
      tileTimeout = null;
    }
  }

  function classifyCompletedTileAttempt() {
    clearTileTimeout();
    if (
      tileStatus.loaded_tiles > 0 &&
      (tileStatus.error_tiles > 0 || tileStatus.pending_tiles > 0)
    ) {
      publishTileStatus("partial");
    } else if (tileStatus.loaded_tiles > 0) {
      publishTileStatus("online");
    } else {
      publishTileStatus("unavailable");
    }
  }

  function updateTileProvider(provider, attemptId) {
    if (tileStatus.provider === provider && tileStatus.attempt_id === attemptId) {
      return;
    }
    clearTileTimeout();
    if (tileLayer) {
      const previousTileLayer = tileLayer;
      tileLayer = null;
      previousTileLayer.removeFrom(map);
    }
    tileStatus = {
      provider,
      attempt_id: attemptId,
      state: provider === "offline" ? "offline" : "loading",
      requested_tiles: 0,
      loaded_tiles: 0,
      error_tiles: 0,
      pending_tiles: 0,
    };
    if (provider === "openstreetmap") {
      const attemptStatus = tileStatus;
      const pendingTiles = new Set();
      const attemptLayer = L.tileLayer(OSM_TILE_URL, {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
        maxZoom: 19,
        noWrap: true,
        updateWhenIdle: true,
      });
      tileLayer = attemptLayer;
      const isCurrentAttempt = () =>
        tileStatus === attemptStatus && tileLayer === attemptLayer;
      attemptLayer.on("tileloadstart", (event) => {
        if (!isCurrentAttempt() || pendingTiles.has(event.tile)) {
          return;
        }
        pendingTiles.add(event.tile);
        tileStatus.requested_tiles += 1;
        tileStatus.pending_tiles += 1;
        publishTileStatus("loading");
      });
      attemptLayer.on("tileload", (event) => {
        if (!isCurrentAttempt() || !pendingTiles.delete(event.tile)) {
          return;
        }
        tileStatus.loaded_tiles += 1;
        tileStatus.pending_tiles = Math.max(0, tileStatus.pending_tiles - 1);
        publishTileStatus(tileStatus.error_tiles > 0 ? "partial" : "online");
      });
      attemptLayer.on("tileerror", (event) => {
        if (!isCurrentAttempt() || !pendingTiles.delete(event.tile)) {
          return;
        }
        tileStatus.error_tiles += 1;
        tileStatus.pending_tiles = Math.max(0, tileStatus.pending_tiles - 1);
        if (tileStatus.loaded_tiles > 0) {
          publishTileStatus("partial");
        }
      });
      attemptLayer.on("tileunload", (event) => {
        if (!isCurrentAttempt() || !pendingTiles.delete(event.tile)) {
          return;
        }
        tileStatus.requested_tiles = Math.max(0, tileStatus.requested_tiles - 1);
        tileStatus.pending_tiles = Math.max(0, tileStatus.pending_tiles - 1);
        publishTileStatus("loading");
      });
      attemptLayer.on("load", () => {
        if (isCurrentAttempt()) {
          classifyCompletedTileAttempt();
        }
      });
      publishTileStatus("loading");
      attemptLayer.addTo(map);
      tileTimeout = window.setTimeout(() => {
        if (isCurrentAttempt()) {
          classifyCompletedTileAttempt();
        }
      }, TILE_TIMEOUT_MS);
    } else {
      publishTileStatus("offline");
    }
    mapElement.dataset.tileProvider = provider;
  }

  function markerIcon(action) {
    const land = action.kind === "land";
    const selected = action.selected ? " is-selected" : "";
    const kindClass = land ? " is-land" : "";
    const body = land ? "" : String(action.sequence);
    const sequence = land
      ? `<span class="mission-marker__sequence">${action.sequence}</span>`
      : "";
    return L.divIcon({
      className: "mission-marker-shell",
      html: `<span class="mission-marker${kindClass}${selected}">${body}${sequence}</span>`,
      iconAnchor: [18, 18],
      iconSize: [36, 36],
    });
  }

  function pendingIcon(sequence) {
    return L.divIcon({
      className: "mission-marker-shell",
      html: `<span class="mission-marker is-pending">${sequence}</span>`,
      iconAnchor: [18, 18],
      iconSize: [36, 36],
    });
  }

  function labelFor(action) {
    let label = `${action.altitude_m} m Above Home`;
    if (action.kind === "hold") {
      label += ` · Hold ${action.hold_time_s} s`;
    } else if (action.kind === "circle") {
      label += ` · Circle r ${action.radius_m} m · CW`;
    } else if (action.kind === "land") {
      label = `Approach ${action.altitude_m} m Above Home · Land`;
    }
    return label;
  }

  function tooltipNode(text) {
    const element = document.createElement("span");
    element.textContent = text;
    return element;
  }

  function eastPoint(point, distanceM) {
    const latitudeRad = (point.latitude_deg * Math.PI) / 180;
    const longitudeDelta =
      (distanceM / (EARTH_RADIUS_M * Math.max(Math.cos(latitudeRad), 1e-12))) *
      (180 / Math.PI);
    return L.latLng(point.latitude_deg, point.longitude_deg + longitudeDelta);
  }

  function circleArcPoints(point, radiusM) {
    const points = [];
    for (let degrees = 35; degrees >= -230; degrees -= 18) {
      const angle = (degrees * Math.PI) / 180;
      const northM = radiusM * Math.sin(angle);
      const eastM = radiusM * Math.cos(angle);
      const latitudeDelta = (northM / EARTH_RADIUS_M) * (180 / Math.PI);
      const latitudeRad = (point.latitude_deg * Math.PI) / 180;
      const longitudeDelta =
        (eastM / (EARTH_RADIUS_M * Math.max(Math.cos(latitudeRad), 1e-12))) *
        (180 / Math.PI);
      points.push(
        L.latLng(
          point.latitude_deg + latitudeDelta,
          point.longitude_deg + longitudeDelta,
        ),
      );
    }
    return points;
  }

  function renderCircle(action, fitPoints) {
    const center = pointLatLng(action.point);
    const edge = eastPoint(action.point, action.radius_m);
    const perimeter = addRenderLayer(
      L.circle(center, {
        className: "circle-perimeter",
        color: "#b76b1c",
        fillColor: "#e18a35",
        fillOpacity: 0.09,
        interactive: false,
        radius: action.radius_m,
        weight: action.selected ? 3 : 2,
      }),
    );
    perimeter.getElement()?.setAttribute("data-radius-m", String(action.radius_m));
    addRenderLayer(
      L.polyline([center, edge], {
        className: "circle-radius-line",
        color: "#9c5a14",
        dashArray: "6 5",
        interactive: false,
        weight: 2,
      }),
    );
    addRenderLayer(
      L.marker(edge, {
        interactive: false,
        icon: L.divIcon({
          className: "circle-radius-label",
          html: `<span>${action.radius_m} m</span>`,
          iconAnchor: [0, 12],
        }),
      }),
    );
    const arc = circleArcPoints(action.point, action.radius_m * 0.78);
    addRenderLayer(
      L.polyline(arc, {
        className: "circle-direction-arc",
        color: "#9c5a14",
        interactive: false,
        weight: 2,
      }),
    );
    addRenderLayer(
      L.marker(arc[arc.length - 1], {
        interactive: false,
        icon: L.divIcon({
          className: "circle-direction-label",
          html: "<span>↻ CW</span>",
          iconAnchor: [16, 12],
        }),
      }),
    );
    fitPoints.push(edge);
  }

  function cancelActiveDrag() {
    if (!activeDrag) {
      return;
    }
    activeDrag.marker.setLatLng(activeDrag.original);
    activeDrag = null;
  }

  function renderAction(action, index, fitPoints) {
    if (action.kind === "circle") {
      renderCircle(action, fitPoints);
    }
    const original = pointLatLng(action.point);
    const marker = addRenderLayer(
      L.marker(original, {
        autoPan: false,
        draggable: true,
        icon: markerIcon(action),
        keyboard: true,
        riseOnHover: true,
        title: `Point ${action.sequence}: ${labelFor(action)}`,
      }),
    );
    marker.bindTooltip(tooltipNode(labelFor(action)), {
      className: "mission-label",
      direction: "right",
      offset: [18, -12],
      opacity: 1,
      permanent: true,
    });
    marker.on("click", (event) => {
      L.DomEvent.stopPropagation(event);
      sendIntent("point_selected", { index });
    });
    marker.on("dragstart", () => {
      activeDrag = { index, marker, original: marker.getLatLng() };
    });
    marker.on("dragend", () => {
      const completed = activeDrag;
      activeDrag = null;
      if (!completed || !pointerInsideMap) {
        marker.setLatLng(completed ? completed.original : original);
        return;
      }
      sendIntent("point_dragged", { index, point: pointValue(marker.getLatLng()) });
    });
    if (marker.dragging && marker.dragging._draggable) {
      marker.dragging._draggable.options.clickTolerance = renderModel.drag_threshold_px;
    }
    marker.getElement()?.setAttribute("data-action-index", String(index));
    actionMarkers.push(marker);
    fitPoints.push(original);
  }

  function fitRenderedGeometry(fitPoints, geometryChanged) {
    const renderSequence = ++viewportRenderSequence;
    if (!geometryChanged || fitPoints.length === 0) {
      suppressViewportIntent = false;
      viewportSettledRenderSequence = renderSequence;
      return;
    }
    suppressViewportIntent = true;
    if (fitPoints.length === 1) {
      map.setView(fitPoints[0], 16, { animate: false });
    } else {
      map.fitBounds(L.latLngBounds(fitPoints).pad(0.28), {
        animate: false,
        maxZoom: 17,
        padding: [72, 72],
      });
    }
    window.setTimeout(() => {
      if (renderSequence !== viewportRenderSequence) {
        return;
      }
      suppressViewportIntent = false;
      viewportSettledRenderSequence = renderSequence;
    }, 0);
  }

  function requestViewportPan(x, y) {
    const requestId = ++viewportPanRequestSequence;
    const requestState = {
      request_id: requestId,
      phase: "waiting_for_settled_render",
      render_sequence: null,
      moveend_sequence_before: null,
      moveend_sequence: null,
    };
    viewportPanStates.set(requestId, requestState);
    const panWhenRenderSettled = () => {
      const rectangle = mapElement.getBoundingClientRect();
      if (!bridge) {
        requestState.phase = "waiting_for_bridge";
        window.setTimeout(panWhenRenderSettled, 0);
        return;
      }
      if (
        suppressViewportIntent ||
        viewportSettledRenderSequence !== viewportRenderSequence ||
        rectangle.width <= 0 ||
        rectangle.height <= 0
      ) {
        requestState.phase = "waiting_for_settled_render";
        window.setTimeout(panWhenRenderSettled, 0);
        return;
      }
      if (pendingViewportPan !== null) {
        requestState.phase = "waiting_for_prior_pan";
        window.setTimeout(panWhenRenderSettled, 0);
        return;
      }
      requestState.phase = "pan_requested";
      requestState.render_sequence = viewportRenderSequence;
      requestState.moveend_sequence_before = viewportMoveendSequence;
      pendingViewportPan = {
        requestId,
        before: map.getCenter(),
        renderSequence: viewportRenderSequence,
        moveendSequenceBefore: viewportMoveendSequence,
      };
      map.panBy([x, y], { animate: false });
    };
    panWhenRenderSettled();
    return requestId;
  }

  function draw() {
    clearRenderLayers();
    updateTileProvider(renderModel.tile_provider, renderModel.tile_attempt_id);
    const fitPoints = [];
    const route = renderModel.actions.map((action) => pointLatLng(action.point));
    if (route.length > 1) {
      addRenderLayer(
        L.polyline(route, {
          className: "mission-route",
          color: "#176b68",
          interactive: false,
          lineCap: "round",
          weight: 4,
        }),
      );
    }
    renderModel.actions.forEach((action, index) => renderAction(action, index, fitPoints));
    if (renderModel.pending_point) {
      const pending = pointLatLng(renderModel.pending_point);
      if (route.length > 0) {
        addRenderLayer(
          L.polyline([route[route.length - 1], pending], {
            color: "#d97720",
            dashArray: "7 7",
            interactive: false,
            weight: 2,
          }),
        );
      }
      const pendingMarker = addRenderLayer(
        L.marker(pending, {
          icon: pendingIcon(renderModel.actions.length + 1),
          interactive: false,
          keyboard: false,
        }),
      );
      pendingMarker.bindTooltip(tooltipNode("Pending · choose an action"), {
        className: "pending-label",
        direction: "right",
        offset: [18, 16],
        opacity: 1,
        permanent: true,
      });
      fitPoints.push(pending);
    }
    const geometrySignature = JSON.stringify({
      actions: renderModel.actions.map((action) => ({
        point: action.point,
        radius_m: action.radius_m ?? null,
      })),
      pending_point: renderModel.pending_point,
    });
    const geometryChanged = geometrySignature !== lastGeometrySignature;
    lastGeometrySignature = geometrySignature;
    fitRenderedGeometry(fitPoints, geometryChanged);
    mapElement.dataset.rendered = "true";
    mapElement.dataset.actionCount = String(renderModel.actions.length);
    mapElement.dataset.pending = String(renderModel.pending_point !== null);
    status.textContent = renderModel.pending_point
      ? "Pending point selected; choose an action in the editor."
      : "Click the map to place a pending mission point.";
    reportMapReady();
  }

  function reportMapReady() {
    if (mapReadyReported || !bridge || mapElement.dataset.rendered !== "true") {
      return;
    }
    const rectangle = mapElement.getBoundingClientRect();
    if (rectangle.width <= 0 || rectangle.height <= 0) {
      window.setTimeout(reportMapReady, 20);
      return;
    }
    mapReadyReported = sendIntent("map_ready", {
      leaflet_version: L.version,
      container_width_px: rectangle.width,
      container_height_px: rectangle.height,
    });
  }

  function exactKeys(value, expected) {
    const actual = Object.keys(value).sort();
    return actual.length === expected.length && actual.every((key, i) => key === expected[i]);
  }

  function validPoint(value) {
    return (
      value !== null &&
      typeof value === "object" &&
      exactKeys(value, ["latitude_deg", "longitude_deg"]) &&
      Number.isFinite(value.latitude_deg) &&
      Number.isFinite(value.longitude_deg) &&
      value.latitude_deg >= -90 &&
      value.latitude_deg <= 90 &&
      value.longitude_deg >= -180 &&
      value.longitude_deg <= 180
    );
  }

  function validAction(action, index) {
    if (!action || typeof action !== "object") {
      return false;
    }
    const common = ["altitude_m", "kind", "point", "selected", "sequence"];
    const kindFields = {
      proceed: common,
      hold: [...common, "hold_time_s"],
      circle: [...common, "direction", "radius_m", "turns"],
      land: common,
    };
    const expected = kindFields[action.kind];
    if (!expected || !exactKeys(action, expected.sort())) {
      return false;
    }
    if (
      action.sequence !== index + 1 ||
      !validPoint(action.point) ||
      !Number.isFinite(action.altitude_m) ||
      typeof action.selected !== "boolean"
    ) {
      return false;
    }
    if (action.kind === "hold") {
      return Number.isFinite(action.hold_time_s) && action.hold_time_s > 0;
    }
    if (action.kind === "circle") {
      return (
        Number.isFinite(action.radius_m) &&
        action.radius_m > 0 &&
        action.turns === 1 &&
        action.direction === "clockwise"
      );
    }
    return true;
  }

  function validRender(value) {
    const keys = [
      "actions",
      "drag_threshold_px",
      "pending_point",
      "schema_version",
      "tile_attempt_id",
      "tile_provider",
      "type",
    ];
    return (
      value &&
      typeof value === "object" &&
      exactKeys(value, keys) &&
      value.schema_version === SCHEMA_VERSION &&
      value.type === "render_mission" &&
      Array.isArray(value.actions) &&
      value.actions.every(validAction) &&
      (value.pending_point === null || validPoint(value.pending_point)) &&
      ["offline", "openstreetmap"].includes(value.tile_provider) &&
      Number.isInteger(value.tile_attempt_id) &&
      value.tile_attempt_id >= 0 &&
      ((value.tile_provider === "offline" && value.tile_attempt_id === 0) ||
        (value.tile_provider === "openstreetmap" && value.tile_attempt_id >= 1)) &&
      Number.isInteger(value.drag_threshold_px) &&
      value.drag_threshold_px > 0
    );
  }

  function acceptRender(payload) {
    let parsed;
    try {
      parsed = JSON.parse(payload);
    } catch (_error) {
      status.textContent = "Map render rejected: invalid JSON.";
      return;
    }
    if (!validRender(parsed)) {
      status.textContent = "Map render rejected: schema mismatch.";
      return;
    }
    renderModel = parsed;
    draw();
  }

  function attach(candidate) {
    if (!candidate || typeof candidate.receive_message !== "function") {
      status.textContent = "Map bridge is unavailable.";
      return;
    }
    bridge = candidate;
    bridge.render_message.connect(acceptRender);
    if (typeof bridge.current_render_message === "string") {
      acceptRender(bridge.current_render_message);
    }
    reportMapReady();
  }

  map.on("click", (event) => {
    sendIntent("map_clicked", { point: pointValue(event.latlng) });
  });
  map.on("moveend", () => {
    viewportMoveendSequence += 1;
    if (suppressViewportIntent) {
      return;
    }
    const bounds = map.getBounds();
    const fields = {
      south_west: pointValue(bounds.getSouthWest()),
      north_east: pointValue(bounds.getNorthEast()),
    };
    if (pendingViewportPan === null) {
      sendIntent("viewport_changed", fields);
      return;
    }
    const completedPan = pendingViewportPan;
    const completedCenter = map.getCenter();
    const completedMoveendSequence = viewportMoveendSequence;
    pendingViewportPan = null;
    const requestState = viewportPanStates.get(completedPan.requestId);
    if (requestState) {
      requestState.phase = "moveend_seen_waiting_for_bridge_callback";
      requestState.moveend_sequence = completedMoveendSequence;
    }
    const dispatched = sendIntent("viewport_changed", fields, (bridgeResult) => {
      viewportPanCompletions.set(completedPan.requestId, {
        request_id: completedPan.requestId,
        bridge_result: bridgeResult,
        before: pointValue(completedPan.before),
        after: pointValue(completedCenter),
        render_sequence: completedPan.renderSequence,
        moveend_sequence_before: completedPan.moveendSequenceBefore,
        moveend_sequence: completedMoveendSequence,
      });
      if (requestState) {
        requestState.phase =
          bridgeResult === "accepted"
            ? "bridge_accepted"
            : bridgeResult === "rejected"
              ? "bridge_rejected"
              : "bridge_return_invalid";
      }
    });
    if (!dispatched && requestState) {
      requestState.phase = "bridge_unavailable_after_moveend";
    }
  });
  mapElement.addEventListener("pointerenter", () => {
    pointerInsideMap = true;
  });
  mapElement.addEventListener("pointerleave", () => {
    pointerInsideMap = false;
  });
  window.addEventListener("blur", cancelActiveDrag);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelActiveDrag();
    }
  });

  if (typeof QWebChannel === "function" && window.qt && window.qt.webChannelTransport) {
    new QWebChannel(window.qt.webChannelTransport, (channel) =>
      attach(channel.objects.mapBridge),
    );
  } else {
    status.textContent = "Map bridge is unavailable.";
  }

  window.skywriterMapTest = Object.freeze({
    acceptRender,
    bridgeConnected: () => bridge !== null,
    actionState: () =>
      renderModel.actions.map((action, index) => ({
        index,
        kind: action.kind,
        radius_m: action.radius_m ?? null,
        selected: action.selected,
      })),
    markerCenter: (index) => {
      const element = actionMarkers[index]?.getElement();
      if (!element) {
        return null;
      }
      const rectangle = element.getBoundingClientRect();
      return {
        x: rectangle.left + rectangle.width / 2,
        y: rectangle.top + rectangle.height / 2,
      };
    },
    mapCenter: () => {
      const rectangle = mapElement.getBoundingClientRect();
      return {
        x: rectangle.left + rectangle.width / 2,
        y: rectangle.top + rectangle.height / 2,
      };
    },
    geographicViewport: () => ({
      center: pointValue(map.getCenter()),
      zoom: map.getZoom(),
    }),
    recenter: (value) => {
      if (
        !value ||
        typeof value !== "object" ||
        !validPoint({
          latitude_deg: value.latitude_deg,
          longitude_deg: value.longitude_deg,
        }) ||
        !Number.isInteger(value.zoom) ||
        value.zoom < DEFAULT_ZOOM ||
        value.zoom > 19
      ) {
        return false;
      }
      map.setView(pointLatLng(value), value.zoom, { animate: false });
      return true;
    },
    synchronizeSize,
    providerStatus: () => ({ ...tileStatus }),
    requestViewportPan,
    viewportPanStatus: (requestId) => viewportPanStates.get(requestId) ?? null,
    viewportPanCompletion: (requestId) =>
      viewportPanCompletions.get(requestId) ?? null,
    snapshot: () => {
      const rectangle = mapElement.getBoundingClientRect();
      return {
        action_count: renderModel.actions.length,
        bridge_connected: bridge !== null,
        container_height: rectangle.height,
        container_width: rectangle.width,
        leaflet_height: map.getSize().y,
        leaflet_width: map.getSize().x,
        pending: renderModel.pending_point !== null,
        pending_viewport_pan: pendingViewportPan !== null,
        provider: renderModel.tile_provider,
        provider_state: tileStatus.state,
        tile_attempt_id: renderModel.tile_attempt_id,
        tile_errors: tileStatus.error_tiles,
        tiles_loaded: tileStatus.loaded_tiles,
        render_sequence: viewportRenderSequence,
        rendered: mapElement.dataset.rendered === "true",
        settled_render_sequence: viewportSettledRenderSequence,
        viewport_intent_suppressed: suppressViewportIntent,
      };
    },
  });
  mapElement.dataset.leafletVersion = L.version;
  mapElement.dataset.ready = "true";
})();
