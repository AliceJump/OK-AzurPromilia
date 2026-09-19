# Action 生命周期封装现状审计

> **状态：已由 `ACTION_LIFECYCLE_RENAME.md` 的方案落地实施。** 本文保留为审计快照与决策依据，
> 其中「建议」章节描述的是**当时的**结论；实际落点与命名以 `DEVELOPMENT.md` 的
> 「Action 生命周期」一节为准。

> 检索范围：整个仓库（`src/` + `.venv` 中的 ok-script 2.0.6 框架源码）。
> 性质：**只读审计**，本次未修改任何代码。
> 目的：确认仓库中是否已存在「检测 → 执行 → 再检测」式的集成函数、Mixin 或任务实现，
> 以决定后续应当复用、扩展还是新增。

## 0. 目标模式定义

本次审计的目标是一个**五环节的 Action 生命周期**：

| 代号 | 环节 | 语义 |
|:---:|---|---|
| **A** | 前置识别 | 执行前判断状态是否满足前置条件。A 未命中则不执行 Action，或走现有重试机制 |
| **Action** | 主 Action | 执行一次点击、按键、切换页面、移动等 |
| **C** | 结果验证 | 执行后判断预期结果是否出现。C 命中即成功；未命中则等待、重试或重新执行 |
| **B** | 持续条件 | 可选。B 持续命中时重复执行附加 Action；B 未命中时立即停止 |
| **Extra Action** | 附加 Action | 持续阶段内被重复执行的操作，每次之后继续检查 C |

## 1. 能力覆盖矩阵

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

**结论速览**：`click_feature()` 是最接近完整生命周期的实现（缺 B）；`TreasureUnlockTask` 是唯一五环节齐全的实现，但它是任务级状态机。

## 2. 按相似度排列的实现详情

### ① `RuntimeMixin.click_feature()`

**位置**：`src/core/base_mixin/runtime_mixin.py:198-257`

**签名**：

```python
def click_feature(
    self,
    feature,
    boxes=None,
    time_out=5,
    after_sleep=0,
    click_after_delay=0,
    settle_time=0,
    blind_point=None,
    blind_delay=1,
    verify_disappear=True,
    verify_timeout=0.5,
    max_click_retry=3,
):
```

**核心流程**：

```python
boxes = [None] + (boxes or [])          # 首个 box 为 None → 走 coco 标注位置
start_time = time.time()
last_blind_time = 0

while time.time() - start_time < time_out:          # 总超时窗口
    frame = self.next_frame()
    for box in boxes:
        result = self.find_feature(feature_name=feature, box=box, frame=frame)
        if result and self.feature_stable(feature, box, settle_time):   # ← A
            retry_count = 0
            while retry_count < max_click_retry:                        # ← 重试上限
                self.sleep(click_after_delay)
                self.click(result, after_sleep=after_sleep)             # ← 主 Action
                if not verify_disappear:
                    return True
                if self.wait_feature_disappear(feature, box, verify_timeout):
                    return True                                         # ← C 命中即成功
                self.log_warning(
                    f"{feature} 点击后未消失，重试 {retry_count + 1}/{max_click_retry}"
                )
                retry_count += 1
    if blind_point and (time.time() - last_blind_time >= blind_delay):
        self.click(blind_point[0], blind_point[1], after_sleep=after_sleep)  # 兜底盲点击
        last_blind_time = time.time()

return False
```

**环节归属**：

- **A** = `find_feature(box=box)` + `feature_stable(feature, box, settle_time)`
  —— 不仅要求命中，还要求**持续命中 `settle_time` 秒**（`settle_time=0` 时直接通过）
- **Action** = `self.click(result)`，前后分别由 `click_after_delay` / `after_sleep` 控制节奏
- **C** = `wait_feature_disappear(feature, box, verify_timeout)`
- **重试** = 内层 `max_click_retry`（默认 3）循环：C 未命中 → 重新 Action
- **B / Extra Action** = 无

