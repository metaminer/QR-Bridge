"""Binary wire format for a single QR frame's payload — shared by sender and receiver.

See docs/packet_spec.md for the full spec. This module is the single source of
truth for both sides so the two can never drift out of sync (they did once:
the sender whitened payloads before encoding but the receiver didn't reverse
it, which broke every decode).

QRT3 puts the whitened bytes straight into the QR code. QRT2 base64-encoded
them first; ``unpack_packet`` still reads those, so a receiver on this version
can decode video captured from an older sender.
"""

from __future__ import annotations

import base64
import random
import struct
from functools import lru_cache
from typing import Tuple

from common.lt_wrapper import Packet

# QRT3 drops QRT2's base64 armour and stores the whitened bytes raw (see
# pack_packet). The layout behind the magic is otherwise identical, so one
# parser reads both. The original filename is repeated in every frame — not
# just packet 0 — so it survives frame loss the same way the LT data does.
# QRT1 (no filename field) is not accepted.
MAGIC = b"QRT3"
LEGACY_MAGIC = b"QRT2"
# big-endian: magic(4s), seq(I), seed(I), total_k(I), file_hash(32s raw), filename_len(H)
_HEADER1 = struct.Struct(">4sIII32sH")
# big-endian: data_len(H) — follows the variable-length filename bytes
_HEADER2 = struct.Struct(">H")

# LT chunks are zero-padded and packets can XOR down to long runs of identical
# bytes; some `qrcode` versions raise ValueError("glog(0)") while Reed-Solomon
# encoding such degenerate/repetitive payloads. XOR-whitening with a fixed
# deterministic keystream before encoding (and reversing it after decoding)
# avoids the pathological input without changing the logical packet content.
_WHITENING_SEED = 0x51515151


@lru_cache(maxsize=8)
def _whitening_stream(length: int) -> bytes:
    rng = random.Random(_WHITENING_SEED)
    return bytes(rng.getrandbits(8) for _ in range(length))


def whiten(payload: bytes) -> bytes:
    """XOR *payload* with the fixed keystream. Involutive: whiten(whiten(x)) == x."""
    stream = _whitening_stream(len(payload))
    return bytes(a ^ b for a, b in zip(payload, stream))


# The keystream is a prefix of one deterministic sequence, so a whitened
# payload always starts with the same 4 bytes for a given magic. That makes
# the format self-identifying without un-whitening first: QRT3 frames start
# with these bytes, QRT2 frames start with base64 ASCII.
_WHITENED_MAGIC = whiten(MAGIC)


def pack_packet(packet: Packet, filename: str = "") -> bytes:
    """Serialize an LT packet dict + original filename into a QR payload (QRT3).

    The whitened bytes go into the QR code as-is, in byte mode. QRT2 wrapped
    them in base64 first, because pyzbar/zbar silently corrupts byte-mode QR
    data containing bytes >= 0x80: its decode path appears to run them through
    a Latin-1 -> UTF-8 transcode, so a byte like 0x99 comes back as 0xC2 0x99
    and desyncs everything after it. Both sides now use zxing-cpp (and ML Kit
    on Android), which return byte-mode payloads unchanged — verified with a
    real encode -> decode round trip over these exact packets. Dropping the
    armour cuts the payload by a quarter (1448 -> 1086 bytes at chunk_size
    1024), which shrinks the QR from 149 to 133 modules at ECC M: the camera
    gets ~12% more pixels per module for the same tile size on screen.
    """
    file_hash = bytes.fromhex(str(packet["file_hash"]))
    data = packet["data"]
    assert isinstance(data, bytes)
    name_bytes = filename.encode("utf-8")
    if len(name_bytes) > 0xFFFF:
        raise ValueError("filename too long to fit in a QR frame")
    header1 = _HEADER1.pack(
        MAGIC, packet["seq"], packet["seed"], packet["total_k"], file_hash, len(name_bytes)
    )
    header2 = _HEADER2.pack(len(data))
    return whiten(header1 + name_bytes + header2 + data)


def unpack_packet(raw_payload: bytes) -> Tuple[Packet, str]:
    """Parse a QR-decoded payload into (packet, filename). Reads QRT3 and QRT2."""
    payload = whiten(_strip_armour(raw_payload))
    if len(payload) < _HEADER1.size:
        raise ValueError("payload too short")
    magic, seq, seed, total_k, file_hash, name_len = _HEADER1.unpack_from(payload)
    if magic not in (MAGIC, LEGACY_MAGIC):
        raise ValueError(f"unknown packet magic: {magic!r}")
    offset = _HEADER1.size
    name_bytes = payload[offset : offset + name_len]
    if len(name_bytes) != name_len:
        raise ValueError("truncated filename field")
    offset += name_len
    if len(payload) < offset + _HEADER2.size:
        raise ValueError("payload too short for data length field")
    (data_len,) = _HEADER2.unpack_from(payload, offset)
    offset += _HEADER2.size
    data = payload[offset : offset + data_len]
    if len(data) != data_len:
        raise ValueError("truncated packet payload")
    try:
        filename = name_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("filename field is not valid utf-8") from exc
    packet: Packet = {
        "seq": seq,
        "seed": seed,
        "data": data,
        "total_k": total_k,
        "file_hash": file_hash.hex(),
    }
    return packet, filename


def _strip_armour(raw_payload: bytes) -> bytes:
    """Return the whitened bytes, undoing QRT2's base64 layer if present.

    A QRT3 frame starts with the fixed whitened magic; a QRT2 frame is base64
    ASCII and cannot, so the prefix alone tells the two apart.
    """
    if raw_payload[: len(_WHITENED_MAGIC)] == _WHITENED_MAGIC:
        return raw_payload
    try:
        return base64.b64decode(raw_payload, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("payload is neither a QRT3 frame nor valid base64") from exc
