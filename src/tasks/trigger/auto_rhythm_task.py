"""花瓣音游：界面出现后独占逐帧判定，退出、暂停、失焦时释放按键。"""

import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import win32gui
from ok import TriggerTask

from src.core.base_game_task import BaseGameTask
from src.icons import Icons
from src.image.rhythm_detector import JUDGE_X, RhythmDetector, RhythmNote

logger = logging.getLogger(__name__)


@dataclass
class _Track:
    note: RhythmNote
    seen: float
    hit: bool = False
    history: list = field(default_factory=list)
    speed: float = 720.0
    fitted_head: float = 0.0

    def predict(self, at):
        return self.fitted_head - self.speed * (at - self.seen)

    def observe(self, note, at):
        if self.history and at <= self.seen:
            return
        self.note, self.seen = note, at
        self.history.append((at, note.head))
        self.history = [(t, x) for t, x in self.history if at - t <= 0.4]
        self.fitted_head = note.head
        if len(self.history) >= 5 and at - self.history[0][0] >= 0.12:
            # 用多帧直线拟合吸收重复画面、游戏帧步进及单帧掩膜边缘抖动。
            points = np.asarray(self.history)
            times = points[:, 0] - at
            centered = times - times.mean()
            velocity = -float(np.dot(centered, points[:, 1] - points[:, 1].mean()) / np.dot(centered, centered))
            if 250 < velocity < 1600:
                self.speed = velocity
                self.fitted_head = float(points[:, 1].mean() + velocity * times.mean())


class RhythmPlayer:
    """时序判定独立于框架，便于用真实截图序列回放。"""

    def __init__(self):
        self.tracks = []
        self.speed = 720.0
        self.held = ()
        self.hold_color = None
        self.release_at = 0.0

    def next_deadline(self, lead_seconds):
        pending = [
            track.seen + (track.fitted_head - JUDGE_X) / track.speed - lead_seconds
            for track in self.tracks
            if not track.hit
        ]
        if self.held:
            pending.append(self.release_at)
        return min(pending, default=float("inf"))

    def _clamp_release(self, lead):
        if not (self.held and self.hold_color):
            return
        conflicts = [
            track.seen + (track.fitted_head - JUDGE_X) / track.speed - lead / self.speed
            for track in self.tracks
            if not track.hit and any(k in self.held for k in track.note.keys)
        ]
        if conflicts:
            self.release_at = min(self.release_at, min(conflicts) - 0.045)

    def update(self, notes, now, lead=12.0, observed_at=None):
        observed_at = now if observed_at is None else observed_at
        self.tracks = [track for track in self.tracks if now - track.seen < 0.8]
        available = list(self.tracks)
        hits = []
        for note in notes:
            if note.head < JUDGE_X + 220:
                continue
            candidates = [
                track
                for track in available
                if track.note.color == note.color and abs(note.head - track.predict(observed_at)) < 38
            ]
            track = min(
                candidates,
                key=lambda item: abs(note.head - item.predict(observed_at)),
                default=None,
            )
            if track is None:
                # 判定圈附近只有特效/已消耗音符的残影，不在此创建新轨迹。
                # 真正的音符会先经过右侧清晰区域，再进入判定圈。
                if not 1000 <= note.head <= 1400:
                    continue
                track = _Track(note, observed_at, speed=self.speed, fitted_head=note.head)
                track.observe(note, observed_at)
                self.tracks.append(track)
            else:
                available.remove(track)
                if note.long != track.note.long and note.head < 1000:
                    continue
                track.observe(note, observed_at)
        speeds = [track.speed for track in self.tracks if len(track.history) >= 5]
        if speeds:
            self.speed = float(np.median(speeds))

        # 松键同样补偿输入延迟；不添加滞后，确保紧随的短音符留足抬起间隙。
        release_offset = - lead / self.speed
        if self.hold_color:
            bodies = [
                note
                for note in notes
                if note.color == self.hold_color and note.long and note.head <= JUDGE_X + 55 and note.tail > JUDGE_X
            ]
            if bodies:
                tail = min(bodies, key=lambda note: note.tail).tail
                self.release_at = observed_at + (tail - JUDGE_X) / self.speed + release_offset
            self._clamp_release(lead)
        if self.held and now >= self.release_at:
            self.held = ()
            self.hold_color = None

        for track in self.tracks:
            note = track.note
            predicted = track.predict(now)
            # 判定圈的花瓣特效会遮住下一枚音符，用进入特效前的轨迹短暂外推。
            if (
                not track.hit
                and now - track.seen < 0.65
                and JUDGE_X - 40 <= predicted <= JUDGE_X + lead * track.speed / self.speed
                and not any(k in self.held for k in note.keys)
            ):
                track.hit = True
                displacement = note.head - predicted
                hits.append(
                    RhythmNote(note.color, predicted, note.tail - displacement, note.y, note.long, note.clipped)
                )

        hit = min(hits, key=lambda note: abs(note.head - JUDGE_X), default=None)
        if hit is not None:
            self.held = hit.keys
            self.hold_color = hit.color if hit.long else None
            self.release_at = now + (max(0, hit.tail - JUDGE_X) / self.speed + release_offset if hit.long else 0.045)
            self._clamp_release(lead)
        return self.held, hit


