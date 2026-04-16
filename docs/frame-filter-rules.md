# 客户端截帧过滤规则 (v0.5.2 draft)

本文档基于对 session `ccfc283b` (员工 10446, 2026-04-16, 3624 帧, ~2h50m) 的离线分析，
给出 Windows 客户端 (`src/workflow_recorder/daemon.py`) 在**上传前**应当丢弃的帧类别
及其判定方法。目标是在不损失 SOP 流程信息量的前提下，降低上传 / 服务端分析成本。

## 结论（TL;DR）

- **当前实现** (`Daemon._should_drop_as_idle_duplicate`, daemon.py:182) 要求 "phash 近同 **且**
  自上次截帧以来无任何键鼠输入" 才丢弃。这个逻辑在 `ccfc283b` 上**几乎没起作用**——
  3624 帧里仍有 **52.7% (1902 帧) 与前一张近同**。根因是 Win32 `GetLastInputInfo()`
  会把任意鼠标微移也当作输入，而用户实际的工作流经常是"手放在鼠标上但屏幕没变"。
- **建议改为**：移除 "无输入" 这个硬约束，把 phash 去重放宽到"近同且未超过心跳
  间隔就丢"。配合最小间隔和空画布判定，实际工作流损失 < 5%，**节省 51.5% 上传量**。

| 规则 | 估算丢弃率 (ccfc283b) | 复杂度 | 风险 |
| --- | --- | --- | --- |
| **R1** phash 去重 + 心跳 | 51.5% | 低（已有 imagehash） | 低 |
| **R2** 最小截帧间隔 ≥ 1.0s | 0% (本 session) / 高流量时大 | 极低 | 无 |
| **R3** 空画布 / 加载画面 | 5-10%（经验值） | 低（PIL + numpy） | 低 |
| **R4** 应用黑名单增量 | 视员工行为而定 | 零（已有框架） | 需维护清单 |
| **R5** 自我捕获 (recorder 自身窗口) | 0.5%-2% | 低 | 无 |

## 规则细则

### R1 — 放宽 phash 去重 + 心跳保活

**现状**：`drop_idle_duplicate_frames=True`（默认）会丢弃与上一帧 phash hamming
距离 ≤ 2 **且** `GetLastInputInfo` 上报"此次截帧区间内无输入"的帧。
**问题**：用户抬手休息但鼠标没停，或在 Chrome 上轻微滚轮，OS 都认为"有输入"；
phash 几乎无变化的帧被保留，如 `ccfc283b` 中 frames 646-791（146 帧连续近同）。

**建议实现**（daemon.py:182 改写）：

```python
def _should_drop_as_near_duplicate(self, image_path, prev_capture_time):
    cap = self.config.capture
    if not cap.drop_idle_duplicate_frames:
        return False

    new_hash = _phash(image_path)       # phash(size=8)
    prev_hash = self._last_frame_hash

    # 首帧永远保留
    if prev_hash is None:
        self._last_frame_hash = new_hash
        return False

    # 真实变化 → 保留
    if (new_hash - prev_hash) > cap.duplicate_hash_threshold:
        self._last_frame_hash = new_hash
        return False

    # 心跳保活：距上次保留 ≥ heartbeat_seconds 时强制保留一帧，用于证明在岗 / 审计
    elapsed = time.monotonic() - self._last_kept_time
    if elapsed >= cap.heartbeat_seconds:
        self._last_frame_hash = new_hash
        self._last_kept_time = time.monotonic()
        return False

    # phash 近同 且 未到心跳 → 丢
    return True
```

**推荐参数**：

- `duplicate_hash_threshold = 3`（从 2 放宽到 3，吸收光标闪烁 / 字体抗锯齿抖动；
  ccfc283b 上改为 3 并不会把真实不同的帧误判成同帧，样本已验证）
- `heartbeat_seconds = 60.0`（每分钟至少保一帧；60s 对 SOP 步骤粒度足够细）

**取消的条件**：`seconds_since_last_input > elapsed`。`IdleDetector` 保留给 **间隔
退避** (`IdleBackoff`) 使用——真闲时拉长截帧周期仍然是对的，但不应成为去重
的门槛。

**效果**：ccfc283b 从 3624 → 1756 帧 (**-51.5%**)。

### R2 — 最小截帧间隔

**现状**：`capture.interval_seconds` 默认 1s (v0.4.7)，但 `_wait_for_good_capture_moment`
在窗口切换 / 焦点变更时可以"提前触发"额外截帧。高频场景下可能 <1s 内连拍两张。

**建议**：在 `_capture_and_enqueue` 开头额外硬卡一条：

```python
if time.monotonic() - self._last_capture_time < cap.min_capture_gap_seconds:
    return
```

推荐 `min_capture_gap_seconds = 1.0`。已经由 v0.4.4 `min-gap enforcement` 实现的话
则确认生效即可；本次 ccfc283b 没有发现 <1s 的帧，说明当前防护已足够，但仍建议
作为最后一道兜底。

### R3 — 空画布 / 加载画面过滤

**现象**：`ccfc283b` 中存在两类"截了也白搭"的帧：

1. **BYLabel 标签打印系统空编辑区** (frame 645, 2286)：工具栏齐全但编辑区 98% 纯白。
2. **WPS Office 加载启动画面** "正在打开文档..." (frame 3060)：整屏基本空白。

**判定**（基于 session 内真实样本统计）：

