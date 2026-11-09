"""Verhindert, dass die Maschine doppelt läuft (sonst würden Clips doppelt gepostet)."""

from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunning(Exception):
    pass


class SingleInstanceLock:
    """Exklusive Dateisperre. Das Betriebssystem gibt sie automatisch frei, wenn der Prozess endet –
    auch nach einem Absturz bleibt also keine "tote" Sperre zurück."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            try:
                fh.seek(0)
                other = fh.read().strip() or "?"
            except OSError:          # Windows: gesperrter Bereich ist nicht lesbar
                other = "?"
            fh.close()
            raise AlreadyRunning(f"Die Clipmaschine läuft bereits (PID {other}). "
                                 f"Erst den anderen Prozess beenden.") from exc
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
