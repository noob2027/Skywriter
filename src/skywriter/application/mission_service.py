"""Presentation-neutral use cases for the complete offline mission workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from skywriter.domain.compiled import CompiledMission
from skywriter.domain.compiler import MissionCompiler
from skywriter.domain.mission import GeoPoint, LandAction, Mission, MissionAction, MissionSettings
from skywriter.domain.validation import (
    ValidationFinding,
    ValidationMode,
    require_valid_mission,
    validate_complete,
    validate_draft,
)


class MissionRepository(Protocol):
    """Persistence port used by the offline mission service."""

    def load(self, path: str | os.PathLike[str]) -> Mission:
        """Load one strict mission document."""

    def save(self, path: str | os.PathLike[str], mission: Mission) -> None:
        """Atomically save one strict mission document."""


class OfflineMissionError(ValueError):
    """Raised when an offline use case is unavailable in the current state."""


@dataclass(frozen=True, slots=True)
class OfflineMissionSnapshot:
    """Immutable state for one offline editing session."""

    mission: Mission | None = None
    selected_index: int | None = None
    revision: int = 0
    compiled_preview: CompiledMission | None = None
    compiled_revision: int | None = None
    source_path: Path | None = None
    saved_revision: int | None = None

    @property
    def draft_findings(self) -> tuple[ValidationFinding, ...]:
        if self.mission is None:
            return ()
        return validate_draft(self.mission)

    @property
    def complete_findings(self) -> tuple[ValidationFinding, ...]:
        if self.mission is None:
            return ()
        return validate_complete(self.mission)

    @property
    def can_save(self) -> bool:
        return self.mission is not None and not self.draft_findings

    @property
    def can_compile(self) -> bool:
        return self.mission is not None and not self.complete_findings

    @property
    def is_dirty(self) -> bool:
        return self.mission is not None and self.saved_revision != self.revision


class OfflineMissionService:
    """Apply validated immutable mission edits and offline persistence use cases."""

    def __init__(
        self,
        repository: MissionRepository,
        compiler: MissionCompiler | None = None,
    ) -> None:
        self._repository = repository
        self._compiler = compiler or MissionCompiler()
        self._snapshot = OfflineMissionSnapshot()

    @property
    def snapshot(self) -> OfflineMissionSnapshot:
        return self._snapshot

    def new_mission(self) -> OfflineMissionSnapshot:
        """Reset every offline artifact and return to Takeoff setup."""

        self._snapshot = OfflineMissionSnapshot(revision=self._snapshot.revision + 1)
        return self._snapshot

    def update_settings(self, settings: MissionSettings) -> OfflineMissionSnapshot:
        """Create Takeoff settings or replace them while retaining mission identity."""

        mission = self._snapshot.mission
        updated = (
            Mission.create(settings) if mission is None else replace(mission, settings=settings)
        )
        return self._commit_edit(updated, selected_index=self._snapshot.selected_index)

    def append_action(self, action: MissionAction) -> OfflineMissionSnapshot:
        mission = self._require_mission()
        if mission.is_closed:
            raise OfflineMissionError("use Remove Land and reopen before adding mission points")
        return self._commit_edit(mission.append_action(action))

    def replace_action(self, index: int, action: MissionAction) -> OfflineMissionSnapshot:
        mission = self._require_mission()
        if isinstance(mission.actions[index], LandAction) and not isinstance(action, LandAction):
            raise OfflineMissionError("Land can only be removed through the explicit reopen action")
        return self._commit_edit(mission.replace_action(index, action))

    def delete_action(self, index: int) -> OfflineMissionSnapshot:
        mission = self._require_mission()
        if isinstance(mission.actions[index], LandAction):
            raise OfflineMissionError("use Remove Land and reopen for the final Land action")
        return self._commit_edit(mission.delete_action(index))

    def move_action(self, index: int, point: GeoPoint) -> OfflineMissionSnapshot:
        mission = self._require_mission()
        if not isinstance(point, GeoPoint):
            raise TypeError("point must be a GeoPoint")
        return self._commit_edit(
            mission.move_action(index, point),
            selected_index=self._snapshot.selected_index,
        )

    def select_action(self, index: int) -> OfflineMissionSnapshot:
        mission = self._require_mission()
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("action index must be an integer")
        if not 0 <= index < len(mission.actions):
            raise OfflineMissionError("action index is outside the mission")
        self._snapshot = replace(self._snapshot, selected_index=index)
        return self._snapshot

    def undo(self) -> OfflineMissionSnapshot:
        mission = self._require_mission()
        if mission.is_closed:
            raise OfflineMissionError("use Remove Land and reopen before undoing mission points")
        return self._commit_edit(mission.undo_last_action())

    def clear_actions(self) -> OfflineMissionSnapshot:
        return self._commit_edit(self._require_mission().clear_actions())

    def remove_land(self) -> OfflineMissionSnapshot:
        mission = self._require_mission()
        if not mission.is_closed:
            raise OfflineMissionError("the mission does not have a final Land action")
        return self._commit_edit(mission.remove_land())

    def save(self, path: str | os.PathLike[str]) -> OfflineMissionSnapshot:
        mission = require_valid_mission(self._require_mission(), ValidationMode.DRAFT)
        destination = Path(path)
        self._repository.save(destination, mission)
        self._snapshot = replace(
            self._snapshot,
            source_path=destination,
            saved_revision=self._snapshot.revision,
        )
        return self._snapshot

    def load(self, path: str | os.PathLike[str]) -> OfflineMissionSnapshot:
        source = Path(path)
        mission = require_valid_mission(self._repository.load(source), ValidationMode.DRAFT)
        revision = self._snapshot.revision + 1
        self._snapshot = OfflineMissionSnapshot(
            mission=mission,
            revision=revision,
            source_path=source,
            saved_revision=revision,
        )
        return self._snapshot

    def compile_preview(self) -> OfflineMissionSnapshot:
        compiled = self._compiler.compile(self._require_mission())
        self._snapshot = replace(
            self._snapshot,
            compiled_preview=compiled,
            compiled_revision=self._snapshot.revision,
        )
        return self._snapshot

    def _commit_edit(
        self,
        mission: Mission,
        *,
        selected_index: int | None = None,
    ) -> OfflineMissionSnapshot:
        validated = require_valid_mission(mission, ValidationMode.DRAFT)
        self._snapshot = replace(
            self._snapshot,
            mission=validated,
            selected_index=selected_index,
            revision=self._snapshot.revision + 1,
            compiled_preview=None,
            compiled_revision=None,
        )
        return self._snapshot

    def _require_mission(self) -> Mission:
        mission = self._snapshot.mission
        if mission is None:
            raise OfflineMissionError("confirm Takeoff settings before using this action")
        return mission