**关键限制**：A 与 C 共用同一个 `feature` 参数。它只能表达「某元素出现 → 点击 → 该元素消失」，
无法表达「看到 A 元素 → 点击 → 期待 C 元素出现」这种 A≠C 的场景。

**依赖的辅助函数** `feature_stable()`（`runtime_mixin.py:186-196`）：

```python
def feature_stable(self, feature, box, duration):
    if duration <= 0:
        return True
    end_time = time.time() + duration
    while time.time() < end_time:
        if not self.find_feature(feature_name=feature, box=box, frame=self.next_frame()):
            return False
        self.sleep(0.05)
    return True
```

**注**：这个函数本身可以视为「B 的判定函数本体」（特征持续命中），但它没有配套的循环执行机制。

---

### ② `TreasureUnlockTask` —— 唯一 A/B/C 齐全

**位置**：`src/tasks/trigger/TreasureUnlockTask.py`（356 行）

**状态常量**：`WAIT_TREASURE` / `CALIBRATING_BANDS` / `UNLOCKING` / `COMPLETION_CHECK` / `FINISHED`

**驱动方式**（`run()`，L96-115）：`budget` 取 `_单次运行时长上限(秒)`（默认 25s），
在 `deadline` 内循环 `next_frame()` → `_step(frame)`；`FINISHED` 则 `_reset()` 返回；
`WAIT_TREASURE` 直接 return（不占用时间片）。

**各环节对应**：

| 环节 | 实现 |
|---|---|
| **A** | `_step_wait(frame)`：`find_one(treasure_icon)` 命中 → 转 `CALIBRATING_BANDS` |
| （前置校准） | `_step_calibrate(frame)`：`_detector().find(frame, roi)` + `_same_layout(prev, bands)` 连续 `_校准稳定帧数(5)` 帧布局一致 → 存 `_calibrated_bands` → 转 `UNLOCKING` |
| **B** | `_step_unlock(frame)`：`find_one(treasure_key_icon, box=self._key_box())` 每帧检测，命中才允许继续 |
| **Action** | `hit_by_center_y(_active_bands, key.center()[1])` 定位目标条带 → `_click_band(band)` |
| **C** | `_click_band` 内 `wait_until(..., settle_time=_消失确认时长(0.35))` |
| **重试** | `_on_click_failed(band)` 失败计数，达 `_点击重试上限(3)` 则把该条带 `pop` 到队尾 |

**`_click_band()`（L218-231）——A/B/C 收束处**：

```python
self.click(band)                                                     # ← Action
self.wait_until(
    lambda: not self._band_present(self.next_frame(), band),
    time_out=_消失确认超时(3.0),
    settle_time=_消失确认时长(0.35),
)                                                                    # ← C
# 未消失 → _on_click_failed(band)
```

**B 未命中的处理**（L157 起 `_step_unlock`）：钥匙丢失超时后检查 `treasure_icon`，
决定 `_reset()`（回到 `WAIT_TREASURE`）还是转 `COMPLETION_CHECK`
—— 这是「B 未命中 → 立即停止附加 Action」的现成范式。

**`_step_completion(frame)`**：

```python
remaining = [b for b in self._calibrated_bands if self._band_present(frame, b)]
# 仍有条带 → 回退 UNLOCKING（C 未命中 → 重新执行）
# 持续无条带达 _完成确认时长(2.5) → 检查 treasure_icon 消失，或 clean >= needed * 2 兜底 → FINISHED
```

**不可复用原因**：整个类耦合了宝箱专属业务——`_calibrated_bands` 布局缓存、
`_detector()` 检测器工厂（按颜色开关 / 分辨率缩放实例化并缓存）、
以及 11 个 `_` 前缀隐藏配置键（`_单次运行时长上限` / `_校准稳定帧数` / `_点击重试上限` /
`_条带存在阈值` / `_完成确认时长` / `_消失确认超时` / `_消失确认时长` 等）+ `画调试框`。

---

### ③ `BaseGameTask.click_confirm()`

