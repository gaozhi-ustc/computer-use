# Workflow Recorder

## 项目概述

Windows 后台常驻截屏分析 daemon，通过 **Qwen3.5-Plus 视觉模型**（阿里百炼 DashScope）理解员工桌面操作，输出可被 computer-use 自动化工具复现的结构化工作流文档，并将每帧识别结果**实时推送到中心服务器**（FastAPI + SQLite）按员工 ID 归档。

配套 **Web Dashboard**（Vue 3 + Naive UI）提供：SOP 自动提炼与编辑、效率分析、合规审计、用户管理。

## 架构

```
客户端（每台员工机器一份）:
[Capture] → [Analysis (qwen3.5-plus)] ─┬─→ 本地 Workflow JSON（aggregation）
                                         │
                                         └─→ [FramePusher thread] ─httpx POST─▶ 服务端
                                                    │
                                                失败回落
                                                    ▼
                                               logs/push_buffer.jsonl
                                               (下次启动时重推)

服务端（单点部署）:
FastAPI
├── POST /frames, /frames/batch          ← 客户端推送（X-API-Key 鉴权）
├── /api/auth/*                          ← JWT 登录 / 刷新 / 当前用户
├── /api/users/*                         ← 用户管理（admin only）
├── /api/sessions/*                      ← 录制 session 列表 / 详情
├── /api/sops/*                          ← SOP CRUD / 状态流转 / 导出
├── /api/dashboard/*, /api/frames/stats  ← 概览 / 效率统计 / 审计搜索
└── / (StaticFiles)                      ← Vue 3 SPA（production 模式）
   │
   ▼
SQLite (frames.db)
├── frames   — UNIQUE(employee_id, session_id, frame_index)  幂等去重
├── users    — 用户账号 + 角色 + 钉钉关联
├── sops     — SOP 元信息（草稿/审核/已发布）
└── sop_steps — SOP 步骤（可排序、可编辑）
```

- **三线程客户端**: 截屏 / 分析 / 推送通过有界队列解耦，互不阻塞
- **隐私优先**: 应用排除名单 + 区域遮罩，在 API 调用前执行
- **推送解耦**: `FramePusher` 独立线程 + JSONL buffer 兜底，断网不丢数据
- **首次运行向导**: `init_wizard.py` 检测 `employee_id` / `openai_api_key` 缺失则交互式 prompt
- **角色权限**: admin（全量）/ manager（部门）/ employee（仅自己），服务端强制过滤

## 技术栈

**客户端（录制 daemon）:**
- Python 3.9+（目标运行环境 Windows Python 3.11+）
- `mss` 截屏 / `pywin32` 窗口检测 / `psutil` 进程信息
- `openai` SDK（调用 qwen3.5-plus via DashScope OpenAI 兼容端点）/ `pydantic` 数据校验
- `httpx` 客户端推送 / `structlog` 结构化日志 / `imagehash` 图像去重
- PyInstaller + Inno Setup 打包 Windows 安装程序

**服务端 + Dashboard:**
- `fastapi` + `uvicorn` + stdlib `sqlite3`（optional extra `[server]`）
- `PyJWT` + `bcrypt` 认证
- Vue 3 + TypeScript + Naive UI + Pinia + Vue Router + Axios

## 项目结构

