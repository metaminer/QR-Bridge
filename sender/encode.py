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

import qrcode
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
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=border)
    # The wire payload is already one Base64 byte stream. Segment analysis
    # cannot improve it and adds measurable work for every live frame.
    qr.add_data(payload, optimize=0)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def make_frame_image(encoder: LTEncoder, seq: int, filename: str = "") -> Image.Image:
    """Build packet *seq* and render it straight to a QR image."""
    packet = encoder.packet(seq)
    return make_qr_image(pack_packet(packet, filename))
