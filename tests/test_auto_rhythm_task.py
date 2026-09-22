"""真实游戏截图回归 + 连续音符/遮挡/长条/停止输入的时序测试。"""

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from src.config import config
from src.image.rhythm_detector import RhythmDetector, RhythmNote
from src.tasks.trigger.auto_rhythm_task import AutoRhythmTask, RhythmPlayer

FIXTURES = Path(__file__).parent / "fixtures" / "rhythm"


def note(x, color="blue", length=0):
    return RhythmNote(color, x, x + length, 535, length > 0)


def task_harness():
    executor, app = MagicMock(), MagicMock()
    app.tr.side_effect = lambda text: text
    executor.paused = False
    executor.exit_event = threading.Event()
    task = AutoRhythmTask(executor, app)
    task._enabled = True
    task.config = dict(task.default_config)
    return task


class TestRhythmVision(unittest.TestCase):
    def setUp(self):
        self.detector = RhythmDetector()

    def test_real_frames_at_multiple_resolutions(self):
        for scale in (0.667, 1.0, 1.333):
            for name, colors in (
                ("blue_red", {"blue", "red"}),
                ("purple", {"purple"}),
                ("long_red", {"red"}),
                ("long_blue", {"blue"}),
            ):
                with self.subTest(scale=scale, name=name):
                    frame = cv2.imread(str(FIXTURES / f"{name}.png"))
                    frame = cv2.resize(frame, None, fx=scale, fy=scale)
                    self.assertTrue(self.detector.is_active(frame))
                    notes = self.detector.detect(frame)
                    self.assertEqual({item.color for item in notes}, colors)
                    if name == "long_red":
                        long = next(item for item in notes if item.long)
                        self.assertAlmostEqual(long.head, 897.5, delta=3)
                        self.assertAlmostEqual(long.tail, 1256.5, delta=3)
                    elif name == "long_blue":
                        long = next(item for item in notes if item.long)
                        self.assertAlmostEqual(long.head, 557.5, delta=3)
                        self.assertAlmostEqual(long.tail, 1250.5, delta=3)

    def test_world_rejected_effect_animation_accepted(self):
        self.assertFalse(self.detector.is_active(cv2.imread(str(FIXTURES / "world.png"))))
        self.assertTrue(self.detector.is_active(cv2.imread(str(FIXTURES / "hit_effect.png"))))
        self.assertFalse(self.detector.is_active(None))

    def test_long_tail_touching_next_same_color_note(self):
        for scale in (0.667, 1.0, 1.333):
            frame = cv2.imread(str(FIXTURES / "touching_red.png"))
            notes = self.detector.detect(cv2.resize(frame, None, fx=scale, fy=scale))
            self.assertEqual(len(notes), 3)
            self.assertEqual([item.long for item in notes], [True, False, False])
            self.assertAlmostEqual(notes[0].tail - notes[0].head, 405, delta=4)
            self.assertAlmostEqual(notes[1].head - notes[0].tail, 45, delta=4)

    def test_touching_red_shifted_separation(self):
        frame = cv2.imread(str(FIXTURES / "touching_red.png"))
        h, w = frame.shape[:2]
        # 长条头部移出 ROI 区域（shift 480~530）时，紧随的短音符不被长条吞噬
        for shift in (480, 500, 520):
            with self.subTest(shift=shift):
                M = np.float32([[1, 0, -shift], [0, 1, 0]])
                shifted = cv2.warpAffine(frame, M, (w, h))
                notes = self.detector.detect(shifted)
                long_notes = [item for item in notes if item.long]
                short_notes = [item for item in notes if not item.long]
                self.assertEqual(len(long_notes), 1)
                self.assertTrue(any(abs(item.head - (1112.5 - shift)) < 10 for item in short_notes))
                self.assertAlmostEqual(long_notes[0].tail, 1068.5 - shift, delta=4)


