from __future__ import annotations

import threading
import time
from collections.abc import Mapping

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton

from skywriter.application.connected import (
    CancellationView,
    ConnectedFailureCode,
    ConnectedTarget,
    ConnectedVerificationState,
    MissionReadback,
    MissionTransferEvidence,
)
from skywriter.application.mission_service import OfflineMissionSnapshot
from skywriter.application.telemetry import (
    HeartbeatTelemetry,
    HomeTelemetry,
    TelemetryLinkKind,
    TelemetryPoint,
    TelemetrySnapshot,
    TimedSignal,
)
from skywriter.compatibility.arducopter_4_6_3 import (
    NativeMissionPackage,
    VehicleIdentity,
    canonicalize_expected,
)
from skywriter.domain.compiled import (
    CompiledMission,
    CompiledMissionItem,
    MissionCommand,
    MissionFrame,
    MissionType,
)
from skywriter.infrastructure.mavlink.connection import (
    IncomingMessage,
    MavlinkAddress,
    MissionLink,
    TransportDescriptor,
    TransportKind,
    TransportOpenError,
    TransportOpenFailureCode,
)
from skywriter.infrastructure.serial_ports import SerialPortInfo
from skywriter.main import create_application
from skywriter.ui.connected import ConnectedMissionWidget
from skywriter.ui.connected_controller import ConnectedMissionController


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def now(self) -> float:
        return self.value


class RecordingEnumerator:
    def __init__(self) -> None:
        self.calls = 0
        self.thread_ids: list[int] = []

    def enumerate(self) -> tuple[SerialPortInfo, ...]:
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        return (SerialPortInfo("COM7", "USB Serial Device", "Test hardware"),)


class FakeLink:
    local_address = MavlinkAddress(255, 190)

    def __init__(self, descriptor: TransportDescriptor) -> None:
        self.descriptor = descriptor
        self.connected = True
        self.close_count = 0

    def is_connected(self) -> bool:
        return self.connected

    def close(self) -> None:
        if self.connected:
            self.connected = False
            self.close_count += 1

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        del timeout_s
        raise AssertionError("fake connected port owns discovery")

    def send_mission_count(self, target: MavlinkAddress, *, count: int, mission_type: int) -> None:
        del target, count, mission_type
        raise AssertionError("fake connected port owns mission transfer")

    def send_mission_item_int(
        self, target: MavlinkAddress, *, item: Mapping[str, int | float]
    ) -> None:
        del target, item
        raise AssertionError("fake connected port owns mission transfer")

    def send_mission_request_list(self, target: MavlinkAddress, *, mission_type: int) -> None:
        del target, mission_type
        raise AssertionError("fake connected port owns mission transfer")

    def send_mission_request_int(
        self,
        target: MavlinkAddress,
        *,
        sequence: int,
        mission_type: int,
    ) -> None:
        del target, sequence, mission_type
        raise AssertionError("fake connected port owns mission transfer")

    def send_mission_ack(self, target: MavlinkAddress, *, result: int, mission_type: int) -> None:
        del target, result, mission_type
        raise AssertionError("fake connected port owns mission transfer")