class AutoRhythmTask(BaseGameTask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动音游"
        self.description = "检测到音游界面自动完成。"
        self.icon = Icons.Trigger
        self.trigger_interval = 0
        self.default_config.update({"输入延迟 (ms)": 50})
        self._rhythm_detector = RhythmDetector()
        self.player = RhythmPlayer()
        self._key_lock = threading.RLock()
        self._held_keys = set()
        self._last_frame_at = 0.0
        self._cancel = threading.Event()

    def _release_keys(self):
        # 不走 BaseTask.send_key_up：它在禁用/暂停时先 check_enabled，会阻止清理。
        with self._key_lock:
            for key in tuple(self._held_keys):
                try:
                    self.executor.interaction.send_key_up(key)
                except Exception:
                    logger.exception("Failed to release rhythm key %s", key)
                else:
                    self._held_keys.discard(key)

    def _set_keys(self, keys, retrigger=False):
        with self._key_lock:
            if self._cancel.is_set():
                return
            wanted = set(keys)
            retriggered = []
            for key in tuple(self._held_keys):
                if key not in wanted or retrigger:
                    self.executor.interaction.send_key_up(key)
                    self._held_keys.remove(key)
                    if key in wanted:
                        retriggered.append(key)
            if retriggered:
                # 重新触发同一按键时保持至少 15ms 的按键抬起态，避免微秒级重触发被游戏输入层判定为持续按住。
                time.sleep(0.015)
            for key in keys:
                if key not in self._held_keys:
                    # 双键连续 key-down，中间不截图、不 sleep，之后统一 key-up。
                    if self.executor.interaction.send_key_down(key, activate=False) is False:
                        raise RuntimeError("Rhythm key-down failed")
                    self._held_keys.add(key)

    def _watch_input(self, done, hwnd):
        while not done.wait(0.02):
            if (
                self.executor.paused
                or not self.enabled
                or self.executor.exit_event.is_set()
                or win32gui.GetForegroundWindow() != hwnd
                or time.perf_counter() - self._last_frame_at > 0.3
            ):
                self._stop_reason = "pause, disable, focus loss or stalled capture"
                self._cancel.set()
                self._release_keys()
                return

    def run(self):
        capture_started = time.perf_counter()
        frame = self.next_frame()
        captured = time.perf_counter()
        if not self._rhythm_detector.is_active(frame):
            return
        hwnd = self.get_game_hwnd()
        if not hwnd or win32gui.GetForegroundWindow() != hwnd:
            return
        self.player = RhythmPlayer()
        self._cancel.clear()
        self._last_frame_at = captured
        done = threading.Event()
        watcher = threading.Thread(target=self._watch_input, args=(done, hwnd), daemon=True)
        last_band = None
        changed_at = captured
        input_lead = max(0.0, min(0.15, float(self.config.get("_input_lead_ms", 45)) / 1000))
        watcher.start()
        try:
            while not self._cancel.is_set():
                now = time.perf_counter()
                self._last_frame_at = now
                if not self._rhythm_detector.is_active(frame):
                    self._stop_reason = "rhythm interface disappeared"
                    break
                height, width = frame.shape[:2]
                band = frame[
                    round(height * 0.44) : round(height * 0.56) : 4, round(width * 0.27) : round(width * 0.79) : 4
                ]
                if last_band is None or not np.array_equal(band, last_band):
                    changed_at = now
                    last_band = band.copy()
                elif now - changed_at > 0.3:
                    self._stop_reason = "unchanged capture"
                    break
                # WGC 时间戳是画面产生时间；不能把识别完成时间当作音符观测时间。
                method = getattr(self.executor, "method", None)
                observed_at = getattr(method, "frame_timestamp", None)
                if not isinstance(observed_at, (float, int)) or abs(observed_at - captured) > 0.2:
                    observed_at = captured - min(captured - capture_started, 0.04) / 2
                notes = self._rhythm_detector.detect(frame)
                now = time.perf_counter()
                keys, hit = self.player.update(notes, now, self.player.speed * input_lead, observed_at)
                self._set_keys(keys, retrigger=hit is not None)
                # 若下一次截图与识别会跨过按键时刻，先服务定时输入，再截图。
                horizon = time.perf_counter() + min(max((captured - capture_started) + 0.025, 0.035), 0.060)
                while not self._cancel.is_set():
                    deadline = self.player.next_deadline(input_lead)
                    now = time.perf_counter()
                    if not now < deadline <= horizon:
                        break
                    time.sleep(max(0, deadline - now))
                    keys, hit = self.player.update([], time.perf_counter(), self.player.speed * input_lead)
                    self._set_keys(keys, retrigger=hit is not None)
                capture_started = time.perf_counter()
                frame = self.next_frame()
                captured = time.perf_counter()
        finally:
            done.set()
            self._cancel.set()
            self._release_keys()
            watcher.join(timeout=0.1)

    def disable(self):
        self._cancel.set()
        self._release_keys()
        super().disable()

    def on_destroy(self):
        self._cancel.set()
        self._release_keys()
        super().on_destroy()