**位置**：`src/core/BaseGameTask.py:453-502`

**签名**：`click_confirm(after_sleep=0, time_out=5, recheck_time=0, disappear_time_out=0.8)`

**核心流程**：

```python
start_time = self.active_time()
while True:
    self.next_frame()
    confirm = self.find_confirm()
    if confirm:                                                      # ← A
        self.click(confirm)                                          # ← Action
        if disappear_time_out > 0:
            self.wait_until(
                lambda: not self.find_confirm(),
                time_out=disappear_time_out,
                raise_if_not_found=False,
            )                                                        # ← C（只等消失）
        if after_sleep > 0:
            self.sleep(after_sleep)
        if recheck_time > 0:                                         # ← 二次复检
            self.sleep(recheck_time)
            if confirm := self.find_confirm():
                self.click(confirm)                                  # 补一次 Action
                if disappear_time_out > 0:
                    self.wait_until(
                        lambda: not self.find_confirm(),
                        time_out=disappear_time_out,
                        raise_if_not_found=False,
                    )
        return True
    if self.active_time() - start_time > time_out:                   # 总超时
        self.log_info("点击确认超时")
        return False
    self.sleep(0.01)
```

**环节归属**：

- **A** = `find_confirm()`，A 未命中时 `sleep(0.01)` 继续轮询直至 `time_out` → 返回 False
- **Action** = `self.click(confirm)`
- **C** = `wait_until(lambda: not self.find_confirm(), ...)`，**只看「消失」，且 C 未命中不触发重试**（仅一次 `recheck_time` 补点）
- **B / Extra Action** = 无

**`find_confirm()`（L503-518）三级回退识别**：

```python
frame = self.next_frame()
return self.find_button(
    frame=frame,
    box=self.box_of_screen(0.6323, 0.7046, 0.7193, 0.7750),
) or self.find_one(
    feature=[FeatureList.confirm_button, FeatureList.confirm_button_2],
    vertical_variance=0.01, horizontal_variance=0.02, frame=frame,
) or self.find_one(
    feature=[FeatureList.confirm_button_2],
    box=self.box_of_screen(0.5753, 0.6116, 0.5957, 0.6420),
    frame=frame,
)
```

硬编码了 3 个 FeatureList 与 3 个固定 box，属于「确认按钮」专用实现。

---

### ④ `RuntimeMixin.wait_click_feature()` / `wait_click_ocr()`

**位置**：`runtime_mixin.py:1138-1187` / `:1189-1300`

**`wait_click_feature` 流程**：

```python
result = self.wait_until(
    lambda: self.find_one(feature, ..., box=box, ...),
    time_out=time_out,
    pre_action=pre_action,
    post_action=post_action,
    raise_if_not_found=raise_if_not_found,
    settle_time=settle_time,
)                                                                    # ← A
if result is not None:
    if click_after_delay > 0:
        self.sleep(click_after_delay)
    if alt:
        x, y = result.relative_with_variance(relative_x, relative_y)
        self.click_with_alt(x, y, name=result.name, after_sleep=after_sleep)
    else:
        self.click_box(result, relative_x, relative_y, after_sleep=after_sleep)  # ← Action
    return True                                                      # ← 点完即成功，无 C
return False
```

**环节归属**：A = `wait_until(find_one(...))` ｜ Action = `click_box` / `click_with_alt` ｜ **C / B 全缺**
—— 点击成功即 `return True`，不做任何结果验证。

**现成消费者**：`src/tasks/test/TestInteractionTask.py`（`visible = self.debug`，
五种点击方式各调用一次 `wait_click_feature(feature=FeatureList.account_switch, time_out=10)`
或 `wait_click_ocr(match="account_switch", time_out=10, alt=True)`）。
`wait_click_ocr` 额外支持 `recheck_time` 复检。

---

### ⑤ `RuntimeMixin.safe_back()` —— A 未命中分支的范式

**位置**：`runtime_mixin.py:390-437`

