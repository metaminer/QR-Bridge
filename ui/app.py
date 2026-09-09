"""DataTransfer main window - ttk.Notebook shell hosting the sender/receiver tabs.

CLI:
    python ui/app.py
    (or: python -m ui.app)
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.sender_tab import SenderTab

try:
    from ui.receiver_tab import ReceiverTab
except ImportError:
    # ui/receiver_tab.py hasn't landed yet — fall back to a placeholder tab
    # so this window still runs standalone in the meantime.
    ReceiverTab = None


class _PendingTab(ttk.Frame):
    """Shown in place of the receiver tab until ui/receiver_tab.py exists."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, padding=20)
        ttk.Label(self, text="수신 탭은 아직 준비 중입니다 (ui/receiver_tab.py 대기).").pack()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DataTransfer – Air-Gap QR Bridge")
        self.after_idle(self._maximize_window)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        notebook.add(SenderTab(notebook), text="송신")
        receiver_tab_cls = ReceiverTab or _PendingTab
        notebook.add(receiver_tab_cls(notebook), text="수신")

    def _maximize_window(self) -> None:
        """Use the monitor work area so two QR rows remain camera-readable."""
        try:
            self.state("zoomed")
        except tk.TclError:
            width = max(1100, int(self.winfo_screenwidth() * 0.95))
            height = max(720, int(self.winfo_screenheight() * 0.90))
            self.geometry(f"{width}x{height}+0+0")


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    # Required when frozen (PyInstaller) on Windows: sender/display.py's
    # SenderApp spins up a ProcessPoolExecutor for QR rendering. Windows
    # multiprocessing has no fork(), so a worker process re-launches this
    # same exe with a special marker; without freeze_support() called first,
    # that re-launch doesn't recognize the marker and just runs main() again,
    # popping up a whole new GUI window per worker instead of running the
    # worker function. Must be the very first thing executed.
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