```
src/workflow_recorder/
├── __main__.py          # CLI 入口，banner + 首次运行向导调度
├── daemon.py            # 单模型主循环，截图 + 分析 + 推送三线程编排
├── config.py            # Pydantic 配置，支持 ${ENV_VAR} / TOML / JSON 预设 / ServerConfig
├── init_wizard.py       # 首次运行交互向导（employee_id + DashScope API key）
├── frame_pusher.py      # 后台推送线程 + 失败帧 JSONL 缓冲 + 启动重推
├── capture/
│   ├── screenshot.py    # mss 截屏
│   ├── window_info.py   # 活动窗口检测 (Win32/macOS)
│   └── privacy.py       # 隐私过滤
├── analysis/
│   ├── vision_client.py # OpenAI 兼容 vision API（支持 base_url 代理）
│   ├── prompts.py       # 提示词模板
│   └── frame_analysis.py# 单帧分析数据模型
├── aggregation/
│   ├── workflow_builder.py  # 帧分析聚合为工作流步骤
│   ├── deduplication.py     # 感知哈希去重
│   └── action_mapper.py     # 映射到 click/type/key/scroll 动作
├── output/
│   ├── schema.py        # Workflow JSON Pydantic 模型
│   ├── writer.py        # JSON/YAML/Markdown 输出
│   └── reference_store.py   # 参考截图管理
└── utils/
    ├── logging.py       # structlog 配置
    ├── retry.py         # 指数退避重试
    └── storage.py       # 临时文件管理
server/
├── __init__.py
├── app.py               # FastAPI 应用，挂载所有 router + 静态文件
├── db.py                # sqlite3 schema（frames/users/sops/sop_steps）+ 全部 CRUD
├── auth.py              # JWT 创建/验证 + bcrypt 密码哈希
├── auth_router.py       # /api/auth/* — login, refresh, me + get_current_user 依赖
├── models.py            # Pydantic schemas（auth/user/sop/step request/response）
├── permissions.py       # 角色权限过滤（admin 全量 / manager 部门 / employee 仅自己）
├── users_router.py      # /api/users/* — 用户 CRUD（admin only）
├── sessions_router.py   # /api/sessions/* — 录制 session 列表/详情
├── sops_router.py       # /api/sops/* — SOP CRUD + 状态流转 + 自动提炼 + 导出
└── stats_router.py      # /api/dashboard/*, /api/frames/stats,search,export
dashboard/
├── package.json         # Vue 3 + Naive UI + Pinia + Vue Router + Axios
├── vite.config.ts       # dev proxy /api → FastAPI :8000
├── src/
│   ├── main.ts          # 入口：Pinia + Router
│   ├── App.vue          # 根布局（NConfigProvider 中文 + Sidebar/Header）
│   ├── router/index.ts  # 9 条路由 + role-based beforeEach guard
│   ├── stores/auth.ts   # Pinia auth store（login/logout/fetchUser/role）
│   ├── api/
│   │   ├── client.ts    # Axios 实例 + JWT 拦截器
│   │   ├── auth.ts      # 登录/刷新/me API
│   │   ├── sessions.ts  # session 列表/详情 API
│   │   ├── sops.ts      # SOP CRUD + 步骤 + 导出 API
│   │   ├── stats.ts     # 统计/搜索/导出 API
│   │   └── users.ts     # 用户管理 API
│   ├── views/
│   │   ├── Login.vue         # 密码登录 + 钉钉扫码 placeholder
│   │   ├── Dashboard.vue     # 概览：KPI 卡片 + 应用分布 + 最近 session
│   │   ├── Recording.vue     # 录制回放：session 列表 + 帧时间线
│   │   ├── SopList.vue       # SOP 管理：状态 tab + 数据表 + 新建
│   │   ├── SopEditor.vue     # SOP 编辑器：拖拽步骤 + inline 编辑 + 导出
│   │   ├── Analytics.vue     # 效率分析：应用分布 + 热力图 + 每日统计
│   │   ├── Audit.vue         # 审计查询：关键词搜索 + CSV 导出
│   │   ├── UserManagement.vue # 用户管理：用户表 + 角色编辑
│   │   └── Settings.vue      # 系统设置：服务器健康信息
│   └── components/layout/
│       ├── Sidebar.vue       # 左侧导航（按角色动态菜单）
│       └── Header.vue        # 顶栏（用户名 + 角色标签 + 退出）
└── dist/                     # 构建输出，FastAPI serve 此目录
installer/
├── build.py                  # 构建自动化脚本
├── workflow_recorder.spec    # PyInstaller 打包配置
├── workflow_recorder.iss     # Inno Setup 安装向导（v0.2.0: 单模型 qwen + 员工 ID）
└── verify_build.py           # 构建产物校验
tests/
├── conftest.py               # 测试 fixture + pytest markers 定义
├── test_*.py                 # 196 个单元测试（含 auth/users/sessions/sops/stats/permissions）
├── integration/              # 5 个集成测试
└── e2e/                      # 2 个端到端测试
docs/superpowers/
├── specs/                    # 设计文档（brainstorming 产出）
└── plans/                    # 实施计划（writing-plans 产出）
```

## 常用命令

