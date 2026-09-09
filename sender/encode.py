"""Sender pipeline: file bytes -> LT packets -> QR frame images.

Splits a file into LT-encoded packets (via ``common.lt_wrapper``) and renders
each one as a QR-code image (via ``common.qr_wire`` for the wire format).
``sender/display.py`` drives this module and shows the frames on screen.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import List, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import segno
from PIL import Image

from common.lt_wrapper import LTEncoder, Packet
from common.qr_wire import pack_packet

PathLike = Union[str, Path]


def build_encoder(data: bytes, chunk_size: int, redundancy: float, seed: int) -> Tuple[LTEncoder, int]:
    """Wrap *data* in an LTEncoder and compute how many frames to send."""
    encoder = LTEncoder(data, chunk_size=chunk_size, seed=seed)
    packet_count = max(encoder.total_k, math.ceil(encoder.total_k * redundancy))
    return encoder, packet_count


def encode_file(
    path: PathLike, chunk_size: int = 1024, redundancy: float = 1.5, seed: int = 0
) -> Tuple[LTEncoder, List[Packet], str]:
    """Read *path* and LT-encode it into the full ordered list of packets to send.

    Returns ``(encoder, packets, filename)`` — ``encoder`` for metadata
    (``total_k``, ``file_hash``), ``packets`` ready to hand to
    ``common.qr_wire.pack_packet`` one at a time, and ``filename`` (the
    original file's base name, e.g. ``"report.pdf"``) to pass alongside each
    packet so the receiver can restore the file under its original name and
    extension.
    """
    path = Path(path)
    data = path.read_bytes()
    encoder, packet_count = build_encoder(data, chunk_size, redundancy, seed)
    packets = encoder.packets(packet_count)
    return encoder, packets, path.name


def make_qr_image(payload: bytes, box_size: int = 8, border: int = 4) -> Image.Image:
    """Render *payload* as a QR code image.

    Uses ``segno`` rather than ``qrcode``: for this project's payload sizes
    (~1.4KB base64, QR version ~30+) it measured ~36% faster end to end
    (encode + rasterize: 202.9ms -> 129.9ms with a numpy raster step) and a
    further ~27% faster (129.9ms -> 95.0ms) once the raster step was
    rewritten below to pack pixels straight into a bytes buffer instead of
    round-tripping through numpy — a ~2.1x total speedup over the previous
    ``qrcode``-based implementation, with no new hard dependency. See
    docs/packet_spec.md for the full benchmark writeup.
    """
    qr = segno.make(payload, error="m")
    matrix = qr.matrix
    size = len(matrix)
    full = size + border * 2
    buf = bytearray(b"\xff" * (full * full))
    for row_index, row in enumerate(matrix):
        base = (row_index + border) * full + border
        for col_index, is_dark in enumerate(row):
            if is_dark:
                buf[base + col_index] = 0
    img = Image.frombytes("L", (full, full), bytes(buf)).convert("RGB")
    if box_size != 1:
        img = img.resize((full * box_size, full * box_size), Image.NEAREST)
    return img


def make_frame_image(encoder: LTEncoder, seq: int, filename: str = "") -> Image.Image:
    """Build packet *seq* and render it straight to a QR image."""
    packet = encoder.packet(seq)
    return make_qr_image(pack_packet(packet, filename))