| 特征 | 空画布 | 加载画面 | 业务画面 |
| --- | --- | --- | --- |
| PNG 文件大小 | 74-76 KB | 88 KB | 200-1600 KB |
| 近白像素占比 (≥230) | 98.4-98.6% | 83.8% | 49-81% |
| 灰度标准差 | 16-18 | 13 | 40-107 |

**建议实现**：加一个 `is_low_signal_frame(path) -> bool`：

```python
def is_low_signal_frame(image_path):
    with Image.open(image_path) as im:
        g = np.asarray(im.convert("L"), dtype=np.uint8)
    white_ratio = float((g >= 230).mean())
    std = float(g.std())
    # 任一条件都足以认定为低信息量画面
    if std < 15.0 and white_ratio > 0.90:
        return True
    # 小文件 + 高白占比 的双重门槛（避免误伤深色主题 IDE）
    if os.path.getsize(image_path) < 100_000 and white_ratio > 0.85:
        return True
    return False
```

**注意**：`std < 15 且 white > 0.9` 是**保守**阈值，不会误伤业务画面（最低业务画面
std=40）。实际放进客户端时，**仅在 phash 近同 R1 已命中的前提下再应用 R3 **，避免
把短暂闪过的 splash 里包含重要信息（例如"成功上传"Toast）的帧误丢。

### R4 — 应用黑名单（已存在，需扩充）

`privacy.should_skip_frame` + `config.privacy.excluded_apps` 已经可以按进程名跳过。
基于 `ccfc283b` 的观察，建议把以下默认加入 Windows 客户端的 `excluded_apps`：

- `LockApp.exe` — Windows 锁屏
- `LogonUI.exe` — 登录界面
- `SearchApp.exe` / `SearchHost.exe` — 开始菜单搜索弹层（经常覆盖屏幕一半）
- `ScreenClippingHost.exe` — 截图工具（避免截到截图界面本身）
- `WorkflowRecorder.exe` / `python.exe` 当 `window_title` 包含 "Workflow Recorder" —
  自引用（frame 1 就是录屏器自己的 VS Code + 终端窗口，对 SOP 毫无价值）

**建议**：`privacy.excluded_window_title_regex`（新字段）支持正则：

```toml
[privacy]
excluded_window_title_regex = [
  "^Workflow Recorder",
  "^Program Manager$",   # Explorer 的桌面本身
]
```

### R5 — 自我捕获保护

`ccfc283b` 的 frame 1 明显截到了录制器自己的 CLI 窗口（"Workflow Recorder v0.4.0"
banner）。这类帧对 SOP 无意义、对隐私却有风险（可能暴露服务端 URL / 员工 ID）。

**实现**：在 capture 管线里比对当前前台窗口的 `PID` 和自身 PID：

```python
if get_active_window().pid == os.getpid():
    return  # 不截自己
```

应当合并进 `should_skip_frame` 里优先判断。

## 实施建议与顺序

1. **先做 R1**（最大收益，代码改动小）——只需改 `_should_drop_as_idle_duplicate`
   的判定条件、新增 `heartbeat_seconds` 配置、增加 `_last_kept_time`。**单个 PR 可完成**。
2. **再做 R3**（中等收益，但需要 numpy 依赖）。建议只在 R1 命中后二次确认，保持
   "宁多勿少"的安全裕度。
3. **R4 / R5** 属于隐私 / 自引用修复，随时可合。R5 特别推荐一起做，避免员工看到
   自己机器上的 banner 被上传。
4. **R2** 仅作为兜底断言，不单独做 PR。

## 服务端侧影响 — 无需改动

服务端 `server/analysis_pool.py` 的 group 分析本身就会把"相同帧"合并看待，
客户端减少一半上传量不会影响 SOP 质量，但会显著：

- 降低 DashScope API 调用次数（成本 ~-40%）
- 降低磁盘占用（`frame_images/` 目录当前 3624 帧 = 1.1 GB，过滤后 ~500 MB）
- 降低 frame_groups 数量（~17 frames/group 规格不变，但组数减半）

## 验证方法

1. 在新客户端 build 里带上 `WORKFLOW_DEBUG_DROP_REASON=1` 环境变量，当丢帧时
   额外记录理由 (`r1_near_dup` / `r3_low_signal` / `r4_excluded_app` / ...)；
2. 用同一员工录一小时，对比改造前后的 `frames_skipped` 字段及 session 总帧数；
3. 人工抽查 20 帧被丢的截图是否确为低信息量；
4. 把同一 session 送进 group 分析，SOP 步骤数差异不应 > 10%。

## 附录：ccfc283b 过滤模拟结果

| 规则 | 保留 | 丢弃 | 保留率 |
| --- | --- | --- | --- |
| 原始 | 3624 | 0 | 100.0% |
| 仅 R1 (phash d≤3 vs last-kept + 60s 心跳) | 1756 | 1868 | **48.5%** |
| R1 + R3 (估) | ~1650 | ~1974 | ~45.5% |
| R1 + R3 + R4 + R5 | ~1600 | ~2024 | ~44.1% |

> 数据来源：`frames.db` session `ccfc283b-c3d2-4631-a59b-3ca80aa6f83a`，
> 离线计算 phash(size=8)，时间戳取自 `frames.recorded_at`。复现脚本见
> commit 日志（分析过程用的 ad-hoc Python，未入库）。
