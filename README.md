# Workflow Recorder

**Windows 桌面操作录制工具** — 后台截屏 + GPT-4o 视觉分析，自动生成可复现的结构化 SOP 工作流文档。

录制员工桌面操作，通过 AI 理解每一步在做什么，输出可被 computer-use 自动化工具直接复现的标准化流程文档。

---

## 功能特性

- **智能截屏录制** — 定时截屏 + 活动窗口检测，自动感知哈希去重
- **GPT-4o 视觉分析** — 每帧截图发送至 GPT-4o，识别用户操作（点击、输入、快捷键、滚动等）
- **结构化 SOP 输出** — 生成 JSON / YAML / Markdown 工作流文档，包含每步操作、坐标、置信度
- **隐私保护** — 排除密码管理器、隐私浏览窗口；支持屏幕区域遮罩
- **Windows 服务** — 可安装为系统服务，开机自动启动
- **开箱即用** — 提供 Windows 一键安装包，无需安装 Python

---

## 快速开始

### 方式一：安装包（推荐）

1. 从 [Releases](https://github.com/gaozhi-ustc/computer-use/releases) 下载 `WorkflowRecorder-x.x.x-Setup.exe`
2. 运行安装向导
3. 编辑安装目录下的 `config.yaml`，填入你的 OpenAI API Key
4. 从开始菜单启动 **Workflow Recorder**

### 方式二：源码运行

```bash
# 克隆项目
git clone https://github.com/gaozhi-ustc/computer-use.git
cd computer-use

# 安装依赖
pip install -e ".[dev]"

# 配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入 openai_api_key

# 运行
PYTHONPATH=src python -m workflow_recorder -c config.yaml
```

---

## 系统架构

```
┌──────────────────┐        ┌──────────────────┐        ┌──────────────┐
│  Capture Thread  │──queue──│ Analysis Thread  │──buf───│  Aggregation │──→ Workflow JSON
│                  │        │                  │        │              │
│  mss 截屏        │        │  GPT-4o Vision   │        │  步骤合并     │
│  win32gui 窗口   │        │  JSON 结构化输出  │        │  动作映射     │
│  隐私过滤        │        │  重试/限流        │        │  哈希去重     │
└──────────────────┘        └──────────────────┘        └──────────────┘
```

- **双线程解耦** — 截屏线程和分析线程通过有界队列连接，队列满时丢帧保证截屏不阻塞
- **隐私优先** — 应用排除名单 + 窗口标题正则 + 区域遮罩，在 API 调用前执行
- **智能去重** — 感知哈希（pHash）比对相邻帧，跳过无变化的空闲画面

---

## 运行效果

启动后控制台显示：

```
==========================================================
  Workflow Recorder v0.1.0
==========================================================

  Screenshot interval:  3s
  Max recording time:   60m 0s
  GPT model:            gpt-4o
  Output directory:     C:\Program Files\WorkflowRecorder\workflows

  Recording is now in progress.
  Press Ctrl+C to stop early.

----------------------------------------------------------
  [05:23] Frames captured: 107  |  Analyzed: 95
```

录制结束后输出摘要：

```
----------------------------------------------------------
  Recording complete!

  Duration:         5m 23s
  Frames captured:  107
  Frames analyzed:  95

  Workflow JSON:     C:\...\workflows\workflow_a1b2c3d4.json
  Workflow Summary:  C:\...\workflows\workflow_a1b2c3d4.md

  All outputs saved to: C:\...\workflows

==========================================================
  Press Enter to close this window...
```

---

## 输出格式

### Workflow JSON

```json
{
  "$schema": "workflow-recorder/v1",
  "metadata": {
    "session_id": "a1b2c3d4-...",
    "recorded_at": "2026-04-08T14:32:38+00:00",
    "duration_seconds": 323.5,
    "total_frames_captured": 107,
    "total_steps": 12
  },
  "environment": {
    "screen_resolution": [1920, 1080],
    "os": "Windows 10",
    "hostname": "DESKTOP-ABC"
  },
  "steps": [
    {
      "step_id": 1,
      "application": {
        "process_name": "chrome.exe",
        "window_title": "Google Chrome"
      },
      "description": "clicking the address bar",
      "actions": [
        {
          "type": "click",
          "target": "address bar",
          "coordinates": [960, 52],
          "button": "left"
        }
      ],
      "confidence": 0.92,
      "reference_screenshot": "screenshots/step_001.png"
    }
  ]
}
```

### 支持的动作类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `click` | 鼠标点击 | `click(Save at [100,200], left)` |
| `type` | 键盘输入 | `type("hello world")` |
| `key` | 快捷键 | `key(ctrl+s)` |
| `scroll` | 滚动 | `scroll(down, 3)` |
| `wait` | 等待/阅读 | `wait(1s)` |

---

## 配置说明

配置文件支持 YAML 和 TOML 格式，支持环境变量插值 `${ENV_VAR}`。

### 核心配置项

```yaml
capture:
  interval_seconds: 3          # 截屏间隔（秒）
  monitor: 0                   # 0=主显示器, -1=所有显示器
  downscale_factor: 0.5        # 缩放因子（0.5=半分辨率，节省 API 费用）

privacy:
  excluded_apps:               # 排除的进程（不截屏）
    - "KeePass.exe"
    - "1Password.exe"
  excluded_window_titles:      # 正则匹配窗口标题
    - ".*Incognito.*"
  masked_regions: []           # 遮罩区域 [[x, y, w, h], ...]

analysis:
  openai_api_key: "${OPENAI_API_KEY}"
  base_url: ""                 # 自定义 API 端点（代理/镜像）
  model: "gpt-4o"
  detail: "low"                # "low" ≈$0.002/帧, "high" ≈$0.01/帧
  rate_limit_rpm: 30           # 每分钟最大 API 请求数

session:
  max_duration_seconds: 3600   # 最长录制时间（秒）
  similarity_threshold: 0.95   # 图像去重阈值（0-1，越高越严格）

output:
  directory: "./workflows"
  format: "both"               # "json" / "yaml" / "both"
  include_reference_screenshots: true
  include_markdown_summary: true
```

完整配置参见 [`config.example.yaml`](config.example.yaml)。

### API 代理支持

支持通过 `base_url` 配置自定义 API 端点，兼容 OpenAI API 协议的代理服务：

```yaml
analysis:
  base_url: "https://your-proxy.com/v1"
  openai_api_key: "your-key"
```

---

## 项目结构

```
src/workflow_recorder/
├── __main__.py              # CLI 入口，控制台 UI
├── daemon.py                # 主循环，双线程编排
├── config.py                # Pydantic 配置模型，YAML/TOML 加载
├── capture/
│   ├── screenshot.py        # mss 截屏
│   ├── window_info.py       # 活动窗口检测 (Win32/macOS)
│   └── privacy.py           # 隐私过滤（排除应用 + 区域遮罩）
├── analysis/
│   ├── vision_client.py     # GPT-4o Vision API 客户端
│   ├── prompts.py           # 系统/用户提示词模板
│   └── frame_analysis.py    # 单帧分析数据模型
├── aggregation/
│   ├── workflow_builder.py  # 帧分析聚合为工作流步骤
│   ├── deduplication.py     # 感知哈希去重
│   └── action_mapper.py     # GPT 描述 → click/type/key/scroll 动作
├── output/
│   ├── schema.py            # Workflow JSON Pydantic 模型
│   ├── writer.py            # JSON/YAML/Markdown 序列化
│   └── reference_store.py   # 参考截图管理
└── utils/
    ├── logging.py           # structlog 结构化日志
    ├── retry.py             # 指数退避重试装饰器
    └── storage.py           # 临时文件管理

installer/
├── workflow_recorder.spec   # PyInstaller 打包配置
├── workflow_recorder.iss    # Inno Setup 安装脚本
├── build.py                 # 一键构建脚本
└── verify_build.py          # 构建验证

tests/
├── conftest.py              # 共享 fixtures + pytest 插件
├── test_*.py                # 13 个单元测试文件 (93 tests)
├── integration/             # GPT API 集成测试 (5 tests)
└── e2e/                     # 端到端管道测试 (2 tests)
```

---

## 测试

项目包含 100 个测试，分为三层：

```bash
# 单元测试（快速，无需 API）
PYTHONPATH=src python -m pytest tests/ -v -m "not integration and not e2e"

# 集成测试（需要 API Key）
PYTHONPATH=src python -m pytest tests/ -v -m integration --run-integration

# 端到端测试（完整录制管道）
PYTHONPATH=src python -m pytest tests/ -v -m e2e --run-e2e

# 全部测试
PYTHONPATH=src python -m pytest tests/ -v --run-integration --run-e2e
```

| 层 | 测试数 | 说明 |
|---|---|---|
| 单元测试 | 93 | 覆盖所有模块，无外部依赖 |
| 集成测试 | 5 | 真实 GPT API 调用验证 |
| 端到端测试 | 2 | 完整 Daemon 运行 + 输出验证 |

---

## 构建安装包

```bash
# 安装构建工具
pip install pyinstaller

# 仅构建可执行文件
python installer/build.py

# 构建完整安装包（需要 Inno Setup 6）
python installer/build.py --installer
```

产出：
- `dist/WorkflowRecorder/` — PyInstaller 输出目录（可直接分发）
- `dist/WorkflowRecorder-x.x.x-Setup.exe` — Windows 安装包

安装包功能：
- 标准安装向导，无需 Python 环境
- Start Menu 快捷方式（运行 + 编辑配置）
- 可选加入系统 PATH
- 可选安装为 Windows 服务（开机自启）
- `config.yaml` 升级不覆盖、卸载不删除
- 完整卸载器

---

## Windows 服务

```powershell
# 安装服务
workflow-recorder-service.exe install

# 启动服务
net start WorkflowRecorder

# 停止服务
net stop WorkflowRecorder

# 卸载服务
workflow-recorder-service.exe remove
```

> 服务以 SYSTEM 账户运行，API Key 需要直接写在 `config.yaml` 中（环境变量在 SYSTEM 账户下不可用）。

---

## API 费用估算

| detail 模式 | 单帧费用 | 3秒间隔/小时 | 每小时费用 |
|-------------|---------|-------------|-----------|
| `low` | ~$0.002 | 1200 帧 | ~$2.40 |
| `high` | ~$0.01 | 1200 帧 | ~$12.00 |

> 实际费用更低：感知哈希去重会跳过 50-80% 的空闲/重复帧。

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 截屏 | [mss](https://github.com/BoboTiG/python-mss) |
| 窗口检测 | [pywin32](https://github.com/mhammond/pywin32) (Win32 API) |
| 进程信息 | [psutil](https://github.com/giampaolo/psutil) |
| AI 分析 | [OpenAI SDK](https://github.com/openai/openai-python) (GPT-4o Vision) |
| 数据校验 | [Pydantic](https://docs.pydantic.dev/) v2 |
| 图像去重 | [imagehash](https://github.com/JohannesBuchner/imagehash) (pHash) |
| 日志 | [structlog](https://www.structlog.org/) |
| 配置 | YAML ([PyYAML](https://pyyaml.org/)) + TOML (stdlib) |
| 打包 | [PyInstaller](https://pyinstaller.org/) + [Inno Setup](https://jrsoftware.org/isinfo.php) |

---

## 许可证

MIT

---

## 贡献

欢迎提交 Issue 和 Pull Request。

```bash
# 开发环境搭建
git clone https://github.com/gaozhi-ustc/computer-use.git
cd computer-use
pip install -e ".[dev]"

# 运行测试确认环境正常
PYTHONPATH=src python -m pytest tests/ -v -m "not integration and not e2e"
```
