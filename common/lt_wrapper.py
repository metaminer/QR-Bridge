"""Small LT fountain-code encoder and belief-propagation decoder.

Packets are dictionaries with exactly these fields::

    {"seq", "seed", "data", "total_k", "file_hash"}

The first ``total_k`` packets are systematic packets. Later packets select a
degree from a robust Soliton distribution and XOR pseudorandom source blocks.
This makes loss-free decoding immediate and still permits recovery after loss.

Optimizations
-------------
* ``soliton_distribution`` is cached with ``lru_cache`` so the O(K) build
  happens once per distinct ``total_k`` instead of once per non-systematic
  packet on both the encode and decode paths (was O(P*K) total; now O(K)
  per distinct total_k).
* ``LTDecoder._peel`` maintains a block->equation adjacency list so that
  resolving a single block touches only the equations that actually reference
  it, instead of scanning every pending equation. Asymptotic peeling cost
  drops from O(K*E) to O(sum of degrees over all equations) ~= O(E log K).
"""

from __future__ import annotations

import hashlib
import math
import random
import struct
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Callable, Dict, Iterable, List, Optional, Sequence, Set, Union

from common.hash_verify import sha256_bytes, sha256_stream, verify_bytes

Packet = Dict[str, object]
PathLike = Union[str, Path]
_LENGTH_HEADER = struct.Struct(">Q")

@dataclass(frozen=True)
class ProtocolLimits:
    """Resource limits shared with the Android protocol implementation."""
    maxPayloadBytes: int = 8 * 1024
    maxFilenameBytes: int = 1024
    maxTotalK: int = 100_000
    maxBlockBytes: int = 4 * 1024
    maxPendingEquations: int = 200_000


# maximum block-index set a packet is allowed to reference; used by the
# adjacency list so degenerate inputs cannot blow up memory.
_MAX_PACKET_DEGREE = 65536


def _xor_into(target: bytearray, source: bytes) -> None:
    """XOR *source* into *target* in place using C-level big-int operations.

    Encoder and decoder call sites always pass equal-sized blocks.  The
    longer-source branch preserves the old loop's partial mutation followed
    by ``IndexError`` for callers that violate that invariant.
    """
    length = len(target)
    if len(source) > length:
        result = int.from_bytes(target, "little") ^ int.from_bytes(source[:length], "little")
        target[:] = result.to_bytes(length, "little")
        raise IndexError("bytearray index out of range")
    result = int.from_bytes(target, "little") ^ int.from_bytes(source, "little")
    target[:] = result.to_bytes(length, "little")


