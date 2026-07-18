# BabyMonitorVL

BabyMonitorVL 是一个使用多模态大语言模型分析 RTSP 婴儿监控画面的 MVP。FFmpeg 只负责解码、定时抽帧和 JPEG 编码；婴儿定位、姿势、脸部遮挡、被子覆盖以及猫是否进入画面的判断全部来自所选视觉语言模型，不使用 OpenCV、检测器或跟踪器。

> 本项目仅供技术演示和人工复核，不是医疗设备、生命安全告警或无人值守监控系统。

当前 `0.1.x` 的正式交付物是包含 FFmpeg、后端和前端静态资源的 Docker 镜像。Python wheel 仅包含后端包，不被视为可独立运行的完整产品发布物。

## 功能

- 单路 RTSP，默认每秒抽取 1 帧，可配置 `0.1–10 FPS`。
- Ollama 本地模型和 Google AI Studio API 两种实现；云端列表包含兼容的 Gemini 与 Gemma 4 图像输入模型。
- 单槽 latest-frame 队列：模型慢时覆盖旧的待分析帧，不累积实时延迟。
- 实时抽帧预览、与结果严格对齐的标注画面、结构化分析和原始响应审计。
- 独立检测画面中的真实家猫，返回猫框、置信度以及与婴儿的距离关系，并在主画面和历史缩略图中叠加紫色框。
- 仅受内存预算限制（默认 1 GiB）的进程内历史；不写入磁盘。
- RTSP 断流自动重连，地址凭据不会进入日志或 API 状态。

## 最快启动：Docker

需要 Docker，以及运行在宿主机的 Ollama（使用 Gemini 时可不安装 Ollama）。

```bash
ollama pull qwen3-vl:4b
cp .env.example .env
docker compose up --build
```

访问 <http://127.0.0.1:8000>。容器已包含 FFmpeg，并通过 `host.docker.internal:11434` 访问宿主机 Ollama。Qwen3-VL 需要 Ollama 0.12.7 或更高版本。

Gemini Key 可以在页面右上角的设置 Dialog 中临时配置，也可以在 `.env` 中提供启动默认值：

```dotenv
GEMINI_API_KEY=your_api_key
```

网页提交的 Key 会先通过 Google API 验证，只保存在后端进程内存中，既不写入浏览器存储或历史，也不会由 API 返回；服务重启后恢复 `.env` 中的启动配置。Gemini 模式会将所选采样帧发送给 Google API，页面也会显示这一提示。由于服务没有用户认证，只应通过默认的本机地址或可信 HTTPS 连接配置 Key。

## 本地开发

要求 Python 3.11+、uv、FFmpeg、Node.js 22+ 和 pnpm。所有 Python 依赖只安装在仓库内的 `.venv`：

```bash
uv sync
pnpm --dir frontend install
```

分别启动后端和 Vite 开发服务器：

```bash
uv run uvicorn babymonitorvl.main:app --reload
pnpm --dir frontend dev
```

开发页面位于 <http://127.0.0.1:5173>，Vite 会代理 `/api` 和 WebSocket。生产单端口构建：

```bash
pnpm --dir frontend build
uv run uvicorn babymonitorvl.main:app --host 127.0.0.1 --port 8000
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API 地址；Docker 中使用 host.docker.internal |
| `GEMINI_API_KEY` | 空 | Gemini Studio API key 启动默认值；可由网页内存配置临时覆盖 |
| `DEFAULT_OLLAMA_MODEL` | `qwen3-vl:4b` | Ollama 默认模型 |
| `DEFAULT_GEMINI_MODEL` | `gemini-3.5-flash` | Gemini 默认模型 |
| `MODEL_TIMEOUT_SECONDS` | `60` | 单次模型调用超时 |
| `HISTORY_MAX_BYTES` | `1073741824` | JPEG 和调试 payload 的历史预算 |
| `FFMPEG_BINARY` | `ffmpeg` | FFmpeg 可执行文件 |

RTSP 地址、FPS、provider、model、TCP/UDP 和图像长边上限可在页面配置。服务同时只运行一个监控会话，停止会话不会清空历史，服务重启会清空。

### Bounding box 坐标约定

API 与前端统一使用 Gemini 风格的 `[ymin, xmin, ymax, xmax]`（归一化到 `0..1000`）。模型请求会按模型原生约定生成 Prompt 和 Schema；Ollama 中模型名匹配 `qwen*` 的整个 Qwen 系列统一使用 `[xmin, ymin, xmax, ymax]`，后端在校验和保存结果前转换为统一格式。原始响应保持不变，模型坐标顺序和统一坐标顺序会记录在历史详情的调用参数中。

完整分析契约由同一份 Pydantic Schema 生成。Ollama 直接接收完整 Schema；Gemini Studio 后端会把它转换为 Google AI 支持的结构化输出子集后再发送，完整约束仍保留在 Prompt 和本地 Pydantic 校验中。该兼容层同时适用于通过 Gemini Studio 调用的 Gemini 与 Gemma 模型。

## API 摘要

- `GET /api/providers`
- `PUT /api/providers/gemini/key`、`DELETE /api/providers/gemini/key`
- `POST /api/monitor/start`
- `POST /api/monitor/stop`
- `GET /api/monitor/status`
- `GET /api/live/image`
- `GET /api/history`、`GET /api/history/{id}`、`GET /api/history/{id}/image`
- `GET /api/prompt`
- `WS /api/events`

交互式文档位于 `/docs`。

## 测试

```bash
uv run pytest
pnpm --dir frontend typecheck
```

真正的 Ollama/Gemini 调用不属于默认单元测试；启动服务后可用自己的 RTSP 源做 smoke test。所有历史数据都在服务内存中，测试和应用代码均不依赖 OpenCV。

## 开发与发布文档

- [Agent 开发约束](AGENTS.md)
- [贡献规范](CONTRIBUTING.md)
- [架构与数据流](docs/ARCHITECTURE.md)
- [开发环境与测试命令](docs/DEVELOPMENT.md)
- [分析 Schema、Prompt、坐标与 Provider 契约](docs/ANALYSIS_CONTRACT.md)
- [Provider 兼容问题的分层诊断与防回归方法](docs/PROVIDER_COMPATIBILITY.md)
- [Gemini/Gemma Provider 兼容性与防回归规范](docs/GEMINI_PROVIDER.md)
- [版本发布清单](docs/RELEASE.md)
- [变更记录](CHANGELOG.md)

## 许可证

本项目采用 [MIT License](LICENSE)，Copyright (c) 2026 Lucky Couple。
