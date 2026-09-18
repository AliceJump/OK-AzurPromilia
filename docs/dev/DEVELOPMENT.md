# 开发指南（DEVELOPMENT）

## 架构

本项目基于 [ok-script](https://github.com/ok-oldking/ok-script)。应用配置集中在 `src/config.py`，任务与 Tab 均在此注册。

**框架版本锁定 `ok-script==2.0.6`**（`pyproject.toml` + `uv.lock` 为唯一来源）。

### 目录职责

| 目录 | 职责 |
|------|------|
| `main.py` / `main_debug.py` | 入口（Release / Debug）。启动前先装 `src/patches/` |
| `src/config.py` | ok-script 应用配置：窗口、OCR、模板匹配、任务与 Tab 注册 |
| `src/core/BaseGameTask.py` | **任务基类**：暂停感知计时、配置迁移、异常处理、配置分组 |
| `src/core/base_mixin/runtime_mixin.py` | 通用能力库：分辨率映射、点击验证、YOLO 检测、画面稳定判定、键鼠 |
| `src/core/base_mixin/framework_override_mixin.py` | 用"同名覆写 + `super()`"扩展框架方法（不复制框架代码） |
| `src/core/config_migration.py` | 配置键迁移工具（改键名时使用，防丢用户配置） |
| `src/core/global_config_store.py` | 本项目自建的全局配置（**不走框架的 `config['global_configs']`**） |
| `src/interaction/` | 窗口与键鼠：`GameInteraction`（自定义后台输入）、`Mouse`、`ScreenPosition`、`KeyConfig` |
| `src/image/` | 图像算法：`rotated_template`（旋转模板匹配）、`stability`（图像指纹）、`frame_processes`、`hsv_config`、`button_detector`（固定 Box 按钮检测，无 OCR）、`treasure_band_detector`（开锁颜色带，HSV+连通域） |
| `src/yolo/` | YOLO 模型注册（`models.py`）与 OpenVINO 推理 |
| `src/tasks/onetime/` | 一次性任务 |
| `src/tasks/trigger/` | 触发式任务 |
| `src/tasks/test/` | 调试/测试任务 |
| `src/tasks/daily/` | 多步骤任务的编排（四态统计、失败标记、分账号轮次、汇总报告） |
| `src/tasks/account/` | 账号作用域配置存储 |
| `src/gui/` | 自定义 Tab（全局配置页、账号配置页） |
| `src/patches/` | 启动补丁。**是对框架内部实现的猴子补丁，升级 ok-script 前必须逐个复核** |
| `src/data/FeatureList.py` | 模板名枚举（**由标注自动生成**，代码里不要写裸字符串模板名） |
| `src/data/lang/` | `assets/lang/*.json` 的读取器 |
| `assets/coco_annotations.json` | 模板标注（COCO 格式） |
| `assets/lang/` | **OCR 匹配文本**（不是 UI 文案） |
| `i18n/` | gettext 目录（**UI 文案**） |
| `scripts/` `tools/` | 语言同步、PO 修复、lang 类型桩生成等工具 |
| `tests/` | unittest 测试 |

### 待补的部分

工程骨架已就位，但**游戏内容尚未开始**。当前空缺：`config['scene']` 帧级缓存、
会话级流程引擎、3D 移动闭环、导航、战斗层，以及**任何视觉回归测试**。

## 宝箱开锁：颜色带检测与开锁任务

开锁小游戏右侧轨道里的颜色带是**低饱和、高亮度、竖向细长**的矩形块，同一区域还有
两类干扰：贴 ROI 边缘的轨道边框、矮胖的横向钥匙。前者靠「不贴边」排除，后者靠长宽比
排除，都不是颜色问题，所以统一走**连通域 + 形状判据**。

### 检测器 `src/image/treasure_band_detector.py`

流程：`Box → ROI → HSV inRange(S≤80, V≥190) → 3x3 开运算 → 连通域 → 形状过滤 → 按 Y 排序`

默认判据（1920×1080 实测）：`area≥300`、`w≤30`、`h≥30`、`h/w≥2.0`、不贴 ROI 左右边缘。

```python
from src.image.treasure_band_detector import TreasureBandDetector

detector = TreasureBandDetector()                 # 复用实例，不要逐帧新建
roi = self.box_of_screen(0.6797, 0.2324, 0.7021, 0.7778)

bands = detector.find(frame, roi)                 # 命中，按 Y 升序
results = detector.analyze(frame, roi)            # 全部连通域，含被过滤项与原因
detector.y_ranges(bands)                          # [(286, 391), (552, 605), (672, 725)]
TreasureBandDetector.hit_by_center_y(bands, 580)  # 钥匙 center_y 落在哪条带
```

| 成员 | 说明 |
|------|------|
| `find(frame, box)` | 命中的帧坐标 `Box` 列表（可直接 `click()` / `draw_boxes()`） |
| `analyze(frame, box)` | 全部连通域的 `BandDetection`，`failed` 给出过滤原因，用于校准阈值 |
| `presence(frame, box)` | Box 内目标颜色像素占比 0~1，**存在性判定专用** |
| `y_ranges(boxes)` / `hit_by_center_y(boxes, y)` | Y 区间 / 钥匙命中 |

阈值集中在 `BandThresholds`，用 `with_(...)` 生成副本。尺寸类阈值**不随分辨率自动缩放**，
调用方按 `resolution_scale()` 自行换算（开锁任务里已做）。

### 存在性判定为什么不用 `find()`

钥匙是横向结构，压在条带上会把它切成上下两段，连通域各自变小后被形状判据拒绝，
结果是「**明明还在却被判消失**」。所以判断条带在不在只统计 bbox 内目标颜色像素的
占比（`presence`），不看形状。真实截图实测：条带在 0.99，被抹掉后 0.00，
阈值 0.15 有充足余量。

### 开锁任务 `src/tasks/trigger/TreasureUnlockTask.py`

触发式任务，状态机跨 `run()` 调用保持：

```text
WAIT_TREASURE ──treasure_icon──> CALIBRATING_BANDS ──连续多帧稳定──> UNLOCKING
                                                                        │
                              钥匙 Y 命中缓存 bbox → 点击 → 稳定消失 → 移出 active
                                                                        ↓
   FINISHED <── 持续无条带 ── COMPLETION_CHECK <──── active 空 ─────────┘
```

要点：

- **校准结果是后续唯一的定位基准**。开锁阶段不再重建坐标，只用缓存 bbox 做
  「钥匙 Y 匹配」和「存在 / 消失判定」。
- 稳定判定一律用连续状态而非单帧：校准用「连续 N 帧布局一致」，消失与完成确认用
  `wait_until(..., settle_time=...)`（复用框架，就是「条件持续成立一段时间」）。
- 点击失败到上限后把该条带移到队尾，避免死磕一条；`active` 清空后还要做
  **完成确认**（`完成确认时长`），期间条带重现就回 `UNLOCKING`。
- 宝箱 UI 只作为「已退出界面」的**辅助**信号，不能反过来阻塞结束，否则图标常驻时
  会永远卡在完成确认里（有 2 倍时长上限兜底）。
- `treasure_key_icon` **必须显式传搜索 box**：`find_feature` 不传 box 时只搜 coco
  标注位置 ±variance（约 4px），而钥匙会沿轨道上下滑动，默认框根本搜不到。

调试任务 `src/tasks/test/TestTreasureBandTask.py` 会在覆盖层实时画框
（红=命中 / 绿=ROI / 蓝=被过滤），用来肉眼校准阈值。

## 固定 Box 按钮检测（中央文本带，无 OCR）

部分按钮（如「跳过剧情 / 确认」）底色为深灰 `RGB(50,50,53)`，与游戏背景几乎一致，
模板匹配和「整块颜色判定」都不稳定。这类按钮的可靠特征是**按钮中央的文字**。

`src/image/button_detector.py` 检测「Box 中央是否存在一条颜色落在指定 HSV 区间内的文本带」，
**只有两个颜色参数**：

- `text_hsv` —— 文字（前景）颜色区间，**主特征**；
- `backdrop_hsv` —— 底色（背景）颜色区间，**可选辅助特征**。

入口挂在 `RuntimeMixin` 上：

```python
box = self.box_of_screen(0.575, 0.61, 0.64, 0.64)
if result := self.find_button(box):      # 默认 = 深灰底 + 亮色文字
    self.click(result)                   # 结果可直接点击

# 直接传颜色
self.find_button(box, text_hsv=((0, 0, 170), (180, 100, 255)))
self.find_button(box, text_hsv=((0, 0, 0), (180, 255, 90)),
                      backdrop_hsv=((0, 0, 170), (180, 80, 255)))

# 或封装成语义常量复用
from src.image.button_detector import ButtonThresholds
SKIP_BUTTON = ButtonThresholds.for_button(
    ((0, 0, 170), (180, 100, 255)),   # 文字：亮色
    ((0, 0, 30), (180, 80, 110)),     # 底色：深灰
    name="skip_button",
)
self.find_button(box, thresholds=SKIP_BUTTON)
```

| 方法 | 返回 | 说明 |
|------|------|------|
| `find_button(box, frame=None, thresholds=None, text_hsv=None, backdrop_hsv=None)` | `Box \| None` | 命中返回可直接 `click()` 的 Box |
| `find_buttons(boxes, ...)` | `Box \| None` | 多个候选 Box，返回首个命中 |
| `wait_button(box, time_out=5, ...)` | `Box \| None` | 以视觉状态等待，不用固定延时 |
| `analyze_button(box, ...)` | `ButtonDetection` | 含中间指标与未命中原因，用于校准阈值 |
| `button_detector(thresholds=None)` | `ButtonDetector` | 缓存的检测器实例 |

预设（`ButtonThresholds` 的类方法 / 常量）：

| 预设 | 文字 HSV | 底色 HSV | 适用 |
|------|----------|----------|------|
| `DARK_BUTTON_THRESHOLDS` / `DEFAULT_BUTTON_THRESHOLDS`（默认） | `(0,0,170)~(180,100,255)` | `(0,0,30)~(180,80,110)` | 深灰 / 黑底 + 亮字 |
| `LIGHT_BUTTON_THRESHOLDS` | `(0,0,0)~(180,255,90)` | `(0,0,170)~(180,80,255)` | 亮 / 白底 + 深字（默认校验底色） |
| `for_button(text_hsv, backdrop_hsv, name=...)` | 任意 | 可选 | 封装任意语义按钮 |

要点：

- 底色区间**只在 Box 不够贴合按钮时才明显生效**：真实 1080P 截图滑窗 884 个位置实测，
  只用文字特征误报 17，加上底色区间后 0，命中率不变，单次 +0.07 ms。按需开启。
- 所有阈值集中在 `ButtonThresholds`，用 `with_(...)` 生成改过的副本，不修改默认值。
- 传入的 Box 要**贴合按钮**；Box 远大于按钮时文本带相对过薄，会被形状判定拒绝。

## 配置键迁移

修改 `default_config` 键名时必须先添加迁移表（同一提交完成）：

```python
class MyTask(BaseGameTask):
    config_key_migrations = {"旧键": "新键"}
```

`BaseGameTask.load_config` 会沿 MRO 收集所有迁移表并执行，详见 `src/core/config_migration.py`。
完整的改键名流程（含 i18n 与文档同步）见 `AGENTS.md`。

## i18n

本项目有**两条互不混用**的翻译链路：

- **UI 文案**：代码用 `self.tr("中文")`，msgid 写入 `i18n/*/LC_MESSAGES/ok.po`，
  再编译 `.mo`：

  ```bash
  python tools/task_i18n_helper.py compile --i18n i18n   # 编译全部 .mo
  python tools/task_i18n_helper.py check --i18n i18n     # 查重复 msgid（有则退出码 1）
  python scripts/validate_all.py                         # 编译 + 查空 msgstr
  ```

  新增文案时，先用 `scan` 把该进 `.po` 的字符串列出来，避免漏翻：

  ```bash
  python tools/task_i18n_helper.py scan --task src/tasks/onetime/DailyTask.py
  ```

- **OCR 匹配文本**：放进 `assets/lang/<模块>.json`，每 key 下 6 种语言节点，
  代码用 `self.lang.<模块>.<key>` 读取。

其它相关工具：

```bash
python tools/gen_lang_stubs.py          # 重新生成 src/data/lang/_lang_typed.py 类型桩
python scripts/lang_fill_missing.py     # 补全 assets/lang/*.json 缺失语言（--dry-run 可预览）
python tools/repair_po_locales.py --apply   # 修复 .po 中语言写错的条目
```

## 测试

- 测试位于 `tests/`，使用 `unittest`。
- 全部跑：`uv run python -m unittest discover -s tests`
  或逐文件跑：`./run_tests.ps1`（CI 在打 tag 时也跑它）。

> ⚠️ 不要用 `python -m ok.test.RunTests` —— 它在跑完测试后会在 `ok.quit()` 处抛
> `AttributeError`，退出码非 0。

写视觉/任务逻辑的测试时用框架的 `TaskTestCase` + `set_image()`（静态图驱动），
它可以断言"识别结果"与"决策方向"，但不能断言真实输入效果。

## 运行

```bash
uv sync
uv run python main.py          # Release
uv run python main_debug.py    # Debug（更多日志、overlay、热重载）
```

命令行参数只有三个：`-t <1 起的序号>`、`-e`（跑完退出）、`-h`（headless）。
注意 `-t` **只接受序号**，不接受任务名。

## 发布

- 本地打 tag：`.\auto_release.ps1 -DryRun`（预览）或直接执行。
- 每日自动发版：`.github/workflows/auto-release.yml` 检查 `deploy.txt` 关注路径是否有变更，
  有则自动递增版本号并打 tag。
- 打 tag 后 `.github/workflows/build.yml` 自动测试、用 pyappify 打包并发布 Release。
- **`requirements.txt` 是 `pyproject.toml` + `uv.lock` 的派生产物，不要手改**。
  依赖变更后重新生成：

  ```bash
  uv export --no-hashes --no-dev --output-file requirements.txt
  ```

  （CI 用 `pip install -r requirements.txt` 装依赖，不一致会导致打包失败。）

## 目录约定

- 新增任务类必须注册进 `src/config.py`，否则 UI 中不可见。
- 任务/配置相关文档与代码同步更新。
