# Action 生命周期（完整参考）

> 本文是 `wait_action_result` / `wait_expectation` 与识别层 `src/core/detector/` 的**完整使用参考**，
> 也是这一主题的**唯一文档**——设计推演、命名取舍、改造前审计的结论都已收进本文（见 §10.1 / §10.2），
> 不再单独维护过程稿。
> `DEVELOPMENT.md` 中的「Action 生命周期」一节是本文摘要，细节以本文为准。

## 目录

- [1. 它解决什么问题](#1)
- [2. 分层模型](#2)
- [3. 快速上手](#3)
- [4. 识别层：统一判据](#4)
  - [4.1 `Hit`：统一命中表示](#41-hit)
  - [4.2 五个适配器（含全部参数）](#42)
  - [4.3 三个组合器](#43)
  - [4.4 判据的 `attach` 与宿主绑定](#44-attach)
- [5. 编排层：`wait_action_result`](#5-wait_action_result)
  - [5.1 三阶段流程](#51)
  - [5.2 完整参数表](#52)
  - [5.3 动作回调签名](#53)
- [6. `wait_expectation`](#6-wait_expectation)
- [7. 典型用法配方](#7)
- [8. 契约与陷阱](#8)
- [9. 什么时候不该用它](#9)
- [10. 从旧 API 迁移](#10-api)
  - [10.1 改造前的调用方实测（决策依据）](#101)
  - [10.2 改造前的实现全景（历史快照）](#102)
- [11. 真实落地样例](#11)

---

## 1. 它解决什么问题

游戏里大量操作是同一个形状：**看到某个东西 → 动它 → 确认预期结果出现了**。
具体拆开是五个环节：

| 代号 | 环节 | 语义 |
|---|---|---|
| **A** | 前置条件 | 识别函数判断当前状态是否满足；未满足则不动作，或按重试机制处理 |
| **Action** | 主动作 | 一次点击 / 按键 / 切页 / 移动 |
| **C** | 结果验证 | 动作后识别预期结果是否出现；命中即成功，未命中则等待/重试/重执行 |
| **B** | 持续条件 | 可选。B 持续命中时重复附加动作；未命中立即停止 |
| **Extra** | 附加动作 | 可选。持续阶段重复执行的动作，每次之后继续查 C |

这套逻辑以前散落在各任务里各写各的，且旧 `click_feature` 有三个无法回避的缺陷：

1. **A 与 C 共用同一个 `feature` 参数** → 只能表达「元素出现 → 点 → 该元素消失」，表达不了 A ≠ C；
2. **只支持模板匹配** → 要等 YOLO / OCR 结果就得另起一套；
3. **没有 B 与附加动作** → 需要「一边成立一边反复操作」时只能手写循环。

现在由两个 API 覆盖：`wait_action_result`（完整生命周期）与 `wait_expectation`（只等结果）。

---

## 2. 分层模型

识别源与生命周期是**两个正交维度**，因此分成两层——否则参数会爆炸，且 A/C 无法使用不同识别源。

```text
┌─────────────────────────────────────────────────────────┐
│  编排层  runtime_mixin.py                                │
│  wait_action_result / wait_expectation                   │
│  职责：「什么时候做、做几次、算不算成功」                    │
└────────────────────────┬────────────────────────────────┘
                         │ 只依赖 Detector.detect(frame)
┌────────────────────────┴────────────────────────────────┐
│  识别层  src/core/detector/                              │
│  Hit + Detector 协议 + 5 个适配器 + 3 个组合器              │
│  职责：「这一帧有没有」                                    │
└────────────────────────┬────────────────────────────────┘
                         │ 包装
┌────────────────────────┴────────────────────────────────┐
│  既有原语  find_feature / find_one / yolo_detect /       │
│            ocr / find_button                             │
└─────────────────────────────────────────────────────────┘
```

**统一判据协议**只有一条：

```python
Detector.detect(frame) -> Hit | None
```

必须**接受外部传入的 frame**，不能自己调 `next_frame()`——否则一次判定内会多次截图，时序错乱。

---

## 3. 快速上手

```python
from src.core.detector import TemplateDetector
from src.data.FeatureList import FeatureList

# A ≠ C：看到宝箱图标 → 点击 → 等解锁界面出现
ok = self.wait_action_result(
    condition=TemplateDetector(FeatureList.treasure_icon),   # A
    action=lambda hit: self.click(hit.box),                  # Action
    expect=TemplateDetector(FeatureList.unlock_ui),          # C
    time_out=5,
    expect_time_out=1.5,
    max_attempts=2,
)
if not ok:
    self.log_info("解锁界面没出现")
```

三个要点：

- `hit` 是 `Hit`，`hit.box` 可直接喂 `self.click()`；
- 判据**不需要手动 `attach`**，编排入口会代劳；
- 返回 `bool`，`True` = 生命周期成功。

---

## 4. 识别层：统一判据

### 4.1 `Hit`：统一命中表示

四类识别源原始返回值各不相同（`Box | None` / `list[Box]` / …），统一归一成 `Hit`：

```python
@dataclass
class Hit:
    box: Box              # 可直接 click() 的目标框（唯一必需字段）
    confidence: float = 1.0
    source: str = ""      # 识别源标识，见 SOURCE_* 常量
    text: str = ""        # OCR 命中文本；非 OCR 源为空串
    raw: object = None    # 原始结果对象，见下
    metrics: dict = field(default_factory=dict)
```

| 字段 | 说明 |
|---|---|
| `box` | 唯一必需。帧坐标，可直接 `click()` / `draw_boxes()` |
| `confidence` | 0~1；未知时 1.0 |
| `source` | `SOURCE_TEMPLATE` / `SOURCE_YOLO` / `SOURCE_OCR` / `SOURCE_BUTTON` / `SOURCE_PREDICATE`；取反判据为原判据名 |
| `text` | OCR 命中的文字，可直接用于日志或分支判断 |
| `raw` | 原始结果：OCR 是 `list[Box]`，模板/按钮是 `Box`。**需要识别器特有字段时从这里取**，避免归一化时丢信息 |
| `metrics` | 预留的指标字典 |

`Hit.__bool__` 恒为 `True` —— 未命中时 `detect()` 返回的是 `None`，不是空 Hit。所以 `if hit:` 是可靠的。

```python
hit = TemplateDetector(FeatureList.main_ui).detect(frame)
if hit:                       # None 才是未命中
    self.click(hit.box)
```

### 4.2 五个适配器（含全部参数）

#### `TemplateDetector` —— 模板匹配

包装 `find_feature` / `find_one`。

```python
TemplateDetector(
    feature,                    # 必填。FeatureList 成员或字符串
    box=None,                   # 搜索区域。None 时沿用 coco 标注位置 ±variance
    horizontal_variance=0.0,
    vertical_variance=0.0,
    threshold=0,                # 匹配阈值；0 = 框架默认
    target_height=0,            # 目标缩放高度；0 = 不缩放
    mask_function=None,         # 掩膜函数，如 make_hsv_isolator(...)
    use_gray_scale=False,
    canny_lower=0,
    canny_higher=0,
    name=None,                  # 判据名；缺省用特征名
    use_find_one=False,         # True 走 find_one，False 走 find_feature
)
```

⚠️ **`box` 的默认行为是陷阱**：`find_feature` 不传 box 时只搜 coco 标注位置 ± variance
（`DEFAULT_VARIANCE = 0.05`）。**会移动的图标必须显式传 `box`**，否则永远搜不到。

⚠️ **`horizontal_variance` / `vertical_variance` 只在使用 `find_feature` 时生效**：
传 0 会在调用处被替换为 `DEFAULT_VARIANCE`（`0.05`），即「用默认方差」而不是「零方差」；
走 `use_find_one=True` 时两个参数原样透传（0 就是 0）。

`use_find_one` 的选择：

| | `find_feature`（默认） | `find_one` |
|---|---|---|
| 特征列表 | ❌ 不支持 | ✅ 任一命中即可 |
| 帧缓存 | ✅ 支持 `Frame` 对象 | — |

#### `YoloDetector` —— YOLO 检测

包装 `yolo_detect`。

```python
YoloDetector(
    name,                       # 必填。目标名或名称列表
    box=None,                   # 裁剪区域；同时作为 pick="center" 的中心基准
    conf=0.7,                   # 置信度阈值
    model_key=None,             # 指定模型键；None 时由框架按首个 name 解析
    pick=PICK_MAX_CONF,         # 多候选选择策略
    task_name=None,             # 判据名；缺省由 name 推导（多个用 _ 连接）
)
```

`pick` 策略（`PICK_*` 常量）：

| 常量 | 值 | 语义 |
|---|---|---|
| `PICK_MAX_CONF` | `max_conf` | **默认**。置信度最高 |
| `PICK_FIRST` | `first` | 框架返回顺序的第一个（`yolo_detect` 按 conf 降序，故接近 max_conf） |
| `PICK_CENTER` | `center` | 离搜索区域中心最近；无 box 时退化到画面中心 |
| `PICK_LEFTMOST` | `leftmost` | 最左（`min(b.x)`） |
| `PICK_TOPMOST` | `topmost` | 最上（`min(b.y)`） |

非法 `pick` 在**构造时**即抛 `ValueError`，不会静默取错候选。

#### `OcrDetector` —— OCR 文本匹配

包装 `ocr`。

```python
OcrDetector(
    match,                      # 必填。文本 / 列表 / 正则 / 正则列表
    box=None,                   # 识别区域；与 x/y/to_x/to_y 二选一
    x=0, y=0, to_x=1, to_y=1,   # 相对区域坐标
    threshold=0,                # OCR 置信度；0 = 框架默认
    lib="default",              # OCR 引擎名
    target_height=0,            # 识别前缩放高度
    pick=PICK_FIRST,            # 多候选选择策略
    name=None,                  # 判据名；缺省用 match 的可读形式
)
```

⚠️ **OCR 的 `pick` 默认是 `PICK_FIRST`，与 YOLO 的 `PICK_MAX_CONF` 不同**。
原因是 `ocr()` 返回的是**按 y 排序**的列表（`ok/task/task.py:818`），
所以 `first` 实际含义是「最上面那个」。这个差异是刻意的。

`pick` 支持的策略与 YOLO 相同（`first` / `max_conf` / `center` / `leftmost` / `topmost`）。

`box` 与相对坐标等价，后者更短：

```python
OcrDetector("确认", box=self.box_of_screen(0.55, 0.58, 0.68, 0.66))
OcrDetector("确认", x=0.55, y=0.58, to_x=0.68, to_y=0.66)     # 等价
```

命中时 `Hit.text` 带出识别到的文字：

```python
hit = OcrDetector("确认", box=area).detect(frame)
self.log_info(f"识别到「{hit.text}」")     # hit.raw 是整个 list[Box]
```

⚠️ **`ocr()` 内部自带调试框**（`emit_draw_box("ocr"+name, ...)`），
所以用 `wait_action_result(draw=True)` 时**不要对 OCR 判据重复绘制**。

#### `ButtonDetectorAdapter` —— 固定 Box 按钮检测

包装 `find_button`（中央文本带，无 OCR）。

```python
ButtonDetectorAdapter(
    box,                        # 必填。按钮区域，必须贴合按钮
    thresholds=None,            # ButtonThresholds 完整阈值
    text_hsv=None,              # 文字颜色区间
    backdrop_hsv=None,          # 底色区间；传入即默认开启底色校验
    require_backdrop=None,      # 显式指定是否校验底色
    name="button",
)
```

⚠️ **`box` 必须贴合按钮**——Box 远大于按钮时文本带相对过薄，会被形状判定拒绝。

用法见 `DEVELOPMENT.md` 的「固定 Box 按钮检测」一节（含 `ButtonThresholds` 预设）。

#### `PredicateDetector` —— 任意判据兜底

把一段自有逻辑包成判据。

```python
PredicateDetector(
    predicate,                  # 必填。frame -> Box | Hit | bool | None
    box=None,                   # predicate 只返回布尔时，用它提供点击目标
    name="predicate",
    source=SOURCE_PREDICATE,
)
```

调用方的返回值决定行为：

| predicate 返回 | 结果 |
|---|---|
| `Hit` | 原样返回 |
| `Box` | 包成 Hit |
| `True`（且给了 `box`） | 用 `box` 造 Hit |
| `True`（未给 `box`） | **返回 None** —— 没有可点的坐标 |
| `False` / `None` | 返回 None |

这也是写「条带还在不在」这类**自定义判定**的入口：

```python
PredicateDetector(
    lambda frame: not self._band_present(frame, band),
    box=band,
    name=f"band_gone_y{band.y}",
)
```

### 4.3 三个组合器

| 组合器 | 签名 | 语义 |
|---|---|---|
| `MultiBoxDetector` | `(detectors, name=None)` | 按序取**首个命中**。`detectors` 为空抛 `ValueError` |
| `FirstHitDetector` | 同上 | `MultiBoxDetector` 的语义别名（多个不同目标的优先级选择时更可读） |
| `InvertedDetector` | `(detector, box=None, name=None)` | 取反：原判据**未**命中即为命中 |
| `BlindPointDetector` | `(x, y, name="blind_point")` | 恒命中，返回指定坐标 |

**`MultiBoxDetector` 可混用不同识别源**——这是它取代旧 `boxes` 参数的关键：

```python
MultiBoxDetector([
    TemplateDetector(FeatureList.treasure_icon),                # 先试模板
    YoloDetector("chest", box=roi, conf=0.7),                    # 再试 YOLO
    OcrDetector("开启", box=text_area),                          # 最后试 OCR
    BlindPointDetector(900, 540),                                # 都搜不到时盲点一次
], name="entry")
```

⚠️ **`InvertedDetector` 必须显式给 `box`**——点击目标无法从取反判据推出。
未给 `box` 时**永不命中**（返回 `None`）。

⚠️ 取反命中时 `Hit.source` 是**原判据名**（如 `not_band_gone`），
因为 `source` 只在 `Hit` 上、detector 本身不持有它。

`FirstHitDetector` 与 `MultiBoxDetector` 行为完全相同，选哪个只看可读性：

- 判据是「同一目标的多个区域」→ `MultiBoxDetector`
- 判据是「多个不同目标的优先级选择」→ `FirstHitDetector`

### 4.4 判据的 `attach` 与宿主绑定

适配器需要宿主任务才能工作（`detect` 内部要调 `self._task.find_feature(...)`）。
**编排入口会自动绑定**，你不需要手动 `attach`：

```python
# 直接构造后传进去即可
self.wait_action_result(
    condition=TemplateDetector(FeatureList.main_ui),
    action=lambda hit: self.click(hit.box),
)
```

自动绑定由 `RuntimeMixin._resolve_detector` 完成，它：

1. 若判据有 `attach` 且尚未绑定，调用 `attach(self)`；
2. 若是组合器（有 `detectors` 属性），**递归**处理内部判据；
3. 若对象没有 `detect(frame)` 方法，抛 `TypeError`。

所以自定义判据只要满足 `detect(frame) -> Hit | None` 就能直接用，
甚至不必继承任何基类——这是协议（Protocol）而非基类继承。

---

## 5. 编排层：`wait_action_result`

### 5.1 三阶段流程

```text
┌─ 阶段一：等条件 ────────────────────────────────┐
│  condition 在 time_out 内命中？                   │
│    否 → raise_if_not_found ? 抛异常 : 返回 False │
│    是 → 继续（settle_time > 0 时要求持续成立）     │
└────────────────────┬────────────────────────────┘
                     ↓
┌─ 阶段二：动作 + 验证（最多 max_attempts 轮）──────┐
│  重取一帧确定目标（防位移）                        │
│  action_delay → action(hit) → after_sleep        │
│  expect is None ?  → 返回 True                   │
│  wait_expectation(expect) 命中 ? → 返回 True     │
│  未命中 → 下一轮                                  │
└────────────────────┬────────────────────────────┘
                     ↓
┌─ 阶段三：持续阶段（需 while_condition 且 repeat_action）┐
│  B 未命中 → 立即 break                            │
│  repeat_interval → repeat_action(hit_b)          │
│  查 expect → 命中则返回 True                      │
│  受 time_out / max_repeat 约束                    │
└────────────────────┬────────────────────────────┘
                     ↓
                  返回 False
```

### 5.2 完整参数表

```python
wait_action_result(
    action,                     # 必填。action(hit: Hit) -> bool | None
    condition,                  # 必填。前置条件判据 A
    expect=None,                # 预期结果判据 C。None = 动作成功即返回
    time_out=5.0,               # 等 condition 的总超时；也约束持续阶段
    settle_time=0.0,            # condition 需持续成立的秒数
    max_attempts=1,             # 动作最多执行次数（含首次）；< 1 会被抬到 1
    action_delay=0.0,           # 每次动作前的延时
    after_sleep=0.0,            # 每次动作后的延时
    expect_time_out=1.0,        # 每次验证 expect 的等待时间
    expect_settle_time=0.0,     # expect 需持续成立的秒数
    while_condition=None,       # 持续条件判据 B
    repeat_action=None,         # 持续阶段重复执行的动作
    repeat_interval=0.0,        # 持续阶段每轮动作的前置延时
    max_repeat=0,               # 持续阶段动作次数上限；0 = 只受 time_out 约束
    draw=False,                 # 是否画调试框
    raise_if_not_found=False,   # 条件未命中时是否抛 WaitFailedException
) -> bool
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `time_out` | `5.0` | **两处生效**：阶段一等 A 的超时、阶段三的总时限 |
| `settle_time` | `0.0` | `0` = 命中一次即可。**非 0 时是「每次判定都要求连续成立够时长」** |
| `max_attempts` | `1` | `1` = 不重试。`expect` 未通过时才进入下一轮 |
| `expect_time_out` | `1.0` | 每次验证 C 的窗口，不宜太大（否则整体变慢） |
| `max_repeat` | `0` | `0` = 不限次数，只受 `time_out` 约束 |
| `draw` | `False` | 绿框 = 动作目标（A），蓝框 = 持续目标（B） |

### 5.3 动作回调签名

`action` 与 `repeat_action` 都接收**当前命中对象**：

```python
action(hit: Hit) -> bool | None      # 返回值未被使用，写成 None 即可
repeat_action(hit: Hit) -> None
```

因为返回值不用，多语句回调写成 lambda 时用元组技巧：

```python
action=lambda hit: (self.log_info(f"点击「{hit.text}」"), self.click(hit.box)),
```

复杂逻辑建议定义独立方法，可读性更好：

```python
def _do_unlock(self, hit):
    self.log_info(f"点击条带 y={hit.box.y}")
    self.click(hit.box)

self.wait_action_result(condition=..., action=self._do_unlock, ...)
```

---

## 6. `wait_expectation`

只用 C 时（只等一个结果）用它——**不要**用 `wait_action_result` 包一层空动作。

```python
wait_expectation(
    expectation,                # 必填。判据
    time_out=1.0,               # 最长等待时间
    settle_time=0.0,            # 需持续成立的秒数
    raise_if_not_found=False,   # 未命中时是否抛 WaitFailedException
) -> Hit | None
```

它是所有「动作之后等结果」的**唯一收口点**——`wait_action_result` 的阶段二/三内部都调它，
以保证 settle 语义一致。

```python
# 等主界面出现
hit = self.wait_expectation(TemplateDetector(FeatureList.main_ui), time_out=2.0)
if hit:
    self.log_info("已进入主界面")

# 等条带稳定消失（要求连续 0.35 秒都不见）
gone = self.wait_expectation(
    PredicateDetector(lambda f: not self._band_present(f, band), box=band),
    time_out=3.0,
    settle_time=0.35,
)
```

---

## 7. 典型用法配方

### 7.1 等到就点（最简）

```python
self.wait_action_result(
    condition=TemplateDetector(FeatureList.skip_dialog),
    action=lambda hit: self.click(hit.box),
    expect=None,                 # 点完即成功
    time_out=3,
)
```

### 7.2 点完等结果出现（A ≠ C）

```python
self.wait_action_result(
    condition=TemplateDetector(FeatureList.treasure_icon),
    action=lambda hit: self.click(hit.box),
    expect=TemplateDetector(FeatureList.unlock_ui),
    time_out=5,
    expect_time_out=1.5,
    max_attempts=2,              # 第一次没开成再点一次
)
```

### 7.3 点完等元素消失

```python
box = self.box_of_screen(...)
self.wait_action_result(
    condition=TemplateDetector(FeatureList.confirm_button, box=box),
    action=lambda hit: self.click(hit.box),
    expect=InvertedDetector(
        TemplateDetector(FeatureList.confirm_button, box=box),
        box=box,                 # 取反必须给 box
    ),
    time_out=5,
    expect_settle_time=0.3,      # 消失要稳定一点
)
```

### 7.4 用 OCR 文本

```python
self.wait_action_result(
    condition=OcrDetector("确认", box=self.box_of_screen(0.55, 0.58, 0.68, 0.66)),
    action=lambda hit: (self.log_info(f"点击「{hit.text}」"), self.click(hit.box)),
    expect=None,
    time_out=3,
)
```

### 7.5 B 成立期间重复操作（旧 API 表达不了）

```python
progress = TemplateDetector(FeatureList.progress_bar, box=bar_box)

self.wait_action_result(
    condition=progress,                                      # A：进度条出现
    action=lambda hit: self.click(hit.box, down_time=0.5),   # 开始长按
    expect=InvertedDetector(progress, box=bar_box),          # C：进度条消失 = 完成
    while_condition=progress,                                # B：进度条还在
    repeat_action=lambda hit: self.send_key("space"),        # 附加动作
    repeat_interval=0.3,
    max_repeat=20,
    time_out=60,
)
```

### 7.6 多区域 / 多识别源回退

```python
MultiBoxDetector([
    TemplateDetector(FeatureList.entry_a),
    TemplateDetector(FeatureList.entry_b, box=region_b),
    OcrDetector("进入", box=text_area),
])
```

### 7.7 条件与预期用不同识别源

```python
self.wait_action_result(
    condition=OcrDetector("可领取", box=button_area),      # A：OCR 判断按钮文字
    action=lambda hit: self.click(hit.box),
    expect=TemplateDetector(FeatureList.reward_icon),      # C：模板判断奖励图标
    time_out=5,
)
```

---

## 8. 契约与陷阱

### `expect=None` 表示「动作成功即返回」

这是**显式声明**「该动作没有可验证的结果」，不是「不验证」也不是「一定失败」。
阶段二执行完 `action` 立即返回 `True`。

⚠️ 如果你其实有结果可验，别偷懒省掉——那正是这个 API 存在的意义。

### `settle_time` 的语义是「每次判定都要连续成立」

不是「总共等 N 秒」。`wait_until` 同语义。**默认 0**。

⚠️ 非 0 会**显著增加耗时**——历史上给 `find_button` 加过 settle 导致性能问题被回滚。
只在「等 UI 稳定下来再确认」时开（如「消失确认」）。

⚠️ 本项目 `src/config.py` 设了 `"wait_until_settle_time": 0`，
所以框架侧的 `settle_time=-1` 在本项目等价于 `0`。但它是用户可配置项，改成非 0 就会分叉。

### 计时用 `active_time()`

暂停感知——脚本暂停期间不消耗 `time_out`。这受 `BaseGameTask` 覆写。

### 条件命中与动作之间会重取一帧

```python
fresh = condition.detect(self.next_frame())
target = fresh if fresh is not None else hit
```

原因：`settle_time > 0` 时动作发生在条件命中之后若干毫秒，**会动的目标**那时可能已位移。
取不到则沿用旧命中值，**不因单帧抖动放弃**。

### `draw=True` 的调试框

- 绿 = 动作目标（A），蓝 = 持续目标（B）
- 框 **4 秒过期**，长循环里会重复绘制
- ⚠️ **OCR 判据不要重复画**——`ocr()` 内部自带调试框

### 返回 `False` 的含义

阶段三也走完仍未成功。调用方据此决定重试、跳过或记录失败，
**不要在里面再写一层 `while True`**——重试逻辑已经由 `max_attempts` / `time_out` 表达了。

### 判据必须接受外部 `frame`

自定义判据若自己调 `next_frame()`，一次判定内会多次截图导致时序错乱。
必须写成 `detect(self, frame)`，用传入的 frame。

---

## 9. 什么时候不该用它

| 场景 | 该用什么 |
|---|---|
| 只等一个结果出现/消失 | `wait_expectation` |
| 只需要「等到就点」 | `wait_click_feature` / `wait_click_ocr` / `wait_click_box` |
| 一次性查一下有没有 | `TemplateDetector(...).detect(frame)` 直接调 |
| 就是要固定延时 | `self.sleep(n)`（但优先用视觉等待） |
| A 未命中要做恢复动作 | `safe_back` 之类，或 `wait_action_result` + `raise_if_not_found` |

⚠️ **等待只应发生在「即将点击的那一次」**。给「查找」类函数加 `settle_time` 会让每帧都变慢。

---

## 10. 从旧 API 迁移

旧 `click_feature` / `wait_feature_disappear` / `feature_stable` / `click_confirm`
**已删除**（均为零调用方的死代码）。对应新写法：

| 旧 | 新 |
|---|---|
| `click_feature(feature, box, ...)` | `wait_action_result(condition=TemplateDetector(feature, box=box), action=..., expect=InvertedDetector(...))` |
| `wait_feature_disappear(feature, box, t)` | `wait_expectation(InvertedDetector(TemplateDetector(feature, box=box), box=box), time_out=t)` |
| `feature_stable(feature, box, d)` | `wait_expectation(TemplateDetector(feature, box=box), time_out=d, settle_time=d)` |
| `click_confirm(...)` | 自建判据；`find_confirm()` 仍可用 |
| `boxes=[None, b1, b2]` 多区域 | `MultiBoxDetector([...])` |
| `blind_point` 参数 | `MultiBoxDetector([..., BlindPointDetector(x, y)])` |

⚠️ **`wait_click_feature` / `wait_click_ocr` 仍保留且在用**（`AccountMixin.login_flow`、
`TestInteractionTask`），暂不转调——收益小于回归风险。它们是「等到就点」的便捷封装，
没有 C 验证需求时继续用它们即可。

### 10.1 改造前的调用方实测（决策依据）

命名与逻辑的取舍取决于「谁在用」。改造前对每个候选函数做过调用方实测：

| 函数 | 改造前位置 | 调用方 | 处置 |
|---|---|---|---|
| `click_feature` | `runtime_mixin.py:198-257` | **无** | ☠️ 删除（能力由新函数承接） |
| `wait_feature_disappear` | `runtime_mixin.py:259-277` | 仅 `click_feature` 内部 | ☠️ 删除（其语义并入 C 判定） |
| `feature_stable` | `runtime_mixin.py:186-196` | 仅 `click_feature` 内部 | ☠️ 删除（改用 `wait_until(settle_time=)`） |
| `click_confirm` | `BaseGameTask.py:453-502` | **无** | ☠️ 删除；**保留 `find_confirm`**（`SkipDialogTask:42` 在用） |
| `safe_back` | `runtime_mixin.py:390-437` | **无** | ⚠️ **保留**（是有效范式） |
| `wait_button` | `runtime_mixin.py:689-727` | **无** | ⚠️ **保留**（文档已公开，删了与文档冲突） |
| `wait_click_feature` | `runtime_mixin.py:1138-1187` | `login_flow`、`TestInteractionTask` | ✅ 保留，暂不转调 |
| `wait_click_ocr` | `runtime_mixin.py:1189-1300` | `TestInteractionTask` | ✅ 保留，暂不转调 |

**关键推论**：这是一次**「先删、再建、最后收口」**的改造，不是「给 `click_feature` 改个名」。
因为它是死代码，最干净的做法是**删除**，让新函数承接能力——而不是把它的名字留下来继续误导人。

> 🪤 **排查教训：`grep click_feature` 会命中 `wait_click_feature` 的子串。**
> 第一轮排查时按 `click_feature` 搜索，把 `wait_click_feature` / `wait_click_ocr` 的调用方
> 误判成了 `click_feature` 的调用方，导致「`click_feature` 有调用方」的错误结论。
> 核实调用方时要用**词边界**（`grep -n "\bclick_feature\b"`）或直接按符号精确定位，
> 不要依赖裸子串匹配。

### 10.2 改造前的实现全景（历史快照）

改造前仓库里存在 8 处形态相近的「识别 → 动作 → 验证」实现，能力覆盖如下
（**A** 前置条件 / **Action** 主动作 / **B** 持续条件 / **Extra Action** B 期间重复动作 / **C** 结果验证）：

| 实现 | 位置 | A | Action | B | Extra Action | C |
|---|---|:---:|:---:|:---:|:---:|:---:|
| `RuntimeMixin.click_feature()` | `runtime_mixin.py:198-257` | ✅ | ✅ | — | — | ⚠️ 消失语义 |
| `TreasureUnlockTask` | `TreasureUnlockTask.py:157-246` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `BaseGameTask.click_confirm()` | `BaseGameTask.py:453-502` | ⚠️ 弱 | ✅ | — | — | ⚠️ 消失语义 |
| `RuntimeMixin.wait_click_feature()` | `runtime_mixin.py:1138-1187` | ✅ | ✅ | — | — | — |
| `RuntimeMixin.wait_click_ocr()` | `runtime_mixin.py:1189-1300` | ✅ | ✅ | — | — | — |
| `RuntimeMixin.safe_back()` | `runtime_mixin.py:390-437` | ⚠️ 反向 | ✅ 恢复 | — | — | — |
| `StarLinkAssistTask.run()` | `StarLinkAssistTask.py:108-147` | ✅ | ✅ | — | — | — |
| `SkipDialogTask.run()` | `SkipDialogTask.py` | ✅ | ✅ | — | — | — |

图例：✅ 已覆盖 ｜ ⚠️ 语义与目标不同 ｜ — 完全缺失

两个关键观测：

1. **`click_feature()` 是当时最接近完整生命周期的实现**（缺 B），但它是死代码；
   而 **A 与 C 共用同一个 `feature` 参数**——它只能表达「某元素出现 → 点击 → 该元素消失」，
   无法表达「看到 A 元素 → 点击 → 期待 C 元素出现」这种 **A ≠ C** 的场景。
   这正是新 API 把 `condition` / `expect` 拆成两个独立判据的直接原因。
2. **`TreasureUnlockTask` 是唯一五环节齐全的实现**，但它是**任务级状态机**
   （`WAIT_TREASURE` / `CALIBRATING_BANDS` / `UNLOCKING` / `COMPLETION_CHECK` / `FINISHED`），
   深度耦合宝箱业务：`_calibrated_bands` 布局缓存、`_detector()` 检测器工厂、
   11 个 `_` 前缀隐藏配置键。它的价值是**被抽取成通用 API 的样本**，
   而不是可复用的组件——所以 §11 只把它的 `_click_band`（C 那一小段）作为落地样例。

---

## 11. 真实落地样例

### `TreasureUnlockTask._click_band`（`src/tasks/trigger/TreasureUnlockTask.py`）

点条带后用「稳定消失」确认生效——只用到 C，所以走 `wait_expectation`：

```python
def _click_band(self, band: Box):
    """点击条带，并用「稳定消失」确认生效。"""
    self.click(band)
    gone = self.wait_expectation(
        PredicateDetector(
            lambda frame: not self._band_present(frame, band),
            box=band,
            name=f"band_gone_y{band.y}",
        ),
        time_out=float(self.config.get("_消失确认超时(秒)", 3.0)),
        settle_time=float(self.config.get("_消失确认时长(秒)", 0.35)),
    )
    if not gone:
        self._on_click_failed(band)
        return
    self._active_bands.remove(band)
    self.log_info(f"条带 y={band.y} 已消失，剩余 {len(self._active_bands)} 条")
```

要点：

- `PredicateDetector` 承载「条带还在不在」这段自有逻辑（用 `presence()` 而非 `find()`，
  因为钥匙压在条带上会把它切成两段）；
- `settle_time=0.35` 要求**连续 0.35 秒都不见**才算消失——这正是 settle 该用的地方；
- 超时与时长都是 `_` 前缀隐藏配置项。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| `DEVELOPMENT.md` | 目录职责、各检测器实现细节、i18n、配置迁移 |
| `QUICKSTART.md` | 从源码运行、开发环境搭建 |
| `tests/TestActionLifecycle.py` | 58 例行为固化，改 API 时会被它挡住 |
