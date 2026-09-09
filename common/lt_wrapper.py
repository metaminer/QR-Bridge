"""Small LT fountain-code encoder and belief-propagation decoder.

Packets are dictionaries with exactly these fields::

    {"seq", "seed", "data", "total_k", "file_hash"}

The first ``total_k`` packets are systematic packets. Later packets select a
degree from a robust Soliton distribution and XOR pseudorandom source blocks.
This makes loss-free decoding immediate and still permits recovery after loss.
"""

from __future__ import annotations

import math
import random
import struct
from collections import deque
from typing import Dict, Iterable, List, Optional, Sequence, Set

from common.hash_verify import sha256_bytes, verify_bytes


Packet = Dict[str, object]
_LENGTH_HEADER = struct.Struct(">Q")


def _xor_into(target: bytearray, source: bytes) -> None:
    for index, value in enumerate(source):
        target[index] ^= value


def soliton_distribution(k: int, c: float = 0.1, delta: float = 0.05) -> List[float]:
    """Return the normalized robust Soliton degree distribution for ``k``."""
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


def _block_indices(total_k: int, seq: int, seed: int) -> Set[int]:
    if seq < total_k:
        return {seq}
    rng = random.Random(seed)
    degree = _sample_degree(rng, soliton_distribution(total_k))
    return set(rng.sample(range(total_k), degree))


class LTEncoder:
    """Encode bytes into a deterministic stream of LT packets."""

    def __init__(self, data: bytes, chunk_size: int = 1024, seed: int = 0) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size
        self.seed = seed
        self.file_hash = sha256_bytes(data)
        framed = _LENGTH_HEADER.pack(len(data)) + data
        self.total_k = max(1, math.ceil(len(framed) / chunk_size))
        framed = framed.ljust(self.total_k * chunk_size, b"\0")
        self.blocks = [
            framed[offset : offset + chunk_size]
            for offset in range(0, len(framed), chunk_size)
        ]

    def packet(self, seq: int) -> Packet:
        """Generate packet number ``seq``; repeated calls are identical."""
        if seq < 0:
            raise ValueError("seq must be non-negative")
        packet_seed = self.seed + seq
        indices = _block_indices(self.total_k, seq, packet_seed)
        payload = bytearray(self.chunk_size)
        for index in indices:
            _xor_into(payload, self.blocks[index])
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
    """Incremental peeling/Belief Propagation decoder for LT packets."""

    def __init__(self) -> None:
        self._packets: List[tuple[Set[int], bytearray]] = []
        self._seen: Set[int] = set()
        self._blocks: Dict[int, bytes] = {}
        self._total_k: Optional[int] = None
        self._file_hash: Optional[str] = None
        self._chunk_size: Optional[int] = None

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
        if seq in self._seen:
            return self.complete
        if self._total_k is None:
            self._total_k, self._file_hash, self._chunk_size = total_k, file_hash, len(data)
        elif (total_k, file_hash, len(data)) != (self._total_k, self._file_hash, self._chunk_size):
            raise ValueError("packet belongs to a different LT stream")

        self._seen.add(seq)
        indices = _block_indices(total_k, seq, seed)
        payload = bytearray(data)
        for index in tuple(indices):
            if index in self._blocks:
                _xor_into(payload, self._blocks[index])
                indices.remove(index)
        self._packets.append((indices, payload))
        self._peel()
        return self.complete

    def _peel(self) -> None:
        queue = deque(i for i, (indices, _) in enumerate(self._packets) if len(indices) == 1)
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
            indices.clear()
            for other_index, (other_indices, other_payload) in enumerate(self._packets):
                if block_index in other_indices:
                    other_indices.remove(block_index)
                    _xor_into(other_payload, block)
                    if len(other_indices) == 1:
                        queue.append(other_index)

    def result(self) -> bytes:
        """Return verified original bytes, or raise if decoding is incomplete."""
        if not self.complete:
            raise ValueError("not enough independent packets to decode")
        framed = b"".join(self._blocks[index] for index in range(self._total_k or 0))
        if len(framed) < _LENGTH_HEADER.size:
            raise ValueError("decoded stream has no length header")
        length = _LENGTH_HEADER.unpack(framed[: _LENGTH_HEADER.size])[0]
        data = framed[_LENGTH_HEADER.size : _LENGTH_HEADER.size + length]
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
