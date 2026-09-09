"""Binary wire format for a single QR frame's payload — shared by sender and receiver.

See docs/packet_spec.md for the full spec. This module is the single source of
truth for both sides so the two can never drift out of sync (they did once:
the sender whitened payloads before encoding but the receiver didn't reverse
it, which broke every decode).
"""

from __future__ import annotations

import base64
import random
import struct
from functools import lru_cache
from typing import Tuple

from common.lt_wrapper import Packet

# QRT2 adds the filename field (QRT1 packets had no filename and are no
# longer accepted). The original filename is repeated in every frame — not
# just packet 0 — so it survives frame loss the same way the LT data does.
MAGIC = b"QRT2"
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


def pack_packet(packet: Packet, filename: str = "") -> bytes:
    """Serialize an LT packet dict + original filename into a QR-safe payload.

    The whitened bytes are base64-encoded before they go into the QR code.
    This is not optional: raw binary QR payloads containing bytes >= 0x80 get
    silently corrupted by pyzbar/zbar's decode path, which appears to run
    byte-mode QR data through a Latin-1 -> UTF-8 transcode step. A byte like
    0x99 comes back as the two bytes 0xC2 0x99 (the UTF-8 encoding of U+0099),
    desyncing every following byte in the packet. This was confirmed with a
    direct qrcode-encode -> pyzbar-decode round trip: it corrupted 100% of
    payloads containing high-bit bytes, at every size tested, regardless of
    the XOR whitening above. Base64 keeps the QR payload pure 7-bit ASCII, so
    zbar has nothing to "reinterpret" and the bytes come back unchanged.
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
    whitened = whiten(header1 + name_bytes + header2 + data)
    return base64.b64encode(whitened)


def unpack_packet(raw_payload: bytes) -> Tuple[Packet, str]:
    """Reverse base64 + whitening and parse a QR-decoded payload into (packet, filename)."""
    try:
        whitened = base64.b64decode(raw_payload, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("payload is not valid base64") from exc
    payload = whiten(whitened)
    if len(payload) < _HEADER1.size:
        raise ValueError("payload too short")
    magic, seq, seed, total_k, file_hash, name_len = _HEADER1.unpack_from(payload)
    if magic != MAGIC:
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
