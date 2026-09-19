# Action 生命周期封装：拓展设计

> **状态：已实施。** 识别层落在 `src/core/detector/`（`Hit` / `Detector` + 四类适配器 +
> 组合器），编排层落在 `RuntimeMixin.wait_action_result` / `wait_expectation`。
> 本文保留设计推演过程；命名与最终参数以 `ACTION_LIFECYCLE_RENAME.md` 与
> `DEVELOPMENT.md`「Action 生命周期」一节为准。
>
> 前置阅读：`ACTION_LIFECYCLE_AUDIT.md`（现状审计）。
> 本文回答「`click_feature` 如何同时支持模板 / YOLO / OCR，并拓展为完整 Action 生命周期」。

## 0. 需求拆解与核心矛盾

### 需求

`click_feature` 现在的问题有两个，**必须一起解决**，否则改完一个会卡在另一个上：

**问题一：只支持一种识别源（模板匹配）**

现有实现全靠 `find_feature`（框架模板匹配）+ 少量 `find_button`（固定 Box 按钮检测）。
但仓库里已经有**四类识别能力**，各有各的返回值形状：

| 识别源 | 入口 | 返回值 | 特殊性 |
|---|---|---|---|
| 模板匹配 | `find_feature` / `find_one` | `Box \| None` | 需要 box 或 coco 标注位置 |
| YOLO | `yolo_detect(name, frame, box, conf)` | `list[Box]`（按 conf 降序） | 返回**多个**候选，要选哪个是业务决策 |
| OCR | `ocr(match=...)` / `wait_ocr(...)` | OCR 结果对象 | 判据是**文本**，且有 `recheck_time` 复检习惯 |
| 专用检测器 | `ButtonDetector` / `GlowTargetDetector` / `TreasureBandDetector` | `.find() -> Box\|None` 或 `list[Box]` | 阈值参数与帧处理各不相同 |

**问题二：生命周期不完整**

按 `ACTION_LIFECYCLE_AUDIT.md` 的结论，`click_feature` 缺的是 **B（持续条件）+ Extra Action**，
且 A/C 共用同一个 `feature` 参数（无法表达 A≠C），C 只有「消失」语义。

### 核心矛盾

**「识别源」与「生命周期」是两个正交维度，但它们现在被同一个 `feature` 参数耦合在一起。**

如果把两者都塞进 `click_feature` 的签名，会得到类似这样的东西：

```python
# ❌ 反例：参数爆炸，且 A 和 C 无法用不同的识别源
def click_feature(self, feature=None, yolo_name=None, ocr_match=None,
                  button_box=None, glue_detector=None, ...):   # 参数越加越多
```

**正确的拆法是分层**：

```
┌─────────────────────────────────────────────┐
│  编排层：Action 生命周期（A → Action → C）    │  ← 只关心「什么时候做什么」
├─────────────────────────────────────────────┤
│  识别层：统一的「判据协议」                    │  ← 把四类识别源归一成同一个形状
├─────────────────────────────────────────────┤
│  现有实现：find_feature / yolo_detect / ...   │  ← 不动
└─────────────────────────────────────────────┘
```

一句话概括方案：**把「识别源」归一化成可调用的判据对象，让生命周期只操作判据对象。**

---

## 1. 识别层设计：统一判据协议（Detector Protocol）

### 1.1 为什么需要一个「工厂」而不是直接传函数

最朴素的写法是让调用方传 lambda：

```python
# 第一直觉
self.run_action_cycle(
    check_a=lambda: self.find_feature(feature_name=FeatureList.confirm_button, frame=self.next_frame()),
    action=lambda: self.click(...),
    verify_c=lambda: self.find_one(FeatureList.main_ui, frame=self.next_frame()),
)
```

**问题：每个 lambda 内部都会自己调 `next_frame()`**，一帧内被截多次图，既慢又会出现
「A 用的是第 3 帧、Action 发生在第 5 帧、C 又截了第 7 帧」的时序错乱。
仓库里已有教训——`StarLinkAssistTask` 的注释专门写了「用 `find_feature(frame=...)` 而不是
`find_one()`，后者会再取一帧，重复截图」。