@lru_cache(maxsize=64)
def soliton_distribution(k: int, c: float = 0.1, delta: float = 0.05) -> List[float]:
    """Return the normalized robust Soliton degree distribution for ``k``.

    Results are cached per ``(k, c, delta)`` so that the O(k) construction
    cost is paid once per distinct total_k rather than once per non-systematic
    packet on both encode and decode paths.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if c <= 0 or not 0 < delta < 1:
        raise ValueError("c must be positive and delta must be between 0 and 1")

    rho = [0.0] * (k + 1)
    rho[1] = 1.0 / k
    for degree in range(2, k + 1):
        rho[degree] = 1.0 / (degree * (degree - 1))

    r = c * math.log(k / delta) * math.sqrt(k)
    pivot = min(k, max(1, int(k / r)))
    tau = [0.0] * (k + 1)
    for degree in range(1, pivot):
        tau[degree] = r / (degree * k)
    tau[pivot] = r * math.log(r / delta) / k

    normalization = sum(rho) + sum(tau)
    return [(rho[d] + tau[d]) / normalization for d in range(k + 1)]


def _sample_degree(rng: random.Random, distribution: Sequence[float]) -> int:
    needle = rng.random()
    cumulative = 0.0
    for degree in range(1, len(distribution)):
        cumulative += distribution[degree]
        if needle <= cumulative:
            return degree
    return len(distribution) - 1


def _block_indices(
    total_k: int,
    seq: int,
    seed: int,
    distribution: Optional[Sequence[float]] = None,
) -> Set[int]:
    """Return the set of source block indices referenced by packet *seq*.

    Systematic packets (seq < total_k) reference exactly {seq}.  Non-systematic
    packets draw a degree from the (cached) robust Soliton distribution and
    sample that many distinct block indices.

    Pass an already-computed *distribution* when one is available (encoder
    computes it once; decoder reuses the encoder's cached distribution via the
    shared lru_cache on ``soliton_distribution``).
    """
    if seq < total_k:
        return {seq}
    if distribution is None:
        distribution = soliton_distribution(total_k)
    rng = random.Random(seed)
    degree = _sample_degree(rng, distribution)
    if degree > _MAX_PACKET_DEGREE:
        raise ValueError(
            f"packet degree {degree} exceeds _MAX_PACKET_DEGREE ({_MAX_PACKET_DEGREE})"
        )
    return set(rng.sample(range(total_k), degree))


class LTEncoder:
    """Encode bytes or a file into a deterministic stream of LT packets."""

    def __init__(self, data: bytes, chunk_size: int = 1024, seed: int = 0) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._data: Optional[bytes] = data
        self._stream: Optional[BinaryIO] = None
        self._initialize(len(data), sha256_bytes(data), chunk_size, seed)

    @classmethod
    def from_file(
        cls,
        path: PathLike,
        chunk_size: int = 1024,
        seed: int = 0,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> "LTEncoder":
        """Create an encoder backed by a temp-file copy of *path*, without
        holding the whole payload in memory.

        *progress_callback* (optional) receives ``(bytes_scanned,
        total_bytes)`` as the file is scanned, so callers can throttle
        progress UI updates; *stop_event*, when set, aborts the scan early.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        total_bytes = Path(path).stat().st_size

        def progress(read: int) -> None:
            progress_callback(read, total_bytes)

        stream = tempfile.TemporaryFile(mode="w+b")
        try:
            with Path(path).open("rb") as source:
                file_hash = sha256_stream(
                    source,
                    copy_to=stream,
                    progress=progress if progress_callback is not None else None,
                    stop_event=stop_event,
                )
            data_length = stream.tell()
            stream.seek(0)
            encoder = cls.__new__(cls)
            encoder._data = None
            encoder._stream = stream
            encoder._initialize(data_length, file_hash, chunk_size, seed)
            return encoder
        except Exception:
            stream.close()
            raise

    def _initialize(
        self, data_length: int, file_hash: str, chunk_size: int, seed: int
    ) -> None:
        self.chunk_size = chunk_size
        self.seed = seed
        self.file_hash = file_hash
        self.data_length = data_length
        self._length_header = _LENGTH_HEADER.pack(data_length)
        self._io_lock = threading.Lock()
        self.total_k = max(1, math.ceil((data_length + _LENGTH_HEADER.size) / chunk_size))

    def _read_block(self, index: int) -> bytes:
        """Read one padded block from the virtual length-header + data stream."""
        start = index * self.chunk_size
        end = start + self.chunk_size
        block = bytearray(self.chunk_size)

        header_end = min(end, _LENGTH_HEADER.size)
        if start < header_end:
            block[: header_end - start] = self._length_header[start:header_end]

        data_start = max(start, _LENGTH_HEADER.size)
        data_end = min(end, _LENGTH_HEADER.size + self.data_length)
        if data_start < data_end:
            source_start = data_start - _LENGTH_HEADER.size
            source_end = data_end - _LENGTH_HEADER.size
            destination_start = data_start - start
            if self._data is not None:
                chunk = self._data[source_start:source_end]
            else:
                if self._stream is None or self._stream.closed:
                    raise ValueError("file-backed LTEncoder is closed")
                with self._io_lock:
                    self._stream.seek(source_start)
                    chunk = self._stream.read(source_end - source_start)
                if len(chunk) != source_end - source_start:
                    raise OSError("source file changed while encoding")
            block[destination_start : destination_start + len(chunk)] = chunk
        return bytes(block)

    def close(self) -> None:
        """Close the source file, if this is a file-backed encoder."""
        if self._stream is not None:
            self._stream.close()

    def __enter__(self) -> "LTEncoder":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def __del__(self) -> None:
        stream = getattr(self, "_stream", None)
        if stream is not None:
            stream.close()

    def packet(self, seq: int) -> Packet:
        """Generate packet number ``seq``; repeated calls are identical."""
        if seq < 0:
            raise ValueError("seq must be non-negative")
        if self._data is None and (self._stream is None or self._stream.closed):
            raise ValueError("file-backed LTEncoder is closed")
        packet_seed = self.seed + seq
        indices = _block_indices(self.total_k, seq, packet_seed)
        payload = bytearray(self.chunk_size)
        for index in indices:
            _xor_into(payload, self._read_block(index))
        return {
            "seq": seq,
            "seed": packet_seed,
            "data": bytes(payload),
            "total_k": self.total_k,
            "file_hash": self.file_hash,
        }

    def packets(self, count: int) -> List[Packet]:
        if count < 0:
            raise ValueError("count must be non-negative")
        return [self.packet(seq) for seq in range(count)]


