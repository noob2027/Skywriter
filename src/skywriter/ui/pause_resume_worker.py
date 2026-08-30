"""Qt worker handoff for blocking native Pause/Resume operations."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from skywriter.application.pause_resume import (
    NativePauseResumeAction,
    NativePauseResumeSnapshot,
)


class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _PauseResumeOperation(QRunnable):
    def __init__(
        self,
        operation: Callable[[], NativePauseResumeSnapshot],
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._operation = operation
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            snapshot = self._operation()
        except Exception as error:  # pragma: no cover - defensive UI boundary
            self.signals.failed.emit(str(error))
            return
        self.signals.completed.emit(snapshot)


class NativePauseResumeWorkerHandoff(QObject):
    """Run one Pause or Resume off-thread and reject overlapping requests."""

    snapshot_ready = Signal(object)
    operation_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        pause_operation: Callable[[], NativePauseResumeSnapshot],
        resume_operation: Callable[[], NativePauseResumeSnapshot],
        *,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._operations = {
            NativePauseResumeAction.PAUSE: pause_operation,
            NativePauseResumeAction.RESUME: resume_operation,
        }
        self._pool = pool or QThreadPool.globalInstance()
        self._busy = False
        self._active: _PauseResumeOperation | None = None

    @property
    def busy(self) -> bool:
        return self._busy

    def submit_pause(self) -> bool:
        return self._submit(NativePauseResumeAction.PAUSE)

    def submit_resume(self) -> bool:
        return self._submit(NativePauseResumeAction.RESUME)

    def _submit(self, action: NativePauseResumeAction) -> bool:
        if self._busy:
            return False
        self._busy = True
        self.busy_changed.emit(True)
        worker = _PauseResumeOperation(self._operations[action])
        self._active = worker
        worker.signals.completed.connect(self._complete)
        worker.signals.failed.connect(self._fail)
        self._pool.start(worker)
        return True

    @Slot(object)
    def _complete(self, snapshot: object) -> None:
        if isinstance(snapshot, NativePauseResumeSnapshot):
            self.snapshot_ready.emit(snapshot)
        else:  # pragma: no cover - protects injected boundaries
            self.operation_failed.emit("Pause/Resume worker returned an invalid snapshot.")
        self._finish()

    @Slot(str)
    def _fail(self, detail: str) -> None:
        self.operation_failed.emit(detail or "Pause/Resume worker failed without detail.")
        self._finish()

    def _finish(self) -> None:
        self._active = None
        self._busy = False
        self.busy_changed.emit(False)
