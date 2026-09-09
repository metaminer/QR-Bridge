"""Receiver tab: decode a captured QR video without blocking tkinter."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from receiver.decode_video import DecodeResult, decode_video
from ui.widgets import FileSelectionRow, ScrollLogPanel


class ReceiverTab(ttk.Frame):
    POLL_MS = 50

    def __init__(self, parent, **kwargs) -> None:
        super().__init__(parent, padding=12, **kwargs)
        self.video_var = tk.StringVar(self)
        self.output_var = tk.StringVar(self)
        self.progress_text = tk.StringVar(self, "수신 패킷: 0 / 목표 확인 중")
        self.hash_text = tk.StringVar(self, "SHA-256: 대기 중")
        # Bound frame-by-frame progress updates so a fast decoder cannot grow
        # the UI queue without limit while Tk is busy repainting.
        self._events: queue.Queue = queue.Queue(maxsize=256)
        self._worker: threading.Thread | None = None

        self.video_row = FileSelectionRow(
            self, "촬영 영상", variable=self.video_var,
            filetypes=(("동영상", "*.mp4 *.mov *.avi *.mkv"), ("모든 파일", "*.*")),
            on_selected=self._suggest_output,
        )
        self.video_row.grid(row=0, column=0, sticky="ew")
        self.output_row = FileSelectionRow(
            self, "복원 위치 (선택)", variable=self.output_var, mode="save",
        )
        self.output_row.grid(row=1, column=0, pady=(8, 0), sticky="ew")

        progress_frame = ttk.Frame(self)
        progress_frame.grid(row=2, column=0, pady=(14, 0), sticky="ew")
        self.progress = ttk.Progressbar(progress_frame, mode="indeterminate", maximum=1)
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.progress_text).grid(row=1, column=0, pady=(4, 0), sticky="w")
        progress_frame.columnconfigure(0, weight=1)

        self.hash_label = ttk.Label(self, textvariable=self.hash_text)
        self.hash_label.grid(row=3, column=0, pady=(10, 0), sticky="w")
        self.start_button = ttk.Button(self, text="디코딩 시작", command=self.start_decode)
        self.start_button.grid(row=4, column=0, pady=(12, 8), sticky="e")
        self.log = ScrollLogPanel(self, height=12)
        self.log.grid(row=5, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

    def _suggest_output(self, video: str) -> None:
        # 원본 파일명은 QR 프레임 안에 들어있으므로(docs/packet_spec.md), 여기서는
        # 영상이 있는 폴더만 제안한다 — 실제 파일명/확장자는 decode_video()가
        # 자동으로 복원한다. 사용자가 직접 파일명을 지정하고 싶으면 덮어쓰면 된다.
        if not self.output_var.get().strip():
            self.output_var.set(str(Path(video).resolve().parent))

    def start_decode(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        video = self.video_var.get().strip()
        output = self.output_var.get().strip() or None
        if not video:
            messagebox.showerror("입력 필요", "촬영 영상을 선택하세요.", parent=self)
            return
        self.log.clear()
        self.hash_text.set("SHA-256: 검사 대기 중")
        self.progress.configure(mode="indeterminate", maximum=1, value=0)
        self.progress.start(12)
        self.progress_text.set("수신 패킷: 0 / 목표 확인 중")
        self._set_running(True)
        self._worker = threading.Thread(target=self._decode_worker, args=(video, output), daemon=True)
        self._worker.start()
        self.after(self.POLL_MS, self._drain_events)

    def _decode_worker(self, video: str, output: str) -> None:
        def progress(received: int, target: int | None, decoded: int, frames: int) -> None:
            try:
                self._events.put_nowait(("progress", received, target, decoded, frames))
            except queue.Full:
                pass

        try:
            result = decode_video(
                video, output, progress_callback=progress,
                log_callback=lambda message: self._events.put(("log", message)),
            )
            self._events.put(("done", result))
        except Exception as error:
            self._events.put(("error", str(error)))

    def _drain_events(self) -> None:
        terminal = False
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "progress":
                _, received, target, decoded, frames = event
                if target:
                    if str(self.progress.cget("mode")) != "determinate":
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                    self.progress.configure(maximum=target, value=min(received, target))
                    self.progress_text.set(f"수신 패킷: {received} / {target}  ·  복원 블록: {decoded}  ·  프레임: {frames}")
                else:
                    self.progress_text.set(f"수신 패킷: {received} / 목표 확인 중  ·  프레임: {frames}")
            elif kind == "log":
                self.log.append(event[1])
            elif kind == "done":
                self._show_result(event[1])
                terminal = True
            elif kind == "error":
                self.progress.stop()
                self.hash_text.set("SHA-256: 확인하지 못함")
                self.log.append(f"오류: {event[1]}")
                self._set_running(False)
                terminal = True
        if not terminal and self._worker and self._worker.is_alive():
            self.after(self.POLL_MS, self._drain_events)

    def _show_result(self, result: DecodeResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=max(1, result.target_packets), value=result.target_packets)
        status = "통과" if result.hash_ok else "실패"
        self.hash_text.set(f"SHA-256 {status}: {result.actual_hash}")
        self.log.append(f"원본 파일명: {result.original_filename or '(알 수 없음)'}")
        self.log.append(f"완료: {result.output_path} ({result.elapsed:.1f}초)")
        self.log.append(f"SHA-256 기대값: {result.expected_hash}")
        self.log.append(f"SHA-256 실제값: {result.actual_hash}")
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.video_row.set_enabled(not running)
        self.output_row.set_enabled(not running)
        self.start_button.configure(state="disabled" if running else "normal")
