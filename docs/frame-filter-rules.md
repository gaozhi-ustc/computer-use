# 服务端预分析过滤规则 (v0.5.2)

## 目标

在 session 结束后、group 分析开始前，服务端**标记**低价值帧为 `skip`，
让后续的 group 分析跳过这些帧，降低 vision API 调用成本和上传后的处理延迟。
**图片文件不删除**——只更新 DB 的 `frames.skip_reason` 字段，方便后续调整阈值
时重新分类而无需重新上传。

## 为什么不在客户端过滤

早期方案在 Windows 客户端过滤，但有三个问题：

1. **调参难**：客户端阈值写死后，要回溯看"如果当时保留了这些帧 SOP 会更好吗？"
   做不到，因为那些帧根本没上传。服务端打标签的话重跑过滤器即可。
2. **Windows 客户端 build 成本**：阈值小改也要重打包 + 推到所有员工机器。
3. **客户端 `_should_drop_as_idle_duplicate` 依赖 `GetLastInputInfo`**，
   但用户手放鼠标上的微动会被 OS 当作"有输入"，
   这个信号**不可靠**；服务端纯基于图像做判断更稳。

## 数据存储

`frames` 表新增列（v0.5.2 迁移，在 `server/db.py:_migrate_add_columns` 幂等执行）：

```sql
skip_reason TEXT DEFAULT ''  -- '' = 保留；'near_duplicate' | 'low_signal' = 已过滤
```

附索引 `idx_frames_session_skip (session_id, skip_reason)`，group 分析时快速
过滤。

**Group 分析侧需要的改动**（尚未实现，下一步 PR）：

- `server/analysis_pool.py::_analyze_group` 加载 group 内 frame 时增加
  `WHERE skip_reason = ''` 条件。
- 如果一个 group 的 17 帧里过滤掉超过一半，考虑降级为不请求 vision
  API，而是直接从上下文 group 的推断继承——但这是优化，不是本期目标。

## 过滤规则

### R1 — 近重复帧 (`near_duplicate`)

**判定**：对按 `frame_index` 有序遍历的每一帧，计算 `imagehash.phash(size=8)`，
与**上一保留帧**的 phash 比较：

```
skip if phash_hamming_distance <= 3
    AND time_gap_to_last_kept < 60 seconds
```

- 3 的阈值：0 = 完全相同；2 = 光标闪烁、字体抗锯齿级别的扰动；
  3 = 再松一格，吸收视频缩略图的轻微变化（电梯内宣传片、右下角时间跳秒）。
  实测 ccfc283b 上用 3 不会把真实业务变化（弹框出现/消失、行选中）误判。
- 60s 心跳：即使 phash 完全相同也至少每分钟保一张，用于审计"人在不在岗"。

**注意**：比较目标是"上一**保留**的帧"，不是"上一帧"。这样即使中间有一串
低信号帧被 R3 过滤掉，新的真正变化帧还能正确地跟真实上一个内容帧对比。

### R3 — 低信息量画面 (`low_signal`)

**判定**：读取灰度图 + 文件大小：

```python
white_ratio = (gray >= 230).mean()   # 近白像素占比
std = gray.std()                     # 灰度标准差
size = os.path.getsize(path)
low_signal = (
    (std < 15 and white_ratio > 0.90)
    or (size < 100_000 and white_ratio > 0.85)
)
```

对应两类：
- **空白编辑区**：BYLabel 标签打印、Photoshop 新建空白画布、空 Word 等。
  std 极低、几乎纯白、但文件仍大（工具栏有颜色）。
- **加载 splash / 对话框**："正在打开文档..." 类过渡画面，文件特别小
  因为大面积重复像素 PNG 压缩好。

**阈值保守度**：在 ccfc283b 上只命中了 4 帧（全是 BYLabel 空画布）。业务画面
最低 std = 40，离 15 的阈值有足够裕度，没有误伤。

### R1 / R3 的优先级

对每一帧**先 R3 后 R1**：

1. 若 `low_signal`：标 `low_signal`，**不**推进 "last-kept" 指针。
   （这样后续真正的内容帧会跟真实的上个内容帧对比，不被空画布带偏。）
