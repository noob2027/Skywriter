"""Operational policy seam tests."""

from skywriter.domain.mission import Mission, MissionSettings
from skywriter.domain.policy import MissionPolicy, MissionPolicyContext, NoOperationalPolicy


def test_no_operational_policy_is_an_inert_mission_policy() -> None:
    policy: MissionPolicy = NoOperationalPolicy()
    mission = Mission(MissionSettings(20.0, 5.0, True))

    assert policy.evaluate(mission, MissionPolicyContext()) == ()