class TestRhythmTiming(unittest.TestCase):
    def test_all_colors_and_adjacent_same_color(self):
        for color, keys in (("blue", ("q",)), ("red", ("e",)), ("purple", ("q", "e"))):
            player = RhythmPlayer()
            hits = []
            for i in range(140):
                now = i / 100
                notes = [note(1100 + offset - 720 * now, color) for offset in (0, 90)]
                notes = [item for item in notes if item.head > 540]
                pressed, hit = player.update(notes, now)
                if hit:
                    hits.append(hit)
                    self.assertEqual(pressed, keys)
            self.assertEqual(len(hits), 2)
            self.assertEqual(player.held, ())

    def test_covered_note_is_predicted_without_rehitting_residue(self):
        player = RhythmPlayer()
        hits = []
        for i in range(130):
            now = i / 100
            x = 1100 - 720 * now
            notes = [note(x)] if x > 850 else []
            if 0.9 < now < 1.1:
                notes.append(note(587))  # 命中后的残影不创建新轨迹。
            _, hit = player.update(notes, now)
            if hit:
                hits.append(hit)
        self.assertEqual(len(hits), 1)

    def test_long_notes_hold_until_tail(self):
        for color in ("blue", "red", "purple"):
            player = RhythmPlayer()
            hits = 0
            for i in range(150):
                now = i / 100
                head = 1100 - 720 * now
                tail = head + 360
                notes = [note(head, color, 360)] if head > 540 else []
                if head <= 540 and tail > 640:
                    notes = [RhythmNote(color, 550, tail, 535, True)]
                keys, hit = player.update(notes, now)
                hits += hit is not None
                if 0.78 < now < 1.18:
                    self.assertEqual(keys, note(0, color).keys)
            self.assertEqual(hits, 1)
            self.assertEqual(player.held, ())

    def test_new_track_at_judge_does_not_press(self):
        player = RhythmPlayer()
        self.assertEqual(player.update([note(587)], 0), ((), None))

    def test_long_release_allows_adjacent_same_color_short(self):
        player = RhythmPlayer()
        events = []
        held_states = []
        for i in range(400):
            now = i * 0.005
            head = 1250 - 600 * now
            notes = [note(head, "red", 405), note(head + 450, "red"), note(head + 540, "red")]
            pressed, hit = player.update(notes, now, lead=player.speed * 0.045)
            if hit:
                events.append(now)
            held_states.append((now, pressed))
        self.assertEqual(len(events), 3)
        for at, offset in zip(events, (0, 450, 540), strict=True):
            self.assertAlmostEqual(at, (1250 + offset - 587) / 600 - 0.045, delta=0.006)
        # 验证长条在短音符击打前成功释放按键（长条松手紧跟着按下）
        releases = [at for at, held in held_states if events[0] < at < events[1] and held == ()]
        self.assertTrue(releases)

    def test_touching_red_real_frames_playback(self):
        detector = RhythmDetector()
        frame = cv2.imread(str(FIXTURES / "touching_red.png"))
        h, w = frame.shape[:2]
        player = RhythmPlayer()
        speed = 720.0
        dt = 0.02
        hits = []
        held_states = []
        for i in range(80):
            now = i * dt
            shift = int(-535.5 + speed * now)
            M = np.float32([[1, 0, -shift], [0, 1, 0]])
            shifted = cv2.warpAffine(frame, M, (w, h))
            notes = detector.detect(shifted)
            held, hit = player.update(notes, now, lead=speed * 0.045)
            if hit:
                hits.append((now, hit))
            held_states.append((now, held))
        self.assertEqual(len(hits), 3)
        self.assertTrue(hits[0][1].long)
        self.assertFalse(hits[1][1].long)
        # 确认真实画面推进时长条在短音符击打前成功松手
        released = any(held == () for t, held in held_states if hits[0][0] < t < hits[1][0])
        self.assertTrue(released)

    def test_capture_and_processing_latency_do_not_shift_hit_late(self):
        for processing_delay in (0.0, 0.02, 0.055):
            player = RhythmPlayer()
            hit_times = []
            for i in range(180):
                captured_at = i * 0.005
                now = captured_at + processing_delay
                notes = [note(1100 - 720 * captured_at)]
                _, hit = player.update(notes, now, lead=720 * 0.045, observed_at=captured_at)
                if hit:
                    hit_times.append(now)
            self.assertEqual(len(hit_times), 1)
            ideal = (1100 - 587) / 720 - 0.045
            self.assertAlmostEqual(hit_times[0], ideal, delta=0.006)

    def test_effect_cannot_turn_tracked_short_note_into_hold(self):
        player = RhythmPlayer()
        hits = []
        for i in range(100):
            now = i / 100
            head = 1100 - 720 * now
            _, hit = player.update([note(head, length=200 if head < 950 else 0)], now)
            if hit:
                hits.append(hit)
        self.assertEqual(len(hits), 1)
        self.assertFalse(hits[0].long)

    def test_long_release_clamped_before_conflicting_note(self):
        # 1. 红色长条紧跟同色短音符，验证长条 release_at 在短音符击打前至少提前 45ms 释放
        player = RhythmPlayer()
        speed = 720.0
        dt = 0.01
        hits, long_release = [], 0.0
        for i in range(110):
            now = i * dt
            h1, h2 = 1200 - speed * now, 1245 - speed * now
            notes = [note(h, "red", 405 if idx == 0 else 0) for idx, h in enumerate((h1, h2)) if h > 820]
            held, hit = player.update(notes, now, lead=speed * 0.045)
            if hit:
                hits.append((now, hit))
                if hit.long:
                    long_release = player.release_at
        self.assertEqual(len(hits), 2)
        self.assertLessEqual(long_release, hits[1][0] - 0.045 + 0.001)

        # 2. 红色长条紧跟紫色短音符（共享 key 'e'），验证同样受冲突约束提前释放
        player2 = RhythmPlayer()
        hits2, long_release2 = [], 0.0
        for i in range(110):
            now = i * dt
            h1, h2 = 1200 - speed * now, 1245 - speed * now
            notes = [
                note(h, "red" if idx == 0 else "purple", 405 if idx == 0 else 0)
                for idx, h in enumerate((h1, h2))
                if h > 820
            ]
            held, hit = player2.update(notes, now, lead=speed * 0.045)
            if hit:
                hits2.append((now, hit))
                if hit.long:
                    long_release2 = player2.release_at
        self.assertEqual(len(hits2), 2)
        self.assertLessEqual(long_release2, hits2[1][0] - 0.045 + 0.001)