2. 否则检查 R1：若近重复，标 `near_duplicate`，不推进指针。
3. 否则：保留，`skip_reason = ''`，推进 `last_kept_hash` 和 `last_kept_time`。

## ccfc283b 实际过滤结果

脚本对 session `ccfc283b-c3d2-4631-a59b-3ca80aa6f83a`（3624 帧，2h50m，员工 10446）
执行一次，结果：

| 类别 | 帧数 | 占比 |
| --- | --- | --- |
| 保留 (`skip_reason = ''`) | **1749** | 48.3% |
| `near_duplicate` | **1871** | 51.6% |
| `low_signal` | **4** | 0.1% |
| 图片缺失（无法判定，保留） | 6 | 0.2% |

`low_signal` 4 帧 (frame_index 397/645/2270/2286) 经目视核查全部是 BYLabel 标签
打印系统的空白画布——属于真正的噪声帧。

`near_duplicate` 典型模式：
- frames 3-10（8 帧）：cursor 停在 (1580, 245) 不动 → 3 保留，4-10 被过滤；
- frames 646-791（146 帧）：WPS 某个表格视图长期不动 → 首帧保留、其余过滤；
- 每分钟会因 60s 心跳保一张，保证审计覆盖。

## 复现

```bash
# 一次性跑过滤（未来会集成进 SessionFinalizer，当前作为 ad-hoc 脚本）
python3 -c "
import os, sqlite3, imagehash, numpy as np
from PIL import Image
from datetime import datetime

DB = './frames.db'
SESSION = 'ccfc283b-c3d2-4631-a59b-3ca80aa6f83a'
PHASH_T, HEARTBEAT = 3, 60.0
LS_STD, LS_WHITE = 15.0, 0.90
LS_SIZE_BYTES, LS_SIZE_WHITE = 100_000, 0.85

def parse_iso(s): return datetime.fromisoformat(s.replace('Z','+00:00'))

con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
con.execute('UPDATE frames SET skip_reason = \"\" WHERE session_id = ?', (SESSION,))
rows = con.execute('SELECT id, image_path, recorded_at FROM frames WHERE session_id = ? ORDER BY frame_index', (SESSION,)).fetchall()

last_h, last_t, updates = None, None, []
for r in rows:
    p = r['image_path']
    if not os.path.exists(p): continue
    with Image.open(p) as im:
        g = np.asarray(im.convert('L'))
        h = imagehash.phash(im)
    white, std, size = float((g>=230).mean()), float(g.std()), os.path.getsize(p)
    if (std<LS_STD and white>LS_WHITE) or (size<LS_SIZE_BYTES and white>LS_SIZE_WHITE):
        updates.append(('low_signal', r['id'])); continue
    t = parse_iso(r['recorded_at'])
    if last_h is not None and (h-last_h)<=PHASH_T and (t-last_t).total_seconds()<HEARTBEAT:
        updates.append(('near_duplicate', r['id'])); continue
    last_h, last_t = h, t
with con:
    con.executemany('UPDATE frames SET skip_reason = ? WHERE id = ?', updates)
print(f'marked {len(updates)} frames')
"
```

## 调参注意事项

- 想更激进（节省更多）→ PHASH_T 调到 4；风险：业务 toast 弹框瞬间可能被并入前一帧。
- 想更保守 → PHASH_T 调到 2 且 HEARTBEAT 改 30s；上传量会上升但可审计度更高。
- 发现 low_signal 把正常的空 Word 新文档误判了 → 把 LS_WHITE 收紧到 0.95
  或者要求 std < 10。
- 重跑前必须先 `UPDATE frames SET skip_reason = '' WHERE session_id = ?`
  清空现有标记，否则 last-kept 指针逻辑会带偏。脚本里已经包含这一步。

## 未来工作

1. 把本过滤器集成进 `server/session_finalizer.py::_finalize_session`，
   在 `group_frames` 之前调用。
2. 让 `server/analysis_pool.py` 在加载 group 内 frame 时 `WHERE skip_reason = ''`
   过滤掉被跳过的帧。
3. Dashboard Recording 页面显示每帧的 `skip_reason`，过滤帧灰度渲染。
4. 给 admin 一个"重新过滤该 session"的按钮，触发 re-run。
