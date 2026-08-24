from __future__ import annotations

from dataclasses import replace

from skywriter.application.telemetry import (
    BatteryTelemetry,
    EkfTelemetry,
    ExtendedStateTelemetry,
    GpsTelemetry,
    HeartbeatTelemetry,
    HomeTelemetry,
    MissionProgressTelemetry,
    PositionTelemetry,
    SensorStatusTelemetry,
    TelemetryConnectionState,
    TelemetryFreshness,
    TelemetryLinkKind,
    TelemetryPoint,
    TelemetryRoute,
    TelemetryRoutePoint,
    TelemetrySnapshot,
    TimedSignal,
    build_map_layers,
)


def snapshot() -> TelemetrySnapshot:
    battery: TimedSignal[BatteryTelemetry] = TimedSignal.unavailable(5.0)
    gps: TimedSignal[GpsTelemetry] = TimedSignal.unavailable(5.0)
    sensors: TimedSignal[SensorStatusTelemetry] = TimedSignal.unavailable(5.0)
    ekf: TimedSignal[EkfTelemetry] = TimedSignal.unavailable(5.0)
    extended_state: TimedSignal[ExtendedStateTelemetry] = TimedSignal.unavailable(5.0)
    return TelemetrySnapshot(
        vehicle_identity="mavlink-system-1-component-1",
        target_system=1,
        target_component=1,
        link_kind=TelemetryLinkKind.SIK,
        link_connected=True,
        heartbeat=TimedSignal(HeartbeatTelemetry(False, 3, "Auto", 4, 2, 3), 10.0, 3.0),
        position=TimedSignal(
            PositionTelemetry(TelemetryPoint(51.5008, -0.1245), 151.2, 4.0, 5.0, 123.0),
            10.0,
            2.0,
        ),
        battery=battery,
        home=TimedSignal(HomeTelemetry(TelemetryPoint(51.5007, -0.1246), 15.1), 10.0, 60.0),
        mission=TimedSignal(MissionProgressTelemetry(3, 6, 4, 1, 2), 10.0, 5.0),
        gps=gps,
        sensors=sensors,
        ekf=ekf,
        extended_state=extended_state,
    )


def test_timed_signal_distinguishes_unavailable_fresh_stale_and_future() -> None:
    unavailable: TimedSignal[str] = TimedSignal.unavailable(3.0)
    available = TimedSignal("value", 10.0, 3.0)

    assert unavailable.freshness(10.0) is TelemetryFreshness.UNAVAILABLE
    assert available.freshness(13.0) is TelemetryFreshness.FRESH
    assert available.freshness(13.001) is TelemetryFreshness.STALE
    assert available.freshness(9.0) is TelemetryFreshness.STALE


def test_heartbeat_staleness_and_disconnect_fail_command_suitability_closed() -> None:
    value = snapshot()

    assert value.connection_state(12.0) is TelemetryConnectionState.CONNECTED
    assert value.command_gate_fresh(12.0) is True
    assert value.connection_state(14.0) is TelemetryConnectionState.STALE
    assert value.command_gate_fresh(14.0) is False
    assert replace(value, link_connected=False).connection_state(10.0) is (
        TelemetryConnectionState.DISCONNECTED
    )


def test_map_layers_use_caller_route_without_importing_mission_semantics() -> None:
    route = TelemetryRoute(
        tuple(
            TelemetryRoutePoint(
                sequence,
                TelemetryPoint(51.5007 + sequence / 10_000, -0.1246 + sequence / 10_000),
                f"Item {sequence}",
            )
            for sequence in range(6)
        )
    )

    layers = build_map_layers(snapshot(), route, now_s=11.0)

    assert layers.aircraft is not None
    assert layers.home is not None
    assert layers.current_target == route.points[3]
    assert [point.sequence for point in layers.completed_route] == [0, 1, 2]
    assert [point.sequence for point in layers.remaining_route] == [3, 4, 5]


def test_stale_position_and_progress_are_not_rendered_as_current_layers() -> None:
    route = TelemetryRoute((TelemetryRoutePoint(0, TelemetryPoint(1, 2), "Home"),))

    layers = build_map_layers(snapshot(), route, now_s=20.0)

    assert layers.aircraft is None
    assert layers.current_target is None
    assert layers.completed_route == ()
    assert layers.remaining_route == route.points
