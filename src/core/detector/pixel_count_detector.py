"""HSV 像素计数判据：统计搜索区内命中 HSV 范围的像素数量。

适用场景（来自项目实测踩坑）：

* 目标绘制在**半透明面板**上时（如主界面左下角延迟显示的白色 ``45ms`` 文本），
  字形与背景混色，模板匹配（含 HSV 掩码）跨明暗背景的得分会在 0.0~1.0 之间
  大幅波动，无法用统一阈值判定；
* 但同一部件里的**不透明纯色元素**（如延迟显示的绿色信号条，H≈71、跨帧逐像素
  一致）不受背景影响。此时对这类元素按 HSV 范围直接数像素，比模板匹配稳健。

与 ``TemplateDetector`` 的关系：模板匹配回答「这个图案在哪」，本判据只回答
「这个颜色的东西出现了没有」，不给出可点击的目标框（Hit 的 box 是搜索区域）。
"""

from __future__ import annotations

from ok import Box

from src.core.detector.hit import SOURCE_HSV, Hit
from src.image.frame_processes import isolate_by_hsv_ranges


class PixelCountDetector:
    """统计 HSV 命中像素数，达到阈值即视为命中。

    Args:
        hsv_ranges: HSV 范围列表，格式同 ``HSVRange`` 成员：
            ``((h1,s1,v1), (h2,s2,v2))`` 或其列表。
        box: 搜索区域（像素坐标 ``Box``）。与 ``x/y/to_x/to_y`` 二选一。
        x, y, to_x, to_y: 相对屏幕的区域坐标（0~1）。
        min_count: 命中所需的最少像素数（默认 1）。
        name: 判据名称；缺省用 ``hsv_count``。

    Example:
        >>> PixelCountDetector(HSVRange.SIGNAL_GREEN,
        ...                    box=self.box_of_screen(0.02, 0.976, 0.038, 0.999),
        ...                    min_count=60)
    """

    def __init__(
        self,
        hsv_ranges,
        box: Box | None = None,
        x: float = 0,
        y: float = 0,
        to_x: float = 1,
        to_y: float = 1,
        min_count: int = 1,
        name: str | None = None,
    ):
        self._hsv_ranges = hsv_ranges
        self._box = box
        self._region = {"x": x, "y": y, "to_x": to_x, "to_y": to_y}
        self._min_count = max(1, int(min_count))
        self._name = name or "hsv_count"
        self._task = None

    @property
    def name(self) -> str:
        return self._name

    def detect(self, frame) -> Hit | None:
        if self._task is None:
            raise RuntimeError(
                f"{self.__class__.__name__} 未绑定任务宿主，"
                "请先调用 attach(task) 或通过任务辅助方法（如 wait_action_result / detect_with_scroll）调用"
            )
        area, origin = self._crop(frame)
        if area is None or area.size == 0:
            return None
        # invert=False：命中范围置白（默认 True 是取反，数命中像素必须显式关掉）
        mask = isolate_by_hsv_ranges(area, self._hsv_ranges, invert=False)
        count = int((mask > 0).sum())
        if count < self._min_count:
            return None
        x, y = origin
        box = Box(x, y, area.shape[1], area.shape[0], name=self._name)
        return Hit(
            box=box,
            confidence=1.0,
            source=SOURCE_HSV,
            raw=count,
            metrics={"pixel_count": count, "min_count": self._min_count},
        )

    def _crop(self, frame):
        """按 box 或相对区域裁剪画面，返回 (区域图, 区域左上角绝对坐标)。"""
        if self._box is not None:
            x, y = int(self._box.x), int(self._box.y)
            w, h = int(self._box.width), int(self._box.height)
            return frame[y:y + h, x:x + w], (x, y)
        width = self._task_size("width")
        height = self._task_size("height")
        if not width or not height:
            return None, (0, 0)
        x = int(self._region["x"] * width)
        y = int(self._region["y"] * height)
        x2 = int(self._region["to_x"] * width)
        y2 = int(self._region["to_y"] * height)
        return frame[y:y2, x:x2], (x, y)

    def _task_size(self, attr: str) -> int:
        """读任务的宽/高；兼容属性与可调用两种形态（与 OcrDetector 一致）。"""
        value = getattr(self._task, attr, 0)
        return value() if callable(value) else value

    def attach(self, task) -> "PixelCountDetector":
        """绑定宿主任务（识别需要读任务的宽高与帧信息）。"""
        self._task = task
        return self