所以判据对象必须**接受外部传入的 frame**：`detect(frame) -> Hit | None`。

### 1.2 归一化的返回值：`Hit`

四类识别源的返回值形状不一，需要一个共同的包装。**不要**用 `Box`——OCR 结果和
YOLO 结果都带额外信息（文本、置信度、多个候选），压成 `Box` 会丢东西。

```python
@dataclass
class Hit:
    """一次识别命中的统一表示。"""
    box: Box                      # 可直接 click() 的目标框
    confidence: float = 1.0
    source: str = ""              # "template" / "yolo" / "ocr" / "button" / "glow" / "band"
    text: str = ""                # OCR 命中文本，其他源为空
    raw: object = None            # 原始结果（OCR result / Detection 对象），逃生舱
    __bool__ = ...                # 缩略：bool(hit) -> True
```

设计要点：

- **`box` 是唯一必需字段**——因为绝大多数 Action 是 `click(box)`。
- **`raw` 是逃生舱**：需要读 `ButtonDetection.metrics` 或 `GlowDetection.radius` 的场景
  （调试任务、调试框绘制）能从 `raw` 拿回完整对象，不损失信息。
- **`__bool__`** 让判据可以写成 `if hit:`，与现有 `ButtonDetection.__bool__` /
  `GlowDetection.__bool__` / `BandDetection.__bool__` 的既有习惯一致（这三个类都已实现 `__bool__`）。

### 1.3 判据协议

```python
class Detector(Protocol):
    def detect(self, frame) -> Hit | None: ...
    @property
    def name(self) -> str: ...        # 用于日志与调试框 key
```

**只要求一个 `detect(frame)` 方法**——刻意保持最小，这样任何 lambda 都能适配。

### 1.4 内置适配器（桥接层）

对每一类识别源提供一个薄适配器，**全部在 `src/core/detector/` 下新建**，
理由是它们只做「参数 → 统一判据」的翻译，不属于 RuntimeMixin 的职责
（`RuntimeMixin` 是「纯工具方法」，这些是「组合封装」）。

| 适配器 | 包装的现有实现 | 关键参数 |
|---|---|---|
| `TemplateDetector` | `find_feature` / `find_one` | `feature` / `feature_name`、`box`、`variance`、`threshold`、`target_height` |
| `YoloDetector` | `yolo_detect` | `name`、`box`、`conf`、`model_key`、`pick`（多候选选择策略） |
| `OcrDetector` | `ocr` | `match`、`box` / `x,y,to_x,to_y`、`threshold`、`lib`、`recheck_time` |
| `ButtonDetectorAdapter` | `find_button` | `box`、`thresholds` / `text_hsv` / `backdrop_hsv` |
| `CallableDetector` | 任意 `frame -> Hit\|Box\|bool\|None` | 常规化包装，供特殊场景兜底 |

`YoloDetector` 的 `pick` 参数值得单独说——YOLO 返回 `list[Box]`，选哪个是业务决策：

```python
YoloDetector(name="target", box=roi, conf=0.7, pick="max_conf")  # 默认：置信度最高
YoloDetector(..., pick="center")                                 # 离 ROI 中心最近
YoloDetector(..., pick="leftmost")                               # 最左（如列表类目标）
```

**`OcrDetector` 已实测确认**（`ok/task/task.py:749-818`）：

```python
# 返回值 docstring：list: A list of Box objects ... sorted by y-coordinate
return sort_boxes(detected_boxes)     # 第 818 行，返回 list[Box]
```

即 `ocr()` **返回 `list[Box]`（按 y 坐标排序）**，未命中返回 `[]`。
所以 `OcrDetector` 的适配是**三类中最简单的一类**：

