# Workflow Recorder

## 项目概述

Windows 后台常驻截屏分析 daemon，通过 GPT-4o 视觉模型理解员工桌面操作，输出可被 computer-use 自动化工具复现的结构化工作流文档。

## 架构

```
[Capture Thread] --queue--> [Analysis Thread] --buffer--> [Aggregation] --> [Workflow JSON]
     │                            │
     ├─ mss 截屏                   ├─ GPT-4o vision API
     ├─ win32gui 活动窗口           ├─ JSON 结构化输出
     └─ 隐私过滤                    └─ 重试/限流
```

- **双线程**: 截屏线程和分析线程通过有界队列解耦，队列满时丢帧
- **隐私优先**: 应用排除名单 + 区域遮罩，在 API 调用前执行

## 技术栈

- Python 3.9+（目标运行环境 Windows Python 3.11+）
- `mss` 截屏 / `pywin32` 窗口检测 / `psutil` 进程信息
- `openai` SDK (GPT-4o vision) / `pydantic` 数据校验
- `imagehash` 图像去重 / `structlog` 结构化日志

## 项目结构

```
src/workflow_recorder/
├── __main__.py          # CLI 入口
├── daemon.py            # 主循环，双线程编排
├── config.py            # Pydantic 配置模型，支持 ${ENV_VAR}
├── capture/
│   ├── screenshot.py    # mss 截屏
│   ├── window_info.py   # 活动窗口检测 (Win32/macOS)
│   └── privacy.py       # 隐私过滤
├── analysis/
│   ├── vision_client.py # OpenAI GPT-4o vision API
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
```

## 常用命令

```bash
# 开发模式运行
PYTHONPATH=src python3 -m workflow_recorder -c config.example.yaml

# 运行测试
PYTHONPATH=src python3 -m pytest tests/ -v

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