class LTDecoder:
    """Incremental peeling/Belief Propagation decoder for LT packets.

    Maintains an adjacency list ``_adj[block_index] -> set(packet_index)`` so
    that, when a block is resolved, only the equations that actually reference
    it are visited — instead of scanning every pending equation.
    """

    def __init__(self, limits: ProtocolLimits = ProtocolLimits()) -> None:
        self._limits = limits
        self._packets: List[tuple[Set[int], bytearray]] = []
        self._seen: Set[int] = set()
        self._seen_fingerprints: Dict[int, tuple[int, int, bytes]] = {}
        self._blocks: Dict[int, bytes] = {}
        self._total_k: Optional[int] = None
        self._file_hash: Optional[str] = None
        self._chunk_size: Optional[int] = None
        # block_index -> set of packet indices (in _packets) that reference it
        self._adj: Dict[int, Set[int]] = {}
        # packet_index -> snapshot of the block indices that packet references
        self._packet_blocks: Dict[int, Set[int]] = {}

    @property
    def complete(self) -> bool:
        return self._total_k is not None and len(self._blocks) == self._total_k

    def add_packet(self, packet: Packet) -> bool:
        """Add one packet and run BP. Return whether decoding is complete."""
        required = {"seq", "seed", "data", "total_k", "file_hash"}
        if set(packet) != required:
            raise ValueError(f"packet fields must be exactly {sorted(required)}")
        seq, seed, total_k = int(packet["seq"]), int(packet["seed"]), int(packet["total_k"])
        data, file_hash = packet["data"], str(packet["file_hash"])
        if not isinstance(data, bytes) or not data or seq < 0 or total_k <= 0:
            raise ValueError("invalid packet values")
        if total_k > self._limits.maxTotalK:
            raise ValueError("total_k is outside allowed range")
        if len(data) > self._limits.maxBlockBytes:
            raise ValueError("packet data length is outside allowed range")
        if len(file_hash.encode("utf-8")) > self._limits.maxFilenameBytes:
            raise ValueError("file_hash is too long")
        fingerprint = (seed, len(data), hashlib.blake2b(data, digest_size=8).digest())
        if seq in self._seen:
            if self._seen_fingerprints.get(seq) != fingerprint:
                raise ValueError("conflicting duplicate LT packet")
            return self.complete
        if self._total_k is None:
            self._total_k, self._file_hash, self._chunk_size = total_k, file_hash, len(data)
        elif (total_k, file_hash, len(data)) != (self._total_k, self._file_hash, self._chunk_size):
            raise ValueError("packet belongs to a different LT stream")
        if len(self._packets) >= self._limits.maxPendingEquations:
            raise ValueError("too many LT equations")

        indices = _block_indices(total_k, seq, seed)
        payload = bytearray(data)
        for index in tuple(indices):
            if index in self._blocks:
                _xor_into(payload, self._blocks[index])
                indices.remove(index)

        # snapshot of the block indices this packet still references (after
        # subtracting already-resolved blocks); used to keep _adj entries tidy
        # when a packet becomes fully resolved.
        packet_blocks = set(indices)
        packet_index = len(self._packets)
        added_adj: List[int] = []
        try:
            self._packets.append((indices, payload))
            self._packet_blocks[packet_index] = packet_blocks
            for index in indices:
                added_adj.append(index)
                self._adj.setdefault(index, set()).add(packet_index)
            self._seen_fingerprints[seq] = fingerprint
            self._seen.add(seq)
        except BaseException:
            self._seen.discard(seq)
            self._seen_fingerprints.pop(seq, None)
            self._packet_blocks.pop(packet_index, None)
            for index in added_adj:
                references = self._adj.get(index)
                if references is not None:
                    references.discard(packet_index)
                    if not references:
                        self._adj.pop(index, None)
            if len(self._packets) > packet_index:
                self._packets.pop()
            raise

        self._peel()
        return self.complete

    def _peel(self) -> None:
        queue = deque(
            i for i, (indices, _) in enumerate(self._packets) if len(indices) == 1
        )
        while queue:
            packet_index = queue.popleft()
            indices, payload = self._packets[packet_index]
            if len(indices) != 1:
                continue
            block_index = next(iter(indices))
            if block_index in self._blocks:
                continue
            block = bytes(payload)
            self._blocks[block_index] = block
            # remove this packet from the adjacency lists of the blocks it
            # still references, so they no longer iterate over a now-resolved
            # equation.  Afterwards drop the block's own list entirely.
            for other_block in self._packet_blocks.get(packet_index, ()):
                self._adj.get(other_block, set()).discard(packet_index)
            self._packet_blocks.pop(packet_index, None)

            # touch only equations that reference *this* block
            for other_index in self._adj.get(block_index, ()):
                other_indices, other_payload = self._packets[other_index]
                if block_index not in other_indices:
                    continue
                other_indices.remove(block_index)
                _xor_into(other_payload, block)
                self._packet_blocks[other_index].discard(block_index)
                if len(other_indices) == 1:
                    queue.append(other_index)
            self._adj.pop(block_index, None)

    def result(self) -> bytes:
        """Return verified original bytes, or raise if decoding is incomplete."""
        if not self.complete:
            raise ValueError("not enough independent packets to decode")
        k = self._total_k
        if k == 0:
            raise ValueError("decoded stream has no blocks")
        chunk_size = self._chunk_size or 0
        if chunk_size == 0:
            raise ValueError("decoded stream has no chunk size")
        first_block = self._blocks[0]
        if len(first_block) < _LENGTH_HEADER.size:
            raise ValueError("decoded stream has no length header")
        length = _LENGTH_HEADER.unpack(first_block[: _LENGTH_HEADER.size])[0]
        data_start = _LENGTH_HEADER.size
        data_end = data_start + length
        total_size = k * chunk_size
        if data_end > total_size:
            raise ValueError(
                f"decoded data length {length} exceeds stream size {total_size - _LENGTH_HEADER.size}"
            )
        parts: List[bytes] = []
        if length > 0:
            first_data_block = data_start // chunk_size
            last_data_block = (data_end - 1) // chunk_size
            for idx in range(first_data_block, last_data_block + 1):
                block = self._blocks[idx]
                block_start = idx * chunk_size
                block_end = block_start + chunk_size
                slice_start = max(data_start, block_start)
                slice_end = min(data_end, block_end)
                if slice_start < slice_end:
                    offset = slice_start - block_start
                    parts.append(block[offset : offset + (slice_end - slice_start)])
        data = b"".join(parts)
        if len(data) != length or not verify_bytes(data, self._file_hash or ""):
            raise ValueError("decoded data failed SHA-256 verification")
        return data


def encode(data: bytes, packet_count: int, chunk_size: int = 1024, seed: int = 0) -> List[Packet]:
    """Convenience wrapper returning ``packet_count`` encoded packets."""
    return LTEncoder(data, chunk_size, seed).packets(packet_count)


def decode(packets: Iterable[Packet]) -> bytes:
    """Convenience wrapper decoding an iterable of packets."""
    decoder = LTDecoder()
    for packet in packets:
        decoder.add_packet(packet)
        if decoder.complete:
            break
    return decoder.result()