```python
class OcrDetector:
    def __init__(self, match, box=None, threshold=0, lib="default",
                 pick="first", **region):
        ...
    def detect(self, frame) -> Hit | None:
        boxes = self.task.ocr(match=self.match, box=self.box,
                              threshold=self.threshold, frame=frame,
                              lib=self.lib, **self.region)
        if not boxes:
            return None
        box = self._pick(boxes)              # 默认取第一个（y 最小，最靠上）
        return Hit(box=box, confidence=getattr(box, "confidence", 1.0),
                   source="ocr", text=str(self.match), raw=boxes)
```

顺带确认两点：

- **`ocr()` 有未使用的 `recheck_time` 需求**：`wait_click_ocr` 里的 `recheck_time`
  是它自己在 `wait_ocr` 之后手动 `sleep` 再调一次 `ocr` 实现的（`runtime_mixin.py:1265-1283`），
  **不是框架参数**。新设计里这个语义由 `verify_c` 表达，不需要单独参数。
- **`ocr()` 内部自己会画调试框**（`emit_draw_box("ocr"+name, ...)`，第 801-803 行），
  所以 `draw=True` 时对 OCR 源不要重复画。

`pick` 策略与 `YoloDetector` 共用同一套实现（"first" / "max_conf" / "center" / "leftmost"）。

---

## 2. 编排层设计：`run_action_cycle`

### 2.1 签名

```python
def run_action_cycle(
    self,
    action,                      # 主 Action：Hit -> bool | None
    check_a: Detector,           # 前置条件 A
    verify_c: Detector | None = None,   # 结果验证 C；None = 不验证，Action 后即成功
    # ── A 的窗口 ──
    timeout: float = 5.0,        # 等待 A 命中的总超时
    settle_a: float = 0.0,       # A 需持续成立 N 秒才算通过（默认 0 = 不要求稳定）
    # ── Action 与重试 ──
    retry: int = 1,              # Action 最多执行次数（不含 C 未命中后的重试）
    click_after_delay: float = 0.0,
    after_sleep: float = 0.0,
    # ── C 的窗口 ──
    verify_timeout: float = 1.0,
    settle_c: float = 0.0,
    verify_retry: int = 0,       # C 未命中后额外重试次数（0 = 不重试，直接返回 False）
    # ── 可选持续阶段（B + Extra Action）──
    check_b: Detector | None = None,
    extra_action=None,           # Hit -> None；B 命中时重复执行
    b_interval: float = 0.0,     # 附带 Action 的最小间隔
    max_extra: int = 0,          # 附带 Action 次数上限（0 = 只受 timeout 约束）
    # ── 其他 ──
    draw: bool = False,          # 是否画调试框（发光框 4 秒过期，长循环需重复画）
    raise_if_not_found: bool = False,
) -> bool:
```

### 2.2 执行流程

```
                    ┌──────────────────────┐
                    │  deadline = 总超时    │
                    └──────────┬───────────┘
                               ▼
              ┌──────── 取帧 frame = next_frame() ────────┐
              │                                            │
              ▼                                            │
      check_a.detect(frame) ──未命中──► 超时？ ──否────────┘
              │ 命中                            │ 是
              │                                 ▼
              ▼                          return False / raise
      settle_a 持续成立检查
              │
              ▼
      ┌── retry 循环 ──────────────────────┐
      │   sleep(click_after_delay)          │
      │   hit = action(hit_a)               │
      │   verify_c is None ──► return True  │
      │   verify_c 命中？ ──► return True    │
      │   未命中 ──► verify_retry 未耗尽 ────┘
      └─────────────────────────────────────┘
              │ verify_retry 耗尽
              ▼
      ┌── 持续阶段（仅当 check_b 给定）─────┐
      │   check_b.detect(frame) 未命中 ──► 跳出
      │   extra_action(hit_b)               │
      │   check_a/verify_c 判定 ──► True 则返回
      └─────────────────────────────────────┘
```

**B 阶段的语义**（对应需求原文「B 持续命中时重复执行附加 Action；B 未命中时立即停止；
每次附加 Action 后继续检查 C；C 命中则结束并判定成功」）：

