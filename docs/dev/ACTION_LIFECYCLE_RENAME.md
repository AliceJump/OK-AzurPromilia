# `click_feature` 及其相关函数的命名与逻辑改造方案

> **状态：已全部实施。** `wait_action_result` / `wait_expectation` 已加入 `RuntimeMixin`，
> `src/core/detector/` 已建立，`click_feature` / `wait_feature_disappear` / `feature_stable` /
> `click_confirm` 已按本文 §0 的处置删除（`find_confirm` 保留）。
> 使用说明见 `DEVELOPMENT.md`「Action 生命周期」一节。
>
> 前置：`ACTION_LIFECYCLE_AUDIT.md`（现状）＋ `ACTION_LIFECYCLE_DESIGN.md`（分层设计）。
> 本文只回答两件事：**改成什么名**、**逻辑怎么改**。

## 0. 先定事实：这些函数的真实状态

命名与逻辑的取舍取决于「谁在用」。实测结果（详见 DESIGN §3.1）：

| 函数 | 现有位置 | 调用方 | 处置 |
|---|---|---|---|
| `click_feature` | `runtime_mixin.py:198-257` | **无** | ☠️ 删除（能力由新函数承接） |
| `wait_feature_disappear` | `runtime_mixin.py:259-277` | 仅 `click_feature` 内部 | ☠️ 删除（其语义并入新 C 判定） |
| `feature_stable` | `runtime_mixin.py:186-196` | 仅 `click_feature` 内部 | ☠️ 删除（改用 `wait_until(settle_time=)`） |
| `click_confirm` | `BaseGameTask.py:453-502` | **无** | ☠️ 删除；**保留 `find_confirm`**（`SkipDialogTask:42` 在用） |
| `safe_back` | `runtime_mixin.py:390-437` | **无** | ⚠️ **保留**（是有效范式，见 §3.3） |
| `wait_button` | `runtime_mixin.py:689-727` | **无** | ⚠️ **保留**（文档已公开，见 §3.4） |
| `wait_click_feature` | `runtime_mixin.py:1138-1187` | `login_flow`、`TestInteractionTask` | ✅ 保留 + 转调（§3.1） |
| `wait_click_ocr` | `runtime_mixin.py:1189-1300` | `TestInteractionTask` | ✅ 保留 + 转调（§3.1） |

**关键推论**：这是一次**「先删、再建、最后收口」**的改造，不是「给 `click_feature` 改个名」。
因为它是死代码，最干净的做法是**删除**，让新函数承接能力——而不是把它的名字留下来继续误导人。

---

## 1. 命名方案

### 1.1 命名原则

从仓库现有命名反推（`find_button` / `wait_button` / `find_buttons` / `analyze_button` /
`wait_click_feature` / `wait_ui_stable`）可归纳出三条约定：

| 约定 | 例证 | 含义 |
|---|---|---|
| `find_*` | `find_button` / `find_one` / `find_feature` | **单帧、无等待**，拿一帧就返回 |
| `wait_*` | `wait_button` / `wait_ocr` / `wait_ui_stable` | **跨帧等待**某个条件成立 |
| `wait_click_*` | `wait_click_feature` / `wait_click_ocr` | 等待 + **成功后立即点击** |

新函数必须落进这三档，不能造第四种风格（如 `do_*` / `run_*`）。

### 1.2 推荐命名

| 新函数 | 动宾结构 | 落进哪一档 | 为什么不选别的 |
|---|---|---|---|
| `wait_action_result` | 「等待动作结果」 | `wait_*` | ✅ 见下 |
| `click_and_verify` | 「点击并验证」 | `*_and_*`（新） | ❌ 见下 |
| `run_action_cycle` | （DESIGN 里的旧名） | ❌ 无先例 | ❌ 见下 |

**先说被我否掉的两个名字**：

- **`run_action_cycle`**（设计文档 §2.1 用的）——**必须改掉**。
  仓库里 `run` 是**任务循环入口**的专用词（`TriggerTask.run()` / `BaseGameTask.run()`），
  一个 Mixin 工具方法叫 `run_*` 会让人误以为是任务驱动入口。
  而且 `cycle` 暗示它自己是个循环体，实际上它只是「等一个条件 → 点一次 → 再等一个条件」。