class FakeConnectedPort:
    def __init__(
        self,
        link: FakeLink,
        clock: FakeClock,
        store: dict[str, NativeMissionPackage],
        *,
        no_heartbeat: bool,
        block_discovery: bool,
    ) -> None:
        self._link = link
        self._clock = clock
        self._store = store
        self._no_heartbeat = no_heartbeat
        self._block_discovery = block_discovery
        self.operation_thread_ids: list[int] = []

    @property
    def link_kind(self) -> TelemetryLinkKind:
        return (
            TelemetryLinkKind.USB
            if self._link.descriptor.kind is TransportKind.USB
            else TelemetryLinkKind.SIK
        )

    def is_connected(self) -> bool:
        return self._link.is_connected()

    def discover(
        self, *, duration_s: float, cancellation: CancellationView
    ) -> tuple[ConnectedTarget, ...]:
        del duration_s
        self.operation_thread_ids.append(threading.get_ident())
        if self._block_discovery:
            deadline = time.monotonic() + 2.0
            while not cancellation.is_cancelled() and time.monotonic() < deadline:
                time.sleep(0.005)
        if cancellation.is_cancelled() or self._no_heartbeat:
            return ()
        return (self._target(),)

    def download_mission(
        self, target: ConnectedTarget, *, cancellation: CancellationView
    ) -> MissionReadback:
        del target
        self.operation_thread_ids.append(threading.get_ident())
        assert not cancellation.is_cancelled()
        package = self._store.get("package")
        return (
            MissionReadback(())
            if package is None
            else MissionReadback(canonicalize_expected(package))
        )

    def upload_and_verify(
        self,
        package: NativeMissionPackage,
        target: ConnectedTarget,
        *,
        approved: bool,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> MissionTransferEvidence:
        del target, target_valid_for_s
        self.operation_thread_ids.append(threading.get_ident())
        assert approved and not cancellation.is_cancelled()
        self._store["package"] = package
        return MissionTransferEvidence(len(package.items), 7, True, *(["a" * 64] * 3))

    def collect_telemetry(
        self,
        target: ConnectedTarget,
        *,
        duration_s: float,
        cancellation: CancellationView,
        require_home: bool,
    ) -> TelemetrySnapshot:
        del duration_s, require_home
        self.operation_thread_ids.append(threading.get_ident())
        assert not cancellation.is_cancelled()
        observed_at_s = self._clock.now()
        return TelemetrySnapshot(
            target.vehicle.value,
            target.system_id,
            target.component_id,
            target.link_kind,
            True,
            TimedSignal(
                HeartbeatTelemetry(False, 0, "Stabilize", 3, 2, 3),
                observed_at_s,
                3.0,
            ),
            TimedSignal.unavailable(5.0),
            TimedSignal.unavailable(5.0),
            TimedSignal(
                HomeTelemetry(TelemetryPoint(-35.363261, 149.165230), 584.0),
                observed_at_s,
                60.0,
            ),
            TimedSignal.unavailable(5.0),
            TimedSignal.unavailable(5.0),
            TimedSignal.unavailable(5.0),
            TimedSignal.unavailable(5.0),
            TimedSignal.unavailable(5.0),
        )

    def _target(self) -> ConnectedTarget:
        return ConnectedTarget(
            VehicleIdentity("mavlink-system-1-component-1"),
            1,
            1,
            self.link_kind,
            2,
            3,
            0,
            self._clock.now(),
        )


class ControllerHarness:
    def __init__(
        self,
        clock: FakeClock,
        *,
        no_heartbeat: bool = False,
        block_discovery: bool = False,
        open_error: TransportOpenError | None = None,
    ) -> None:
        self.clock = clock
        self.no_heartbeat = no_heartbeat
        self.block_discovery = block_discovery
        self.open_error = open_error
        self.descriptors: list[TransportDescriptor] = []
        self.open_thread_ids: list[int] = []
        self.links: list[FakeLink] = []
        self.ports: list[FakeConnectedPort] = []
        self.store: dict[str, NativeMissionPackage] = {}

    def open_link(self, descriptor: TransportDescriptor) -> FakeLink:
        self.open_thread_ids.append(threading.get_ident())
        self.descriptors.append(descriptor)
        if self.open_error is not None:
            raise self.open_error
        link = FakeLink(descriptor)
        self.links.append(link)
        return link

    def make_port(self, link: MissionLink, clock: object) -> FakeConnectedPort:
        assert isinstance(link, FakeLink)
        assert clock is self.clock
        port = FakeConnectedPort(
            link,
            self.clock,
            self.store,
            no_heartbeat=self.no_heartbeat,
            block_discovery=self.block_discovery,
        )
        self.ports.append(port)
        return port


def compiled() -> CompiledMission:
    return CompiledMission(
        (
            CompiledMissionItem(
                0,
                MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                MissionCommand.NAV_TAKEOFF,
                True,
                True,
                0,
                0,
                0,
                0,
                -353632610,
                1491652300,
                10,
                MissionType.MISSION,
            ),
            CompiledMissionItem(
                1,
                MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                MissionCommand.NAV_LAND,
                False,
                True,
                0,
                0,
                0,
                0,
                -353632600,
                1491652310,
                0,
                MissionType.MISSION,
            ),
        )
    )


def wait_until(predicate: object, *, timeout_s: float = 5.0) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        create_application().processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError("condition was not satisfied before the test deadline")


def prepare_serial_selection(widget: ConnectedMissionWidget) -> None:
    refresh = widget.findChild(QPushButton, "refreshSerialPortsButton")
    ports = widget.findChild(QComboBox, "serialPortSelection")
    assert refresh is not None and ports is not None
    refresh.click()
    wait_until(lambda: ports.count() == 2)
    assert ports.currentData() is None
    ports.setCurrentIndex(1)


def verification_state(controller: ConnectedMissionController) -> ConnectedVerificationState:
    return controller.service.snapshot.verification_state


def test_production_controller_composes_full_usb_to_sik_mission_flow_off_thread() -> None:
    create_application(["skywriter-connected-controller-flow"])
    widget = ConnectedMissionWidget()
    widget.show()
    clock = FakeClock()
    enumerator = RecordingEnumerator()
    harness = ControllerHarness(clock)
    controller = ConnectedMissionController(
        widget,
        serial_ports=enumerator,
        link_factory=harness.open_link,
        port_factory=harness.make_port,
        clock=clock,
    )
    controller.sync_mission(
        OfflineMissionSnapshot(
            revision=1,
            compiled_preview=compiled(),
            compiled_revision=1,
        )
    )
    ui_thread = threading.get_ident()
    assert enumerator.calls == 0
    assert harness.descriptors == []

    prepare_serial_selection(widget)
    assert enumerator.calls == 1
    assert enumerator.thread_ids[0] != ui_thread
    discover = widget.findChild(QPushButton, "discoverSelectedLinkButton")
    assert discover is not None and discover.isEnabled()
    discover.click()
    wait_until(lambda: bool(controller.service.snapshot.candidates) and not controller.busy)
    assert controller.service.snapshot.selected_target is None
    assert harness.descriptors[0] == TransportDescriptor("COM7", TransportKind.USB, 115200)
    assert harness.open_thread_ids[0] != ui_thread

    targets = widget.findChild(QComboBox, "connectedTargetSelection")
    assert targets is not None and targets.count() == 2
    targets.activated.emit(1)
    wait_until(
        lambda: controller.service.snapshot.selected_target is not None and not controller.busy
    )
    inspect = widget.findChild(QPushButton, "inspectOnboardMissionButton")
    assert inspect is not None and inspect.isEnabled()
    inspect.click()
    wait_until(lambda: controller.service.snapshot.onboard is not None and not controller.busy)
    replacement = widget.findChild(QCheckBox, "confirmMissionReplacement")
    upload = widget.findChild(QPushButton, "uploadAndVerifyButton")
    assert replacement is not None and upload is not None
    replacement.setChecked(True)
    assert upload.isEnabled()
    upload.click()
    wait_until(
        lambda: (
            controller.service.snapshot.verification_state
            is ConnectedVerificationState.USB_VERIFIED
        )
    )

    disconnect = widget.findChild(QPushButton, "disconnectConnectedButton")
    assert disconnect is not None and disconnect.isEnabled()
    disconnect.click()
    wait_until(lambda: not controller.service.snapshot.link_connected and not controller.busy)
    assert harness.links[0].close_count == 1
    assert (
        controller.service.snapshot.verification_state
        is ConnectedVerificationState.REVERIFY_REQUIRED
    )

    link_kind = widget.findChild(QComboBox, "serialLinkKindSelection")
    baudrate = widget.findChild(QComboBox, "serialBaudrateSelection")
    assert link_kind is not None and baudrate is not None
    link_kind.setCurrentIndex(1)
    assert baudrate.currentData() == 57600
    assert discover.isEnabled()
    discover.click()
    wait_until(
        lambda: (
            controller.service.snapshot.link_kind is TelemetryLinkKind.SIK
            and bool(controller.service.snapshot.candidates)
            and not controller.busy
        )
    )
    assert harness.descriptors[1] == TransportDescriptor("COM7", TransportKind.SIK, 57600)
    targets.activated.emit(1)
    wait_until(
        lambda: controller.service.snapshot.selected_target is not None and not controller.busy
    )
    reverify = widget.findChild(QPushButton, "reverifyConnectedMissionButton")
    assert reverify is not None and reverify.isEnabled()
    reverify.click()
    wait_until(lambda: verification_state(controller) is ConnectedVerificationState.SIK_VERIFIED)
    assert controller.service.snapshot.connected_ready(clock.now())
    assert all(
        thread_id != ui_thread for port in harness.ports for thread_id in port.operation_thread_ids
    )
    controller.shutdown()
    widget.close()


def test_no_heartbeat_closes_link_and_reports_port_kind_baud_guidance() -> None:
    create_application(["skywriter-connected-no-heartbeat"])
    widget = ConnectedMissionWidget()
    clock = FakeClock()
    harness = ControllerHarness(clock, no_heartbeat=True)
    controller = ConnectedMissionController(
        widget,
        serial_ports=RecordingEnumerator(),
        link_factory=harness.open_link,
        port_factory=harness.make_port,
        clock=clock,
    )
    prepare_serial_selection(widget)
    discover = widget.findChild(QPushButton, "discoverSelectedLinkButton")
    assert discover is not None
    discover.click()
    wait_until(lambda: controller.service.snapshot.failure is not None and not controller.busy)

    failure = controller.service.snapshot.failure
    assert failure is not None
    assert failure.code is ConnectedFailureCode.NO_HEARTBEAT
    assert "115200 baud" in failure.detail
    assert "link kind" in failure.detail
    assert "Mission Planner" in failure.detail
    assert harness.links[0].close_count == 1
    assert not controller.service.snapshot.link_connected


@pytest.mark.parametrize(
    ("open_code", "connected_code", "detail"),
    [
        (
            TransportOpenFailureCode.BUSY,
            ConnectedFailureCode.PORT_BUSY,
            "Close Mission Planner before retrying.",
        ),
        (
            TransportOpenFailureCode.UNAVAILABLE,
            ConnectedFailureCode.PORT_UNAVAILABLE,
            "Refresh the port list after reconnecting the device.",
        ),
    ],
)
def test_typed_open_failures_are_visible_without_creating_a_link_owner(
    open_code: TransportOpenFailureCode,
    connected_code: ConnectedFailureCode,
    detail: str,
) -> None:
    create_application(["skywriter-connected-open-failure"])
    widget = ConnectedMissionWidget()
    harness = ControllerHarness(FakeClock(), open_error=TransportOpenError(open_code, detail))
    controller = ConnectedMissionController(
        widget,
        serial_ports=RecordingEnumerator(),
        link_factory=harness.open_link,
        port_factory=harness.make_port,
        clock=harness.clock,
    )
    prepare_serial_selection(widget)
    discover = widget.findChild(QPushButton, "discoverSelectedLinkButton")
    assert discover is not None
    discover.click()
    wait_until(lambda: controller.service.snapshot.failure is not None and not controller.busy)

    failure = controller.service.snapshot.failure
    visible = widget.findChild(QLabel, "connectedFailure")
    assert failure is not None and visible is not None
    assert failure.code is connected_code
    assert detail in visible.text()
    assert harness.links == []


def test_cancel_during_discovery_closes_the_only_opened_link() -> None:
    create_application(["skywriter-connected-cancel"])
    widget = ConnectedMissionWidget()
    harness = ControllerHarness(FakeClock(), block_discovery=True)
    controller = ConnectedMissionController(
        widget,
        serial_ports=RecordingEnumerator(),
        link_factory=harness.open_link,
        port_factory=harness.make_port,
        clock=harness.clock,
    )
    prepare_serial_selection(widget)
    discover = widget.findChild(QPushButton, "discoverSelectedLinkButton")
    disconnect = widget.findChild(QPushButton, "disconnectConnectedButton")
    assert discover is not None and disconnect is not None
    discover.click()
    wait_until(lambda: controller.busy and bool(harness.links))
    assert disconnect.isEnabled()
    disconnect.click()
    wait_until(lambda: not controller.busy)

    assert len(harness.links) == 1
    assert harness.links[0].close_count == 1
    assert not controller.service.snapshot.link_connected
