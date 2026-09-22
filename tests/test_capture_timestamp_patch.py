import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

from src.patches import capture_timestamp_patch


class TestCaptureTimestampPatch(unittest.TestCase):
    def test_qpc_timestamp_matches_converted_pixels_and_install_is_idempotent(self):
        pixels = np.zeros((2, 2, 4), dtype=np.uint8)
        method = SimpleNamespace()
        frame = SimpleNamespace(SystemRelativeTime=1234567890)
        with (
            patch.object(capture_timestamp_patch, "_INSTALLED", False),
            patch.object(
                WindowsGraphicsCaptureMethod, "convert_dx_frame", lambda self, source: pixels if source else None
            ),
        ):
            capture_timestamp_patch.install_capture_timestamp_patch()
            installed = WindowsGraphicsCaptureMethod.convert_dx_frame
            capture_timestamp_patch.install_capture_timestamp_patch()
            self.assertIs(installed, WindowsGraphicsCaptureMethod.convert_dx_frame)
            self.assertIs(installed(method, frame), pixels)
            self.assertEqual(method.frame_timestamp, 123.456789)
            self.assertIsNone(installed(method, None))
            self.assertEqual(method.frame_timestamp, 123.456789)


if __name__ == "__main__":
    unittest.main()
