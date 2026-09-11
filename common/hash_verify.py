"""SHA-256 helpers for byte strings and files."""

from __future__ import annotations

import hashlib
import hmac
import threading
from pathlib import Path
from typing import BinaryIO, Callable, Optional, Union


PathLike = Union[str, Path]


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_stream(
    stream: BinaryIO,
    block_size: int = 1024 * 1024,
    copy_to: Optional[BinaryIO] = None,
    progress: Optional[Callable[[int], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> str:
    """Hash a stream incrementally, optionally copying it to another stream.

    *progress* (when given) is called after each block with the cumulative
    number of bytes read, so callers can throttle UI updates.  *stop_event*,
    when set between blocks, aborts the scan by raising ``InterruptedError``.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    digest = hashlib.sha256()
    read = 0
    while block := stream.read(block_size):
        digest.update(block)
        read += len(block)
        if copy_to is not None:
            copy_to.write(block)
        if progress is not None:
            progress(read)
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("scan interrupted by stop_event")
    return digest.hexdigest()


def sha256_file(path: PathLike, block_size: int = 1024 * 1024) -> str:
    """Return the lowercase SHA-256 hex digest of a file."""
    with Path(path).open("rb") as stream:
        return sha256_stream(stream, block_size)


def verify_bytes(data: bytes, expected_hash: str) -> bool:
    """Compare a byte string with an expected SHA-256 digest."""
    return hmac.compare_digest(sha256_bytes(data), expected_hash.lower())


def verify_file(path: PathLike, expected_hash: str) -> bool:
    """Compare a file with an expected SHA-256 digest."""
    return hmac.compare_digest(sha256_file(path), expected_hash.lower())
