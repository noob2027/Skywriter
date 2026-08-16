"""Operational mission-policy port and the deliberately inert prototype policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from skywriter.domain.mission import Mission
from skywriter.domain.validation import ValidationFinding


@dataclass(frozen=True, slots=True)
class MissionPolicyContext:
    """Reserved context seam for future, separately reviewed operational profiles."""


class MissionPolicy(Protocol):
    """Port for operational findings kept separate from structural validation."""

    def evaluate(
        self, mission: Mission, context: MissionPolicyContext
    ) -> tuple[ValidationFinding, ...]:
        """Evaluate policy without mutating the mission."""


@dataclass(frozen=True, slots=True)
class NoOperationalPolicy:
    """Prototype policy that imposes no operational envelope or safety claim."""

    def evaluate(
        self, mission: Mission, context: MissionPolicyContext
    ) -> tuple[ValidationFinding, ...]:
        del mission, context
        return ()