- **`click_and_verify`** —— 语义准确，但把「Action 固定为 click」写进了名字。
  而设计里 `action` 是**可注入的回调**（可能是 `press_key` / `move` / `click`），
  所以名字不该绑定 click。

**推荐 `wait_action_result`**，理由：

1. `wait_` 前缀符合第二档约定，明确表达「会阻塞、有超时」。
2. 主体是 `action_result`（动作的结果），**不绑定具体 Action 类型**，与可注入的 `action` 参数一致。
3. 与 `wait_click_feature` 形成清晰的层级关系——
   `wait_click_feature` 是「等到元素出现就点」，`wait_action_result` 是
   「等条件 → 动作 → 再等结果」，后者包含前者。

### 1.3 参数命名改进（相比设计文档 §2.1）

设计文档里的参数名有几个与仓库既有习惯不一致，一并调整：

| 设计文档原名 | 建议改为 | 理由 |
|---|---|---|
| `timeout` | `time_out` | 仓库一律用 `time_out`（带下划线）：`wait_button(time_out=5)`、`wait_click_feature(time_out=0)` |
| `check_a` | `condition` | `check_a` 是本文档的记号（A/B/C），**不该泄漏进 API** |
| `verify_c` | `expect` | 同上；且 `expect` 比 `verify` 更贴合「预期结果」的语义 |
| `check_b` | `while_condition` | 同上；且明确「持续成立期间一直做」的语义 |
| `extra_action` | `repeat_action` | 与 `while_condition` 配对，表达「条件成立时重复执行」 |
| `settle_a` / `settle_c` | `settle_time` / `expect_settle_time` | 后者需要区分，前者就是主 `settle_time` |
| `retry` | `max_attempts` | `retry` 语义模糊（是「重试次数」还是「总次数」）；`max_attempts` 无歧义 |

**命名忌讳**：不要出现 `a` / `b` / `c` 这样的单字母参数名。它们只在文档里作为记号有意义。

### 1.4 `verify_after_action` 的命名

| 候选 | 评价 |
|---|---|
| `verify_after_action` | ⚠️ `after_action` 冗余——「验证」本身就隐含「某事之后」 |
| `wait_result` | ❌ 太泛，不知道是谁的结果 |
| `wait_expect` | ❌ 不成词 |
| **`wait_expectation`** | ✅ 推荐 |

`wait_expectation(predicate, time_out=1.0, settle_time=0.0, raise_if_not_found=False)`
—— 落进 `wait_*` 档，名词 `expectation` 与参数 `expect` / `expect_settle_time` 同族。

### 1.5 识别层适配器的命名

| 类名 | 说明 |
|---|---|
| `Hit` | 统一命中结果（`box` / `confidence` / `source` / `text` / `raw`） |
| `Detector` | 判据协议基类（`detect(frame) -> Hit \| None`） |
| `TemplateDetector` | 模板匹配（包 `find_feature` / `find_one`） |
| `YoloDetector` | YOLO（包 `yolo_detect`） |
| `OcrDetector` | OCR（包 `ocr`） |
| `ButtonDetectorAdapter` | 按钮检测（包 `find_button`） |
| `PredicateDetector` | 包任意 `frame -> bool` 或 `frame -> Box\|None` |

**`PredicateDetector` 优于 `CallableDetector`**：`Callable` 是实现细节（它是个可调用对象），
`Predicate` 是语义（它判断一个条件）。命名应描述**用途**而非**类型**。

`Hit` 保持短名——它会出现在大量函数签名的返回值位置（`-> Hit | None`），
`DetectionResult` 之类会让签名变长。

---

## 2. 逻辑怎么改

### 2.1 `click_feature` 的逻辑缺陷

现有实现（`runtime_mixin.py:198-257`）有三个缺陷，**新逻辑必须逐个解决**：

