"""泛光 / 准心检测器测试（合成帧，不依赖真实截图）。"""

import unittest

import cv2
import numpy as np
from ok import Box

from src.image.glow_target_detector import (
    ALL_GLOW_COLORS,
    DEFAULT_GLOW_THRESHOLDS,
    GLOW_GREEN,
    GLOW_RED,
    GLOW_YELLOW,
    GlowTargetDetector,
    GlowThresholds,
)

# ── ROI：归一化 (0.4724, 0.4426, 0.5365, 0.5611) 在 1920x1080 下的像素框 ──
FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080
ROI_BOX = Box(907, 478, 123, 128)

# 深色背景：V=30，远低于三色的 V 下限
BACKGROUND_BGR = (30, 30, 30)

# ROI 内的一块泛光，中心 (962, 542)
GLOW_RECT = (947, 527, 30, 30)


def _bgr_from_hsv(h, s, v):
    """按 HSV 生成 BGR 颜色，避免手算通道值出错。"""
    pixel = np.uint8([[[h, s, v]]])
    return tuple(int(channel) for channel in cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0][0])


GREEN_BGR = _bgr_from_hsv(60, 255, 255)
YELLOW_BGR = _bgr_from_hsv(30, 255, 255)
RED_BGR = _bgr_from_hsv(0, 255, 255)
RED_FAR_BGR = _bgr_from_hsv(170, 255, 255)  # 红色在 HSV 另一端的取值


def _background():
    return np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), BACKGROUND_BGR, dtype=np.uint8)


def _fill(frame, rect, color):
    x, y, w, h = rect
    cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), color, thickness=-1)


