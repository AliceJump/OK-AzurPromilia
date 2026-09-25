"""PixelCountDetector（HSV 像素计数判据）的测试。

背景：主界面左下角延迟显示绘制在半透明面板上，白色文本与背景混色导致
模板匹配跨背景不稳定；其信号图标为不透明纯色绿（H≈71），改用 HSV
像素计数判定是否已登录。这里用合成帧验证判据本身的行为。
"""

import unittest

import cv2
import numpy as np
from ok import Box

from src.core.detector.hit import SOURCE_HSV
from src.core.detector.pixel_count_detector import PixelCountDetector
from src.image.hsv_config import HSVRange

W, H = 1920, 1080
GREEN_HSV = (71, 200, 200)


def _bgr_of(hsv):
    return cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0]


def _frame_with_green(x=50, y=1060, w=16, h=13):
    """左下角含一块图标绿的 1080P 合成帧。"""
    frame = np.zeros((H, W, 3), np.uint8)
    frame[y:y + h, x:x + w] = _bgr_of(GREEN_HSV)
    return frame


def _make_task():
    """提供 width/height 的最小任务桩（检测器只需要这两个尺寸）。"""

    class _Task:
        width = W
        height = H

    return _Task()


class TestPixelCountDetector(unittest.TestCase):
    def test_detects_green_pixels_with_relative_region(self):
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, x=0.02, y=0.976, to_x=0.038, to_y=0.999, min_count=60,
        ).attach(_make_task())
        hit = detector.detect(_frame_with_green())
        self.assertIsNotNone(hit)
        self.assertEqual(hit.source, SOURCE_HSV)
        self.assertGreaterEqual(hit.metrics["pixel_count"], 60)

    def test_detects_with_pixel_box(self):
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, box=Box(40, 1050, 40, 30), min_count=60,
        ).attach(_make_task())
        hit = detector.detect(_frame_with_green())
        self.assertIsNotNone(hit)
        # Hit 的 box 是搜索区域，不是可点击目标
        self.assertEqual(hit.box.name, detector.name)

    def test_detects_yellow_bars(self):
        # 高延迟预留：黄/橙信号条（S≥180 排除草地 S≈130）
        frame = np.zeros((H, W, 3), np.uint8)
        frame[1060:1073, 50:66] = _bgr_of((30, 220, 220))
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, x=0.02, y=0.976, to_x=0.038, to_y=0.999, min_count=60,
        ).attach(_make_task())
        self.assertIsNotNone(detector.detect(frame))

    def test_detects_red_bars(self):
        # 高延迟预留：红信号条（含 H 跨 0° 两端）
        frame = np.zeros((H, W, 3), np.uint8)
        frame[1060:1073, 50:66] = _bgr_of((5, 220, 200))
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, x=0.02, y=0.976, to_x=0.038, to_y=0.999, min_count=60,
        ).attach(_make_task())
        self.assertIsNotNone(detector.detect(frame))

    def test_grass_green_is_ignored(self):
        # 草地绿 H≈35 S≈130：色相与饱和度都低于黄条阈值，不得误报
        frame = np.zeros((H, W, 3), np.uint8)
        frame[1060:1073, 50:66] = _bgr_of((35, 130, 153))
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, x=0.02, y=0.976, to_x=0.038, to_y=0.999, min_count=60,
        ).attach(_make_task())
        self.assertIsNone(detector.detect(frame))

    def test_returns_none_when_count_below_min(self):
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, x=0.02, y=0.976, to_x=0.038, to_y=0.999, min_count=10_000,
        ).attach(_make_task())
        self.assertIsNone(detector.detect(_frame_with_green()))

    def test_returns_none_without_green(self):
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, x=0.02, y=0.976, to_x=0.038, to_y=0.999, min_count=60,
        ).attach(_make_task())
        self.assertIsNone(detector.detect(np.zeros((H, W, 3), np.uint8)))

    def test_different_hue_is_ignored(self):
        # 青蓝色场景渗色（实测 H≈101-112，S≈219）：不在任何信号条色段内
        frame = np.zeros((H, W, 3), np.uint8)
        frame[1060:1073, 50:66] = _bgr_of((108, 219, 146))
        detector = PixelCountDetector(
            HSVRange.SIGNAL_BARS, x=0.02, y=0.976, to_x=0.038, to_y=0.999, min_count=60,
        ).attach(_make_task())
        self.assertIsNone(detector.detect(frame))

    def test_detect_requires_attached_task(self):
        detector = PixelCountDetector(HSVRange.SIGNAL_BARS, min_count=1)
        with self.assertRaises(RuntimeError):
            detector.detect(_frame_with_green())

    def test_min_count_floor_at_one(self):
        detector = PixelCountDetector(HSVRange.SIGNAL_BARS, min_count=0)
        self.assertEqual(detector._min_count, 1)


if __name__ == "__main__":
    unittest.main()