```python
# 现有实现的骨架
while time.time() - start_time < time_out:          # 缺陷②：用 time.time()
    frame = self.next_frame()
    for box in boxes:                                # 缺陷③：boxes 线性回退
        result = self.find_feature(feature_name=feature, box=box, frame=frame)
        if result and self.feature_stable(feature, box, settle_time):   # 缺陷①：A=C=feature
            retry_count = 0
            while retry_count < max_click_retry:
                self.click(result, after_sleep=after_sleep)
                if not verify_disappear:
                    return True
                if self.wait_feature_disappear(feature, box, verify_timeout):
                    return True
                retry_count += 1
    if blind_point and ...:
        self.click(blind_point[0], blind_point[1])   # 缺陷④：盲点击混在正常流程里
return False
```

| # | 缺陷 | 后果 | 新逻辑的解法 |
|---|---|---|---|
| ① | A 与 C 共用 `feature` | 只能表达「出现→点击→消失」，**无法 A≠C** | `condition` 与 `expect` 是**两个独立判据对象** |
| ② | 用 `time.time()` | 游戏暂停/脚本挂起期间**判定失败** | 一律 `active_time()`（暂停感知） |
| ③ | `boxes` 线性回退做进签名 | 无法组合不同识别源；参数语义混杂 | 用 `MultiBoxDetector` 组合器 |
| ④ | `blind_point` 盲点击混在循环里 | 盲点击与「检测后点击」的失败语义混在一起 | **移出**主流程（见 §2.4） |

### 2.2 新逻辑：`wait_action_result`

```python
def wait_action_result(
    self,
    action,                              # Hit -> bool | None
    condition,                           # Detector：前置条件
    expect=None,                         # Detector | None：预期结果；None = 动作成功即返回
    # ── 前置条件窗口 ──
    time_out=5.0,
    settle_time=0.0,                     # 必须默认 0
    # ── 动作与重试 ──
    max_attempts=1,                      # 动作最多执行次数
    action_delay=0.0,                    # 动作前延时
    after_sleep=0.0,                     # 动作后延时
    expect_retry=0,                      # expect 未命中后的额外重试次数
    # ── 预期结果窗口 ──
    expect_time_out=1.0,
    expect_settle_time=0.0,
    # ── 持续阶段（可选）──
    while_condition=None,                # Detector | None
    repeat_action=None,                  # Hit -> None
    repeat_interval=0.0,
    max_repeat=0,
    # ── 调试 ──
    draw=False,
    raise_if_not_found=False,
) -> bool:
```

**流程图**（与设计文档 §2.2 一致，此处补充参数落点）：

```
t0 = active_time()
deadline_a = t0 + time_out

┌─ 阶段一：等条件 ──────────────────────────────────┐
│  while active_time() < deadline_a:                 │
│      frame = next_frame()                          │
│      hit = condition.detect(frame)                 │
│      if hit:                                       │
│          if settle_time <= 0: break  → 阶段二      │
│          if wait_until(lambda: condition.detect(next_frame()),   │
│                        time_out=settle_time):      │
│              break  → 阶段二                        │
│      sleep(0.01)                                   │
│  else:  # 超时                                     │
│      if raise_if_not_found: raise WaitFailedException()
│      return False                                  │
└────────────────────────────────────────────────────┘
                        ▼
┌─ 阶段二：执行动作 + 验证 ─────────────────────────┐
│  attempt = 0                                       │
│  while attempt < max_attempts:                     │
│      # 重取一次帧，拿最新 box（防移动目标）        │
│      fresh = condition.detect(next_frame())        │
│      target = fresh or hit          # 失败沿用旧值  │
│      sleep(action_delay)                           │
│      action(target)                                │
│      sleep(after_sleep)                            │
│      attempt += 1                                  │
│                                                    │
│      if expect is None: return True                │
│      if wait_expectation(expect, expect_time_out,  │
│                           expect_settle_time):     │
│          return True                               │
│      # expect 未命中 → 继续 attempt 循环            │
│                                    ← max_attempts  │
└────────────────────────────────────────────────────┘
                        ▼ （attempt 耗尽）
┌─ 阶段三：持续阶段（仅当 while_condition 给定）────┐
│  repeat = 0                                        │
│  while active_time() < deadline_b:                 │
│      if max_repeat and repeat >= max_repeat: break │
│      frame = next_frame()                          │
│      hit_b = while_condition.detect(frame)         │
│      if not hit_b: break          # ← B 未命中即停  │
│      sleep(repeat_interval)                        │
│      repeat_action(hit_b)                          │
│      repeat += 1                                   │
│      if expect and wait_expectation(expect, ...):  │
│          return True              # ← C 命中即成功  │
│  return False                                      │
└────────────────────────────────────────────────────┘
```

