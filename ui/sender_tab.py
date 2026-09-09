"""Sender tab with controls, an embedded QR slideshow, and a live log."""

from __future__ import annotations

import queue
import math
import sys
import threading
import tkinter as tk
import time
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sender.encode import build_encoder
from sender.display import SenderApp


class _LabeledEntry(ttk.Frame):
    """label + ttk.Entry pair. -> ui/widgets.py candidate (used 3x below)."""

    def __init__(self, parent: tk.Widget, label: str, default: str, width: int = 8):
        super().__init__(parent)
        ttk.Label(self, text=label).pack(side="left")
        self.var = tk.StringVar(value=default)
        ttk.Entry(self, textvariable=self.var, width=width).pack(side="left", padx=(4, 0))

    def get(self) -> str:
        return self.var.get().strip()


class SenderTab(ttk.Frame):
    """송신 탭: 파일 선택 -> LT 인코딩 -> QR 슬라이드쇼."""

    START_DELAY_MS = 2000
    FHD_WIDTH = 1920
    FHD_HEIGHT = 1080
    WIDTH_USAGE = 0.90
    HEIGHT_USAGE = 0.62
    TWO_ROW_HEIGHT_USAGE = 0.78

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, padding=12)

        self.file_path: Optional[Path] = None
        self._worker: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._sender_app: Optional[SenderApp] = None
        self._loop_started_at: Optional[float] = None

        self._build_file_row()
        self._build_settings_row()
        self._build_controls_row()
        self._build_content_area()

        self._poll_log_queue()

    # --- layout ----------------------------------------------------------

    def _build_file_row(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="Browse...", command=self._on_browse).pack(side="left")
        self.file_label_var = tk.StringVar(value="(파일을 선택하세요)")
        ttk.Label(row, textvariable=self.file_label_var, anchor="w").pack(
            side="left", fill="x", expand=True, padx=(8, 0)
        )

    def _build_settings_row(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 8))
        self.fps_entry = _LabeledEntry(row, "FPS:", "15", width=6)
        self.fps_entry.pack(side="left", padx=(0, 12))
        self.redundancy_entry = _LabeledEntry(row, "중복도(overhead):", "1.5", width=6)
        self.redundancy_entry.pack(side="left", padx=(0, 12))
        ttk.Label(row, text="동시 QR 수:").pack(side="left")
        self.cols_var = tk.StringVar(value="1")
        self.cols_combo = ttk.Combobox(
            row,
            textvariable=self.cols_var,
            values=tuple(str(value) for value in range(1, 9)),
            width=4,
            state="readonly",
        )
        self.cols_combo.pack(side="left", padx=(4, 0))
        self.cols_combo.bind("<<ComboboxSelected>>", self._update_auto_size_label)
        self.auto_size_var = tk.StringVar()
        ttk.Label(row, textvariable=self.auto_size_var).pack(side="left", padx=(12, 0))
        self._update_auto_size_label()

    def _auto_tile_size(self, cols: int) -> int:
        """Choose a square QR tile size for the current display, capped at FHD."""
        screen_width = min(self.winfo_screenwidth(), self.FHD_WIDTH)
        screen_height = min(self.winfo_screenheight(), self.FHD_HEIGHT)
        usable_width = max(320, int(screen_width * self.WIDTH_USAGE) - 48)
        height_usage = self.TWO_ROW_HEIGHT_USAGE if cols >= 5 else self.HEIGHT_USAGE
        usable_height = max(280, int(screen_height * height_usage))
        rows = 2 if cols >= 5 else 1
        grid_cols = math.ceil(cols / rows)
        tile_size = min(usable_width // grid_cols, usable_height // rows)
        if rows == 2:
            tile_size = min(tile_size, 420)
        return max(140, tile_size)

    def _update_auto_size_label(self, _event=None) -> None:
        try:
            cols = max(1, int(self.cols_var.get()))
        except ValueError:
            cols = 1
        tile_size = self._auto_tile_size(cols)
        rows = 2 if cols >= 5 else 1
        grid_cols = math.ceil(cols / rows)
        self.auto_size_var.set(
            f"자동 크기: 각 {tile_size}px · {grid_cols}열×{rows}행 · "
            f"전체 {tile_size * grid_cols}×{tile_size * rows}px"
        )

    def _build_controls_row(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(row, text="시작", command=self._on_start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(row, text="정지", command=self._on_stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))

    def _build_content_area(self) -> None:
        content = ttk.Panedwindow(self, orient="vertical")
        content.pack(fill="both", expand=True)

        qr_panel = ttk.LabelFrame(content, text="QR 슬라이드쇼", padding=8)
        log_panel = ttk.LabelFrame(content, text="전송 로그", padding=8)
        content.add(qr_panel, weight=8)
        content.add(log_panel, weight=1)

        self._qr_container = ttk.Frame(qr_panel)
        self._qr_container.pack(fill="both", expand=True)
        self._qr_placeholder = tk.Canvas(
            self._qr_container,
            width=420,
            height=420,
            background="#f7f7f7",
            highlightthickness=0,
        )
        self._qr_placeholder.bind("<Configure>", self._draw_qr_placeholder)
        self._qr_placeholder.pack(fill="both", expand=True)

        self.log_text = ScrolledText(log_panel, height=3, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def _draw_qr_placeholder(self, _event=None) -> None:
        canvas = self._qr_placeholder
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        size = max(80, min(width, height, 460) - 40)
        left = (width - size) // 2
        top = (height - size) // 2
        canvas.create_rectangle(
            left, top, left + size, top + size,
            outline="#8a8a8a", width=2, dash=(7, 5),
        )
        canvas.create_text(
            width // 2,
            height // 2,
            text="QR 표시 영역\n\n파일을 선택한 뒤 시작을 누르세요",
            justify="center",
            fill="#555555",
            font=("TkDefaultFont", 12),
        )

    # --- UI actions --------------------------------------------------------

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(title="전송할 파일 선택")
        if not path:
            return
        self.file_path = Path(path)
        self.file_label_var.set(str(self.file_path))

    def _on_start(self) -> None:
        if self.file_path is None:
            messagebox.showwarning("파일 없음", "먼저 전송할 파일을 선택하세요.")
            return
        if self._worker is not None and self._worker.is_alive():
            return

        try:
            fps        = float(self.fps_entry.get())
            redundancy = float(self.redundancy_entry.get())
            cols       = int(self.cols_var.get())
        except ValueError:
            messagebox.showerror("입력 오류", "FPS/중복도/동시 QR 수를 숫자로 입력하세요.")
            return
        if fps <= 0 or redundancy < 1.0 or not 1 <= cols <= 8:
            messagebox.showerror(
                "입력 오류",
                "FPS는 양수, 중복도는 1.0 이상, 동시 QR 수는 1~8이어야 합니다.",
            )
            return
        tile_size = self._auto_tile_size(cols)
        rows = 2 if cols >= 5 else 1
        grid_cols = math.ceil(cols / rows)
        target_size = tile_size * grid_cols

        self._cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._log(
            f"[시작] {self.file_path.name} 인코딩 중... "
            f"(redundancy={redundancy}, QR={cols}×{tile_size}px, "
            f"{fps:.1f}fps, 동시 QR={cols})"
        )

        self._worker = threading.Thread(
            target=self._encode_worker,
            args=(self.file_path, redundancy, target_size, fps, cols),
            daemon=True,
        )
        self._worker.start()

    def _on_stop(self) -> None:
        self._cancel_event.set()
        self._clear_qr_display()
        self._log("[정지] 사용자가 전송을 중단했습니다.")
        self._reset_buttons()

    # --- background work (runs off the Tk main thread) ----------------------

    def _encode_worker(
        self, path: Path, redundancy: float, target_size: int, fps: float, cols: int
    ) -> None:
        """Read the file and build only the encoder; packets stream on demand."""
        try:
            data = path.read_bytes()
            encoder, packet_count = build_encoder(
                data, chunk_size=1024, redundancy=redundancy, seed=0
            )
        except Exception as exc:  # surface any failure back to the UI thread
            self._log_queue.put(f"[오류] 인코딩 실패: {exc}")
            self.after(0, self._reset_buttons)
            return

        if self._cancel_event.is_set():
            return

        self._log_queue.put(
            f"[인코더 준비] 청크 K={encoder.total_k}, 전송 패킷 {packet_count}개 "
            f"(SHA-256 {encoder.file_hash[:16]}...)"
        )
        # SenderApp/Tkinter must be touched on the main thread only.
        self.after(0, self._start_slideshow, encoder, packet_count, target_size, fps, cols)

    def _start_slideshow(
        self, encoder, packet_count: int, target_size: int, fps: float, cols: int
    ) -> None:
        if self._cancel_event.is_set():
            self._reset_buttons()
            return

        self._clear_qr_display(show_placeholder=False)
        self._loop_started_at = None
        filename = self.file_path.name if self.file_path is not None else ""
        self._sender_app = SenderApp(
            self._qr_container,
            encoder,
            packet_count,
            fps,
            target_size=target_size,
            cols=cols,
            progress_callback=self._on_frame_displayed,
            start_delay_ms=self.START_DELAY_MS,
            filename=filename,
        )
        rows = 2 if cols >= 5 else 1
        grid_cols = math.ceil(cols / rows)
        self._log(
            f"[대기] {self.START_DELAY_MS / 1000:g}초 후 QR 전송 시작 · "
            f"{packet_count}개 패킷 · 프레임당 {cols}개 · "
            f"각 {self._auto_tile_size(cols)}px · {grid_cols}열×{rows}행 · {fps:.1f}fps"
        )

    def _on_frame_displayed(self, frame_number: int, loop_number: int) -> None:
        # One entry per pass shows ongoing activity without flooding the log.
        if frame_number == 1:
            now = time.perf_counter()
            if self._loop_started_at is not None:
                elapsed = now - self._loop_started_at
                self._log(f"[재생] loop {loop_number - 1} 완료 · 소요시간 {elapsed:.2f}초")
            filename = self.file_path.name if self.file_path else ""
            self._log(f"[재생] loop {loop_number} 시작 ({filename})")
            self._loop_started_at = now

    def _clear_qr_display(self, *, show_placeholder: bool = True) -> None:
        if self._sender_app is not None:
            self._sender_app.stop()
            self._sender_app = None
        self._loop_started_at = None
        for child in self._qr_container.winfo_children():
            if child is not self._qr_placeholder:
                try:
                    child.destroy()
                except tk.TclError:
                    pass
        if show_placeholder:
            self._qr_placeholder.pack(fill="both", expand=True)
            self._draw_qr_placeholder()
        else:
            self._qr_placeholder.pack_forget()

    # --- log panel -----------------------------------------------------------

    def _log(self, message: str) -> None:
        self._log_queue.put(message)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                message = self._log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", message + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _reset_buttons(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
