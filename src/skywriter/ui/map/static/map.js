(() => {
  "use strict";

  const SCHEMA_VERSION = 1;
  const canvas = document.getElementById("mission-map");
  const status = document.getElementById("status");
  const context = canvas.getContext("2d");
  let bridge = null;
  let renderModel = { actions: [], pending_point: null };

  function sendIntent(type, fields) {
    if (!bridge || typeof bridge.receive_message !== "function") {
      status.textContent = "Map bridge is not connected.";
      return;
    }
    bridge.receive_message(JSON.stringify({ schema_version: SCHEMA_VERSION, type, ...fields }));
  }

  function pointFromEvent(event) {
    const bounds = canvas.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    return {
      latitude_deg: 38.91 - y * 0.04,
      longitude_deg: -77.06 + x * 0.05,
    };
  }

  function draw() {
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = "rgba(84,118,113,0.2)";
    for (let step = 1; step < 8; step += 1) {
      const x = (canvas.width * step) / 8;
      const y = (canvas.height * step) / 8;
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, canvas.height);
      context.moveTo(0, y);
      context.lineTo(canvas.width, y);
      context.stroke();
    }
    renderModel.actions.forEach((action, index) => {
      const x = 80 + index * 120;
      const y = canvas.height / 2 + (index % 2) * 80;
      if (action.kind === "circle") {
        context.beginPath();
        context.setLineDash([8, 6]);
        context.strokeStyle = "#b76b1c";
        context.arc(x, y, 42, 0, Math.PI * 2);
        context.stroke();
        context.setLineDash([]);
      }
      context.beginPath();
      context.fillStyle = action.kind === "land" ? "#f4d5dc" : "#ffffff";
      context.strokeStyle = action.selected ? "#f4a340" : "#0c4543";
      context.lineWidth = action.selected ? 5 : 2;
      context.arc(x, y, 14, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      context.fillStyle = "#183b3a";
      context.fillText(String(action.sequence), x - 3, y + 4);
    });
  }

  canvas.addEventListener("click", (event) => {
    sendIntent("map_clicked", { point: pointFromEvent(event) });
  });

  function attach(candidate) {
    bridge = candidate;
    if (bridge && bridge.render_message) {
      bridge.render_message.connect((payload) => {
        const parsed = JSON.parse(payload);
        if (parsed.schema_version === SCHEMA_VERSION && parsed.type === "render_mission") {
          renderModel = parsed;
          draw();
        }
      });
    }
  }

  if (typeof QWebChannel === "function" && window.qt && window.qt.webChannelTransport) {
    new QWebChannel(window.qt.webChannelTransport, (channel) => attach(channel.objects.mapBridge));
  }
  window.skywriterMapBridge = { attach, draw };
  draw();
})();
