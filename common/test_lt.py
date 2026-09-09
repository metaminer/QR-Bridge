import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.hash_verify import sha256_bytes, verify_bytes
from common.lt_wrapper import LTDecoder, LTEncoder, decode


class LTCodeTests(unittest.TestCase):
    DATA = bytes((index * 31 + 17) % 256 for index in range(48_137))

    def test_hash_helpers(self):
        digest = sha256_bytes(self.DATA)
        self.assertTrue(verify_bytes(self.DATA, digest))
        self.assertFalse(verify_bytes(self.DATA + b"x", digest))

    def _assert_recovers_with_loss(self, loss_rate: float) -> None:
        encoder = LTEncoder(self.DATA, chunk_size=512, seed=20260909)
        # Extra fountain packets leave enough independent equations after 30% loss.
        packets = encoder.packets(encoder.total_k * 3)
        rng = random.Random(1000 + int(loss_rate * 100))
        kept = [packet for packet in packets if rng.random() >= loss_rate]
        rng.shuffle(kept)
        self.assertEqual(decode(kept), self.DATA)

    def test_recovers_with_0_percent_loss(self):
        self._assert_recovers_with_loss(0.0)

    def test_recovers_with_10_percent_loss(self):
        self._assert_recovers_with_loss(0.1)

    def test_recovers_with_30_percent_loss(self):
        self._assert_recovers_with_loss(0.3)

    def test_incremental_decoder_and_duplicate_packet(self):
        encoder = LTEncoder(b"incremental", chunk_size=8, seed=7)
        decoder = LTDecoder()
        first = encoder.packet(0)
        decoder.add_packet(first)
        decoder.add_packet(first)
        for packet in encoder.packets(encoder.total_k * 3)[1:]:
            if decoder.add_packet(packet):
                break
        self.assertEqual(decoder.result(), b"incremental")


if __name__ == "__main__":
    unittest.main()