**签名**：`safe_back(match=None, feature=None, box=None, time_out=30, once_time_out=2)`

```python
while True:
    if self.active_time() - start_time > time_out:
        return False
    remaining = time_out - (self.active_time() - start_time)

    def target_visible():
        if match is not None and self.ocr(match=match, box=box):
            return True
        if feature is not None and self.find_one(feature, vertical_variance=0.05,
                                                 horizontal_variance=0.05, box=box):
            return True
        return False

    if self.wait_until(target_visible, time_out=max(0.01, min(once_time_out, remaining)),
                       raise_if_not_found=False):
        return True                                                  # ← A 命中，退出
    self.log_info("safe_back 观察超时，发送返回键")
    self.back()                                                      # ← A 未命中 → 恢复 Action
```

这是「A 未命中 → 执行恢复 Action，直到 A 命中或总超时」的干净范式，
可作为新封装中「A 未命中分支」的参考写法，但不适合作为生命周期主体。

---

### ⑥ `StarLinkAssistTask` / `SkipDialogTask` —— 自建限流代替 C

**`StarLinkAssistTask.run()`**（`src/tasks/trigger/StarLinkAssistTask.py:108-147`）：

```
next_frame()
  → find_feature(feature_name=FeatureList.star_link_icon, frame=frame)   # ← A 前置闸门
      未命中 → _streak = 0; return
  → _detector().analyze(frame, roi)                                      # 检测
  → _draw(detection)
  → 未命中 → 清 _streak
  → _streak += 1；未达 _连续命中帧数(1) → return
  → _last_click_at 冷却检查                                              # ← 限流
  → self.click(detection.box, down_time=..., after_sleep=...)            # ← Action
  → 日志
```

源码注释明确写了「前置闸门」「连续命中帧数」「点击冷却」的设计理由。
**无 C**——靠点击冷却（`_last_click_at`）抑制重复点击，而不是验证结果。

**`SkipDialogTask.run()`**（`src/tasks/trigger/SkipDialogTask.py`，48 行）：

```
find_one(skip_dialog) 未命中 → return                                    # ← A
deadline = active_time() + 3
循环：
  find_feature(skip_dialog) 命中 → click 并重置 deadline = active_time() + 3
  否则 find_confirm() 命中 → click 并重置 deadline
  否则 sleep(0.05)
```

注意：`deadline` 的重置发生在**每轮轮询命中时**，而不是「结果验证通过时」
—— 这是「Activity 续期」语义，**不是成功判定**。两者不可混淆。

---

## 3. 是否能直接复用

| 实现 | 结论 |
|---|---|
| `click_feature` | **可复用，但 C 语义需匹配**。目标模式要求 C 是「预期结果**出现**」，它是「**本特征消失**」。若场景恰为「点掉某个可点元素」，可直接使用 |
| `TreasureUnlockTask` | **不可直接复用**。完整任务类，`_calibrated_bands` / `_detector()` / 11 个业务配置键全部专属宝箱 |
| `click_confirm` | **不可通用化复用**。`find_confirm` 硬编码 3 个 FeatureList + 3 个固定 box |
| `wait_click_feature` / `wait_click_ocr` | **可用于「A + Action」的一半**，但需接受「点完即成功」语义 |
| `safe_back` | 不作为生命周期主体，作为「A 未命中分支」的参考写法 |

## 4. 不能直接复用时，各缺什么

| 实现 | 缺失项 |
|---|---|
| `click_feature` | ① B（持续条件）② 附加 Action 循环 ③ C 的「出现」语义 ④ A 与 C 无法使用不同特征（共用 `feature` 参数） |
| `click_confirm` | ① A 的通用性（写死确认按钮）② C 未命中时不重试（仅警告）③ B |
| `wait_click_feature` / `wait_click_ocr` | ① C 整体 ② 重试 ③ B |

**全仓库共同缺口**：

- **没有一处把 `wait_until` 的 `settle_time` 机制同时用于 A 与 C 两侧的稳定判定**。
  `settle_time` 只在 A 侧被使用（`click_feature` 的 `feature_stable`、`wait_click_*` 透传给 `wait_until`）。
