"""Isolated Qt WebEngine host for the packaged Leaflet mission map."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QSizePolicy

from skywriter.config import DEFAULT_CONFIG
from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    MissionAction,
    ProceedAction,
)
from skywriter.ui.map.bridge import (
    MapBridge,
    MapClicked,
    MapReady,
    PointDragged,
    PointSelected,
    ProviderState,
    ProviderStatusChanged,
    RenderAction,
    RenderActionKind,
    RenderModel,
    TileProvider,
    ViewportChanged,
)

_STATIC_ROOT = Path(__file__).resolve().parent / "static"
_MAP_DOCUMENT = _STATIC_ROOT / "map.html"
_OSM_HOST = "tile.openstreetmap.org"
_MAP_CACHE_BYTES = 128 * 1024 * 1024
_MAP_CACHE_ROOT_ENVIRONMENT = "SKYWRITER_MAP_CACHE_ROOT"
LOGGER = logging.getLogger("skywriter.ui.map")


class _MapRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Block every resource outside packaged assets and the selected tile origin."""

    def __init__(self, static_root: Path, test_tile_origin: QUrl | None = None) -> None:
        super().__init__()
        self._static_root = static_root.resolve()
        self._test_tile_origin = test_tile_origin
        self.tile_provider = TileProvider.OFFLINE

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        url = info.requestUrl()
        scheme = url.scheme().lower()
        allowed = False
        if scheme == "file":
            candidate = Path(url.toLocalFile()).resolve()
            allowed = candidate == self._static_root or self._static_root in candidate.parents
        elif scheme == "qrc":
            allowed = url.path() == "/qtwebchannel/qwebchannel.js"
        elif self.tile_provider is TileProvider.OPENSTREETMAP:
            allowed_origin = (
                scheme == "https" and url.host().lower() == _OSM_HOST and url.port() in {-1, 443}
            ) or self._matches_test_tile_origin(url)
            if (
                allowed_origin
                and not url.hasQuery()
                and not url.hasFragment()
                and not url.userName()
                and not url.password()
                and info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage
            ):
                allowed = _is_osm_tile_path(url.path())
        if not allowed:
            LOGGER.debug(
                "Blocked map resource request",
                extra={"scheme": scheme, "host": url.host(), "path": url.path()},
            )
            info.block(True)
        elif scheme in {"http", "https"}:
            LOGGER.debug(
                "Allowed selected basemap tile request",
                extra={"host": url.host(), "path": url.path()},
            )

    def _matches_test_tile_origin(self, url: QUrl) -> bool:
        origin = self._test_tile_origin
        return bool(
            origin
            and url.scheme().lower() == origin.scheme().lower()
            and url.host().lower() == origin.host().lower()
            and url.port() == origin.port()
        )


class _MapPage(QWebEnginePage):
    """A page that cannot navigate away from the packaged map document."""

    def __init__(self, profile: QWebEngineProfile, allowed_url: QUrl) -> None:
        super().__init__(profile)
        self._allowed_url = allowed_url

    def acceptNavigationRequest(  # noqa: N802
        self,
        url: QUrl | str,
        navigation_type: QWebEnginePage.NavigationType,
        is_main_frame: bool,
    ) -> bool:
        del navigation_type
        requested_url = QUrl(url) if isinstance(url, str) else url
        return is_main_frame and requested_url == self._allowed_url


