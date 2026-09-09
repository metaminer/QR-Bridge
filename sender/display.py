"""QR Stream Transfer - sender entry point.

Tkinter slideshow that displays the QR frames produced by ``sender/encode.py``
at a fixed frame rate, looping forever so the receiver can capture as many
passes as it needs to recover from frame loss.

CLI:
    python sender/display.py --file <path> --fps 15 --redundancy 1.5
    (or: python -m sender.display --file <path> --fps 15 --redundancy 1.5)

Wire format for one QR frame's binary payload is documented in
``docs/packet_spec.md``.
"""

from __future__ import annotations

import argparse
import math
import sys
import tkinter as tk
import time
from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageTk

from sender.encode import build_encoder, make_qr_image
from common.lt_wrapper import LTEncoder
from common.qr_wire import pack_packet


def _make_compact_qr(payload: bytes) -> Image.Image:
    """Render a QR matrix in compact 1-bit form for workers and the cache."""
    return make_qr_image(payload, box_size=1).convert("1")


class SenderApp:
    """QR slideshow embeddable in a window or any regular Tk container."""

    QR_CACHE_LIMIT_BYTES = 64 * 1024 * 1024

    def __init__(
        self,
        parent: tk.Misc,
        encoder: LTEncoder,
        packet_count: int,
        fps: float,
        target_size: int = 760,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        filename: str = "",
        cols: int = 1,
    ) -> None:
        if packet_count <= 0:
            raise ValueError("packet_count must be positive")
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.parent = parent
        self.encoder = encoder
        self.packet_count = packet_count
        self.fps = fps
        self.target_size = target_size
        self.progress_callback = progress_callback
        self.filename = filename
        self.cols = max(1, cols)
        self.rows = 2 if self.cols >= 5 else 1
        self.grid_cols = math.ceil(self.cols / self.rows)
        # target_size is the total QR grid width. Each tile is square.
        self.tile_size = max(1, target_size // self.grid_cols)
        self.play_index = 0
        self.loop_count = 0
        self._running = True
        self._after_id: str | None = None
        self._escape_binding: str | None = None
        self._executor: ProcessPoolExecutor | None = None
        self._render_futures: list[Future] = []
        self._render_indices: list[int] = []
        self._render_started = 0.0
        self._qr_cache: OrderedDict[int, Image.Image] = OrderedDict()
        self._qr_cache_bytes = 0
        if self.cols > 1:
            try:
                self._executor = ProcessPoolExecutor(max_workers=self.cols)
            except (OSError, PermissionError):
                # Restricted runtimes may disallow child processes. Rendering
                # still works through the synchronous fallback below.
                self._executor = None

        self._tile_frame: tk.Frame | None = None
        self._tiles: list[tk.Canvas] = []
        if self.cols == 1:
            tile = tk.Canvas(
                parent, width=self.tile_size, height=self.tile_size,
                bg="white", highlightthickness=0,
            )
            tile.pack(fill="both", expand=True)
            self._tiles.append(tile)
        else:
            self._tile_frame = tk.Frame(parent, bg="white")
            self._tile_frame.pack(fill="both", expand=True)
            for row in range(self.rows):
                self._tile_frame.grid_rowconfigure(row, weight=1, uniform="qr_row")
            for column in range(self.grid_cols):
                self._tile_frame.grid_columnconfigure(column, weight=1, uniform="qr_tile")
            for tile_index in range(self.cols):
                row, column = divmod(tile_index, self.grid_cols)
                tile = tk.Canvas(
                    self._tile_frame,
                    width=self.tile_size,
                    height=self.tile_size,
                    bg="white",
                    highlightthickness=0,
                )
                tile.grid(row=row, column=column, sticky="nsew")
                self._tiles.append(tile)

        self.canvas = self._tiles[0]
        self._current_images: list[ImageTk.PhotoImage | None] = [None] * self.cols
        self._image_items = [tile.create_image(0, 0, anchor="center") for tile in self._tiles]
        self._overlays = [
            tile.create_text(0, 0, text="", fill="red", font=("Consolas", 12, "bold"))
            for tile in self._tiles
        ]
        # First-tile aliases preserve compatibility for existing callers.
        self.image_item = self._image_items[0]
        self.overlay = self._overlays[0]

        if isinstance(parent, (tk.Tk, tk.Toplevel)):
            self._escape_binding = parent.bind("<Escape>", self._close_window, add="+")
        if isinstance(parent, tk.Toplevel):
            parent.geometry(f"{target_size}x{self.tile_size * self.rows + 42}")
        for tile in self._tiles:
            tile.bind("<Configure>", self._position_items, add="+")
        self._position_items()
        for tile, overlay in zip(self._tiles, self._overlays):
            tile.itemconfigure(overlay, text="QR 스트림 시작 중...")
        self._after_id = self.canvas.after(10, self._play_step)

    def _close_window(self, _event=None) -> None:
        self.stop()
        self.parent.destroy()

    def stop(self, *, clear: bool = True) -> None:
        """Stop scheduled playback and optionally clear the canvas."""
        if not self._running:
            return
        self._running = False
        if self._after_id is not None:
            try:
                self.canvas.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._escape_binding and isinstance(self.parent, (tk.Tk, tk.Toplevel)):
            try:
                self.parent.unbind("<Escape>", self._escape_binding)
            except tk.TclError:
                pass
            self._escape_binding = None
        self._current_images = [None] * self.cols
        self._qr_cache.clear()
        self._qr_cache_bytes = 0
        for future in self._render_futures:
            future.cancel()
        self._render_futures.clear()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        if clear:
            for tile in self._tiles:
                try:
                    tile.delete("all")
                except tk.TclError:
                    pass

    def _position_items(self, _event=None) -> None:
        for tile, image_item, overlay in zip(self._tiles, self._image_items, self._overlays):
            width = max(1, tile.winfo_width())
            height = max(1, tile.winfo_height())
            tile.coords(image_item, width // 2, height // 2)
            tile.coords(overlay, width // 2, max(12, height - 18))

    def _fit_to_canvas(self, img: Image.Image, tile_index: int = 0) -> Image.Image:
        canvas = self._tiles[tile_index]
        canvas_width = max(1, canvas.winfo_width())
        canvas_height = max(1, canvas.winfo_height())
        available = max(1, min(canvas_width, canvas_height, self.tile_size) - 4)
        w, h = img.size
        scale = max(1, available // max(w, h))
        return img.resize((w * scale, h * scale), Image.NEAREST)

    def _play_step(self) -> None:
        if not self._running or not self.canvas.winfo_exists():
            return
        if self.loop_count == 0:
            self.loop_count = 1
        packet_indices = [
            (self.play_index + tile_index) % self.packet_count
            for tile_index in range(self.cols)
        ]
        payloads = [
            pack_packet(self.encoder.packet(packet_index), self.filename)
            for packet_index in packet_indices
        ]
        step_started = time.perf_counter()

        cached_images = [self._qr_cache.get(index) for index in packet_indices]
        if all(image is not None for image in cached_images):
            for index in packet_indices:
                self._qr_cache.move_to_end(index)
            self._display_rendered_batch(
                [image for image in cached_images if image is not None],
                packet_indices,
                step_started,
            )
            return

        if self._executor is not None:
            try:
                self._render_indices = packet_indices
                self._render_started = step_started
                self._render_futures = [
                    self._executor.submit(_make_compact_qr, payload)
                    for payload in payloads
                ]
                self._after_id = self.canvas.after(2, self._poll_render_batch)
                return
            except (OSError, RuntimeError):
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
                self._render_futures.clear()

        images = [_make_compact_qr(payload) for payload in payloads]
        self._display_rendered_batch(images, packet_indices, step_started)

    def _poll_render_batch(self) -> None:
        if not self._running:
            return
        if not all(future.done() for future in self._render_futures):
            self._after_id = self.canvas.after(2, self._poll_render_batch)
            return
        try:
            images = [future.result() for future in self._render_futures]
        except Exception:
            # Preserve transmission if a worker fails; subsequent frames use
            # the proven single-process path.
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
            images = [
                _make_compact_qr(
                    pack_packet(self.encoder.packet(packet_index), self.filename)
                )
                for packet_index in self._render_indices
            ]
        packet_indices = self._render_indices
        step_started = self._render_started
        self._render_futures = []
        self._render_indices = []
        self._display_rendered_batch(images, packet_indices, step_started)

    def _display_rendered_batch(
        self,
        images: list[Image.Image],
        packet_indices: list[int],
        step_started: float,
    ) -> None:
        if not self._running:
            return
        displayed_loop = self.loop_count
        for tile_index, (packet_index, raw_image) in enumerate(zip(packet_indices, images)):
            self._cache_qr(packet_index, raw_image)
            image = self._fit_to_canvas(
                raw_image,
                tile_index=tile_index,
            )
            photo = ImageTk.PhotoImage(image, master=self._tiles[tile_index])
            self._current_images[tile_index] = photo
            self._tiles[tile_index].itemconfigure(
                self._image_items[tile_index], image=photo
            )
            self._tiles[tile_index].itemconfigure(
                self._overlays[tile_index],
                text="",
            )
        self._position_items()
        if self.progress_callback:
            self.progress_callback(self.play_index + 1, displayed_loop)
        next_index = self.play_index + self.cols
        if next_index >= self.packet_count:
            self.play_index = 0
            self.loop_count += 1
        else:
            self.play_index = next_index
        # QR generation happens synchronously in this callback. Subtract that
        # work from the frame budget; otherwise N tiles add their render time
        # on top of every interval and visibly slow the slideshow down.
        render_ms = (time.perf_counter() - step_started) * 1000
        delay_ms = max(1, round(1000 / self.fps - render_ms))
        self._after_id = self.canvas.after(delay_ms, self._play_step)

    def _cache_qr(self, packet_index: int, image: Image.Image) -> None:
        """Keep compact QR matrices in a bounded LRU cache across loops."""
        compact = image if image.mode == "1" else image.convert("1")
        cost = ((compact.width + 7) // 8) * compact.height
        previous = self._qr_cache.pop(packet_index, None)
        if previous is not None:
            self._qr_cache_bytes -= ((previous.width + 7) // 8) * previous.height
        self._qr_cache[packet_index] = compact
        self._qr_cache_bytes += cost
        while self._qr_cache_bytes > self.QR_CACHE_LIMIT_BYTES and self._qr_cache:
            _, evicted = self._qr_cache.popitem(last=False)
            self._qr_cache_bytes -= ((evicted.width + 7) // 8) * evicted.height


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QR Stream Transfer - sender")
    parser.add_argument("--file", required=True, help="전송할 파일 경로")
    parser.add_argument("--fps", type=float, default=15.0, help="슬라이드쇼 프레임 속도 (기본 15)")
    parser.add_argument("--redundancy", type=float, default=1.5, help="LT 패킷 중복률 (기본 1.5)")
    parser.add_argument("--chunk-size", type=int, default=1024, help="청크 크기 바이트 (기본 1024)")
    parser.add_argument("--seed", type=int, default=0, help="LT 인코딩 시드")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    path = Path(args.file)
    if not path.is_file():
        print(f"파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 1
    if args.fps <= 0:
        print("--fps는 0보다 커야 합니다", file=sys.stderr)
        return 1
    if args.redundancy < 1.0:
        print("--redundancy는 1.0 이상이어야 합니다", file=sys.stderr)
        return 1
    if args.chunk_size <= 0 or args.chunk_size > 1024:
        print("--chunk-size는 1~1024 사이여야 합니다 (QR 프레임당 용량 제한)", file=sys.stderr)
        return 1

    data = path.read_bytes()
    encoder, packet_count = build_encoder(data, args.chunk_size, args.redundancy, args.seed)

    print(f"파일: {path.name} ({len(data)} bytes)")
    print(f"청크 수(K): {encoder.total_k}, 전송 프레임 수: {packet_count} (redundancy={args.redundancy})")
    print(f"SHA-256: {encoder.file_hash}")
    print("Tkinter 창에서 ESC를 누르면 종료됩니다.")

    root = tk.Tk()
    root.title(f"QR Stream Transfer - {path.name}")
    root.resizable(False, False)
    SenderApp(root, encoder, packet_count, args.fps, filename=path.name)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
