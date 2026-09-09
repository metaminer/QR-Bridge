"""Receiver-side integrity check: verify a reconstructed file's SHA-256.

Thin wrapper around ``common.hash_verify`` that reports the result the way
``receiver/decode_video.py`` needs it (pass/fail + printed summary).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.hash_verify import sha256_file, verify_file


def verify_and_report(output_path: Path, expected_hash: str) -> bool:
    """Verify *output_path* against *expected_hash*, printing a summary. Returns success."""
    actual_hash = sha256_file(output_path)
    if verify_file(output_path, expected_hash):
        print("  ✓ SHA-256 검증 통과")
        print(f"  SHA-256: {actual_hash}")
        return True
    print("  ✗ SHA-256 검증 실패!", file=sys.stderr)
    print(f"    기대: {expected_hash}", file=sys.stderr)
    print(f"    실측: {actual_hash}", file=sys.stderr)
    return False
