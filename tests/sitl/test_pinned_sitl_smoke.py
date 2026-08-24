"""Read-only smoke evidence against the exact approved stock SITL target."""

from scripts.sitl.pinned import (
    EXPECTED_CUSTOM_VERSION,
    EXPECTED_FLIGHT_SW_VERSION,
    MAVLINK_DIALECT,
    MAVLINK_VERSION,
    PYMAVLINK_VERSION,
    CleanMissionState,
    SitlEndpoint,
    SitlTargetIdentity,
)


def test_pinned_stock_sitl_is_ready_disarmed_and_clean(
    sitl_endpoint: SitlEndpoint,
    sitl_target_identity: SitlTargetIdentity,
    sitl_clean_mission_state: CleanMissionState,
) -> None:
    assert sitl_endpoint.host == "127.0.0.1"
    assert 1024 <= sitl_endpoint.tcp_port <= 65_535
    assert sitl_target_identity == SitlTargetIdentity(
        system_id=1,
        component_id=1,
        flight_sw_version=EXPECTED_FLIGHT_SW_VERSION,
        flight_custom_version=EXPECTED_CUSTOM_VERSION,
        mavlink_dialect=MAVLINK_DIALECT,
        mavlink_version=MAVLINK_VERSION,
        pymavlink_version=PYMAVLINK_VERSION,
    )
    assert sitl_clean_mission_state == CleanMissionState(count=0, mission_type=0)
