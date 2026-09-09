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

import zxingcpp
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

    Uses ``zxing-cpp`` (native C++ ZXing bindings, prebuilt Windows wheel, no
    extra runtime dependency) instead of the pure-Python ``segno``/``qrcode``:
    measured ~4.8ms per QR here versus segno's ~95ms (~20x) and qrcode's
    ~203ms (~42x), verified correct via a real QR-image -> pyzbar decode
    round trip. See docs/packet_spec.md for the full benchmark writeup.
    """
    barcode = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.QRCode, ecLevel="M")
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


def make_frame_image(encoder: LTEncoder, seq: int, filename: str = "") -> Image.Image:
    """Build packet *seq* and render it straight to a QR image."""
    packet = encoder.packet(seq)
    return make_qr_image(pack_packet(packet, filename))