### 2.3 三个必须写进代码注释的行为约定

**① `settle_time` 默认 0，且注释里要写明含义**

```python
settle_time=0.0,   # 条件需「持续成立」的秒数；0 = 命中一次即可。
                   # ⚠️ 非 0 时每次判定都要求连续成立够时长（不是总共等这么久），
                   #    会显著增加耗时，只在「等 UI 稳定下来再点」时开启。
```

这是上次给 `find_button` 加 `settle_time` 导致「变慢很多」被 revert 的那条教训
（AUDIT §5.3 / 6.5）。**把含义写进 docstring，不要假设后来者知道。**

**② 阶段二的「重取帧」不能省**

```python
# 条件在 t0 命中，但若 settle_time > 0，动作发生在 t0 + settle_time，
# 此时 target 可能已位移（star_link_icon / treasure_key_icon 这类会动的图标）。
# 所以重取一帧；取不到则沿用旧值，不因单帧抖动放弃。
fresh = condition.detect(self.next_frame())
target = fresh if fresh is not None else hit
```

**③ `expect=None` 的语义是「动作成功即返回」**

不能把 `expect=None` 当成「不验证所以一定失败」，也不能当成「跳过验证但继续 attempt」。
它就是契约：**调用方声明「这个动作没有可验证的结果」**，函数立即返回 True。

### 2.4 `blind_point` 的处理：移出主流程

现有 `click_feature` 把盲点击塞在同一循环里，语义混乱（盲点击和检测点击混在一起，
`return` 路径也不同）。**新设计不接收 `blind_point`**，改为两个办法：

- **调用方自行兜底**：`if not self.wait_action_result(...): self.click_blind_point(...)`
- **用 `PredicateDetector` 表达**：把「盲点击」作为一个恒真判据——

```python
# 明确表达「先试检测，超时后盲点」
self.wait_action_result(
    condition=MultiBoxDetector([
        TemplateDetector(feature=FeatureList.treasure_key_icon, box=kbox),
        BlindPointDetector(self, x, y),      # 恒命中，兜底
    ]),
    ...
)
```

**不建议**在 `wait_action_result` 里保留 `blind_point` 参数——它属于「识别策略」，
不属于「生命周期编排」。

### 2.5 `wait_click_feature` / `wait_click_ocr` 如何转调

这两个是**有活调用方**的（`login_flow` / `TestInteractionTask`），转调必须**保持行为等价**。
它们本质是 `wait_action_result` 的 `expect=None` 退化形式：

```python
def wait_click_feature(self, feature, ..., settle_time=-1, after_sleep=0, alt=False):
    # 保持旧签名（有调用方），内部转调
    detector = TemplateDetector(
        feature=feature,
        box=box,
        horizontal_variance=horizontal_variance,
        vertical_variance=vertical_variance,
        threshold=threshold,
        use_gray_scale=use_gray_scale,
        canny_lower=canny_lower,
        canny_higher=canny_higher,
        target_height=target_height,
    )
    return self.wait_action_result(
        action=lambda hit: (self.click_with_alt(hit.box, after_sleep=after_sleep)
                            if alt else
                            self.click_box(hit.box, relative_x, relative_y,
                                           after_sleep=after_sleep)),
        condition=detector,
        expect=None,                       # 旧行为：点完即成功
        time_out=time_out or 0,
        settle_time=settle_time if settle_time >= 0 else -1,   # ⚠️ 注意
        action_delay=click_after_delay,
        raise_if_not_found=raise_if_not_found,
    )
```

**⚠️ 三个必须小心的地方**：