- **底层原语不缺，缺的是编排层**。框架 `ok/task/task.py` 已提供全套等待/点击原语：

  | 原语 | 行号 |
  |---|---|
  | `click` | L140 |
  | `click_box_if_name_match` | L288 |
  | `click_relative` | L372 |
  | `click_box` | L405 |
  | `wait_scene` | L437 |
  | `sleep` | L451 |
  | `wait_until` | L508 |
  | `wait_click_box` | L526 |
  | `wait_feature` | L650 |
  | `wait_click_feature` | L664 |
  | `find_one` | L684 |
  | `wait_click_ocr` | L1032 |
  | `wait_ocr` | L1052 |
  | `sleep_check` | L1133 |

## 5. 是否存在应统一抽象的重复实现

**存在，但只应抽象两个不同粒度，不要合并成一个。**

### 5.1 应当统一：C（结果验证）

现在有 **4 套各写各的「点完等结果」**（⚠️ 但其中 3 套是死代码 —— 见本节末）：

| 位置 | C 的写法 | settle 支持 |
|---|---|---|
| `click_feature:238` | `wait_feature_disappear(feature, box, verify_timeout)` | ❌（裸 `time.time()` 轮询） |
| `click_confirm:474` | `wait_until(lambda: not self.find_confirm(), time_out=disappear_time_out)` | ❌ |
| `TreasureUnlockTask._click_band:224` | `wait_until(lambda: not self._band_present(...), time_out=3.0, settle_time=0.35)` | ✅ |
| `SkipDialogTask.run` | 无（用 `deadline` 重置代替） | — |

四者的差别**仅在判据函数与超时参数**，判定逻辑完全可以收敛。
注意 `_click_band` 用了 `settle_time=0.35`，而 `wait_feature_disappear` 没有 settle 概念
—— 这两者行为不同，统一时必须保留 `settle_time` 参数才能覆盖。

### 5.1.1 ⚠️ 调用方实测：其中 3 套是死代码

按「谁真的在调用」复核（全仓库 `*.py` 检索）：

| 函数 | 调用方 | 状态 |
|---|---|---|
| `click_feature` | **无**（`wait_click_feature` 是不同函数，属子串误匹配） | ☠️ 死代码 |
| `wait_feature_disappear` | 仅 `click_feature:238` 内部 | ☠️ 死代码 |
| `click_confirm` | **无** | ☠️ 死代码 |
| `safe_back` | **无** | ☠️ 死代码 |
| `wait_button` | **无** | ☠️ 死代码 |
| `find_confirm` | `SkipDialogTask:42` | ✅ 在用 |
| `TreasureUnlockTask._click_band` | 自身状态机 | ✅ 在用 |
| `wait_click_feature` / `wait_click_ocr` | `AccountMixin.login_flow`、`TestInteractionTask` | ✅ 在用 |

⇒ **真正需要收口的 C 只有 `TreasureUnlockTask._click_band` 一处**（外加
`SkipDialogTask` 的无 C 设计）。其余是历史遗留，应优先考虑**删除**而非「统一」。
这也改变了优先级判断：统一抽象的收益比审计初判时低，
但**清理死代码**的收益比初判时高。

### 5.2 不建议统一：整条 A→Action→B→C 生命周期

目前全仓库真正需要 **B（持续阶段）** 的只有 `TreasureUnlockTask` 一处。
为单个用例造通用状态机属于过度抽象。

### 5.3 需要注意：两套并存的「持续成立」实现

| 实现 | 计时方式 | 暂停行为 |
|---|---|---|
| `feature_stable()` | `time.time()` + `self.sleep(0.05)` | **暂停期间会被判定为失败** |
| `wait_until(settle_time=...)` | `self.active_time()` | 暂停时冻结（`BaseGameTask` 覆写） |

