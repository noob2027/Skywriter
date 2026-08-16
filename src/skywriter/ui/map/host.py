"""Isolated Qt WebEngine host for the packaged Leaflet mission map."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QSizePolicy

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
    PointDragged,
    PointSelected,
    RenderAction,
    RenderActionKind,
    RenderModel,
    TileProvider,
    ViewportChanged,
)

_STATIC_ROOT = Path(__file__).resolve().parent / "static"
_MAP_DOCUMENT = _STATIC_ROOT / "map.html"
_OSM_HOST = "tile.openstreetmap.org"


class _MapRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Block every resource outside packaged assets and the selected tile origin."""

    def __init__(self, static_root: Path) -> None:
        super().__init__()
        self._static_root = static_root.resolve()
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
        elif (
            self.tile_provider is TileProvider.OPENSTREETMAP
            and scheme == "https"
            and url.host().lower() == _OSM_HOST
            and url.port() in {-1, 443}
            and not url.hasQuery()
            and not url.hasFragment()
            and not url.userName()
            and not url.password()
            and info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage
        ):
            allowed = _is_osm_tile_path(url.path())
        if not allowed:
            info.block(True)


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
    bridge_message_rejected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("missionMapHost")
        self.setAccessibleName("Interactive mission route map")
        self.setMinimumSize(520, 440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tile_provider = TileProvider.OFFLINE
        self._actions: tuple[MissionAction, ...] = ()
        self._pending_point: GeoPoint | None = None
        self._selected_index: int | None = None
        self._loaded = False

        self._profile = QWebEngineProfile(self)
        self._profile.setHttpUserAgent("SKYWriter/0.1.0 (+https://github.com/noob2027/Skywriter)")
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self._request_interceptor = _MapRequestInterceptor(_STATIC_ROOT)
        self._profile.setUrlRequestInterceptor(self._request_interceptor)

        document_url = QUrl.fromLocalFile(str(_MAP_DOCUMENT))
        self._map_page = _MapPage(self._profile, document_url)
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

    def set_tile_provider(self, provider: TileProvider) -> None:
        """Select one closed provider identifier; arbitrary URLs are never accepted."""

        self._tile_provider = provider
        self._request_interceptor.tile_provider = provider
        self._publish_snapshot()

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
