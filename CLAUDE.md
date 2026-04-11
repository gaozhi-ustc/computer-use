# Workflow Recorder

## 项目概述

Windows 后台常驻截屏分析 daemon，通过 **Qwen3.5-Plus 视觉模型**（阿里百炼 DashScope）理解员工桌面操作，输出可被 computer-use 自动化工具复现的结构化工作流文档，并将每帧识别结果**实时推送到中心服务器**（FastAPI + SQLite）按员工 ID 归档。

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
FastAPI (POST /frames, POST /frames/batch, GET /frames, GET /health)
   │
   ▼
SQLite (frames.db)  —  UNIQUE(employee_id, session_id, frame_index)  幂等去重
```

- **双线程**: 截屏线程和分析线程通过有界队列解耦，队列满时丢帧
- **隐私优先**: 应用排除名单 + 区域遮罩，在 API 调用前执行
- **推送解耦**: `FramePusher` 是独立后台线程 + 自己的队列；网络卡 / 服务端宕都不会拖慢分析管线，失败帧落盘到 JSONL，下次启动时自动重推
- **首次运行向导**: `init_wizard.py` 启动时检测 `employee_id` / `openai_api_key` 缺失则交互式 prompt 并写回配置文件

## 技术栈

- Python 3.9+（目标运行环境 Windows Python 3.11+）
- `mss` 截屏 / `pywin32` 窗口检测 / `psutil` 进程信息
- `openai` SDK（调用 qwen3.5-plus via DashScope OpenAI 兼容端点）/ `pydantic` 数据校验
- `httpx` 客户端推送 / `structlog` 结构化日志 / `imagehash` 图像去重
- 服务端: `fastapi` + `uvicorn` + stdlib `sqlite3`（optional extra `[server]`）
- PyInstaller + Inno Setup 打包 Windows 安装程序

## 项目结构

```
src/workflow_recorder/
├── __main__.py          # CLI 入口，banner + 首次运行向导调度
├── daemon.py            # 单模型主循环，截图 + 分析 + 推送三线程编排
├── config.py            # Pydantic 配置，支持 ${ENV_VAR} / TOML / JSON 预设 / ServerConfig
├── init_wizard.py       # 首次运行交互向导（employee_id + DashScope API key）
├── frame_pusher.py      # 后台推送线程 + 失败帧 JSONL 缓冲 + 启动重推
├── dual_daemon.py       # [legacy] 双模型扇出 daemon（--dual 隐藏标志保留）
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
│   ├── reference_store.py   # 参考截图管理
│   └── comparison.py    # [legacy] 双模型工作流对比报告
└── utils/
    ├── logging.py       # structlog 配置
    ├── retry.py         # 指数退避重试
    └── storage.py       # 临时文件管理
server/
├── __init__.py
├── app.py                   # FastAPI 应用，路由 + X-API-Key 鉴权
└── db.py                    # sqlite3 schema + insert/query/count
installer/
├── build.py                 # 构建自动化脚本
├── workflow_recorder.spec   # PyInstaller 打包配置
├── workflow_recorder.iss    # Inno Setup 安装向导（含模型选择页面）
└── verify_build.py          # 构建产物校验
tests/
├── conftest.py              # 测试 fixture + pytest markers 定义
├── test_*.py                # 93 个单元测试
├── integration/             # 5 个集成测试
└── e2e/                     # 2 个端到端测试
model_config.example.json    # 模型预设配置示例（Qwen3.5-Plus 默认 active）
```

## 常用命令

```bash
# 客户端：开发模式运行（首次会走向导提示员工 ID + API key）
PYTHONPATH=src python3 -m workflow_recorder -c model_config.json

# 服务端：启动 FastAPI 收集服务（默认 127.0.0.1:8000，frames.db 在当前目录）
pip install -e ".[server]"
WORKFLOW_SERVER_KEY=changeme uvicorn server.app:app --host 0.0.0.0 --port 8000

# 服务端查询（示例）
curl -H "X-API-Key: changeme" "http://127.0.0.1:8000/frames?employee_id=E001&limit=20"

# [legacy] 双模型并行录制（隐藏标志，向后兼容）
PYTHONPATH=src python3 -m workflow_recorder -c dual_model_config.json --dual

# 运行测试
PYTHONPATH=src python3 -m pytest tests/ -v

# 运行集成/端到端测试
PYTHONPATH=src python3 -m pytest tests/ -v --run-integration --run-e2e

# 构建 Windows 安装程序
python installer/build.py --installer

# Windows 服务安装
python setup_service.py install
net start WorkflowRecorder
```

## 输出格式

工作流文档为 JSON，核心结构：
- `metadata`: session_id, recorded_at, duration, total_steps
- `environment`: screen_resolution, os, hostname
- `steps[]`: 每步包含 application, description, actions (click/type/key/scroll/wait), verification, confidence
- `variables`: 支持 `{placeholder}` 运行时填充（如凭据）

## 配置

参见 `config.example.yaml`，支持环境变量插值 `${OPENAI_API_KEY}`。

关键配置项：
- `capture.interval_seconds`: 截屏间隔（默认 3 秒）
- `privacy.excluded_apps`: 排除的进程名列表
- `analysis.detail`: "low"（~$0.002/帧）或 "high"（~$0.01/帧）
- `session.similarity_threshold`: 图像去重阈值（默认 0.95）

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

**服务端侧**（`server/` 包，`pip install -e ".[server]"`）:

- `server/db.py` — 单文件 SQLite，`frames` 表带 `UNIQUE(employee_id, session_id, frame_index)`，`INSERT OR IGNORE` 实现幂等（重试不会重复）；`mouse_position` 和 `ui_elements` 以 JSON 字符串列存
- `server/app.py` — FastAPI 应用，路由：
  - `POST /frames` 单条 ingest，返回 `{ok, id, duplicate}`
  - `POST /frames/batch` 批量 ingest，返回 `{inserted, duplicates, total}`
  - `GET /frames?employee_id=X&session_id=Y&limit=N&offset=M` 分页查询
  - `GET /health` 健康检查（不需要鉴权）
- **鉴权**: env `WORKFLOW_SERVER_KEY` 存在时强制 `X-API-Key` header 匹配；env 不设则开放（仅建议本地开发）。客户端的 key 从 `ServerConfig.api_key` 读
- **数据库路径**: env `WORKFLOW_SERVER_DB` 覆盖默认 `./frames.db`

### [legacy] 双模型模式 / aicodemirror

`dual_daemon.py`、`output/comparison.py`、`--dual` / `--recover` CLI 标志仍在仓库里，但已从 `--help` 输出中隐藏。不建议新部署使用，留着是防止回滚老会话用。`aicodemirror` 代理在 2026-04-11 烟测里已确认不再路由 `gpt-4o`（返回 `SETTLEMENT_UNKNOWN_MODEL` HTTP 400），所以当前主流程也不再依赖它。

## 测试

测试套件分层：
- **单元测试** (93 个): 各模块独立逻辑，mock 外部依赖
- **集成测试** (5 个): 配置加载 + 分析管线端到端
- **端到端测试** (2 个): 完整录制流程验证

使用 pytest markers 区分：`@pytest.mark.integration` / `@pytest.mark.e2e`，通过 `--run-integration` / `--run-e2e` 启用。

## Windows 安装程序

基于 PyInstaller + Inno Setup 构建，特性：
- 安装向导含模型选择页面（GPT-4o / Qwen3.5-Plus）
- 自动生成 `model_config.json`
- 可选 PATH 集成和 Windows 服务安装
- 配置文件在升级时保留，卸载时不删除
- 构建：`python installer/build.py --installer`