```bash
# ── 客户端 ──
# 开发模式运行（首次会走向导提示员工 ID + API key）
PYTHONPATH=src python3 -m workflow_recorder -c model_config.json

# 构建 Windows 安装程序
python installer/build.py --installer

# ── 服务端 ──
# 安装服务端依赖
pip install -e ".[server]"

# 启动 FastAPI 服务（开发）
WORKFLOW_SERVER_DB=./frames.db uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload

# 启动（生产，含 dashboard 静态文件）
cd dashboard && npm run build && cd ..
uvicorn server.app:app --host 0.0.0.0 --port 8000

# ── Dashboard 前端 ──
# 开发模式（Vite dev server + proxy /api → :8000）
cd dashboard && npm install && npm run dev   # → http://localhost:5173

# 构建生产版
cd dashboard && npm run build                # → dashboard/dist/

# ── 测试 ──
PYTHONPATH=src python3 -m pytest tests/ -v
PYTHONPATH=src python3 -m pytest tests/ -v --run-integration --run-e2e

# ── Windows 服务 ──
python setup_service.py install
net start WorkflowRecorder
```

## Dashboard

### 角色与权限

| 角色 | 数据范围 | 能力 |
|------|---------|------|
| **admin** | 全部员工 | 用户管理、系统设置、全部 SOP 生命周期、审计 |
| **manager** | 本部门员工 | 查看录制、SOP 审核/编辑/发布、效率报表 |
| **employee** | 仅自己 | 查看自己录制、查看已发布 SOP、提交 SOP 草稿 |

认证方式：JWT（access 30min + refresh 7天），密码登录 + 钉钉 SSO（Phase 6 待实现）。首次启动自动 seed `admin`/`admin` 账号。

### SOP 生命周期

```
[录制帧] → [自动提炼草稿] → [人工 Review/编辑] → [提交审核] → [发布] → [导出 MD/JSON]
                                    ↑                    │
                                    └── 打回修改 ─────────┘
```

状态流转：`draft` → `in_review` → `published`（可打回到 `draft`）

从"录制回放"页选一个 session → "从此会话生成 SOP" → 后端自动按应用分组提炼步骤 → 进入编辑器人工修正 → 提交审核 → 发布 → 导出给人看（Markdown）或给机器跑（Workflow JSON）

### 页面导航

| 页面 | employee | manager | admin |
|------|----------|---------|-------|
| 概览 Dashboard | ✅ | ✅ | ✅ |
| 录制回放 | ✅ 仅自己 | ✅ 部门 | ✅ 全部 |
| SOP 管理 | ✅ 只读已发布 | ✅ 编辑/发布 | ✅ 全部 |
| 效率分析 | ✅ 仅自己 | ✅ 部门 | ✅ 全部 |
| 审计查询 | ❌ | ✅ 部门 | ✅ 全部 |
| 用户管理 | ❌ | ❌ | ✅ |
| 系统设置 | ❌ | ❌ | ✅ |

## 输出格式

工作流文档为 JSON，核心结构：
- `metadata`: session_id, recorded_at, duration, total_steps
- `environment`: screen_resolution, os, hostname
- `steps[]`: 每步包含 application, description, actions (click/type/key/scroll/wait), verification, confidence
- `variables`: 支持 `{placeholder}` 运行时填充（如凭据）

## 配置

参见 `config.example.yaml`，支持环境变量插值 `${OPENAI_API_KEY}`。

关键配置项：
- `capture.interval_seconds`: 截屏间隔（**生产环境建议 ≥ 15 秒**，见下面 RTT 章节）
- `privacy.excluded_apps`: 排除的进程名列表
- `analysis.detail`: "low"（~$0.002/帧）或 "high"（~$0.01/帧）
- `session.similarity_threshold`: 图像去重阈值（默认 0.95）

### 性能与 interval 建议

**实测 qwen3.5-plus 视觉调用 RTT ≈ 12 秒**（2026-04-11 烟测，单帧 low detail，DashScope OpenAI 兼容端点，`downscale_factor = 0.5`）。含义：

- **`capture.interval_seconds` 不应小于 15** —— 否则 capture 线程会比 analysis 快很多，分析队列永远堆积，新帧不停从 capture 端丢弃（或推迟分析到几分钟之后），既浪费截屏又错过实时信号。
- **Capture 速度与 Analysis 速度需要粗略匹配**。如果你想要更高帧率，只能降低单帧 analysis 耗时（缩小图片 / 降低 max_tokens / 裁剪到活动窗口），而不是拉短 interval。
- **队列不会阻塞 capture**：`capture.max_queue_size` 保证 capture 不被 analysis 卡死。队列满时最新帧直接丢弃而不是阻塞，所以 capture interval 可以短，但后果是大量 drop。`frame_dropped` 警告日志就是这种情况。
- **推送管线的 queue 和 capture queue 是独立的**。即使 analysis 慢到推送跟不上，`frame_pusher` 也有自己的 JSONL buffer 兜底。