二者语义**不等价**。新写通用封装时，A 的稳定判定应统一走 `wait_until(settle_time=...)`，
而不是 `feature_stable`。

## 6. 建议

**结论：新增一个薄封装，并清理死代码。不改造现有函数的签名，不在任务里重写。**

> ⚠️ 本节已按 5.1.1 的调用方实测结果修订。原版基于「`click_feature` 有调用方」的判断，
> 复核后该前提**不成立**（属子串误匹配导致的误判）。

### 理由

1. **`click_feature` 无调用方**（5.1.1 实测），所以不存在「扩展会破坏兼容」的问题。
   但也不建议把它改造撑大——它的 A/C 共用 `feature` 参数是**设计缺陷**，
   在缺陷上继续加参数不如让新设计取代它。建议直接删除（与其内部的
   `wait_feature_disappear` 一起）。
2. **不要扩展 `click_confirm`**。它是确认按钮专用（`find_confirm` 硬编码 3 个
   FeatureList + 3 个固定 box），通用化等于重写；且它同样零调用方，属可清理对象。
3. **放在 `src/core/base_mixin/runtime_mixin.py`**，而非 `BaseGameTask`
   —— 因为它不覆写框架方法，符合 `RuntimeMixin`（纯工具）/ `FrameworkOverrideMixin`（覆写框架）
   的分层约定。识别层适配器另放 `src/core/detector/`，见设计文档 §5。

### 建议新增的两个接口

#### `verify_after_action(predicate, timeout=1.0, settle_time=0, raise_if_not_found=False) -> bool`

统一 5.1 中的四处 C。内部走 `wait_until`，支持「出现」与「消失」两种 predicate，支持 settle。
**实际收益收敛**：四处中三处是死代码，所以它的首要收益不是「统一」，
而是①给 `TreasureUnlockTask._click_band` 一个可复用的 C；
②为新设计提供 C 的收口点（`run_action_cycle` 的 C 阶段复用它）；
③**让未来不再出现第 5 套各写各的 C**。

#### `run_action_cycle(check_a, action, verify_c, timeout, retry, settle_time, pre_b=None, extra_action=None) -> bool`

表达完整生命周期：

- `pre_b` 为空 → 退化为「A → Action → C → 重试」
- `pre_b` 给定 → 进入持续阶段：每轮先判 `pre_b`，命中则 `extra_action` 并继续判 `verify_c`；
  未命中则**立即结束循环**

**不要把它做成状态机**。状态机（`TreasureUnlockTask` 那样）适合有跨帧业务状态的场景；
通用封装只负责「一段连续的重试窗口」。

### 其他处置

4. **`TreasureUnlockTask` 保持不动**。它是任务级状态机，含校准、队尾重排、完成兜底等
   无法通用的业务语义。但可把 `_click_band` 内层的
   `wait_until(lambda: not self._band_present(...))` 换成 `verify_after_action`
   —— 这是零风险的收益。

5. **`settle_time` 必须默认 0**。历史教训：曾给 `find_button` 加 `settle_time`，
   导致「变慢很多」并被 revert。原因是 `wait_until` 的 settle 语义要求**每次调用**都连续成立够时长。
   等待只应发生在「即将点击的那一次」，不要放进通用检测路径。

## 附：检索方法与范围

- 通读：`src/core/BaseGameTask.py`（518 行）、`src/core/base_mixin/runtime_mixin.py`（1300 行）、
  `src/core/base_mixin/framework_override_mixin.py`、`src/core/sequence_parser.py`
- 通读全部 4 个 trigger 任务：`TreasureUnlockTask` / `StarLinkAssistTask` /
  `SkipDialogTask` / `TemplateMonitorTask`，以及 `DailyTask.py`（编排示例）、
  `TestInteractionTask.py`（`wait_click_*` 的现成消费者）
- 在框架源码 `.venv/Lib/site-packages/ok/task/task.py` 中定位全部等待/点击原语的精确行号与签名
- 检索方式：按行为模式而非函数名匹配，逐处阅读调用关系与上下文
