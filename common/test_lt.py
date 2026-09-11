import random
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.hash_verify import sha256_bytes, verify_bytes
from common.lt_wrapper import LTDecoder, LTEncoder, ProtocolLimits, decode


class LTCodeTests(unittest.TestCase):
    DATA = bytes((index * 31 + 17) % 256 for index in range(48_137))

    def test_protocol_limits_match_android_defaults(self):
        limits = ProtocolLimits()
        self.assertEqual(limits.maxPayloadBytes, 8 * 1024)
        self.assertEqual(limits.maxFilenameBytes, 1024)
        self.assertEqual(limits.maxTotalK, 100_000)
        self.assertEqual(limits.maxBlockBytes, 4 * 1024)
        self.assertEqual(limits.maxPendingEquations, 200_000)

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

    def test_decoder_can_retry_seq_after_index_calculation_fails(self):
        encoder = LTEncoder(b"retry", chunk_size=16, seed=5)
        packet = encoder.packet(encoder.total_k)
        decoder = LTDecoder()

        with mock.patch(
            "common.lt_wrapper._block_indices",
            side_effect=[ValueError("degree limit"), {0}],
        ):
            with self.assertRaisesRegex(ValueError, "degree limit"):
                decoder.add_packet(packet)

            self.assertNotIn(packet["seq"], decoder._seen)
            self.assertEqual(decoder._packets, [])
            self.assertEqual(decoder._adj, {})
            self.assertEqual(decoder._packet_blocks, {})

            decoder.add_packet(packet)

        self.assertIn(packet["seq"], decoder._seen)
        self.assertEqual(len(decoder._packets), 1)

    def test_decoder_rolls_back_partial_equation_commit(self):
        class ExplodingSet(set):
            def add(self, _value):
                raise RuntimeError("adjacency failure")

        class ExplodingAdjacency(dict):
            def setdefault(self, key, default=None):
                if key == 1:
                    default = ExplodingSet()
                return super().setdefault(key, default)

        encoder = LTEncoder(b"rollback", chunk_size=16, seed=5)
        packet = encoder.packet(encoder.total_k)
        decoder = LTDecoder()
        decoder._adj = ExplodingAdjacency()

        with mock.patch("common.lt_wrapper._block_indices", return_value={0, 1}):
            with self.assertRaisesRegex(RuntimeError, "adjacency failure"):
                decoder.add_packet(packet)

        self.assertNotIn(packet["seq"], decoder._seen)
        self.assertEqual(decoder._packets, [])
        self.assertEqual(decoder._adj, {})
        self.assertEqual(decoder._packet_blocks, {})

    def test_decoder_rejects_conflicting_duplicate_packets(self):
        packet = LTEncoder(b"duplicate", chunk_size=16, seed=11).packet(0)
        decoder = LTDecoder()
        decoder.add_packet(packet)

        conflicts = (
            dict(packet, seed=int(packet["seed"]) + 1),
            dict(packet, data=bytes([packet["data"][0] ^ 1]) + packet["data"][1:]),
        )
        for conflicting in conflicts:
            with self.subTest(fields=conflicting):
                with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                    decoder.add_packet(conflicting)

        self.assertEqual(decoder.add_packet(packet), decoder.complete)

    def test_file_encoder_matches_byte_encoder_packets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            for size in (0, 1, 7, 8, 9, 15, 16, 17, 137):
                with self.subTest(size=size):
                    data = bytes((index * 19 + 3) % 256 for index in range(size))
                    path.write_bytes(data)
                    byte_encoder = LTEncoder(data, chunk_size=16, seed=23)
                    file_encoder = LTEncoder.from_file(path, chunk_size=16, seed=23)
                    try:
                        self.assertEqual(file_encoder.file_hash, byte_encoder.file_hash)
                        self.assertEqual(file_encoder.total_k, byte_encoder.total_k)
                        self.assertEqual(
                            file_encoder.packets(file_encoder.total_k + 5),
                            byte_encoder.packets(byte_encoder.total_k + 5),
                        )
                    finally:
                        file_encoder.close()

    def test_file_encoder_uses_an_immutable_source_snapshot(self):
        original = b"A" * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(original)
            file_encoder = LTEncoder.from_file(path, chunk_size=16, seed=3)
            path.write_bytes(b"B" * len(original))
            try:
                expected = LTEncoder(original, chunk_size=16, seed=3)
                self.assertEqual(
                    file_encoder.packets(file_encoder.total_k + 3),
                    expected.packets(expected.total_k + 3),
                )
            finally:
                file_encoder.close()

    def test_from_file_reports_progress(self):
        data = os.urandom(3 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(data)
            updates: list[tuple[int, int]] = []
            encoder = LTEncoder.from_file(
                path,
                chunk_size=16,
                seed=0,
                progress_callback=lambda read, total: updates.append((read, total)),
            )
            try:
                self.assertTrue(updates, "progress callback was never called")
                self.assertEqual(updates[-1], (len(data), len(data)))
                scanned = [read for read, total in updates]
                self.assertEqual(scanned, sorted(scanned))
                self.assertTrue(all(total == len(data) for _, total in updates))
                self.assertEqual(encoder.file_hash, sha256_bytes(data))
            finally:
                encoder.close()

    def test_from_file_without_callback_is_unchanged(self):
        data = b"no callback" * 100
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(data)
            encoder = LTEncoder.from_file(path, chunk_size=16, seed=0)
            try:
                self.assertEqual(encoder.file_hash, sha256_bytes(data))
            finally:
                encoder.close()

    def test_from_file_stop_event_aborts_scan(self):
        data = os.urandom(4 * 1024 * 1024)
        stop = threading.Event()

        def abort_early(read: int, total: int) -> None:
            stop.set()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(data)
            with self.assertRaises(InterruptedError):
                LTEncoder.from_file(path, chunk_size=16, seed=0,
                                    progress_callback=abort_early,
                                    stop_event=stop)

    def test_from_file_set_stop_event_before_scan_aborts_immediately(self):
        data = os.urandom(4 * 1024 * 1024)
        stop = threading.Event()
        stop.set()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(data)
            with self.assertRaises(InterruptedError):
                LTEncoder.from_file(path, chunk_size=16, seed=0, stop_event=stop)

    def test_decoder_rejects_total_k_above_protocol_limit(self):
        packet = LTEncoder(b"limited", chunk_size=8).packet(0)
        packet["total_k"] = 100_001

        with self.assertRaises(ValueError):
            LTDecoder().add_packet(packet)

    def test_decoder_rechecks_total_k_limit_for_duplicate_sequence(self):
        packet = LTEncoder(b"limited", chunk_size=8).packet(0)
        decoder = LTDecoder()
        decoder.add_packet(packet)
        oversized = dict(packet, total_k=100_001)

        with self.assertRaisesRegex(ValueError, "total_k is outside allowed range"):
            decoder.add_packet(oversized)

    def test_decoder_rejects_data_above_block_limit(self):
        packet = LTEncoder(b"limited", chunk_size=8).packet(0)
        packet["data"] = b"x" * 4097

        with self.assertRaises(ValueError):
            LTDecoder().add_packet(packet)

    def test_decoder_caps_pending_equations_with_custom_limits(self):
        encoder = LTEncoder(b"limited", chunk_size=8)
        decoder = LTDecoder(ProtocolLimits(maxPendingEquations=1))
        decoder.add_packet(encoder.packet(0))

        with self.assertRaisesRegex(ValueError, "too many LT equations"):
            decoder.add_packet(encoder.packet(1))

    def test_decoder_checks_stream_identity_before_equation_limit(self):
        encoder = LTEncoder(b"limited", chunk_size=8)
        decoder = LTDecoder(ProtocolLimits(maxPendingEquations=1))
        decoder.add_packet(encoder.packet(0))
        foreign_packet = dict(encoder.packet(1), file_hash="0" * 64)

        with self.assertRaisesRegex(ValueError, "different LT stream"):
            decoder.add_packet(foreign_packet)

    def test_decoder_rejects_oversized_file_hash(self):
        packet = LTEncoder(b"limited", chunk_size=8).packet(0)
        packet["file_hash"] = "x" * 1025

        with self.assertRaisesRegex(ValueError, "file_hash is too long"):
            LTDecoder().add_packet(packet)


if __name__ == "__main__":
    unittest.main()
