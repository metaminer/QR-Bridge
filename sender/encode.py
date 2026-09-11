"""Sender pipeline: file bytes -> LT packets -> QR frame images.

Splits a file into LT-encoded packets (via ``common.lt_wrapper``) and renders
each one as a QR-code image (via ``common.qr_wire`` for the wire format).
``sender/display.py`` drives this module and shows the frames on screen.
"""

from __future__ import annotations

import math
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import zxingcpp
from PIL import Image

from common.lt_wrapper import LTEncoder, Packet
from common.qr_wire import pack_packet

PathLike = Union[str, Path]
EncoderInput = Union[bytes, str, Path]


def build_encoder(
    data: EncoderInput,
    chunk_size: int,
    redundancy: float,
    seed: int,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[LTEncoder, int]:
    """Build an LT encoder from bytes or a file path and size the stream.

    For file inputs the whole file is scanned (hash + temp copy) before the
    encoder can report its size.  *progress_callback*, when given, is called
    with ``(bytes_scanned, total_bytes)`` as that scan advances so callers
    can show progress; *stop_event*, when set, aborts the scan.  Both are
    ignored for in-memory ``bytes`` input, where nothing is read.
    """
    if isinstance(data, bytes):
        encoder = LTEncoder(data, chunk_size=chunk_size, seed=seed)
    else:
        encoder = LTEncoder.from_file(
            data,
            chunk_size=chunk_size,
            seed=seed,
            progress_callback=progress_callback,
            stop_event=stop_event,
        )
    try:
        packet_count = max(encoder.total_k, math.ceil(encoder.total_k * redundancy))
    except Exception:
        encoder.close()
        raise
    return encoder, packet_count


def encode_file(
    path: PathLike,
    chunk_size: int = 1024,
    redundancy: float = 1.5,
    seed: int = 0,
    lazy: bool = False,
) -> Tuple[LTEncoder, Union[List[Packet], Iterator[Packet]], str]:
    """Read *path* and LT-encode it into the full ordered list of packets to send.

    Returns ``(encoder, packets, filename)`` — ``encoder`` for metadata
    (``total_k``, ``file_hash``), ``packets`` ready to hand to
    ``common.qr_wire.pack_packet`` one at a time, and ``filename`` (the
    original file's base name, e.g. ``"report.pdf"``) to pass alongside each
    packet so the receiver can restore the file under its original name and
    extension.

    By default *packets* is a ``List[Packet]`` containing every packet eagerly.
    For large files this can use ``packet_count * chunk_size`` bytes of memory
    at once.  Pass ``lazy=True`` to receive an ``Iterator[Packet]`` instead;
    packets are produced one at a time via ``encoder.packet(seq)`` and the
    source file is closed as soon as the iterator is exhausted (or as soon as
    it is garbage-collected / ``.close()``-ed).  When ``lazy=True`` the caller
    must consume or close the iterator before the returned *encoder* is
    discarded — the encoder's underlying file handle is owned by the iterator's
    cleanup, not by ``encode_file`` itself.
    """
    path = Path(path)
    encoder, packet_count = build_encoder(path, chunk_size, redundancy, seed)
    if lazy:
        def _gen() -> Iterator[Packet]:
            try:
                for seq in range(packet_count):
                    yield encoder.packet(seq)
            finally:
                encoder.close()

        return encoder, _gen(), path.name
    try:
        packets = encoder.packets(packet_count)
    finally:
        encoder.close()
    return encoder, packets, path.name


# Reed-Solomon overhead per level. Lower ECC means a smaller symbol for the
# same payload, which means more camera pixels per module — and with an LT
# fountain code underneath, a frame the camera cannot read costs one frame,
# not the transfer, so the usual reason to buy heavy ECC is already covered.
# Measured module counts for a chunk_size=1024 QRT3 frame (incl. quiet zone):
# L 117, M 133, Q 153. Which level actually wins depends on the capture —
# smaller symbol versus less correction — so it is a knob, not a constant.
EC_LEVELS = ("L", "M", "Q")
DEFAULT_EC_LEVEL = "M"


def make_qr_image(
    payload: bytes,
    box_size: int = 8,
    border: int = 4,
    ec_level: str = DEFAULT_EC_LEVEL,
) -> Image.Image:
    """Render *payload* as a QR code image at error-correction *ec_level*.

    Uses ``zxing-cpp`` (native C++ ZXing bindings, prebuilt Windows wheel, no
    extra runtime dependency) instead of the pure-Python ``segno``/``qrcode``:
    measured ~4.8ms per QR here versus segno's ~95ms (~20x) and qrcode's
    ~203ms (~42x), verified correct via a real QR-image -> pyzbar decode
    round trip. See docs/packet_spec.md for the full benchmark writeup.
    """
    if ec_level not in EC_LEVELS:
        raise ValueError(f"ec_level must be one of {EC_LEVELS}, got {ec_level!r}")
    barcode = zxingcpp.create_barcode(
        payload, zxingcpp.BarcodeFormat.QRCode, ecLevel=ec_level
    )
    if not barcode.valid:
        raise ValueError("zxing-cpp failed to encode this payload as a QR code")
    if border == 4:
        # 4 modules is zxing-cpp's own default quiet zone, so this is the
        # common case (every caller in this codebase uses the default
        # border=4) and skips the manual padding pass below entirely.
        raw = barcode.to_image(scale=1, add_quiet_zones=True)
        img = Image.frombytes("L", (raw.shape[1], raw.shape[0]), bytes(raw)).convert("RGB")
    else:
        raw = barcode.to_image(scale=1, add_quiet_zones=False)
        size = raw.shape[0]
        full = size + border * 2
        buf = bytearray(b"\xff" * (full * full))
        raw_bytes = bytes(raw)
        for row in range(size):
            src_base = row * size
            dst_base = (row + border) * full + border
            buf[dst_base : dst_base + size] = raw_bytes[src_base : src_base + size]
        img = Image.frombytes("L", (full, full), bytes(buf)).convert("RGB")
    if box_size != 1:
        img = img.resize((img.width * box_size, img.height * box_size), Image.NEAREST)
    return img


def make_frame_image(
    encoder: LTEncoder, seq: int, filename: str = "", ec_level: str = DEFAULT_EC_LEVEL
) -> Image.Image:
    """Build packet *seq* and render it straight to a QR image."""
    packet = encoder.packet(seq)
    return make_qr_image(pack_packet(packet, filename), ec_level=ec_level)
