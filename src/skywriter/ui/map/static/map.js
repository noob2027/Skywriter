(() => {
  "use strict";

  const SCHEMA_VERSION = 1;
  const DEFAULT_CENTER = [38.8895, -77.0353];
  const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
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
  }).setView(DEFAULT_CENTER, 13);

  let bridge = null;
  let tileLayer = null;
  let renderModel = emptyRenderModel();
  let actionMarkers = [];
  let renderLayers = [];
  let activeDrag = null;
  let pointerInsideMap = true;
  let suppressViewportIntent = false;

  function emptyRenderModel() {
    return {
      schema_version: SCHEMA_VERSION,
      type: "render_mission",
      actions: [],
      pending_point: null,
      tile_provider: "offline",
      drag_threshold_px: 10,
    };
  }

  function sendIntent(type, fields) {
    if (!bridge || typeof bridge.receive_message !== "function") {
      status.textContent = "Map bridge is not connected.";
      return;
    }
    bridge.receive_message(
      JSON.stringify({ schema_version: SCHEMA_VERSION, type, ...fields }),
    );
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

  function updateTileProvider(provider) {
    if (tileLayer) {
      tileLayer.removeFrom(map);
      tileLayer = null;
    }
    if (provider === "openstreetmap") {
      tileLayer = L.tileLayer(OSM_TILE_URL, {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
        maxZoom: 19,
        noWrap: true,
        updateWhenIdle: true,
      });
      tileLayer.on("tileerror", () => {
        status.textContent = "Basemap unavailable; mission editing remains available.";
      });
      tileLayer.addTo(map);
      providerStatus.textContent = "OpenStreetMap Standard · network";
    } else {
      providerStatus.textContent = "No basemap · offline";
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

  function fitRenderedGeometry(fitPoints) {
    suppressViewportIntent = true;
    if (fitPoints.length === 0) {
      map.setView(DEFAULT_CENTER, 13, { animate: false });
    } else if (fitPoints.length === 1) {
      map.setView(fitPoints[0], 16, { animate: false });
    } else {
      map.fitBounds(L.latLngBounds(fitPoints).pad(0.28), {
        animate: false,
        maxZoom: 17,
        padding: [72, 72],
      });
    }
    window.setTimeout(() => {
      suppressViewportIntent = false;
    }, 0);
  }

  function draw() {
    clearRenderLayers();
    updateTileProvider(renderModel.tile_provider);
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
    fitRenderedGeometry(fitPoints);
    mapElement.dataset.rendered = "true";
    mapElement.dataset.actionCount = String(renderModel.actions.length);
    mapElement.dataset.pending = String(renderModel.pending_point !== null);
    status.textContent = renderModel.pending_point
      ? "Pending point selected; choose an action in the editor."
      : "Click the map to place a pending mission point.";
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
    status.textContent = "Local map bridge connected.";
  }

  map.on("click", (event) => {
    sendIntent("map_clicked", { point: pointValue(event.latlng) });
  });
  map.on("moveend", () => {
    if (suppressViewportIntent) {
      return;
    }
    const bounds = map.getBounds();
    sendIntent("viewport_changed", {
      south_west: pointValue(bounds.getSouthWest()),
      north_east: pointValue(bounds.getNorthEast()),
    });
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
    viewportIntentReady: () => !suppressViewportIntent,
    panBy: (x, y) => map.panBy([x, y], { animate: false }),
    snapshot: () => ({
      action_count: renderModel.actions.length,
      pending: renderModel.pending_point !== null,
      provider: renderModel.tile_provider,
      rendered: mapElement.dataset.rendered === "true",
    }),
  });
  mapElement.dataset.leafletVersion = L.version;
  mapElement.dataset.ready = "true";
})();
