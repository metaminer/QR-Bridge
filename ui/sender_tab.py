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

from sender.encode import DEFAULT_EC_LEVEL, EC_LEVELS, build_encoder, make_qr_image
from common.qr_wire import pack_packet
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

    FHD_WIDTH = 1920
    FHD_HEIGHT = 1080
    WIDTH_USAGE = 0.98
    HEIGHT_USAGE = 0.62
    TWO_ROW_HEIGHT_USAGE = 0.88
    MAX_LOG_LINES = 2000

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, padding=12)

        self.file_path: Optional[Path] = None
        self._worker: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._run_generation = 0
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._progress_queue: "queue.Queue[Optional[float]]" = queue.Queue()
        self._sender_app: Optional[SenderApp] = None
        self._loop_started_at: Optional[float] = None
        self._log_history: list[str] = []
        self._log_window: Optional[tk.Toplevel] = None
        self._log_text: Optional[ScrolledText] = None

        self._build_toolbar()
        self._build_content_area()

        self._poll_log_queue()

    # --- layout ----------------------------------------------------------

    def _build_toolbar(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 6))
        ttk.Button(row, text="Browse...", command=self._on_browse).grid(row=0, column=0)
        self.file_label_var = tk.StringVar(value="(파일을 선택하세요)")
        ttk.Label(row, textvariable=self.file_label_var, anchor="w").grid(
            row=0, column=1, padx=(8, 12), sticky="ew"
        )
        self.fps_entry = _LabeledEntry(row, "FPS:", "15", width=5)
        self.fps_entry.grid(row=0, column=2, padx=(0, 10))
        self.redundancy_entry = _LabeledEntry(row, "중복도:", "1.5", width=5)
        self.redundancy_entry.grid(row=0, column=3, padx=(0, 10))
        ttk.Label(row, text="동시 QR:").grid(row=0, column=4)
        self.cols_var = tk.StringVar(value="1")
        self.cols_combo = ttk.Combobox(
            row, textvariable=self.cols_var,
            values=tuple(str(value) for value in range(1, 11)),
            width=3, state="readonly",
        )
        self.cols_combo.grid(row=0, column=5, padx=(4, 10))
        self.cols_combo.bind("<<ComboboxSelected>>", self._update_auto_size_label)
        ttk.Label(row, text="ECC:").grid(row=0, column=6)
        self.ec_level_var = tk.StringVar(value=DEFAULT_EC_LEVEL)
        self.ec_combo = ttk.Combobox(
            row, textvariable=self.ec_level_var, values=EC_LEVELS,
            width=3, state="readonly",
        )
        self.ec_combo.grid(row=0, column=7, padx=(4, 10))
        self.ec_combo.bind("<<ComboboxSelected>>", self._update_auto_size_label)
        self.auto_size_var = tk.StringVar()
        ttk.Label(row, textvariable=self.auto_size_var).grid(row=0, column=8, padx=(0, 10))
        self.start_button = ttk.Button(row, text="시작", command=self._on_start)
        self.start_button.grid(row=0, column=9)
        self.stop_button = ttk.Button(row, text="정지", command=self._on_stop, state="disabled")
        self.stop_button.grid(row=0, column=10, padx=(6, 10))
        self.log_button = ttk.Button(row, text="로그 보기", command=self._show_log_popup)
        self.log_button.grid(row=0, column=11, padx=(0, 10))
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(row, textvariable=self.status_var, anchor="e").grid(
            row=0, column=12, sticky="e"
        )
        row.columnconfigure(1, weight=1)
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
            tile_size = min(tile_size, 460)
        return max(140, tile_size)

    def _qr_modules(self) -> int | None:
        """Modules per side of a real frame at the current settings, or None.

        Rendering one throwaway QR is the only honest way to get this: the
        symbol version depends on the payload length, which depends on the
        filename, and on the ECC level. Needs a selected file.
        """
        if self.file_path is None:
            return None
        try:
            encoder, _ = build_encoder(bytes(4096), chunk_size=1024, redundancy=1.0, seed=0)
            payload = pack_packet(encoder.packet(0), self.file_path.name)
            return make_qr_image(
                payload, box_size=1, ec_level=self.ec_level_var.get()
            ).width
        except Exception:
            return None

    def _update_auto_size_label(self, _event=None) -> None:
        try:
            cols = max(1, int(self.cols_var.get()))
        except ValueError:
            cols = 1
        tile_size = self._auto_tile_size(cols)
        rows = 2 if cols >= 5 else 1
        grid_cols = math.ceil(cols / rows)
        modules = self._qr_modules()
        detail = (
            f"전체 {tile_size * grid_cols}×{tile_size * rows}px"
            if modules is None
            else f"{tile_size / modules:.2f}px/모듈"
        )
        self.auto_size_var.set(
            f"자동 크기: 각 {tile_size}px · {grid_cols}열×{rows}행 · {detail}"
        )

    def _build_content_area(self) -> None:
        # Encoding progress: shown only while the file is being scanned
        # (read + hash) into the LT encoder; hidden otherwise.
        self._progress_row = ttk.Frame(self)
        self.progress_label_var = tk.StringVar(value="인코딩 진행률: 0%")
        ttk.Label(self._progress_row, textvariable=self.progress_label_var, anchor="w").pack(
            side="left"
        )
        self._progress_bar = ttk.Progressbar(
            self._progress_row, orient="horizontal", mode="determinate", maximum=1000
        )
        self._progress_bar.pack(side="left", fill="x", expand=True, padx=(8, 0))

        qr_panel = ttk.LabelFrame(self, text="QR 슬라이드쇼", padding=4)
        qr_panel.pack(fill="both", expand=True)

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
        # px/모듈은 파일명 길이에 따라 달라지므로 선택 직후 다시 계산한다.
        self._update_auto_size_label()

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
            ec_level   = self.ec_level_var.get()
        except ValueError:
            messagebox.showerror("입력 오류", "FPS/중복도/동시 QR 수를 숫자로 입력하세요.")
            return
        if fps <= 0 or redundancy < 1.0 or not 1 <= cols <= 10:
            messagebox.showerror(
                "입력 오류",
                "FPS는 양수, 중복도는 1.0 이상, 동시 QR 수는 1~10이어야 합니다.",
            )
            return
        tile_size = self._auto_tile_size(cols)
        rows = 2 if cols >= 5 else 1
        grid_cols = math.ceil(cols / rows)
        target_size = tile_size * grid_cols

        self._run_generation += 1
        run_generation = self._run_generation
        self._cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._show_encode_progress()
        self._log(
            f"[시작] {self.file_path.name} 인코딩 중... "
            f"(redundancy={redundancy}, QR={cols}×{tile_size}px, "
            f"{fps:.1f}fps, 동시 QR={cols})"
        )

        self._worker = threading.Thread(
            target=self._encode_worker,
            args=(
                self.file_path, redundancy, target_size, fps, cols, ec_level,
                run_generation,
            ),
            daemon=True,
        )
        self._worker.start()

    def _on_stop(self) -> None:
        self._run_generation += 1
        self._cancel_event.set()
        self._hide_encode_progress()
        self._clear_qr_display()
        self._log("[정지] 사용자가 전송을 중단했습니다.")
        self._reset_buttons()

    # --- background work (runs off the Tk main thread) ----------------------

    PROGRESS_LOG_STEP = 10  # one log line per 10% of the file scan

    def _encode_worker(
        self, path: Path, redundancy: float, target_size: int, fps: float, cols: int,
        ec_level: str, run_generation: int,
    ) -> None:
        """Scan the file and build only the encoder; packets stream on demand.

        The file is read in 1 MB chunks (hashed + copied to a temp file), so
        progress is reported continuously through ``self._progress_queue``
        and the scan stops promptly when the user hits 정지 (stop_event is
        checked per chunk).  Tkinter is only touched via self.after(...).
        """
        last_logged_percent = -1

        def on_progress(read_bytes: int, total_bytes: int) -> None:
            nonlocal last_logged_percent
            if total_bytes <= 0:
                return
            percent = read_bytes * 100 // total_bytes
            self._progress_queue.put(percent / 100.0)
            if percent // self.PROGRESS_LOG_STEP > last_logged_percent:
                last_logged_percent = percent // self.PROGRESS_LOG_STEP
                self._log(f"[읽기] {percent}%  ({read_bytes}/{total_bytes} 바이트)")

        try:
            encoder, packet_count = build_encoder(
                path,
                chunk_size=1024,
                redundancy=redundancy,
                seed=0,
                progress_callback=on_progress,
                stop_event=self._cancel_event,
            )
        except Exception as exc:  # surface any failure back to the UI thread
            if run_generation == self._run_generation:
                self._progress_queue.put(None)
                if isinstance(exc, InterruptedError):
                    self._log_queue.put("[정지] 인코딩을 중단했습니다.")
                else:
                    self._log_queue.put(f"[오류] 인코딩 실패: {exc}")
                self.after(0, self._reset_buttons_if_current, run_generation)
            return

        if self._cancel_event.is_set() or run_generation != self._run_generation:
            encoder.close()
            return

        self._progress_queue.put(None)
        self._log_queue.put(
            f"[인코더 준비] 청크 K={encoder.total_k}, 전송 패킷 {packet_count}개 "
            f"(SHA-256 {encoder.file_hash[:16]}...)"
        )
        # SenderApp/Tkinter must be touched on the main thread only.
        self.after(
            0, self._start_slideshow,
            encoder, packet_count, target_size, fps, cols, ec_level, run_generation,
        )

    def _start_slideshow(
        self, encoder, packet_count: int, target_size: int, fps: float, cols: int,
        ec_level: str, run_generation: int,
    ) -> None:
        if self._cancel_event.is_set() or run_generation != self._run_generation:
            encoder.close()
            self._hide_encode_progress()
            return

        self._hide_encode_progress()
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
            filename=filename,
            ec_level=ec_level,
        )
        rows = 2 if cols >= 5 else 1
        grid_cols = math.ceil(cols / rows)
        self._log(
            f"[시작] QR 전송 시작 · "
            f"{packet_count}개 패킷 · 프레임당 {cols}개 · "
            f"각 {self._auto_tile_size(cols)}px · {grid_cols}열×{rows}행 · "
            f"{fps:.1f}fps · ECC {ec_level}"
        )

    def _on_frame_displayed(self, frame_number: int, loop_number: int) -> None:
        self.status_var.set(f"재생: frame {frame_number} · loop {loop_number}")
        if frame_number == 1:
            now = time.perf_counter()
            if self._loop_started_at is not None:
                elapsed = now - self._loop_started_at
                self._log(
                    f"loop {loop_number - 1} 완료 {elapsed:.2f}초 → "
                    f"loop {loop_number} 시작"
                )
            filename = self.file_path.name if self.file_path else ""
            if self._loop_started_at is None:
                self._log(f"loop {loop_number} 시작 ({filename})")
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

    # --- encoding progress (main thread only) --------------------------------

    def _show_encode_progress(self) -> None:
        """Show the progress bar and reset it to 0% for a new encode run."""
        self._progress_bar["value"] = 0
        self.progress_label_var.set("인코딩 진행률: 0%")
        self._progress_row.pack(fill="x", pady=(0, 6))

    def _hide_encode_progress(self) -> None:
        self._progress_row.pack_forget()

    def _set_encode_progress(self, fraction: float) -> None:
        fraction = min(1.0, max(0.0, fraction))
        self._progress_bar["value"] = int(fraction * 1000)
        self.progress_label_var.set(f"인코딩 진행률: {fraction * 100:.0f}%")

    # --- log panel -----------------------------------------------------------

    def _log(self, message: str) -> None:
        self._log_queue.put(message)

    def _show_log_popup(self) -> None:
        if self._log_window is not None and self._log_window.winfo_exists():
            self._log_window.deiconify()
            self._log_window.lift()
            self._log_window.focus_force()
            return

        window = tk.Toplevel(self)
        window.title("QR 전송 로그")
        window.geometry("760x420")
        window.minsize(520, 260)
        window.transient(self.winfo_toplevel())
        window.protocol("WM_DELETE_WINDOW", self._close_log_popup)

        text = ScrolledText(window, wrap="word", state="normal")
        text.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        if self._log_history:
            text.insert("end", "\n".join(self._log_history) + "\n")
            text.see("end")
        text.configure(state="disabled")

        controls = ttk.Frame(window)
        controls.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(controls, text="로그 지우기", command=self._clear_log_history).pack(side="left")
        ttk.Button(controls, text="닫기", command=self._close_log_popup).pack(side="right")
        self._log_window = window
        self._log_text = text

    def _close_log_popup(self) -> None:
        if self._log_window is not None:
            try:
                self._log_window.destroy()
            except tk.TclError:
                pass
        self._log_window = None
        self._log_text = None

    def _clear_log_history(self) -> None:
        self._log_history.clear()
        if self._log_text is not None and self._log_text.winfo_exists():
            self._log_text.configure(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.configure(state="disabled")

    def _append_popup_log(self, message: str) -> None:
        if self._log_text is None or not self._log_text.winfo_exists():
            return
        self._log_text.configure(state="normal")
        self._log_text.insert("end", message + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        latest = None
        try:
            while True:
                latest = self._log_queue.get_nowait()
                self._log_history.append(latest)
                if len(self._log_history) > self.MAX_LOG_LINES:
                    del self._log_history[: len(self._log_history) - self.MAX_LOG_LINES]
                self._append_popup_log(latest)
        except queue.Empty:
            pass
        # Drain the progress queue; only the latest value is displayed, so a
        # fast scan never floods the UI — the bar just catches up to 100%.
        latest_progress: Optional[float] = None
        try:
            while True:
                latest_progress = self._progress_queue.get_nowait()
        except queue.Empty:
            pass
        if latest_progress is not None:
            self._set_encode_progress(latest_progress)
        if latest is not None:
            self.status_var.set(latest)
        self.after(100, self._poll_log_queue)

    def _reset_buttons(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _reset_buttons_if_current(self, run_generation: int) -> None:
        if run_generation == self._run_generation:
            self._reset_buttons()
