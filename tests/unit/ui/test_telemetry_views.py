from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QLabel, QPushButton

from skywriter.application.telemetry import (
    BatteryTelemetry,
    EkfTelemetry,
    ExtendedStateTelemetry,
    GpsTelemetry,
    HeartbeatTelemetry,
    HomeTelemetry,
    MissionProgressTelemetry,
    NativeStatusText,
    PositionTelemetry,
    SensorStatusTelemetry,
    TelemetryLinkKind,
    TelemetryPoint,
    TelemetryRoute,
    TelemetryRoutePoint,
    TelemetrySnapshot,
    TimedSignal,
)
from skywriter.main import create_application
from skywriter.ui.flight import FlightTelemetryWidget
from skywriter.ui.preflight import PreflightTelemetryWidget
from skywriter.ui.telemetry import NativeMessagesList, TelemetryCard


def snapshot(*, observed_at_s: float = 100.0) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        vehicle_identity="mavlink-system-1-component-1",
        target_system=1,
        target_component=1,
        link_kind=TelemetryLinkKind.SIK,
        link_connected=True,
        heartbeat=TimedSignal(HeartbeatTelemetry(False, 3, "Auto", 4, 2, 3), observed_at_s, 3.0),
        position=TimedSignal(
            PositionTelemetry(
                TelemetryPoint(35.3633021, 149.1652374),
                584.2,
                21.4,
                7.25,
                92.0,
            ),
            observed_at_s,
            2.0,
        ),
        battery=TimedSignal(BatteryTelemetry(0, 15.8, 3.4, 76), observed_at_s, 10.0),
        home=TimedSignal(
            HomeTelemetry(TelemetryPoint(35.363261, 149.16523), 583.9),
            observed_at_s,
            60.0,
        ),
        mission=TimedSignal(MissionProgressTelemetry(4, 8, 3, 0, 3), observed_at_s, 5.0),
        gps=TimedSignal(GpsTelemetry(3, 16, 0.84), observed_at_s, 5.0),
        sensors=TimedSignal(SensorStatusTelemetry(0x3FFF, 0x3FFF, 0x3FFF), observed_at_s, 5.0),
        ekf=TimedSignal(EkfTelemetry(831, 0.01, 0.02, 0.03, 0.04, 0.05), observed_at_s, 5.0),
        extended_state=TimedSignal(ExtendedStateTelemetry(2, 0), observed_at_s, 5.0),
        native_messages=(NativeStatusText(6, "ArduCopter V4.6.3 ready", 0, 0, observed_at_s),),
    )


def route() -> TelemetryRoute:
    return TelemetryRoute(
        tuple(
            TelemetryRoutePoint(
                sequence,
                TelemetryPoint(35.363261 + sequence * 0.00005, 149.16523 + sequence * 0.00004),
                f"Item {sequence}",
            )
            for sequence in range(8)
        )
    )


def card(widget: PreflightTelemetryWidget | FlightTelemetryWidget, name: str) -> TelemetryCard:
    result = widget.findChild(TelemetryCard, name)
    assert result is not None
    return result


def test_preflight_renders_missing_fresh_stale_and_recovered_observations() -> None:
    create_application(["skywriter-preflight-telemetry-test"])
    view = PreflightTelemetryWidget()

    assert card(view, "telemetryGps").property("freshness") == "unavailable"
    view.render_snapshot(snapshot(), now_s=101.0)
    assert card(view, "telemetryConnection").value_label.text() == "Connected"
    assert card(view, "telemetryGps").property("freshness") == "fresh"
    assert "satellites 16" in card(view, "telemetryGps").value_label.text()

    view.render_snapshot(snapshot(), now_s=110.0)
    assert card(view, "telemetryConnection").value_label.text() == "Stale"
    assert card(view, "telemetryGps").property("freshness") == "stale"
    assert card(view, "telemetryGps").value_label.text().startswith("Stale")

    recovered = snapshot(observed_at_s=110.0)
    view.render_snapshot(recovered, now_s=110.5)
    assert card(view, "telemetryConnection").value_label.text() == "Connected"
    assert card(view, "telemetryGps").property("freshness") == "fresh"
    messages = view.findChild(NativeMessagesList, "nativeStatusMessages")
    assert messages is not None
    assert "ArduCopter V4.6.3 ready" in messages.item(0).text()


def test_flight_renders_route_progress_and_fail_closed_freshness() -> None:
    application = create_application(["skywriter-flight-telemetry-test"])
    view = FlightTelemetryWidget()
    sample = snapshot()
    mission_route = route()

    view.render_snapshot(sample, now_s=101.0, route=mission_route)
    layers = view.map_layers_widget.layers
    assert layers.aircraft == sample.position.value
    assert layers.home == sample.home.value
    assert layers.current_target == mission_route.points[4]
    assert tuple(point.sequence for point in layers.completed_route) == (0, 1, 2, 3)
    assert tuple(point.sequence for point in layers.remaining_route) == (4, 5, 6, 7)
    assert card(view, "flightPosition").property("freshness") == "fresh"
    view.resize(1200, 760)
    view.show()
    application.processEvents()
    assert not view.grab().isNull()

    view.render_snapshot(sample, now_s=110.0, route=mission_route)
    assert card(view, "flightConnection").value_label.text().endswith("stale")
    assert card(view, "flightPosition").property("freshness") == "stale"
    assert view.map_layers_widget.layers.aircraft is None
    assert view.map_layers_widget.layers.current_target is None

    view.render_snapshot(None, now_s=111.0, route=mission_route)
    assert card(view, "flightConnection").property("freshness") == "unavailable"
    assert view.map_layers_widget.layers.remaining_route == mission_route.points


def test_telemetry_stays_read_only_around_only_approved_serial_actions() -> None:
    create_application(["skywriter-telemetry-control-confinement-test"])
    preflight = PreflightTelemetryWidget()
    flight = FlightTelemetryWidget()

    assert [button.objectName() for button in preflight.findChildren(QPushButton)] == [
        "requestNativePrearmButton",
        "normalArmButton",
    ]
    assert [button.objectName() for button in flight.findChildren(QPushButton)] == [
        "nativeAutoStartButton",
        "nativePauseButton",
        "nativeResumeButton",
    ]
    preflight_disclaimer = preflight.findChild(QLabel, "preflightTelemetryDisclaimer")
    flight_disclaimer = flight.findChild(QLabel, "flightTelemetryDisclaimer")
    assert preflight_disclaimer is not None
    assert flight_disclaimer is not None
    assert "READ-ONLY" in preflight_disclaimer.text()
    assert "READ-ONLY" in flight_disclaimer.text()


def test_link_disconnect_is_visible_without_erasing_last_observation() -> None:
    create_application(["skywriter-telemetry-disconnect-test"])
    view = FlightTelemetryWidget()
    disconnected = replace(snapshot(), link_connected=False)

    view.render_snapshot(disconnected, now_s=101.0)

    assert card(view, "flightConnection").value_label.text().endswith("disconnected")
    assert "21.4 m Above Home" in card(view, "flightPosition").value_label.text()