class MissionMapHost(QWebEngineView):
    """Mount the local Leaflet page and translate its four map-only intents."""

    map_clicked = Signal(object)
    point_selected = Signal(int)
    point_dragged = Signal(int, object)
    viewport_changed = Signal(object)
    provider_status_changed = Signal(object)
    map_ready = Signal(object)
    bridge_message_rejected = Signal(str)

    def __init__(
        self,
        *,
        test_tile_origin: QUrl | None = None,
        test_tile_timeout_ms: int = 1_500,
    ) -> None:
        super().__init__()
        _validate_test_tile_origin(test_tile_origin)
        if (
            isinstance(test_tile_timeout_ms, bool)
            or not isinstance(test_tile_timeout_ms, int)
            or not 100 <= test_tile_timeout_ms <= 60_000
        ):
            raise ValueError("test tile timeout must be 100..60000 milliseconds")
        self.setObjectName("missionMapHost")
        self.setAccessibleName("Interactive mission route map")
        self.setMinimumSize(520, 440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tile_provider = TileProvider.OFFLINE
        self._tile_attempt_id = 0
        self._provider_status = ProviderStatusChanged(
            TileProvider.OFFLINE, 0, ProviderState.OFFLINE, 0, 0, 0, 0
        )
        self._map_ready: MapReady | None = None
        self._actions: tuple[MissionAction, ...] = ()
        self._pending_point: GeoPoint | None = None
        self._selected_index: int | None = None
        self._loaded = False
        self._map_size_timer = QTimer(self)
        self._map_size_timer.setInterval(120)
        self._map_size_timer.setSingleShot(True)
        self._map_size_timer.timeout.connect(self._synchronize_map_size)

        configured_cache_root = os.environ.get(_MAP_CACHE_ROOT_ENVIRONMENT)
        use_isolated_test_profile = (
            configured_cache_root is not None or test_tile_origin is not None
        )
        storage_name = (
            f"skywriter-mission-map-test-{uuid4().hex}"
            if use_isolated_test_profile
            else "skywriter-mission-map"
        )
        self._profile = QWebEngineProfile(storage_name, self)
        self._profile.setHttpUserAgent(
            f"SKYWriter/{DEFAULT_CONFIG.version} (+https://github.com/noob2027/Skywriter)"
        )
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self._profile.setHttpCacheMaximumSize(_MAP_CACHE_BYTES)
        cache_root = (
            Path(configured_cache_root) / storage_name
            if configured_cache_root
            else Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
            )
        )
        if test_tile_origin is not None and configured_cache_root is None:
            cache_root /= storage_name
        cache_root.mkdir(parents=True, exist_ok=True)
        self._profile.setCachePath(str(cache_root / "mission-map-http"))
        self._profile.setPersistentStoragePath(str(cache_root / "mission-map-profile"))
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self._request_interceptor = _MapRequestInterceptor(_STATIC_ROOT, test_tile_origin)
        self._profile.setUrlRequestInterceptor(self._request_interceptor)

        document_url = QUrl.fromLocalFile(str(_MAP_DOCUMENT))
        self._map_page = _MapPage(self._profile, document_url)
        if test_tile_origin is not None:
            template = test_tile_origin.toString().rstrip("/") + "/{z}/{x}/{y}.png"
            test_script = QWebEngineScript()
            test_script.setName("skywriter-controlled-tile-fixture")
            test_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            test_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            test_script.setRunsOnSubFrames(False)
            test_script.setSourceCode(
                f"window.__skywriterControlledTileTemplate = {json.dumps(template)};"
                f"window.__skywriterControlledTileTimeoutMs = {test_tile_timeout_ms};"
            )
            self._map_page.scripts().insert(test_script)
        settings = self._map_page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.NavigateOnDropEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.setPage(self._map_page)

        self._bridge = MapBridge()
        self._bridge.setParent(self)
        self._channel = QWebChannel(self._map_page)
        self._channel.registerObject("mapBridge", self._bridge)
        self._map_page.setWebChannel(self._channel)
        self._bridge.intent_received.connect(self._on_map_intent)
        self._bridge.message_rejected.connect(self.bridge_message_rejected.emit)
        self.loadFinished.connect(self._on_load_finished)
        self.load(document_url)

    @property
    def bridge(self) -> MapBridge:
        return self._bridge

    @property
    def static_root(self) -> Path:
        return _STATIC_ROOT

    @property
    def tile_provider(self) -> TileProvider:
        return self._tile_provider

    @property
    def provider_status(self) -> ProviderStatusChanged:
        return self._provider_status

    @property
    def readiness(self) -> MapReady | None:
        return self._map_ready

    def set_tile_provider(self, provider: TileProvider) -> None:
        """Select one closed provider identifier; arbitrary URLs are never accepted."""

        if provider is self._tile_provider:
            return
        self._tile_provider = provider
        self._tile_attempt_id = 0 if provider is TileProvider.OFFLINE else 1
        self._request_interceptor.tile_provider = provider
        LOGGER.info(
            "Mission basemap provider selected",
            extra={"provider": provider.value, "attempt_id": self._tile_attempt_id},
        )
        self._publish_snapshot()

    def retry_tiles(self) -> None:
        """Deliberately retry the currently selected network provider."""

        if self._tile_provider is not TileProvider.OPENSTREETMAP:
            return
        self._tile_attempt_id += 1
        LOGGER.info(
            "Mission basemap retry requested",
            extra={"provider": self._tile_provider.value, "attempt_id": self._tile_attempt_id},
        )
        self._publish_snapshot()

    def recenter(self, point: GeoPoint, *, zoom: int = 15) -> None:
        """Move the mounted map to one validated operator-selected coordinate."""

        if not self._loaded:
            return
        payload = json.dumps(
            {
                "latitude_deg": point.latitude_deg,
                "longitude_deg": point.longitude_deg,
                "zoom": zoom,
            },
            separators=(",", ":"),
            allow_nan=False,
        )
        self._map_page.runJavaScript(f"window.skywriterMapTest.recenter({payload})")

    def render_mission(
        self,
        actions: tuple[MissionAction, ...],
        pending_point: GeoPoint | None,
        selected_index: int | None,
    ) -> None:
        """Replace the sanitized map snapshot and publish it after the page is ready."""

        self._actions = tuple(actions)
        self._pending_point = pending_point
        self._selected_index = selected_index
        self._publish_snapshot()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Synchronize Leaflet after the WebEngine viewport reaches its settled size."""

        super().resizeEvent(event)
        self._map_size_timer.start()

    def _synchronize_map_size(self) -> None:
        if self._loaded:
            self._map_page.runJavaScript("window.skywriterMapTest?.synchronizeSize()")

    def _on_load_finished(self, succeeded: bool) -> None:
        self._loaded = succeeded
        if succeeded:
            self._publish_snapshot()

    def _publish_snapshot(self) -> None:
        if not self._loaded:
            return
        self._bridge.publish_render_model(
            RenderModel(
                actions=tuple(
                    _render_action(index, action, index == self._selected_index)
                    for index, action in enumerate(self._actions)
                ),
                pending_point=self._pending_point,
                tile_provider=self._tile_provider,
                tile_attempt_id=self._tile_attempt_id,
                drag_threshold_px=max(QApplication.startDragDistance(), 1),
            )
        )

    def _on_map_intent(self, value: object) -> None:
        if isinstance(value, MapClicked):
            self.map_clicked.emit(value.point)
        elif isinstance(value, PointSelected):
            if self._valid_index(value.index):
                self.point_selected.emit(value.index)
            else:
                self.bridge_message_rejected.emit("point_selected index is outside render snapshot")
        elif isinstance(value, PointDragged):
            if self._valid_index(value.index):
                self.point_dragged.emit(value.index, value.point)
            else:
                self.bridge_message_rejected.emit("point_dragged index is outside render snapshot")
        elif isinstance(value, ViewportChanged):
            self.viewport_changed.emit(value)
        elif isinstance(value, ProviderStatusChanged):
            if value.provider is self._tile_provider and value.attempt_id == self._tile_attempt_id:
                self._provider_status = value
                LOGGER.info(
                    "Mission basemap status changed",
                    extra={
                        "provider": value.provider.value,
                        "attempt_id": value.attempt_id,
                        "state": value.state.value,
                        "requested_tiles": value.requested_tiles,
                        "loaded_tiles": value.loaded_tiles,
                        "error_tiles": value.error_tiles,
                        "pending_tiles": value.pending_tiles,
                    },
                )
                self.provider_status_changed.emit(value)
            else:
                LOGGER.debug(
                    "Ignored stale mission basemap status",
                    extra={
                        "provider": value.provider.value,
                        "attempt_id": value.attempt_id,
                        "selected_provider": self._tile_provider.value,
                        "selected_attempt_id": self._tile_attempt_id,
                    },
                )
        elif isinstance(value, MapReady):
            self._map_ready = value
            self.map_ready.emit(value)

    def _valid_index(self, index: int) -> bool:
        return 0 <= index < len(self._actions)


def _render_action(index: int, action: MissionAction, selected: bool) -> RenderAction:
    if isinstance(action, ProceedAction):
        return RenderAction(
            sequence=index + 1,
            kind=RenderActionKind.PROCEED,
            point=action.point,
            altitude_m=action.altitude_m,
            selected=selected,
        )
    if isinstance(action, HoldAction):
        return RenderAction(
            sequence=index + 1,
            kind=RenderActionKind.HOLD,
            point=action.point,
            altitude_m=action.altitude_m,
            hold_time_s=action.hold_time_s,
            selected=selected,
        )
    if isinstance(action, CircleAction):
        return RenderAction(
            sequence=index + 1,
            kind=RenderActionKind.CIRCLE,
            point=action.point,
            altitude_m=action.altitude_m,
            radius_m=action.radius_m,
            selected=selected,
        )
    if isinstance(action, LandAction):
        return RenderAction(
            sequence=index + 1,
            kind=RenderActionKind.LAND,
            point=action.point,
            altitude_m=action.approach_altitude_m,
            selected=selected,
        )
    raise TypeError(f"Unsupported map action: {type(action).__name__}")


def _is_osm_tile_path(path: str) -> bool:
    components = path.strip("/").split("/")
    if len(components) != 3 or not components[2].endswith(".png"):
        return False
    zoom, x_value, y_value = components
    return zoom.isdigit() and x_value.isdigit() and y_value.removesuffix(".png").isdigit()


def _validate_test_tile_origin(origin: QUrl | None) -> None:
    if origin is None:
        return
    if (
        origin.scheme().lower() != "http"
        or origin.host().lower() not in {"127.0.0.1", "localhost"}
        or origin.port() < 1
        or origin.path() not in {"", "/"}
        or origin.hasQuery()
        or origin.hasFragment()
        or origin.userName()
        or origin.password()
    ):
        raise ValueError("test tile origin must be an explicit loopback HTTP origin")
