# Codex Bridge

让 Codex 桌面端连接其他 AI 模型的本地桥接服务。通过 Responses API 到 Chat Completions 格式转换，实现 Codex 与小米 MiMo 等模型的无缝对接。

## 功能

- **Responses API → Chat Completions 转换** — 自动翻译两种 API 格式
- **Tool Call 完整支持** — function_call / function_call_output / tools 参数透传
- **reasoning_content 缓存** — 自动处理 MiMo 思考模式的多轮回传要求
- **webSearchEnabled 自动处理** — 有 tools 时自动启用 MiMo 内置搜索
- **模型名映射** — 客户端模型名 ↔ 实际 API 模型名双向映射
- **流式 / 非流式** — 完整支持两种响应模式
- **Web 管理面板** — 浏览器可视化配置，无需手动编辑文件
- **代理支持** — 可配置 HTTP 代理访问上游 API
- **一键拉取模型** — 从上游 API 自动获取可用模型列表

## 架构

```
Codex 桌面端
    │ Responses API
    ▼
Bridge (:3080)         ← 格式转换 + 模型映射 + reasoning 缓存
    │ Chat Completions
    ▼ (可选代理)
    │
上游 API               ← MiMo / DeepSeek / OpenAI 等
```

## 快速开始

### 1. 环境

- Python 3.10+（无额外依赖，纯标准库）

### 2. 配置

```bash
cp config.example.json config.json
```

编辑 `config.json`：

| 字段 | 说明 | 示例 |
|------|------|------|
| `api_url` | 上游 API 地址 | `https://api.xiaomimimo.com` |
| `api_key` | API Key | `sk-xxx` |
| `proxy_url` | 代理地址（留空直连） | `http://127.0.0.1:7897` |
| `listen_port` | 监听端口 | `3080` |
| `model_mapping` | 模型名映射 | `{"GPT-5.5": "mimo-v2.5-pro"}` |

也可以启动后通过 Web 面板修改：`http://127.0.0.1:3080/`

### 3. 启动

```bash
python bridge.py
```

或 Windows 双击 `start.bat`

### 4. 配置 Codex

编辑 `~/.codex/config.toml`：

```toml
model_provider = "bridge"
model = "GPT-5.5"

[model_providers.bridge]
name = "Codex Bridge"
base_url = "http://127.0.0.1:3080/v1"
wire_api = "responses"
requires_openai_auth = true
request_max_retries = 1

[windows]
sandbox = "unelevated"
```

`auth.json` 中的 `OPENAI_API_KEY` 填任意值（桥接层使用 config.json 中的 key）。

## Web 管理面板

启动后访问 `http://127.0.0.1:3080/`：

- 修改 API 地址、Key、代理
- 模型映射：左边下拉选 Codex 模型名，右边下拉选上游实际模型
- 一键拉取上游模型列表
- 测试 API 连接
- 配置自动保存，无需重启

## 支持的上游 API

任何 OpenAI Chat Completions 兼容的 API 均可使用，包括但不限于：

| 服务商 | API 地址示例 |
|--------|-------------|
| 小米 MiMo | `https://token-plan-cn.xiaomimimo.com` |
| DeepSeek | `https://api.deepseek.com` |
| OpenAI | `https://api.openai.com` |
| 其他兼容 API | 任意 OpenAI 格式接口 |

## MiMo 特殊处理

桥接层针对小米 MiMo API 自动处理以下兼容性问题：

1. **reasoning_content 回传** — MiMo 要求多轮 tool call 时必须回传 reasoning_content，桥接层自动缓存并注入
2. **webSearchEnabled** — 有 tools 参数时自动启用
3. **tools 格式转换** — Responses API 的 tools 格式自动转为 Chat Completions 格式

## 文件说明

```
bridge.py              # 核心程序（API 转换 + Web 管理面板）
config.json            # 配置文件（不会上传到 Git）
config.example.json    # 配置示例
start.bat              # Windows 启动脚本
.gitignore             # Git 忽略规则
```

## 许可

MIT
