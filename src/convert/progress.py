"""Single-line stderr progress bar for convert/preview hosts."""

from __future__ import annotations

import shutil
import sys
import threading
from typing import Callable


class Progress:
    """Single-line stderr bar. Callback always fires; stderr only when enabled."""

    def __init__(
        self,
        total: int,
        enabled: bool,
        on_progress: Callable[[int, int, str, str], None] | None = None,
    ) -> None:
        self.total = max(total, 0)
        self.enabled = enabled
        self.on_progress = on_progress
        self._width = 0
        self._lock = threading.Lock()

    def update(self, current: int, action: str, name: str) -> None:
        with self._lock:
            if self.on_progress is not None:
                self.on_progress(current, self.total, action, name)
            if not self.enabled:
                return
            total = self.total
            frac = 1.0 if total == 0 else min(current / total, 1.0)
            bar_w = 24
            filled = int(bar_w * frac) if total else bar_w
            bar = "#" * filled + "-" * (bar_w - filled)
            denom = total if total else current
            label = f"[{bar}] {current}/{denom}  {action}  {name}"
            cols = shutil.get_terminal_size((80, 24)).columns
            if cols > 8 and len(label) > cols - 1:
                label = label[: cols - 2] + "…"
            pad = max(self._width - len(label), 0)
            sys.stderr.write("\r" + label + (" " * pad))
            sys.stderr.flush()
            self._width = len(label)

    def close(self) -> None:
        with self._lock:
            if not self.enabled:
                return
            sys.stderr.write("\n")
            sys.stderr.flush()
            self.enabled = False
