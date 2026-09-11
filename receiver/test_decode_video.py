import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from receiver import decode_video as receiver_decode


class DecodeVideoLogTests(unittest.TestCase):
    def test_completion_log_includes_elapsed_time(self):
        capture = mock.Mock()
        capture.isOpened.return_value = True
        capture.get.side_effect = [0, 30.0]
        capture.release.return_value = None
        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_FRAME_COUNT=1,
            CAP_PROP_FPS=2,
            VideoCapture=mock.Mock(return_value=capture),
        )
        decoder = mock.Mock()
        decoder.complete = True
        decoder._blocks = {0: b"restored"}
        decoder._total_k = 1
        decoder._file_hash = "abc123"
        decoder.result.return_value = b"restored"
        logs = []

        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "capture.mp4"
            output_path = Path(directory) / "restored.bin"
            video_path.write_bytes(b"video")
            with (
                mock.patch.dict(sys.modules, {"cv2": fake_cv2}),
                mock.patch.object(receiver_decode, "LTDecoder", return_value=decoder),
                mock.patch.object(receiver_decode.time, "time", side_effect=[100.0, 112.34]),
                mock.patch("common.hash_verify.sha256_file", return_value="abc123"),
            ):
                receiver_decode.decode_video(
                    video_path,
                    output_path,
                    log_callback=logs.append,
                )

        completion_log = next(log for log in logs if log.startswith("복원 완료"))
        self.assertIn("소요시간 12.3초", completion_log)


if __name__ == "__main__":
    unittest.main()