```python
# 持续阶段伪代码
extra_count = 0
while self.active_time() < deadline:
    if max_extra and extra_count >= max_extra:
        break
    frame = self.next_frame()
    if not check_b.detect(frame):      # ← B 未命中 → 立即停止
        break
    self.sleep(b_interval)
    extra_action(hit_b)
    extra_count += 1
    if verify_c is not None and verify_c.detect(self.next_frame()):
        return True                    # ← C 命中 → 结束并判定成功
return False
```

### 2.3 三个必须明确的行为约定

**① `timeout` 用 `active_time()` 而不是 `time.time()`**

`active_time()` 是暂停感知的（`BaseGameTask` 覆写），`feature_stable` 用的是
`time.time()`——**暂停期间会被判定为失败**。新封装一律用 `active_time()`，
`sleep()` 也用框架的（同样是暂停感知）。这是与 `feature_stable` 的**行为差异**，
不是笔误（见 `ACTION_LIFECYCLE_AUDIT.md` 5.3）。

**② `settle_a` / `settle_c` 默认必须为 0**

`settle` 走 `wait_until(settle_time=...)`，语义是「**每次调用**都要求连续成立够时长」。
历史教训：给 `find_button` 加 `settle_time` 导致「变慢很多」被 revert
（`ACTION_LIFECYCLE_AUDIT.md` 6.5）。等待只应发生在「即将点击的那一次」。

**③ 取帧时机：A 通过后要重新取帧再 Action**

A 的判定用的是 t0 帧，若 `settle_a > 0`，Action 发生在 t0 + settle_a，
此时 `hit_a.box` 可能已经位移（会动的图标）。所以：
**A 通过后重新 `next_frame()` 并再 detect 一次取最新 box**，成功则用新 box，
失败则沿用旧 box（不因一帧抖动放弃）。这条对移动目标（`star_link_icon`、
`treasure_key_icon`）是必需的。

### 2.4 返回值的语义

`bool` 只表达「Action 生命周期是否成功」，**不表达失败原因**。
调用方若需区分「A 没等到」和「C 没通过」，用 `raise_if_not_found=True`
走 `WaitFailedException`，或读日志。刻意保持签名简单——
仓库既有惯例也是返回 `bool`（`wait_click_feature` / `click_feature` 都是），
保持一致可降低迁移成本。

---

## 3. 与现有实现的关系（迁移策略）

### 3.1 调用方实测结果（重要更正）

**`click_feature` 本身：全仓库零调用方。**

对 `*.py` 全仓库检索 `click_feature(`，只命中两处**同名不同函数**：

| 命中 | 实际是 | 性质 |
|---|---|---|
| `runtime_mixin.py:198` | 定义本身 | — |
| `runtime_mixin.py:1138` | `wait_click_feature`（是 `wait_` + `click_feature` 的子串误匹配） | 不同函数 |
| `account_mixin.py:131`、`TestInteractionTask.py:44/55` | 调的是 `wait_click_feature`（`self.wait_click_feature(`） | 不同函数 |

`tests/` 目录检索 `click_feature` → **零命中**（`TestButtonDetector.py` 等都不覆盖它）。
git 历史里 `click_feature` 的引入也未见配套测试。

⇒ **结论修正**：`click_feature` 是**事实上的死代码**。
我此前说「有调用方依赖，不能改签名」是**错的**，依据是子串误匹配。
它可以直接改造、甚至删除，不存在兼容性负担。

**真正有活调用方的是 `wait_click_feature` / `wait_click_ocr`**：

```
TestInteractionTask._test_post/_test_foreground_post   ← self.wait_click_feature(account_switch)
TestInteractionTask._test_pynput/_test_pydirect/_test_alt_click ← self.wait_click_ocr(...)

AccountMixin.login_flow(username)                       ← self.wait_click_feature(confirm_button_2)
      ↑
      └─ 由 AccountMixin.iter_multi_account_context() 调用（account_mixin.py:207）
              ↑
              └─ 由 DailyTaskRunner 通过 getattr(self.task, "iter_multi_account_context", None)
                 调用（daily_task_runner.py:96 与 :194）
                      ↑
                      └─ DailyTask(AccountMixin, BaseGameTask)  ← 已注册的正式任务
```

