"""Reusable tkinter/ttk widgets for the QR Stream Transfer UI."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Optional, Sequence, Tuple


class FileSelectionRow(ttk.Frame):
    """A label, path entry, and browse button bound to one StringVar."""

    def __init__(
        self,
        parent,
        label: str,
        *,
        variable: Optional[tk.StringVar] = None,
        mode: str = "open",
        filetypes: Sequence[Tuple[str, str]] = (("모든 파일", "*.*"),),
        button_text: str = "찾아보기…",
        dialog_title: Optional[str] = None,
        on_selected: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        if mode not in {"open", "save"}:
            raise ValueError("mode must be 'open' or 'save'")
        self.variable = variable or tk.StringVar(self)
        self.mode = mode
        self.filetypes = tuple(filetypes)
        self.dialog_title = dialog_title or label
        self.on_selected = on_selected

        ttk.Label(self, text=label).grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.entry = ttk.Entry(self, textvariable=self.variable)
        self.entry.grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(self, text=button_text, command=self._browse)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))
        self.columnconfigure(1, weight=1)

    def _browse(self) -> None:
        chooser = filedialog.askopenfilename if self.mode == "open" else filedialog.asksaveasfilename
        path = chooser(parent=self.winfo_toplevel(), title=self.dialog_title, filetypes=self.filetypes)
        if path:
            self.variable.set(path)
            if self.on_selected:
                self.on_selected(path)

    def get(self) -> str:
        return self.variable.get().strip()

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.entry.configure(state=state)
        self.browse_button.configure(state=state)


class ScrollLogPanel(ttk.Frame):
    """Read-only scrolling text log with append and clear operations."""

    def __init__(self, parent, *, height: int = 12, width: int = 80, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.text = tk.Text(self, height=height, width=width, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    def append(self, message: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", message.rstrip("\n") + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


# Short aliases for callers that prefer concise widget names.
FileSelectRow = FileSelectionRow
LogPanel = ScrollLogPanel