1. **`settle_time` 的哨兵值 —— 已实测，风险比预想小**。

   链路（实测确认）：

   ```
   wait_click_feature(settle_time=-1)                       # 旧签名默认
     → wait_until(settle_time=-1)                            # ok/task/task.py:508
       → executor.wait_condition(settle_time=-1)             # TaskExecutor.py:409
           if settle_time == -1:
               settle_time = self.wait_until_settle_time     # TaskExecutor.py:424
   ```

   而 `wait_until_settle_time` 在本项目的来源：

   ```python
   # src/config.py:53
   "wait_until_settle_time": 0,  # 调用 wait_until 时候, 在第一次满足条件的时候,
                                 # 会等待再次检测, 以避免某些滑动动画没到预定位置就在动画路径中被检测到
   ```

   ⇒ **本项目 `wait_until_settle_time = 0`**，所以 `settle_time=-1` 最终解析为
   **0（不要求稳定）**。这意味着 `settle_time=-1` 与 `settle_time=0` **在本项目里行为等价**。

   转调时的正确处理（无需读 executor）：

   ```python
   settle = 0.0 if settle_time <= 0 else float(settle_time)
   ```

   即 **把 `-1` 和 `0` 都映射成 0**。这在当前配置下行为完全一致。
   但仍建议在转调处留一行注释说明这段链路——因为 `wait_until_settle_time` 是
   **用户可配置项**，若将来被改成非 0，`-1` 与 `0` 就会分叉。

2. **`time_out=0` 的语义**。旧实现 `time_out=0` 表示「用 `wait_until` 的默认超时」
   （`TaskExecutor.py:413-414` → `self.wait_scene_timeout`，来自
   `config.get(...)` 的 `wait_until_timeout`，框架默认 10）。
   转调时若直接传 0，需确保新函数把 0 也解释为「走下游默认」，语义才等价。

3. **`login_flow` 那处的 `settle_time=1`** 是全仓库唯一带 settle 的真实调用
   （`account_mixin.py:131-135`）。它是**显式传 1**，不受上述哨兵值影响，
   转调后行为应保持一致（要求按钮连续稳定 1 秒）。转调后需实测账号切换流程耗时。

**如果这三点的处理成本超过收益，就先不转调**——保留两个函数并存，
只在文档里注明「新代码用 `wait_action_result`」。**不要为了「统一」而引入行为回归。**

### 2.6 `find_confirm` 保留、`click_confirm` 删除

`click_confirm` 零调用方，但它内部的 `find_confirm` 被 `SkipDialogTask:42` 直接调用。

```python
# click_confirm 删除后，SkipDialogTask 的逻辑不受影响（它本来就没走 click_confirm）

# BaseGameTask.py 改动
- def click_confirm(self, ...):      # 整块删除（L453-502）
  def find_confirm(self):            # 保留（L503-518）
```

但要注意：`click_confirm` 里的「点击 → 等消失 → 二次复检」是有效逻辑，
**删除前应确认它没有被 `SkipDialogTask` 之外的间接路径依赖**（已确认无）。
若将来需要，用新函数表达即可：

```python
self.wait_action_result(
    condition=PredicateDetector(lambda f: self.find_confirm()),
    action=lambda hit: self.click(hit.box),
    expect=PredicateDetector(lambda f: not self.find_confirm(), invert=...),
    time_out=5, expect_time_out=0.8, max_attempts=2,
)
```

### 2.7 `feature_stable` 的删除

`feature_stable` 自己写 `time.time()` 循环，与 `wait_until(settle_time=)` **语义不等价**
（前者暂停期间判失败，后者冻结）。删除后所有稳定判定统一走 `wait_until`。

若某处确实需要「暂停期间判失败」的旧语义——**目前没有这样的调用方**——
应显式用 `PredicateDetector` + 自建循环表达，而不是保留一个语义特殊的公共函数。

---

## 3. 保留项的说明

### 3.1 `wait_click_feature` / `wait_click_ocr` —— 保留 + 条件转调

见 §2.5。**核心判断**：它们有活调用方，转调收益（少一份实现）小于回归风险。
建议**先只加文档注释**指向新函数，等 `wait_action_result` 在 `_click_band` 上验证稳定后再转调。

### 3.2 `find_feature` / `find_one` / `yolo_detect` / `ocr` —— 不动

它们是被适配器包装的底层原语，**不是改造对象**。

### 3.3 `safe_back` —— 保留

虽然零调用方，但它是**正确且有用的范式**（「A 未命中 → 执行恢复 Action，直到 A 命中或超时」），
与 `wait_action_result` 是**互补关系**不是替代关系：