**关键点**：`login_flow` 里的调用带了 `settle_time=1`
（`account_mixin.py:131-135`，对 `confirm_button_2` 且传了固定 box）。
这是**目前唯一带 `settle_time` 的真实调用**——改造时它的行为必须保持一致
（`settle_time=1` 意味着要求按钮连续稳定 1 秒），也是「settle 会不会太慢」的现成观察样本。

**其他相关函数的调用方**：

| 函数 | 调用方 |
|---|---|
| `wait_feature_disappear` | 仅 `click_feature:238`（即死代码内）——**外部零调用** |
| `click_confirm` | 全仓库**零调用**（`find_confirm` 被 `SkipDialogTask:42` 直接调用，但没走 `click_confirm`） |
| `safe_back` | 全仓库**零调用** |
| `wait_button` | 全仓库**零调用** |

⇒ **这批「点完等结果」的函数绝大多数是死代码**（只有 `click_confirm` 内部的
`find_confirm` 被单独复用）。审计报告里说的「4 套各写各的 C」中，
真正在跑的只有 `TreasureUnlockTask._click_band` 那一处。

### 3.2 不破坏 `click_feature`

`click_feature` 保持原样，**新增生命周期函数并让 `click_feature` 内部转调**：

```python
def click_feature(self, feature, boxes=None, time_out=5, ...):
    # 兼容层：把旧的 boxes 参数映射成「多个 box 依次尝试」的判据
    ...
```

**但它零调用方（见 3.1），所以不必做兼容层。** 更干净的做法是二选一：

- **方案 A（推荐）**：直接删除 `click_feature` 与 `wait_feature_disappear`，
  由 `run_action_cycle` + `TemplateDetector` 取代。理由：无调用方、无测试、
  语义已被新设计完整覆盖（且新设计支持 A≠C）。
- **方案 B（保守）**：保留原地不动，仅新增。代价是仓库里长期留着一段死代码，
  后续维护者会困惑「该用哪个」。

建议先做方案 A 的**判定**（确认无外部引用，包括 `.vscode/` 的模板缓存与文档），
确认后在同一个 PR 里删除。建议顺序：

1. **阶段一**：新增识别层（`src/core/detector/`）+ `verify_after_action`
   + `run_action_cycle`，**不改任何现有函数**。补单元测试。
2. **阶段二**：把 `TreasureUnlockTask._click_band` 的内层换成 `verify_after_action`
   （唯一在跑的 C，行为等价，用现有 182 例测试兜底）。
3. **阶段三（可选）**：删除死代码 `click_feature` / `wait_feature_disappear`，
   并评估 `click_confirm` / `safe_back` / `wait_button` 是否也是死代码应清理。
4. **阶段四（仅在有明确收益时）**：把有活调用方的 `wait_click_feature` /
   `wait_click_ocr` 改为转调新实现。**这一步风险最高**（`login_flow` 带
   `settle_time=1`），建议单独 PR 并实测账号切换流程。

### 3.2 `TreasureUnlockTask` 的渐进替换

它是唯一 A/B/C 全齐的实现，也是新设计的**第一个真实用例**。但**不要整体重写**——
它是状态机，有校准、队尾重排、完成兜底等业务语义。推荐只替换最内层：

```python
# 现在（TreasureUnlockTask._click_band:218-231）
self.click(band)
self.wait_until(
    lambda: not self._band_present(self.next_frame(), band),
    time_out=self._消失确认超时(3.0),
    settle_time=self._消失确认时长(0.35),
)

# 替换为
self.verify_after_action(
    predicate=NotBandPresent(self._detector(), band),
    timeout=self._消失确认超时(3.0),
    settle_time=self._消失确认时长(0.35),
)
```

零风险（行为等价），且验证了新封装的 `settle` 语义在真实场景可用。

### 3.3 需要同时新增 `verify_after_action`

