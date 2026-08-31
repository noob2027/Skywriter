"""Qt worker handoff for the blocking confirmed native Land operation."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from skywriter.application.land_here_now import NativeLandHereNowSnapshot


class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _LandHereNowOperation(QRunnable):
    def __init__(self, operation: Callable[[], NativeLandHereNowSnapshot]) -> None:
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


class NativeLandHereNowWorkerHandoff(QObject):
    """Run one confirmed Land request off-thread and reject overlaps."""

    snapshot_ready = Signal(object)
    operation_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        operation: Callable[[], NativeLandHereNowSnapshot],
        *,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._operation = operation
        self._pool = pool or QThreadPool.globalInstance()
        self._busy = False
        self._active: _LandHereNowOperation | None = None

    @property
    def busy(self) -> bool:
        return self._busy

    def submit(self) -> bool:
        if self._busy:
            return False
        self._busy = True
        self.busy_changed.emit(True)
        worker = _LandHereNowOperation(self._operation)
        self._active = worker
        worker.signals.completed.connect(self._complete)
        worker.signals.failed.connect(self._fail)
        self._pool.start(worker)
        return True

    @Slot(object)
    def _complete(self, snapshot: object) -> None:
        if isinstance(snapshot, NativeLandHereNowSnapshot):
            self.snapshot_ready.emit(snapshot)
        else:  # pragma: no cover - protects injected boundaries
            self.operation_failed.emit("Land Here Now worker returned an invalid snapshot.")
        self._finish()

    @Slot(str)
    def _fail(self, detail: str) -> None:
        self.operation_failed.emit(detail or "Land Here Now worker failed without detail.")
        self._finish()

    def _finish(self) -> None:
        self._active = None
        self._busy = False
        self.busy_changed.emit(False)
