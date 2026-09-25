"""login_flow 登录态分支与 wait_feature 覆写的测试。

AccountMixin.login_flow 现在先检测主界面左下角的延迟显示（信号图标，
HSV 像素计数）判断是否已登录：
- 已登录 → 先调用 logout_to_login_screen（占位，待实现）再走既有流程；
- 未登录 → 直接走既有流程；
- login_out 的检测由 find_feature 改为 wait_feature（mask_function 经
  FrameworkOverrideMixin 覆写透传），并显式 raise_if_not_found=False。
"""

import unittest

import cv2
import numpy as np
from ok import Box

from src.core.base_mixin.framework_override_mixin import FrameworkOverrideMixin
from src.data.feature_list import FeatureList
from src.tasks.daily.account_mixin import AccountMixin


def _make_task(ms_found, login_out_hit="fake-hit"):
    """构造绕过框架初始化的 AccountMixin 实例，桩掉所有游戏交互。"""
    task = object.__new__(AccountMixin)
    task.current_user = ""
    task.current_account_id = ""
    task._logged_in = False
    task.calls = []

    task._wait_ms_indicator = lambda: ms_found
    task.logout_to_login_screen = lambda: task.calls.append("logout")

    def fake_wait_feature(feature, **kwargs):
        task.calls.append(("wait_feature", feature, kwargs))
        return login_out_hit

    task.wait_feature = fake_wait_feature
    task.click = lambda *a, **k: task.calls.append("click")
    task.active_and_send_mouse_delta = lambda *a, **k: True
    task.box_of_screen = lambda *a, **k: Box(0, 0, 10, 10)
    # 确认弹窗未出现 → 既有语义在此提前返回，后续步骤无需桩
    task.wait_click_feature = lambda **k: False
    task.log_info = lambda *a, **k: None
    task.log_error = lambda *a, **k: None
    return task


class TestLoginFlowLoggedInBranch(unittest.TestCase):
    def test_logged_in_calls_logout_first(self):
        task = _make_task(ms_found=True)
        AccountMixin.login_flow(task, "1234567890")
        self.assertEqual(task.calls[0], "logout")
        # 登出占位之后仍会继续尝试既有流程
        self.assertIn("click", task.calls)

    def test_not_logged_in_skips_logout(self):
        task = _make_task(ms_found=False)
        AccountMixin.login_flow(task, "1234567890")
        self.assertNotIn("logout", task.calls)
        self.assertIn("click", task.calls)

    def test_login_out_uses_wait_feature_without_raise(self):
        task = _make_task(ms_found=False)
        AccountMixin.login_flow(task, "1234567890")
        wait_calls = [c for c in task.calls if isinstance(c, tuple) and c[0] == "wait_feature"]
        self.assertEqual(len(wait_calls), 1)
        _, feature, kwargs = wait_calls[0]
        self.assertEqual(feature, FeatureList.login_out)
        self.assertIs(kwargs["raise_if_not_found"], False)
        self.assertIsNotNone(kwargs.get("mask_function"))


class TestLogoutPlaceholder(unittest.TestCase):
    def test_placeholder_logs_and_does_not_raise(self):
        task = object.__new__(AccountMixin)
        task.logged = []
        task.log_info = lambda msg, **k: task.logged.append(msg)
        AccountMixin.logout_to_login_screen(task)
        self.assertTrue(task.logged)


class TestWaitFeatureOverride(unittest.TestCase):
    """FrameworkOverrideMixin.wait_feature 的 mask_function 透传。"""

    def test_mask_function_reaches_find_one(self):
        seen = {}

        class _Host:
            """提供 find_one 桩与 wait_until 直通的最小宿主。"""

            def find_one(self, feature, *args, **kwargs):
                seen["feature"] = feature
                seen["kwargs"] = kwargs
                return "hit"

            def wait_until(self, condition, **kwargs):
                return condition()

        host = _Host()
        mask = lambda frame: frame  # noqa: E731
        result = FrameworkOverrideMixin.wait_feature(
            host, "some_feature", time_out=0, mask_function=mask,
        )
        self.assertEqual(result, "hit")
        self.assertEqual(seen["feature"], "some_feature")
        self.assertIs(seen["kwargs"]["mask_function"], mask)

    def test_raise_if_not_found_forwarded(self):
        class _RaisingHost:
            def find_one(self, feature, *args, **kwargs):
                return None

            def wait_until(self, condition, **kwargs):
                seen = {"raise": kwargs.get("raise_if_not_found")}
                if seen["raise"]:
                    raise RuntimeError("not found")
                return condition()

        with self.assertRaises(RuntimeError):
            FrameworkOverrideMixin.wait_feature(
                _RaisingHost(), "f", time_out=0, raise_if_not_found=True,
            )


class TestWaitMsIndicatorWiring(unittest.TestCase):
    """_wait_ms_indicator 的装配：框选区域 + 阈值 + 循环取帧。"""

    def _task_with_frames(self, frames):
        task = object.__new__(AccountMixin)
        task.logged = []
        task.log_info = lambda *a, **k: None
        task.box_of_screen = lambda *args: Box(44, 1057, 27, 19)

        def loop(time_out=10, **kwargs):
            for f in frames:
                yield f

        task.loop = loop
        return task

    def test_green_in_box_reports_logged_in(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        green_bgr = cv2.cvtColor(np.uint8([[(71, 200, 200)]]), cv2.COLOR_HSV2BGR)[0, 0]
        frame[1060:1073, 47:62] = green_bgr
        task = self._task_with_frames([frame])
        self.assertTrue(AccountMixin._wait_ms_indicator(task))

    def test_black_frame_reports_not_logged_in(self):
        task = self._task_with_frames([np.zeros((1080, 1920, 3), np.uint8)])
        self.assertFalse(AccountMixin._wait_ms_indicator(task))


if __name__ == "__main__":
    unittest.main()
