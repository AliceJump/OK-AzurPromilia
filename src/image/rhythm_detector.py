"""花瓣音游的颜色/形状识别，坐标统一换算到 1920×1080。

只处理像素，不发送输入。轨道上下有浮动，使用整条带而非单行采样。
"""

from dataclasses import dataclass

import cv2
import numpy as np

COLORS = {
    "blue": ((85, 45, 150), (112, 220, 255)),
    "red": ((155, 45, 150), (179, 220, 255)),
    "purple": ((113, 45, 150), (145, 220, 255)),
}
JUDGE_X = 587.0


@dataclass(frozen=True)
class RhythmNote:
    color: str
    head: float
    tail: float
    y: float
    long: bool
    clipped: bool = False

    @property
    def keys(self):
        return {"blue": ("q",), "red": ("e",), "purple": ("q", "e")}[self.color]


def _crop(frame, x1, y1, x2, y2):
    height, width = frame.shape[:2]
    roi = frame[
        round(y1 * height / 1080) : round(y2 * height / 1080), round(x1 * width / 1920) : round(x2 * width / 1920), :3
    ]
    return cv2.resize(roi, (x2 - x1, y2 - y1))


class RhythmDetector:
    @staticmethod
    def _touching_tail(mask, bright, left):
        """用长条圆角的上轮廓还原尾心；相邻短音符可覆盖圆角右半边。"""
        if left < 80:
            return None
        rows = np.arange(mask.shape[0])[:, None]
        top = np.where(mask > 0, rows, mask.shape[0]).min(axis=0)
        bright_top = np.where(bright > 0, rows, mask.shape[0]).min(axis=0)
        columns = np.arange(left - 45, min(left + 13, mask.shape[1]))
        base = np.median(top[left - 80 : left - 45])
        tails = np.arange(left - 45, left + 15, 0.5)
        dx = np.maximum(columns[None, :] - tails[:, None], 0)
        curve = base + 33.5 - np.sqrt(np.maximum(33.5**2 - dx**2, 0))
        curve[dx > 33.5] = mask.shape[0]
        prediction = np.minimum(curve, bright_top[columns])
        errors = np.mean(np.minimum(abs(prediction - top[columns]), 8), axis=1)
        best = int(np.argmin(errors))
        return float(515 + tails[best]) if errors[best] < 2.5 else None

    def is_active(self, frame):
        if frame is None or frame.ndim != 3 or frame.shape[2] < 3 or min(frame.shape[:2]) < 120:
            return False
        # 右侧三枚说明图标固定不动；底部大按键会随命中特效缩放，不能用作闸门。
        for color, y in (("blue", 678), ("red", 726), ("purple", 774)):
            hsv = cv2.cvtColor(_crop(frame, 1846, y - 14, 1874, y + 14), cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, *COLORS[color])
            white = cv2.inRange(hsv, (0, 0, 225), (179, 40, 255))
            if np.mean(mask > 0) < 0.30 or np.mean(white > 0) < 0.025:
                return False
        return True

    def detect(self, frame):
        roi = _crop(frame, 515, 475, 1500, 600)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        notes = []
        for color, bounds in COLORS.items():
            mask = cv2.inRange(hsv, *bounds)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
            _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
            # 圆形音符不透明，长条身体半透明。高亮掩膜能拆开粘连的同色音符。
            bright = cv2.inRange(hsv, (bounds[0][0], bounds[0][1], 240), bounds[1])
            bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
            _, _, bright_stats, _ = cv2.connectedComponentsWithStats(bright)
            circles = [
                (int(cx), int(cy), int(cw), int(ch))
                for cx, cy, cw, ch, ca in bright_stats[1:]
                if 50 <= cw <= 80 and 45 <= ch <= 85 and ca >= 1200
            ]
            for x, y, w, h, area in stats[1:]:
                long = w > 100
                max_h = 105 if long else 85
                if not (45 <= h <= max_h and w >= 43 and area >= 1200):
                    continue
                if y <= 0:
                    continue
                if y + h >= roi.shape[0]:
                    if not long or y > 55 or area / (w * h) < 0.50:
                        continue
                elif area / (w * h) < 0.38:
                    continue
                # 单音符的红色掩膜底部被白色图案分割，圆心应取宽度而非高度。
                radius = 33.5 if long else w / 2
                head = float(515 + x + radius)
                tail = float(515 + x + w - radius)
                if long:
                    following = [
                        (cx, cy, cw, ch)
                        for cx, cy, cw, ch in circles
                        if (cx - x > 30) and cx + cw <= x + w + 2 and abs(cy - y) < 15
                    ]
                    for cx, cy, cw, ch in following:
                        center = float(515 + cx + cw / 2)
                        notes.append(RhythmNote(color, center, center, float(475 + cy + ch / 2), False))
                    if following:
                        first_cx = min(cx for cx, _, _, _ in following)
                        fitted_tail = self._touching_tail(mask, bright, first_cx)
                        tail = fitted_tail if fitted_tail is not None else float(515 + first_cx - 10.5)
                # 花瓣背景也可能有蓝色，要求音符头部内有白色乐符。
                core = hsv[y : y + h, x : x + min(w, 67)]
                white = cv2.inRange(core, (0, 0, 230), (179, 45, 255))
                if not long and np.count_nonzero(white) < 80:
                    continue
                notes.append(RhythmNote(color, head, tail, float(475 + y + h / 2), long, x + w >= roi.shape[1] - 2))
        return sorted(notes, key=lambda note: note.head)
