"""단위 테스트: receiver_tab.progress 이벤트 처리 로직(퍼센트, 텍스트 포맷, 방어 코드)."""

import unittest
import tkinter as tk
from unittest.mock import MagicMock

# tkinter를 실제로 띄우지 않고도 로직만 검증한다.
# StringVar를 만들려면 기본 루트 윈도우가 필요하므로, nobody에게 붙인다.
_root = tk.Tk()
_root.withdraw()


class DummyWidget:
    def __init__(self):
        self._mode = "indeterminate"
        self._maximum = 1
        self._value = 0
        self._stopped = False

    def cget(self, key):
        return {"mode": self._mode}[key]

    def configure(self, *, mode=None, maximum=None, value=None):
        if mode is not None:
            self._mode = mode
        if maximum is not None:
            self._maximum = maximum
        if value is not None:
            self._value = value

    def stop(self):
        self._stopped = True


class ProgressDrainTest(unittest.TestCase):
    def setUp(self):
        from ui.receiver_tab import ReceiverTab
        from tkinter import StringVar
        self.text_var = StringVar()
        self.progress = DummyWidget()
        # ReceiverTab.__init__는 실제 tkinter 위젯을 만든다 — 단위 테스트에서는
        # 그 내부를 쓰지 않고 _drain_events만 직접 호출한다.
        # 따라서 Row/Log 등 나머지 의존성만 모킹한다.
        self.tab = ReceiverTab.__new__(ReceiverTab)
        self.tab.video_var = MagicMock()
        self.tab.output_var = MagicMock()
        self.tab.progress_text = self.text_var
        self.tab.hash_text = MagicMock()
        self.tab.progress = self.progress
        self.tab.log = MagicMock()
        self.tab._set_running = MagicMock()
        self.tab._events = __import__("queue").Queue(maxsize=256)
        self.tab.POLL_MS = 50
        self.tab._worker = None

    def _feed(self, received, target, decoded, frames):
        self.tab._events.put(("progress", received, target, decoded, frames))
        self.tab._drain_events()

    # ---- target이 알려진 구간: restoration-based progress ----

    def test_progress_bar_value_is_decoded_not_received(self):
        # received와 decoded가 어긋나도 바 값은 decoded를 따라야 한다.
        self._feed(received=30, target=100, decoded=20, frames=3)
        self.assertEqual(self.progress._maximum, 100)
        self.assertEqual(self.progress._value, 20)
        # indeterminate → determinate 전환 과정에서 stop()이 한 번 불리는 것은 정상.
        self.assertTrue(self.progress._stopped)

    def test_progress_bar_does_not_exceed_target(self):
        self._feed(received=100, target=100, decoded=100, frames=5)
        self.assertEqual(self.progress._value, 100)

    def test_progress_bar_stays_at_target_even_if_decoded_clamps(self):
        # decoded가 target을 넘어도(발신 측 버그 등) 바는 target에 고정.
        self._feed(received=120, target=100, decoded=105, frames=8)
        self.assertEqual(self.progress._value, 100)

    def test_progress_text_shows_restoration_percentage(self):
        self._feed(received=50, target=100, decoded=25, frames=4)
        label = self.text_var.get()
        self.assertIn("%", label)
        self.assertIn("25.0%", label)

    def test_text_includes_received_and_decoded_later(self):
        # 기존 "수신 패킷: ... / ... · 복원 블록: ..." 서식이 남아 있어야 한다.
        self._feed(received=70, target=100, decoded=35, frames=6)
        label = self.text_var.get()
        self.assertIn("수신 패킷", label)
        self.assertIn("복원 블록", label)
        self.assertIn("프레임", label)

    def test_text_rounded_percentage_one_decimal(self):
        self._feed(received=50, target=100, decoded=33, frames=4)
        label = self.text_var.get()
        self.assertIn("33.0%", label)

    # ---- target이 아직 None인 초기 구간: 불확정 모드 유지 ----

    def test_indeterminate_unchanged_when_target_none(self):
        self._feed(received=3, target=None, decoded=0, frames=0)
        self.assertEqual(self.progress._mode, "indeterminate")
        # target이 None일 때 stop()을 부르면 안 된다(이미 start()된 상태).
        self.assertFalse(self.progress._stopped)

    def test_text_when_target_unknown(self):
        self._feed(received=3, target=None, decoded=0, frames=2)
        label = self.text_var.get()
        self.assertIn("목표 확인 중", label)
        self.assertIn("프레임", label)
        self.assertIn("복원율", label)

    def test_mode_becomes_determinate_on_first_known_target(self):
        self._feed(received=1, target=None, decoded=0, frames=1)
        self.assertEqual(self.progress._mode, "indeterminate")
        self.assertFalse(self.progress._stopped)
        self._feed(received=2, target=50, decoded=1, frames=2)
        self.assertEqual(self.progress._mode, "determinate")
        # 첫 알려진 target에서 indeterminate 정지 → determinate 재구성.
        self.assertTrue(self.progress._stopped)

    # ---- target이 0 이하인 방어 케이스 ----

    def test_target_zero_or_negative_falls_back_to_unknown_mode_text(self):
        # target <= 0 이면 total_k를 아직 모르는 것과 동일하게 처리한다.
        self._feed(received=0, target=0, decoded=0, frames=0)
        label = self.text_var.get()
        self.assertIn("목표 확인 중", label)
        self.assertEqual(self.progress._mode, "indeterminate")

    def test_target_negative_same_as_unknown(self):
        self._feed(received=0, target=-1, decoded=0, frames=0)
        label = self.text_var.get()
        self.assertIn("목표 확인 중", label)
        self.assertEqual(self.progress._mode, "indeterminate")


class PercentFormatTest(unittest.TestCase):
    """퍼센트 계산 자체를 직접 검증한다."""

    def setUp(self):
        # 헬퍼는 모듈 레벨이므로 바로 import된다.
        from ui.receiver_tab import _restoration_percentage
        self.helper = _restoration_percentage

    def test_exact_half(self):
        self.assertEqual(self.helper(decoded=5, target=10), 50.0)

    def test_zero_decoded(self):
        self.assertEqual(self.helper(decoded=0, target=10), 0.0)

    def test_full(self):
        self.assertEqual(self.helper(decoded=10, target=10), 100.0)

    def test_target_zero_returns_zero(self):
        self.assertEqual(self.helper(decoded=5, target=0), 0.0)

    def test_target_negative_returns_zero(self):
        self.assertEqual(self.helper(decoded=5, target=-1), 0.0)

    def test_one_decimal_rounding(self):
        self.assertAlmostEqual(self.helper(decoded=1, target=3), 33.3, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
