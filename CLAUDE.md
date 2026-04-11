# Workflow Recorder

## 项目概述

Windows 后台常驻截屏分析 daemon，通过 GPT-4o 视觉模型理解员工桌面操作，输出可被 computer-use 自动化工具复现的结构化工作流文档。

## 架构

```
单模型模式:
[Capture Thread] --queue--> [Analysis Thread] --buffer--> [Aggregation] --> [Workflow JSON]
     │                            │
     ├─ mss 截屏                   ├─ GPT-4o / Qwen vision API
     ├─ win32gui 活动窗口           ├─ JSON 结构化输出
     └─ 隐私过滤                    └─ 重试/限流

双模型模式 (--dual):
                            ┌──queue──> [AnalysisWorker A] ──┐
[Capture Thread] ──fan-out──┤                                 ├──> [Per-model Workflow + Comparison.md]
                            └──queue──> [AnalysisWorker B] ──┘
                                         │
                                         └─ 每帧增量落盘 analyses_<label>.jsonl (崩溃可恢复)
```

- **双线程**: 截屏线程和分析线程通过有界队列解耦，队列满时丢帧
- **隐私优先**: 应用排除名单 + 区域遮罩，在 API 调用前执行
- **多模型扇出**: 双模型模式下单次截屏同时喂给所有 worker，每个 worker 独立队列 + 独立 VisionClient，互不阻塞

## 技术栈

- Python 3.9+（目标运行环境 Windows Python 3.11+）
- `mss` 截屏 / `pywin32` 窗口检测 / `psutil` 进程信息
- `openai` SDK (GPT-4o / Qwen3.5-Plus vision) / `pydantic` 数据校验
- `imagehash` 图像去重 / `structlog` 结构化日志
- PyInstaller + Inno Setup 打包 Windows 安装程序

## 项目结构

```
src/workflow_recorder/
├── __main__.py          # CLI 入口，启动 banner + 模型/端点信息显示，--dual/--recover 支持
├── daemon.py            # 单模型主循环，双线程编排
├── dual_daemon.py       # 双模型扇出 daemon + AnalysisWorker + recover_and_build
├── config.py            # Pydantic 配置模型，支持 ${ENV_VAR} / TOML / JSON 预设 / load_dual_model_configs
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
│   └── comparison.py    # 双模型工作流对比报告（markdown）
└── utils/
    ├── logging.py       # structlog 配置
    ├── retry.py         # 指数退避重试
    └── storage.py       # 临时文件管理
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
model_config.example.json    # 模型预设配置示例（GPT-4o / Qwen3.5-Plus）
```

## 常用命令

```bash
# 开发模式运行（单模型）
PYTHONPATH=src python3 -m workflow_recorder -c config.example.yaml

# 双模型并行录制（所有 model_presets 同时跑）
PYTHONPATH=src python3 -m workflow_recorder -c dual_model_config.json --dual

# 从 JSONL 增量存档恢复（daemon 崩溃后重建工作流）
PYTHONPATH=src python3 -m workflow_recorder -c dual_model_config.json --recover

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

### 多模型支持

通过 `model_config.json` 配置模型预设，支持 GPT-4o 和 Qwen3.5-Plus 等 OpenAI 兼容视觉模型：
- `active_preset`: 当前激活的预设名称
- 每个预设包含 `model`, `base_url`, `api_key`, `detail` 等字段
- 安装向导中可选择模型并输入 API Key，自动生成配置文件

### API 代理

`analysis.base_url` 字段支持自定义 API 代理端点，适配 GPT 代理服务或国内模型 API。

**aicodemirror 现状**（2026-04-11 烟测确认）：`base_url = https://api.aicodemirror.com/api/codex/backend-api/codex/v1` + OpenAI SDK chat-completions wire 的调用链路代码侧完全可用；但该代理当前套餐已不再把 `gpt-4o` 映射到有效计费计划，请求会被 `SETTLEMENT_UNKNOWN_MODEL` (HTTP 400) 拒掉。如需在此代理上继续跑，只能切换到它仍支持的模型（当前仓库内默认 `gpt-5.4`）。代理支持列表见 `https://www.aicodemirror.com/dashboard/pricing`。

### 双模型并行录制

通过 `--dual` 标志同时用多个模型分析同一批截屏，用于对比不同模型对同一工作流的理解差异。

- **配置**: JSON 文件里声明 `model_presets`（至少 2 个），每个 preset 有独立的 `model`/`base_url`/`openai_api_key`。`--dual` 模式会用 `load_dual_model_configs()` 读出全部 preset。
- **扇出**: 单条 capture thread，N 个 `AnalysisWorker`，每个 worker 有独立 `queue.Queue` + 独立 `VisionClient`。任一模型慢不会拖慢其它模型（队列满只丢自己那路的帧）。
- **增量落盘**: 每完成一帧分析就把 worker 的 `frame_analyses` 序列化到 `<output_dir>/analyses_<label>.jsonl`，一行一个 `FrameAnalysis`。daemon 被 kill -9 也不丢数据。
- **崩溃恢复**: `--recover` 不重新录制，只扫描 `analyses_*.jsonl` 用 `recover_and_build()` 重建每个模型的 workflow JSON + 对比报告。
- **输出布局**: `<output_dir>/<label>/workflow_*.json` + `<output_dir>/<label>/workflow_*.md` + `<output_dir>/comparison_<session_id[:8]>.md`。
- **对比报告**: `output/comparison.py` 按时间戳（±1 分钟窗）贪心对齐两条 workflow 的 steps，计算 Jaccard 描述相似度分类为 `yes`/`partial`/`no`，汇总 step-by-step 表 + 置信度统计 + action 差异列表。

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
