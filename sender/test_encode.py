import math
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with mock.patch.dict(sys.modules, {"zxingcpp": types.SimpleNamespace()}):
    from sender import encode as sender_encode

from common.lt_wrapper import LTEncoder


class SenderEncodeTests(unittest.TestCase):
    def test_build_encoder_file_path_matches_bytes(self):
        data = bytes((index * 11 + 5) % 256 for index in range(173))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(data)
            encoder, packet_count = sender_encode.build_encoder(path, 32, 1.5, 7)
            try:
                expected = LTEncoder(data, chunk_size=32, seed=7)
                self.assertEqual(
                    packet_count,
                    max(expected.total_k, math.ceil(expected.total_k * 1.5)),
                )
                self.assertEqual(
                    encoder.packets(packet_count), expected.packets(packet_count)
                )
            finally:
                encoder.close()

    def test_encode_file_closes_source_after_materializing_packets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"materialized packets")

            encoder, packets, filename = sender_encode.encode_file(
                path, chunk_size=8, redundancy=1.0, seed=2
            )

            self.assertTrue(encoder._stream.closed)
            self.assertEqual(len(packets), encoder.total_k)
            self.assertEqual(filename, path.name)

    def test_build_encoder_forwards_progress_and_stop(self):
        data = os.urandom(3 * 1024 * 1024)
        updates: list[tuple[int, int]] = []
        stop = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(data)
            encoder, packet_count = sender_encode.build_encoder(
                path,
                16,
                1.5,
                0,
                progress_callback=lambda read, total: updates.append((read, total)),
                stop_event=stop,
            )
            try:
                self.assertTrue(updates, "progress callback was not forwarded")
                self.assertEqual(updates[-1], (len(data), len(data)))
            finally:
                encoder.close()

    def test_build_encoder_stop_event_aborts_scan(self):
        data = os.urandom(4 * 1024 * 1024)
        stop = threading.Event()

        def abort_early(read: int, total: int) -> None:
            stop.set()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(data)
            with self.assertRaises(InterruptedError):
                sender_encode.build_encoder(
                    path, 16, 1.5, 0,
                    progress_callback=abort_early, stop_event=stop,
                )

    def test_build_encoder_closes_source_if_packet_count_fails(self):
        encoder = mock.Mock(total_k=1)
        with mock.patch.object(
            sender_encode.LTEncoder, "from_file", return_value=encoder
        ):
            with self.assertRaises(ValueError):
                sender_encode.build_encoder("payload.bin", 8, float("nan"), 0)

        encoder.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