class TestGlowTargetDetector(unittest.TestCase):
    def setUp(self):
        self.detector = GlowTargetDetector()

    # ── 1. 三色命中 ─────────────────────────────────────────

    def test_detects_green_glow(self):
        frame = _background()
        _fill(frame, GLOW_RECT, GREEN_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertTrue(result.matched)
        self.assertEqual(result.color, GLOW_GREEN)
        self.assertEqual((result.x, result.y, result.width, result.height), GLOW_RECT)

    def test_detects_yellow_glow(self):
        frame = _background()
        _fill(frame, GLOW_RECT, YELLOW_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertTrue(result.matched)
        self.assertEqual(result.color, GLOW_YELLOW)

    def test_detects_red_glow_on_both_hsv_ends(self):
        for color in (RED_BGR, RED_FAR_BGR):
            frame = _background()
            _fill(frame, GLOW_RECT, color)
            result = self.detector.analyze(frame, ROI_BOX)
            self.assertTrue(result.matched, f"{color} 应命中红色区间")
            self.assertEqual(result.color, GLOW_RED)

    def test_box_is_frame_absolute_and_clickable(self):
        """返回的 Box 必须是整帧坐标，可直接交给 click()。"""
        frame = _background()
        _fill(frame, GLOW_RECT, GREEN_BGR)
        box = self.detector.find(frame, ROI_BOX)
        self.assertIsNotNone(box)
        self.assertEqual(box.center(), (962, 542))
        self.assertEqual(box.name, DEFAULT_GLOW_THRESHOLDS.box_name)

    def test_confidence_and_color_votes(self):
        frame = _background()
        _fill(frame, GLOW_RECT, GREEN_BGR)
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertEqual(max(result.counts, key=lambda c: result.counts[c]), GLOW_GREEN)
        self.assertGreaterEqual(result.box.confidence, 0.5)
        self.assertLessEqual(result.box.confidence, 1.0)

    # ── 2. 未命中 ───────────────────────────────────────────

    def test_dark_frame_has_no_detection(self):
        result = self.detector.analyze(_background(), ROI_BOX)
        self.assertFalse(result.matched)
        self.assertEqual(result.failed, "no_contour")
        self.assertIsNone(self.detector.find(_background(), ROI_BOX))

    def test_empty_input(self):
        self.assertEqual(self.detector.analyze(None, ROI_BOX).failed, "empty_input")
        self.assertEqual(self.detector.analyze(_background(), None).failed, "empty_input")

    def test_glow_outside_roi_is_ignored(self):
        """ROI 之外的泛光不该被点到。"""
        frame = _background()
        _fill(frame, (300, 300, 40, 40), GREEN_BGR)
        self.assertFalse(self.detector.analyze(frame, ROI_BOX).matched)

    def test_tiny_blob_rejected_by_area(self):
        frame = _background()
        _fill(frame, (960, 540, 5, 5), GREEN_BGR)  # 面积 25 < 60
        result = self.detector.analyze(frame, ROI_BOX)
        self.assertFalse(result.matched)
        self.assertEqual(result.failed, "area")
        self.assertLess(result.area, DEFAULT_GLOW_THRESHOLDS.min_area)

    def test_low_saturation_glow_rejected(self):
        """灰白色高亮（S 低）不该被当成泛光。"""
        frame = _background()
        _fill(frame, GLOW_RECT, (220, 220, 220))
        self.assertFalse(self.detector.analyze(frame, ROI_BOX).matched)

    def test_min_area_threshold_can_be_raised(self):
        frame = _background()
        _fill(frame, GLOW_RECT, GREEN_BGR)
        detector = GlowTargetDetector(DEFAULT_GLOW_THRESHOLDS.with_(min_area=5000))
        self.assertFalse(detector.analyze(frame, ROI_BOX).matched)

    # ── 3. 颜色开关 ─────────────────────────────────────────

    def test_disabled_color_is_not_detected(self):
        frame = _background()
        _fill(frame, GLOW_RECT, YELLOW_BGR)
        detector = GlowTargetDetector(colors=(GLOW_GREEN,))
        self.assertFalse(detector.analyze(frame, ROI_BOX).matched)

    def test_enabled_color_still_detected(self):
        frame = _background()
        _fill(frame, GLOW_RECT, GREEN_BGR)
        detector = GlowTargetDetector(colors=(GLOW_GREEN,))
        self.assertTrue(detector.analyze(frame, ROI_BOX).matched)

    # ── 4. 阈值 / 输入兼容 ─────────────────────────────────

    def test_default_thresholds_cover_all_colors(self):
        self.assertEqual(ALL_GLOW_COLORS, ("green", "yellow", "red"))
        for color in ALL_GLOW_COLORS:
            self.assertTrue(DEFAULT_GLOW_THRESHOLDS.ranges(color))

    def test_red_has_two_hsv_ranges(self):
        self.assertEqual(len(DEFAULT_GLOW_THRESHOLDS.ranges(GLOW_RED)), 2)
        self.assertEqual(len(DEFAULT_GLOW_THRESHOLDS.ranges(GLOW_GREEN)), 1)

    def test_unknown_color_raises(self):
        with self.assertRaises(ValueError):
            DEFAULT_GLOW_THRESHOLDS.ranges("purple")

    def test_thresholds_with_unknown_raises(self):
        with self.assertRaises(TypeError):
            DEFAULT_GLOW_THRESHOLDS.with_(no_such_field=1)

    def test_thresholds_are_immutable(self):
        """with_ 返回副本，不改动基线阈值。"""
        GlowThresholds().with_(min_area=999)
        self.assertEqual(DEFAULT_GLOW_THRESHOLDS.min_area, 60.0)

    def test_accepts_bgra_and_gray_frames(self):
        frame = _background()
        _fill(frame, GLOW_RECT, GREEN_BGR)
        bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.assertTrue(self.detector.analyze(bgra, ROI_BOX).matched)
        gray_result = self.detector.analyze(gray, ROI_BOX)
        self.assertFalse(gray_result.matched, "灰度帧无色相信息，不应命中")

    def test_box_clamped_to_frame(self):
        """ROI 超出帧范围时不抛异常，只检测帧内部分。"""
        oversized = Box(1900, 1000, 200, 200)
        self.assertFalse(self.detector.analyze(_background(), oversized).matched)


if __name__ == "__main__":
    unittest.main()