class TestRhythmTaskLifecycle(unittest.TestCase):
    def test_construct_and_registered(self):
        task = task_harness()
        self.assertEqual(task.name, "自动音游")
        self.assertIn(["src.tasks.trigger.auto_rhythm_task", "AutoRhythmTask"], config["trigger_tasks"])

    def test_double_down_before_either_up_and_disable_cleanup(self):
        task = task_harness()
        interaction = task.executor.interaction
        task._set_keys(("q", "e"), retrigger=True)
        task.disable()
        actions = [(call[0], call.args[0]) for call in interaction.mock_calls]
        self.assertEqual(actions[:2], [("send_key_down", "q"), ("send_key_down", "e")])
        self.assertEqual(set(actions[2:]), {("send_key_up", "q"), ("send_key_up", "e")})
        self.assertFalse(task._held_keys)
        task._set_keys(("q",))
        self.assertEqual(len(interaction.mock_calls), 4)

    def test_watchdog_releases_on_pause_focus_loss_stop_and_capture_stall(self):
        for reason in ("pause", "focus", "stop", "stall"):
            with self.subTest(reason=reason):
                task = task_harness()
                task._set_keys(("q", "e"))
                task._last_frame_at = time.perf_counter() - (1 if reason == "stall" else 0)
                task.executor.paused = reason == "pause"
                if reason == "stop":
                    task.executor.exit_event.set()
                with patch(
                    "src.tasks.trigger.auto_rhythm_task.win32gui.GetForegroundWindow",
                    return_value=0 if reason == "focus" else 123,
                ):
                    task._watch_input(threading.Event(), 123)
                self.assertTrue(task._cancel.is_set())
                self.assertFalse(task._held_keys)

    def test_exception_after_press_releases_keys(self):
        task = task_harness()
        frame = cv2.imread(str(FIXTURES / "blue_red.png"))
        task.next_frame = MagicMock(side_effect=[frame, RuntimeError("capture failed")])
        task.get_game_hwnd = lambda: 123
        player = MagicMock()
        player.next_deadline.return_value = float("inf")
        player.update.return_value = (("q", "e"), note(587, "purple"))
        with (
            patch("src.tasks.trigger.auto_rhythm_task.win32gui.GetForegroundWindow", return_value=123),
            patch("src.tasks.trigger.auto_rhythm_task.RhythmPlayer", return_value=player),
            self.assertRaisesRegex(RuntimeError, "capture failed"),
        ):
            task.run()
        self.assertFalse(task._held_keys)
        self.assertEqual(task.executor.interaction.send_key_up.call_count, 2)


    @patch("src.tasks.trigger.auto_rhythm_task.time.sleep")
    def test_retrigger_sleeps_between_up_and_down(self, mock_sleep):
        task = task_harness()
        task._held_keys = {"e"}
        task._set_keys(("e",), retrigger=True)
        mock_sleep.assert_called_once_with(0.015)
        calls = [(call[0], call.args[0]) for call in task.executor.interaction.mock_calls]
        self.assertEqual(calls, [("send_key_up", "e"), ("send_key_down", "e")])


if __name__ == "__main__":
    unittest.main()
