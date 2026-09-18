"""屏幕中心「泛光 / 准心」检测（HSV 阈值 + 连通域，无 OCR、无模板匹配）。

适用问题
--------
瞄准 / 交互提示会在屏幕中心偏上区域出现一块**高饱和、高亮度的彩色光效**：

* 绿色泛光（高概率提示）；
* 黄色泛光（中等概率提示）；
* 红色泛光（低概率提示）；
* 中间带百分比的圆形特殊准心（同样是高饱和高亮彩色）。

这些目标的形状不稳定（有实心块、有圆环、有十字），但**颜色稳定**，
因此统一走 HSV 阈值 + 最大连通域：不看形状，只看「这块区域里有没有
足够大的高饱和亮色团块」，顺带用各颜色掩膜的像素数投票出颜色。

流程
----
    Box ──> 裁剪 ROI ──> RGB→HSV ──> 三色 inRange ──> 闭运算去噪
       ──> 合并掩膜 ──> 取最大连通域 ──> 面积过滤
       ──> 轮廓内各色像素投票 ──> 返回帧坐标 Box + 颜色

判定条件（默认，1920x1080 实测样本）
--------------------------------
    H/S/V 落在 green / yellow / red 区间之一（红色跨 0° 两侧，需两段区间）
    连通域面积 >= 60          # 过滤抗锯齿噪点

坐标约定
--------
返回的 ``Box`` 是**整帧坐标**（不是 ROI 内相对坐标），可直接 ``click()`` /
交给 ``draw_boxes()`` 画在覆盖层上。框采用 OpenCV 语义：
``x, y`` 为左上，``width / height`` 为实际宽高（右下角 = x + width）。

阈值全部集中在 :class:`GlowThresholds`，便于按具体画面微调。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

import cv2
import numpy as np
from ok import Box

#: HSV 三元组 (H, S, V)
HSV = tuple[int, int, int]

#: 三种可检测的泛光颜色
GLOW_GREEN = "green"
GLOW_YELLOW = "yellow"
GLOW_RED = "red"

#: 默认检测顺序（也用作投票平票时的优先顺序）
ALL_GLOW_COLORS: tuple[str, ...] = (GLOW_GREEN, GLOW_YELLOW, GLOW_RED)

#: 颜色显示名（日志用）
GLOW_COLOR_LABELS = {
    GLOW_GREEN: "绿色",
    GLOW_YELLOW: "黄色",
    GLOW_RED: "红色",
}


@dataclass(frozen=True)
class GlowThresholds:
    """泛光检测的全部阈值。

    命名约定：

    * ``green`` / ``yellow`` / ``red`` —— HSV 区间。红色在 HSV 里跨 0° 两端，
      因此需要两段区间，其余颜色一段；
    * ``min_area`` —— 连通域面积下限，单位像素；
    * ``morph_kernel`` —— 闭运算核大小，填缝用（抗锯齿 / 模糊光效会被切断）。

    色相区间刻意**不重叠**：黄绿交界取在 34 / 36 之间（黄 ``15~34``、
    绿 ``36~85``），避免同一像素被两色同时计数导致投票失真。
    实际画面偏色时按 :meth:`with_` 调整。
    """

    # ── 1. 颜色（主特征） ────────────────────────────────────────
    green: tuple[HSV, HSV] = ((36, 80, 80), (85, 255, 255))
    yellow: tuple[HSV, HSV] = ((15, 100, 100), (34, 255, 255))
    red: tuple[tuple[HSV, HSV], tuple[HSV, HSV]] = (
        ((0, 100, 100), (10, 255, 255)),
        ((160, 100, 100), (180, 255, 255)),
    )

    # ── 2. 形态学去噪 ────────────────────────────────────────────
    # 3x3 闭运算：把抗锯齿 / 模糊切断的光效连成整块，不会明显放大边界。
    morph_kernel: int = 3

    # ── 3. 形状过滤 ──────────────────────────────────────────────
    min_area: float = 60.0  # 连通域面积下限（像素）

    # ── 4. 输出 ──────────────────────────────────────────────────
    box_name: str = "glow_target"

    def ranges(self, color: str) -> list[tuple[HSV, HSV]]:
        """返回某颜色的一组 HSV 区间（红色两段，其余一段）。

        Args:
            color: :data:`GLOW_GREEN` / :data:`GLOW_YELLOW` / :data:`GLOW_RED`。

        Returns:
            list[tuple[HSV, HSV]]: ``[(lower, upper), ...]``。

        Raises:
            ValueError: 传入了未知颜色。
        """
        if color not in ALL_GLOW_COLORS:
            raise ValueError(f"未知的泛光颜色: {color}")
        value = getattr(self, color)
        if color == GLOW_RED:
            return [tuple(item) for item in value]
        return [tuple(value)]

    def with_(self, **overrides) -> GlowThresholds:
        """返回改写了部分阈值的副本（不修改基线）。

        Raises:
            TypeError: 传入了不存在的阈值名。
        """
        unknown = sorted(set(overrides) - {f.name for f in fields(self)})
        if unknown:
            raise TypeError(f"未知的阈值参数: {unknown}")
        return replace(self, **overrides)


#: 默认阈值
DEFAULT_GLOW_THRESHOLDS = GlowThresholds()


@dataclass(frozen=True)
class GlowDetection:
    """单次检测结果。

    Attributes:
        matched: 是否命中（是真正的泛光 / 准心）。
        box: 命中时的帧坐标 Box；未命中为 None。
        color: 命中的颜色（``green`` / ``yellow`` / ``red``）。
        area: 连通域面积（像素），阈值校准时看这个。
        counts: 轮廓内各颜色的像素数，用于判断投票是否明确。
        failed: 未命中原因（``empty_input`` / ``empty_roi`` / ``no_color``
            / ``no_contour`` / ``area``）。
        x / y / width / height: 命中区域的实际位置与尺寸（帧坐标）。
    """

    matched: bool
    box: Box | None = None
    color: str = ""
    area: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)
    failed: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def __bool__(self) -> bool:
        return self.matched


class GlowTargetDetector:
    """泛光 / 特殊准心检测器。

    实例只持有阈值与启用颜色，不持有帧数据；请复用实例，不要在每次检测时新建。

    Example:
        >>> detector = GlowTargetDetector(colors=("green",))
        >>> box = task.box_of_screen(0.4724, 0.4426, 0.5365, 0.5611)
        >>> result = detector.analyze(task.next_frame(), box)
        >>> result.color, result.box
        ('green', Box(953, 512, 34, 30))
    """

    def __init__(self, thresholds: GlowThresholds | None = None, colors=None):
        """
        Args:
            thresholds: 自定义阈值，缺省用 :data:`DEFAULT_GLOW_THRESHOLDS`。
            colors: 启用的颜色；缺省全部启用。顺序用于投票平票时的优先级。
        """
        self.thresholds = thresholds or DEFAULT_GLOW_THRESHOLDS
        self.colors = tuple(colors) if colors else ALL_GLOW_COLORS

    # ── 对外 API ─────────────────────────────────────────────

    def find(self, frame, box, name: str | None = None) -> Box | None:
        """检测 Box 内最大的泛光团块。

        Args:
            frame: BGR 帧。
            box: 检测区域（``box_of_screen`` 生成）。
            name: 结果 Box 的名称。

        Returns:
            Box | None: 命中的帧坐标 Box（可直接 click），未命中返回 None。
        """
        return self.analyze(frame, box, name=name).box

    def analyze(self, frame, box, name: str | None = None) -> GlowDetection:
        """执行检测并返回完整结果（含未命中原因与各颜色像素数）。

        Args:
            frame: BGR 帧。
            box: 检测区域。
            name: 命中 Box 的名称。

        Returns:
            GlowDetection: 检测结果。

        Example:
            >>> result = detector.analyze(frame, box)
            >>> result.matched, result.color, result.failed
            (True, 'green', '')
        """
        t = self.thresholds
        if frame is None or box is None:
            return GlowDetection(False, failed="empty_input")

        frame_height, frame_width = frame.shape[:2]
        x, y, width, height = self._clamp_box(box, frame_width, frame_height)
        if width <= 0 or height <= 0:
            return GlowDetection(False, failed="empty_roi")

        roi = frame[y : y + height, x : x + width]
        if roi.size == 0:
            return GlowDetection(False, failed="empty_roi")

        hsv = cv2.cvtColor(self._to_bgr(roi), cv2.COLOR_BGR2HSV)
        masks = {color: self._mask(hsv, color) for color in self.colors}
        if not masks:
            return GlowDetection(False, failed="no_color")

        combined = self._combine(list(masks.values()))
        contours = self._contours(combined)
        if not contours:
            return GlowDetection(False, failed="no_contour")

        largest = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(largest))
        bx, by, bw, bh = cv2.boundingRect(largest)
        common = dict(area=area, x=x + bx, y=y + by, width=bw, height=bh)

        if area < t.min_area:
            return GlowDetection(False, failed="area", **common)

        fill = np.zeros(combined.shape, dtype=np.uint8)
        cv2.drawContours(fill, [largest], -1, 255, thickness=-1)
        counts = {
            color: int(cv2.countNonZero(cv2.bitwise_and(mask, fill)))
            for color, mask in masks.items()
        }
        color = max(counts, key=lambda item: counts[item])
        ratio = counts[color] / max(1, int(np.count_nonzero(fill)))

        return GlowDetection(
            matched=True,
            box=Box(x + bx, y + by, bw, bh, self._confidence(ratio), name or t.box_name),
            color=color,
            counts=counts,
            **common,
        )

    # ── 内部步骤 ─────────────────────────────────────────────

    def _mask(self, hsv: np.ndarray, color: str) -> np.ndarray:
        """单色 HSV inRange（多段区间取并集）+ 闭运算填缝。"""
        t = self.thresholds
        mask = None
        for lower, upper in t.ranges(color):
            part = cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8))
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        size = max(1, int(t.morph_kernel))
        if size <= 1:
            return mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    @staticmethod
    def _combine(masks: list[np.ndarray]) -> np.ndarray:
        """把所有启用颜色的掩膜合并成一张。"""
        combined = masks[0]
        for mask in masks[1:]:
            combined = cv2.bitwise_or(combined, mask)
        return combined

    @staticmethod
    def _contours(mask: np.ndarray):
        """取外轮廓，兼容 OpenCV 3 / 4 的返回值差异。"""
        found = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return found[0] if len(found) == 2 else found[1]

    @staticmethod
    def _confidence(ratio: float) -> float:
        """把「主色像素 / 轮廓像素」折算成 0.5~1.0 的置信度，仅用于展示 / 排序。"""
        return round(0.5 + 0.5 * min(1.0, max(0.0, ratio)), 4)

    @staticmethod
    def _clamp_box(box, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        """把 Box 裁剪到帧内，返回 (x, y, width, height)。"""
        x = max(0, min(int(box.x), max(0, frame_width - 1)))
        y = max(0, min(int(box.y), max(0, frame_height - 1)))
        width = max(0, min(int(box.width), frame_width - x))
        height = max(0, min(int(box.height), frame_height - y))
        return x, y, width, height

    @staticmethod
    def _to_bgr(roi: np.ndarray) -> np.ndarray:
        """统一成 3 通道 BGR，兼容灰度 / BGRA 输入。"""
        if roi.ndim == 2:
            return cv2.cvtColor(roi, cv2.COLOR_GRAY2BGR)
        if roi.shape[2] == 4:
            return cv2.cvtColor(roi, cv2.COLOR_BGRA2BGR)
        return roi