- `wait_action_result`：A 命中 → 动作 → 验证
- `safe_back`：A 未命中 → **持续做恢复动作** → 直到 A 命中

它对应需求里「A 未命中时，不执行 Action，或按照现有重试机制处理」的**后半句**。
将来 `wait_action_result` 可以加 `on_missing` 回调来吸收它，但**不要现在合并**。

### 3.4 `wait_button` —— 保留

零调用方，但 **`docs/dev/DEVELOPMENT.md:149` 已经把它的用法写成公开文档**了。
删除会与文档冲突。它是 `find_button` 的等待版，语义清晰（`wait_until` 的直接包装），
**建议保留并在 DEVELOPMENT.md 里补一行「也可用 `wait_action_result` 表达」**。

---

## 4. 改造后全景

### 4.1 新增

```
src/core/detector/
  __init__.py            # 导出 Hit / Detector / 各适配器
  hit.py                 # Hit
  base.py                # Detector 协议 + PredicateDetector
  template_detector.py   # TemplateDetector
  yolo_detector.py       # YoloDetector
  ocr_detector.py        # OcrDetector
  button_adapter.py      # ButtonDetectorAdapter
  combinators.py         # MultiBoxDetector（+ 将来的 FirstHitDetector）

src/core/base_mixin/runtime_mixin.py
  + wait_expectation(...)
  + wait_action_result(...)
```

### 4.2 删除

```
src/core/base_mixin/runtime_mixin.py
  - click_feature          (198-257)
  - wait_feature_disappear (259-277)
  - feature_stable         (186-196)

src/core/BaseGameTask.py
  - click_confirm          (453-502)   ← find_confirm (503-518) 保留
```

### 4.3 保留不动

```
runtime_mixin.py:  wait_click_feature / wait_click_ocr / safe_back / wait_button
                   find_feature / find_one / find_button / yolo_detect / ocr
BaseGameTask.py:   find_confirm
```

### 4.4 同步文档

| 文件 | 改动 |
|---|---|
| `docs/dev/DEVELOPMENT.md` | 新增「Action 生命周期」一节；`wait_button` 那行补一句「等价表达」 |
| `docs/dev/QUICKSTART.md` | L25 提到 `wait_click_feature`，可补一句新 API 指引 |

---

## 5. 命名与逻辑决策速查

| 项 | 决策 | 核心理由 |
|---|---|---|
| 主函数名 | **`wait_action_result`** | 落进 `wait_*` 档；不绑定 Action 类型；`run_*` 与任务循环入口冲突 |
| C 判定短函数名 | **`wait_expectation`** | `after_action` 冗余；与 `expect` 参数同族 |
| 是否保留 `click_feature` 之名 | **否，删除** | 零调用方；A=C 是设计缺陷，不该留在名字里 |
| 参数名用 `check_a`/`verify_c` | **否**，改 `condition`/`expect` | 文档记号不该泄漏进 API |
| 超时参数名 | **`time_out`**（带下划线） | 与仓库既有 `wait_button(time_out=5)` 一致 |
| 计时函数 | **`active_time()`** | 暂停感知；旧实现的 `time.time()` 是缺陷 |
| `settle_time` 默认 | **0**，且注释写明含义 | 历史教训：settle 每次判定都要求稳定，会显著变慢 |
| `blind_point` | **移出主流程** | 属识别策略，不属生命周期编排 |
| `boxes` 多区域回退 | **`MultiBoxDetector` 组合器** | 能组合不同识别源，参数不膨胀 |
| `wait_click_feature` 是否转调 | **暂不**，先并存 | 有活调用方；虽然 `settle_time=-1` 已确认等价于 0，但转调仍需实测 |
| `click_confirm` 处置 | **删除函数，保留 `find_confirm`** | `SkipDialogTask:42` 在用后者 |
| `feature_stable` 处置 | **删除** | 与 `wait_until(settle_time=)` 语义不等价，统一到后者 |
| `safe_back` / `wait_button` | **保留** | 前者是 A 未命中范式；后者已被 DEVELOPMENT.md 公开 |
| `-1` 哨兵值处理 | **`0 if settle_time <= 0 else settle_time`** | 实测本项目 `wait_until_settle_time = 0`（`src/config.py:53`），故 `-1` ≡ `0` |
