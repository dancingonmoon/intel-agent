"""Small stderr progress reporter for long collection and report runs."""

from __future__ import annotations

import sys
import time


class Progress:
    def __init__(self, label: str = "intel", stream=None, enabled: bool = True):
        self.label = label
        self.stream = stream or sys.stderr
        self.enabled = enabled
        self.started = time.monotonic()
        self._last_width = 0
        self._stage = ""
        self._stage_started = self.started

    @property
    def elapsed(self) -> str:
        return _fmt(time.monotonic() - self.started)

    def _write(self, text: str, newline: bool = False) -> None:
        if not self.enabled:
            return
        terminal = bool(getattr(self.stream, "isatty", lambda: False)())
        if terminal and not newline:
            pad = max(0, self._last_width - len(text))
            self.stream.write("\r" + text + (" " * pad))
            self._last_width = len(text)
        else:
            if terminal and self._last_width:
                self.stream.write("\n")
            self.stream.write(text + "\n")
            self._last_width = 0
        self.stream.flush()

    def stage(self, name: str) -> None:
        self._stage = name
        self._stage_started = time.monotonic()
        self._write(f"[{self.elapsed}] {name} ...", newline=True)

    def step(self, done: int, total: int, label: str = "") -> None:
        total = max(total, 1)
        pct = min(100, int(done * 100 / total))
        width = 20
        filled = int(width * pct / 100)
        bar = "#" * filled + "-" * (width - filled)
        detail = f" {label}" if label else ""
        self._write(f"[{self.elapsed}] {self._stage} [{bar}] {done}/{total}{detail}", newline=False)

    def info(self, message: str) -> None:
        self._write(f"[{self.elapsed}] {message}", newline=True)

    def finish(self, message: str = "") -> None:
        if self._last_width:
            self._write("", newline=True)
        if message:
            self._write(f"[{self.elapsed}] {message}", newline=True)


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{sec:02d}s"