审计报告的结论仍然成立：**C 有 4 套各写各的实现**，应单独收口成一个
比 `run_action_cycle` 更小的函数：

```python
def verify_after_action(self, predicate, timeout=1.0, settle_time=0.0,
                        raise_if_not_found=False) -> bool:
```

它内部就是 `wait_until(predicate.detect 包装, time_out=timeout, settle_time=settle_time)`。
`run_action_cycle` 的 C 阶段**复用**它（而不是再写一遍 `wait_until`）。

---

## 4. 使用示例

### 例 1：模板 A + 点击 + 模板 C（A≠C，现在无法表达）

```python
self.run_action_cycle(
    check_a=TemplateDetector(feature=FeatureList.treasure_icon),
    action=lambda hit: self.click(hit.box),
    verify_c=TemplateDetector(feature=FeatureList.unlock_ui),
    timeout=5, verify_timeout=1.5, verify_retry=2,
)
```

### 例 2：OCR 识别 + 点击（替代 `wait_click_ocr`，但带结果验证）

```python
self.run_action_cycle(
    check_a=OcrDetector(match="确认", box=confirm_area, threshold=0.8),
    action=lambda hit: self.click(hit.box, after_sleep=0.2),
    verify_c=OcrDetector(match="已完成"),      # C 用不同的文本
    timeout=10, verify_timeout=2.0,
)
```

### 例 3：YOLO + 持续阶段（对应需求的可选 B 阶段）

```python
self.run_action_cycle(
    check_a=YoloDetector(name="target", box=roi, conf=0.7),
    action=lambda hit: self.click(hit.box),
    check_b=YoloDetector(name="charge_bar", box=roi),        # B：蓄力条仍在
    extra_action=lambda hit: self.click(hit.box),            # 每轮补一次
    verify_c=YoloDetector(name="success_mark", box=roi),     # 出现成功标记即结束
    b_interval=0.3, max_extra=10, timeout=15,
)
```

### 例 4：混合识别源（A 用模板，C 用 OCR）

```python
self.run_action_cycle(
    check_a=TemplateDetector(feature=FeatureList.skip_dialog),
    action=lambda hit: self.click(hit.box),
    verify_c=OcrDetector(match="剧情已跳过"),
    timeout=3,
)
```

**例 4 是本次设计的核心价值**——A 和 C 可以用完全不同的识别源，
而这在现有 `click_feature` 里根本表达不了。

---

## 5. 落点与文件规划

```
src/core/
  detector/                        ← 新建目录
    __init__.py                    ← 导出 Hit / Detector / 各适配器
    hit.py                         ← Hit 数据类
    template_detector.py
    yolo_detector.py
    ocr_detector.py
    button_detector_adapter.py
    callable_detector.py
  base_mixin/
    runtime_mixin.py               ← 新增 verify_after_action / run_action_cycle
```

**为什么适配器放 `src/core/detector/` 而不是 `src/image/`？**
`src/image/` 现在是「图像算法实现」（`button_detector` / `glow_target_detector` /
`treasure_band_detector` / `stability` / `hsv_config`），它们**不做截图、不调任务方法**，
是纯函数式的算法层。适配器需要调用 `self.find_feature` / `self.next_frame`，依赖 Mixin 的
上下文，语义上是「编排胶水」。放 `src/image/` 会破坏该目录的既有职责边界。

**为什么 `run_action_cycle` 放 `runtime_mixin.py`？**
它不覆写框架方法，符合 `RuntimeMixin`（纯工具）/ `FrameworkOverrideMixin`（覆写框架）的分层。
虽然命名为 `run_*`，但它不是任务循环驱动，只是一个普通方法。

---

## 6. 风险与待确认项

