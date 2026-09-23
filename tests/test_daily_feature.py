"""DailyFeature 包装器与子任务参数取值（_sub_task_cfg）的测试。

架构约定：
- HomeDailyTask 是自包含单任务（直接继承 BaseGameTask）；
- DailyTask 经 DailyFeature 组合接入，不继承业务逻辑；
- DailyFeature 执行时从 executor 找子任务实例，注入宿主账号上下文与
  账号覆盖查询能力，结束后恢复原状；
- 子任务参数解析顺序：多账号覆盖 → 子任务自身配置（框架已加载
  configs/<任务名>.json）→ 声明默认值。
"""

import unittest

from src.tasks.daily.daily_feature import DailyFeature
from src.tasks.onetime.home_daily_task import HomeDailyTask


def _make_impl(config=None, provider=None):
    """构造绕过框架初始化的 HomeDailyTask 实例（只填测试所需的属性）。"""
    impl = object.__new__(HomeDailyTask)
    impl.name = "家园每日"
    impl.config = config if config is not None else {}
    impl._account_override_provider = provider
    return impl


class _FakeExecutor:
    def __init__(self, tasks):
        self.onetime_tasks = tasks


class _FakeHost:
    """模拟 DailyTask 宿主：提供 executor、账号上下文与覆盖查询能力。"""

    def __init__(self, tasks, account_id="", account_name="", overrides=None):
        self.executor = _FakeExecutor(tasks)
        self.current_account_id = account_id
        self.current_user = account_name
        self._overrides = overrides or {}
        self.logged = []

    def _account_override_for(self, config_name):
        return self._overrides

    def log_info(self, message, *args, **kwargs):
        self.logged.append(str(message))


class TestSubTaskCfg(unittest.TestCase):
    """HomeDailyTask._sub_task_cfg 的解析顺序。"""

    KEY = "喂饭阈值"

    def _impl(self, config, provider=None):
        impl = _make_impl(config=config, provider=provider)
        impl.sub_task_default_config = {self.KEY: 100}
        return impl

    def test_reads_own_config(self):
        impl = self._impl({self.KEY: 55})
        self.assertEqual(impl._sub_task_cfg(self.KEY), 55)

    def test_missing_key_falls_back_to_declared_default(self):
        impl = self._impl({})
        self.assertEqual(impl._sub_task_cfg(self.KEY), 100)

    def test_override_wins_with_type_coercion(self):
        # 磁盘覆盖存字符串 '7'，自身配置基准是 int 55 → 校正为 int 7
        impl = self._impl({self.KEY: 55}, provider=lambda name: {self.KEY: "7"})
        value = impl._sub_task_cfg(self.KEY)
        self.assertEqual(value, 7)
        self.assertIsInstance(value, int)

    def test_provider_exception_is_swallowed(self):
        def broken(name):
            raise RuntimeError("boom")

        impl = self._impl({self.KEY: 55}, provider=broken)
        self.assertEqual(impl._sub_task_cfg(self.KEY), 55)

    def test_undeclared_key_uses_caller_default(self):
        impl = self._impl({})
        self.assertEqual(impl._sub_task_cfg("未声明键", default="兜底"), "兜底")


class TestDailyFeature(unittest.TestCase):
    """DailyFeature 的实例解析、上下文注入与恢复。"""

    def _feature_and_impl(self, tasks=None, overrides=None):
        impl = _make_impl()
        captured = {}

        def fake_run():
            # 执行期间应看到注入的宿主上下文与覆盖查询能力
            captured["account_id"] = impl.current_account_id
            captured["provider"] = impl._account_override_provider
            return "done"

        impl.run = fake_run
        host = _FakeHost(tasks if tasks is not None else [impl], account_id="acc_x",
                         account_name="0705", overrides=overrides)
        feature = DailyFeature(host, HomeDailyTask, switch_key="家园每日")
        return feature, impl, captured, host

    def test_plan_item_returns_switch_key_and_callable(self):
        feature, _, _, _ = self._feature_and_impl()
        key, func = feature.plan_item()
        self.assertEqual(key, "家园每日")
        self.assertTrue(callable(func))

    def test_run_resolves_impl_and_injects_context(self):
        feature, _, captured, host = self._feature_and_impl()
        overrides = {"喂饭阈值": "9"}
        host._overrides = overrides
        self.assertEqual(feature.run(), "done")
        # 注入的 provider 行为上等价于宿主的覆盖查询（绑定方法每次访问是新对象，不能比身份）
        self.assertEqual(captured["provider"]("HomeDailyTask"), overrides)
        self.assertEqual(captured["account_id"], "acc_x")

    def test_run_restores_impl_state_afterwards(self):
        feature, impl, _, _ = self._feature_and_impl()
        feature.run()
        self.assertEqual(impl.current_account_id, "")
        self.assertEqual(impl.current_user, "")
        self.assertIsNone(impl._account_override_provider)

    def test_run_returns_false_when_impl_missing(self):
        feature, _, _, host = self._feature_and_impl(tasks=[])
        self.assertFalse(feature.run())
        self.assertTrue(host.logged)  # 有日志说明走的是「未找到实例」分支

    def test_run_without_host_override_capability(self):
        # 宿主没有 _account_override_for 时也应正常执行并注入 None
        class _PlainHost:
            def __init__(self, tasks, account_id=""):
                self.executor = _FakeExecutor(tasks)
                self.current_account_id = account_id
                self.current_user = ""
                self.logged = []

            def log_info(self, message, *args, **kwargs):
                self.logged.append(str(message))

        impl = _make_impl()
        impl.run = lambda: "ok"
        host = _PlainHost([impl], account_id="acc_x")
        feature = DailyFeature(host, HomeDailyTask, switch_key="家园每日")
        self.assertEqual(feature.run(), "ok")
        self.assertIsNone(impl._account_override_provider)


if __name__ == "__main__":
    unittest.main()
