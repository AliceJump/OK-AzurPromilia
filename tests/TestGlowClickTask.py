"""泛光点击触发任务测试。

框架调用（截图 / 点击 / 计时 / 覆盖层）全部用 stub 替换，
只验证「检测 → 点击」这条链路的门槛逻辑；检测本身跑在真实合成帧上，不做假。
"""

import unittest

import cv2
import numpy as np
from ok import Box

from src.tasks.trigger.GlowClickTask import GlowClickTask

FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080
ROI = Box(907, 478, 123, 128)  # 归一化 (0.4724, 0.4426, 0.5365, 0.5611)

BACKGROUND_BGR = (30, 30, 30)
GREEN_BGR = (0, 255, 0)  # BGR，HSV 约 (60, 255, 255)
YELLOW_BGR = (0, 255, 255)  # BGR，HSV 约 (30, 255, 255)

# ROI 内的一块泛光
GLOW_RECT = (947, 527, 30, 30)

DEFAULT_CONFIG = {
    "检测绿色": True,
    "检测黄色": True,
    "检测红色": True,
    "最小面积": 60,
    "连续命中帧数": 1,
    "点击冷却(秒)": 0.35,
    "按下时长(秒)": 0.01,
    "点击后等待(秒)": 0.0,
    "记录点击日志": True,
    "画调试框": False,
}


def _make_frame(glow_color=None):
    frame = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), BACKGROUND_BGR, dtype=np.uint8)
    if glow_color is not None:
        x, y, w, h = GLOW_RECT
        cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), glow_color, thickness=-1)
    return frame


class TaskHarness:
    """把 GlowClickTask 从框架里剥出来：假时钟 + 假截图 + 假点击。"""

    def __init__(self, glow_color=None, **config_overrides):
        self.glow_color = glow_color
        self.now = 0.0
        self.logs: list[str] = []
        self.clicks: list[tuple[int, int]] = []

        task = GlowClickTask.__new__(GlowClickTask)
        task.config = dict(DEFAULT_CONFIG)
        task.config.update(config_overrides)

        task._streak = 0
        task._last_click_at = None
        task._detector_cache = None
        task._detector_key = None
        task._roi_cache = None

        task.log_info = lambda message, notify=False: self.logs.append(str(message))
        task.active_time = lambda: self.now
        task.next_frame = lambda: _make_frame(self.glow_color)
        task.click = lambda box, **kwargs: self.clicks.append(box.center())
        task.draw_boxes = lambda *args, **kwargs: None
        task.resolution_scale = lambda: 1.0
        task.box_of_screen = lambda x, y, to_x, to_y, name=None, **kwargs: Box(
            round(x * FRAME_WIDTH),
            round(y * FRAME_HEIGHT),
            round((to_x - x) * FRAME_WIDTH),
            round((to_y - y) * FRAME_HEIGHT),
            name=name,
        )

        self.task = task

    def advance(self, seconds):
        self.now += seconds

    def run(self, times=1, gap=0.1):
        for _ in range(times):
            self.task.run()
            self.advance(gap)


class TestGlowClickTask(unittest.TestCase):
    # ── 1. 命中即点击 ───────────────────────────────────────

    def test_clicks_once_on_green_glow(self):
        harness = TaskHarness(GREEN_BGR)
        harness.run()
        self.assertEqual(harness.clicks, [(962, 542)])

    def test_clicks_on_yellow_glow(self):
        harness = TaskHarness(YELLOW_BGR)
        harness.run()
        self.assertEqual(len(harness.clicks), 1)
        self.assertIn("黄色泛光", harness.logs[0])

    def test_no_click_without_glow(self):
        harness = TaskHarness()
        harness.run(times=5)
        self.assertEqual(harness.clicks, [])

    # ── 2. 冷却 ─────────────────────────────────────────────

    def test_cooldown_prevents_click_storm(self):
        harness = TaskHarness(GREEN_BGR, **{"点击冷却(秒)": 0.35})
        harness.run(times=5, gap=0.1)  # 第 0s / 0.4s 各一次，中间 3 次被冷却挡掉
        self.assertEqual(len(harness.clicks), 2)

    def test_clicks_again_after_cooldown(self):
        harness = TaskHarness(GREEN_BGR, **{"点击冷却(秒)": 0.2})
        harness.run()
        harness.advance(0.5)
        harness.run()
        self.assertEqual(len(harness.clicks), 2)

    # ── 3. 连续命中帧数 ─────────────────────────────────────

    def test_streak_gate_delays_first_click(self):
        harness = TaskHarness(GREEN_BGR, **{"连续命中帧数": 3, "点击冷却(秒)": 0.0})
        harness.run(times=2)
        self.assertEqual(harness.clicks, [], "两帧未达阈值，不该点击")
        harness.run()
        self.assertEqual(len(harness.clicks), 1)

    def test_streak_resets_when_glow_disappears(self):
        harness = TaskHarness(GREEN_BGR, **{"连续命中帧数": 3, "点击冷却(秒)": 0.0})
        harness.run()
        harness.glow_color = None  # 光效消失
        harness.run()
        harness.glow_color = GREEN_BGR
        harness.run()
        self.assertEqual(harness.clicks, [], "中断后重新计数，尚未达到 3 帧")

    # ── 4. 颜色开关 ─────────────────────────────────────────

    def test_disabled_color_is_not_clicked(self):
        harness = TaskHarness(YELLOW_BGR, **{"检测黄色": False})
        harness.run()
        self.assertEqual(harness.clicks, [])

    def test_enabled_color_is_clicked(self):
        harness = TaskHarness(YELLOW_BGR, **{"检测绿色": False, "检测红色": False})
        harness.run()
        self.assertEqual(len(harness.clicks), 1)

    # ── 5. 其他 ─────────────────────────────────────────────

    def test_no_frame_is_ignored(self):
        harness = TaskHarness(GREEN_BGR)
        harness.task.next_frame = lambda: None
        harness.run()
        self.assertEqual(harness.clicks, [])

    def test_roi_matches_configured_region(self):
        harness = TaskHarness()
        self.assertEqual(
            (harness.task._roi().x, harness.task._roi().y,
             harness.task._roi().width, harness.task._roi().height),
            (ROI.x, ROI.y, ROI.width, ROI.height),
        )


if __name__ == "__main__":
    unittest.main()
