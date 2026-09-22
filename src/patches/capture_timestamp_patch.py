"""保留 WGC 帧的 QPC 时间戳，供音游预测补偿截图和识别耗时。

ok-script 2.0.6 在 convert_dx_frame 后只保留像素。SystemRelativeTime 是
100ns 单位的 QPC 时间，与 time.perf_counter 使用同一时间轴。升级框架时复核。
"""

from functools import wraps

_INSTALLED = False


def install_capture_timestamp_patch():
    global _INSTALLED
    if _INSTALLED:
        return
    from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

    original = WindowsGraphicsCaptureMethod.convert_dx_frame

    @wraps(original)
    def convert_with_timestamp(self, frame):
        timestamp = frame.SystemRelativeTime / 10_000_000 if frame is not None else None
        image = original(self, frame)
        if image is not None:
            self.frame_timestamp = timestamp
        return image

    WindowsGraphicsCaptureMethod.convert_dx_frame = convert_with_timestamp
    _INSTALLED = True
