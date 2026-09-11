import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with mock.patch.dict(sys.modules, {"zxingcpp": types.SimpleNamespace()}):
    from ui.sender_tab import SenderTab


class SenderTabGenerationTests(unittest.TestCase):
    def test_stale_generation_cannot_reset_current_run_buttons(self):
        fake_tab = types.SimpleNamespace(_run_generation=2)
        fake_tab._reset_buttons = mock.Mock()

        SenderTab._reset_buttons_if_current(fake_tab, 1)

        fake_tab._reset_buttons.assert_not_called()

    def test_current_generation_can_reset_buttons(self):
        fake_tab = types.SimpleNamespace(_run_generation=2)
        fake_tab._reset_buttons = mock.Mock()

        SenderTab._reset_buttons_if_current(fake_tab, 2)

        fake_tab._reset_buttons.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
