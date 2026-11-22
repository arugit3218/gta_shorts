"""Cooperative cancellation shared between the worker and running subprocesses."""

from __future__ import annotations

import subprocess
import threading

from .errors import Cancelled


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            for p in list(self._procs):
                if p.poll() is None:
                    p.terminate()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.add(proc)
            if self._event.is_set():
                proc.terminate()

    def detach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)


NEVER = CancelToken()  # for callers that never cancel