仓库里的 `config.record.toml` / `dual_model_config.json` 已同步调到 `interval_seconds = 15`。集成测试 `config.test.toml` 保留 2 秒作为极端情况用例。

### 主流程（qwen3.5-plus）

默认且**唯一**推荐的模型是阿里百炼 DashScope 托管的 `qwen3.5-plus`：

```
base_url = https://coding.dashscope.aliyuncs.com/v1
model    = qwen3.5-plus
```

通过 `model_config.json` 的 `model_presets` + `active_preset` 指定，或直接写入顶层 `[analysis]`。API key 由首次运行向导填入。

### 首次运行向导 (`init_wizard.py`)

- 启动时 `load_config()` 之后检查：`employee_id` 是否空 OR `analysis.openai_api_key` 是否空
- 缺则进入交互式 prompt（`input()` + `getpass` 隐藏 API key 输入），值直接写回配置 JSON 并更新 in-memory config，下次启动跳过
- 非 TTY 环境（被管道/服务调用）会报错退出而不是卡住 `input()`
- 写回策略：若 JSON 有 `model_presets` + 具体 `active_preset`，写到该 preset 的 `openai_api_key`；否则写到顶层 `analysis.openai_api_key`；`active_preset == "__all__"` 时给所有空 preset 补同一个 key

### 服务端推送 (`frame_pusher.py` + `server/`)

**客户端侧**: `FramePusher` 是 `daemon.py` 持有的后台线程，独立 `queue.Queue(maxsize=server.queue_size)`。`_analysis_loop` 每得到一个成功的 `FrameAnalysis` 就 `pusher.enqueue(analysis)`（非阻塞，队列满直接落盘）。pusher 线程自己用 `httpx.Client` 发 `POST {server.url}/frames`：

- Payload = `FrameAnalysis.model_dump()` + `employee_id` + `session_id`
- 失败重试：指数退避（1s/2s/4s...），`max_retries` 次后把 payload 追加到 `server.buffer_path`（默认 `./logs/push_buffer.jsonl`）
- 启动恢复：每次 pusher 线程启动先 `_replay_buffer()` 把上次遗留的 JSONL 行逐行重推，成功的删除、失败的保留
- 4xx 非 retry（408/429 除外）直接放弃，避免死循环
- `daemon._finalize()` 里先 `pusher.stop()` 再聚合 workflow，确保 Ctrl+C 也能把 queue 里残留帧发完再退出

### 历史备忘

- 2026-04-11 烟测确认 aicodemirror 代理已不再路由 `gpt-4o`（返回 `SETTLEMENT_UNKNOWN_MODEL` HTTP 400），所以主流程完全迁移到 DashScope + qwen3.5-plus。
- 旧版双模型并行录制代码（`dual_daemon.py` / `output/comparison.py` / `--dual` / `--recover` / `load_dual_model_configs`）在单模型化完成后已整体删除；如需回看，检出 commit `86219a6` 之前的历史即可。

## 测试

测试套件分层：
- **单元测试** (196 个): 各模块独立逻辑，mock 外部依赖（含 auth/users/sessions/sops/stats/permissions/frame_pusher/init_wizard）
- **集成测试** (5 个): 配置加载 + 分析管线端到端
- **端到端测试** (2 个): 完整录制流程验证

使用 pytest markers 区分：`@pytest.mark.integration` / `@pytest.mark.e2e`，通过 `--run-integration` / `--run-e2e` 启用。

`frame_pusher` 测试用 `FakeClient` 替换 `httpx.Client`（monkeypatch），避免真的走网络；`server/db.py` 测试通过 `WORKFLOW_SERVER_DB` env var 把 DB 指到 `tmp_path`，每个测试都是隔离的空库；API 路由测试用 FastAPI `TestClient` + 临时 DB。

## Windows 安装程序

基于 PyInstaller + Inno Setup 构建，特性：
- 安装向导收集员工 ID + DashScope API key，写入 `model_config.json`
- 可选 PATH 集成和 Windows 服务安装
- 配置文件在升级时保留，卸载时不删除
- 构建：`python installer/build.py --installer`
- 当前版本：v0.2.0（单模型 qwen3.5-plus + 服务端推送）

## 待完成

- [ ] Phase 6: 钉钉 SSO 登录（需要在钉钉开放平台创建企业内部应用）
- [ ] 截图文件存储与回显（当前 frames 表只存元数据，截图文件留在客户端本地）
- [ ] Dashboard 移动端适配
- [ ] SOP 版本历史 / diff 视图
- [ ] WebSocket 实时推送（当前轮询）