| 项 | 说明 | 处理 |
|---|---|---|
| ~~OCR 结果转 Box~~ | ✅ **已实测确认**：`ocr()` 返回 `list[Box]`（`ok/task/task.py:818`） | 无风险，见 1.4 |
| **`ocr()` 自带调试框** | 它在内部 `emit_draw_box`（第 801-803 行） | `draw=True` 时不重复画 |
| **`yolo_detect` 的帧参数** | 它接受 `frame=`，可直接复用统一帧 | 已确认（`runtime_mixin.py:796`） |
| **`find_feature` 的 frame 参数** | 同上，已确认可传 | 已确认（`framework_override_mixin.py:15-51`） |
| **`settle` 性能** | 见 2.3 ②，默认 0 | 已定 |
| **调试框 4 秒过期** | `draw_boxes` 广播的框 4 秒过期，长循环需重复画 | `draw=True` 时每轮重画 |
| **`button_detector` 缓存约定** | `RuntimeMixin.button_detector()` 已实现实例缓存，适配器应复用 | 已确认（`runtime_mixin.py:568-583`） |
| **是否要 `boxes` 多区域回退** | 现 `click_feature` 支持 `boxes=[None, box1, box2]` 逐个尝试 | 用 `MultiBoxDetector([d1, d2, d3])` 组合器表达，不放进签名 |

最后一项值得展开：`click_feature` 的 `boxes` 参数把「多区域回退」做进了签名，
新设计**不这么做**——用组合器表达更清晰，且能组合不同识别源：

```python
MultiBoxDetector([
    TemplateDetector(feature=FeatureList.confirm_button, box=box1),
    TemplateDetector(feature=FeatureList.confirm_button, box=box2),
    ButtonDetectorAdapter(box=box3),
])
```

组合器（`MultiBoxDetector` / `FirstHitDetector`）是自然的下一步扩展，
建议**在真有第二个用例时再加**，不要一开始就造。

---

## 7. 建议的实施顺序

1. **先写 `Hit` + `Detector` 协议 + `TemplateDetector`**（最小可用集）
2. **写 `verify_after_action`**，用它替换 `TreasureUnlockTask._click_band` 内层
   （一次真实的、可验证的重构，跑通 182 例测试）
3. **写 `run_action_cycle`**（先只支持 A + Action + C，`check_b=None` 路径）
4. **补 `YoloDetector` / `OcrDetector` / `ButtonDetectorAdapter`**
5. **写 `check_b` + `extra_action` 持续阶段**，用 `TreasureUnlockTask` 的
   UNLOCKING 阶段做验证（它的 B = `treasure_key_icon`、Extra Action = 点条带，天然匹配）
6. 最后再评估 `wait_click_feature` / `wait_click_ocr` / `click_feature` 的转调

**第 5 步是唯一真正的「新能力」**——前 4 步本质是把现有行为归一化。
如果时间有限，前 4 步的收益（A≠C、多识别源）已经能覆盖大部分场景。

---

## 附：设计决策速查

| 决策 | 选择 | 理由 |
|---|---|---|
| 判据对象 vs lambda | **判据对象** | lambda 各自截帧 → 时序错乱 + 性能损失 |
| 返回值归一成 `Box` 还是 `Hit` | **`Hit`** | `Box` 承载不了 text / confidence / raw |
| `detect(frame)` 是否强制传 frame | **是** | 保证一帧内只截一次图 |
| A/C 是否同一识别源 | **否，正交** | 这是本次改造的核心目标 |
| `settle` 默认值 | **0** | 历史教训：settle 每次调用都要求稳定，会显著变慢 |
| 计时用 `time.time()` vs `active_time()` | **`active_time()`** | 暂停感知；与 `feature_stable` 的行为差异是刻意的 |
| 适配器放 `src/image/` 还是 `src/core/` | **`src/core/detector/`** | `src/image/` 是纯算法层，不依赖 Mixin 上下文 |
| 是否改 `click_feature` 签名 | **零调用方，可直接删**（详见 3.1） | 无兼容负担；此前误判为「有调用方」 |
| 多区域回退放签名还是组合器 | **组合器** | 能组合不同识别源，且不膨胀签名 |
| 是否造通用状态机 | **否** | 只有 `TreasureUnlockTask` 需要跨帧状态 |
