# MiMo Bridge

本地 API 中转站，将任意 OpenAI 兼容的 API 转换为 Responses API 格式，并支持模型名称映射。主要为 Codex 桌面端设计。

## 功能

- **Responses API 转换** — 自动将 Responses API 格式转为 Chat Completions 格式
- **模型名映射** — 将客户端模型名映射到实际 API 模型名（如 `GPT-5.5` → `mimo-v2-omni`）
- **Web 管理面板** — 可视化配置所有参数，无需手动编辑文件
- **代理支持** — 可配置 HTTP 代理访问上游 API
- **一键拉取模型** — 从上游 API 自动获取可用模型列表

## 架构

```
客户端 (Codex / 其他)
        │
        ▼
  Bridge (:3080)         ← Responses API 转换 + 模型映射
        │
        ▼ (可选代理)
        │
  上游 API               ← 实际模型服务
```

## 快速开始

### 1. 安装依赖

需要 Python 3.10+（无额外依赖，纯标准库）

### 2. 配置

```bash
cp config.example.json config.json
```

编辑 `config.json`，填入你的 API 信息：

| 字段 | 说明 |
|------|------|
| `api_url` | 上游 API 地址 |
| `api_key` | API Key |
| `proxy_url` | 代理地址（留空直连） |
| `listen_port` | 监听端口 |
| `model_mapping` | 模型名映射（客户端名 → 实际模型） |
| `model_list` | 客户端显示的模型列表 |

也可以启动后通过 Web 面板修改：`http://127.0.0.1:3080/`

### 3. 启动

```bash
python bridge.py
```

或双击 `start.bat`（Windows）

### 4. 配置 Codex

编辑 `~/.codex/config.toml`：

```toml
model_provider = "mimo-onerelay"
model = "GPT-5.5"

[model_providers.mimo-onerelay]
name = "MiMo (via bridge)"
base_url = "http://127.0.0.1:3080/v1"
wire_api = "responses"
requires_openai_auth = true
request_max_retries = 1
```

## Web 管理面板

启动后访问 `http://127.0.0.1:3080/`，可以：

- 修改 API 地址、Key、代理
- 增删改模型映射（支持从 Codex 模型列表选择）
- 从上游 API 一键拉取模型列表
- 测试 API 连接
- 配置自动保存，无需重启

## 文件说明

```
bridge.py              # 核心程序（API 转换 + Web 管理面板）
config.json            # 配置文件（自动生成，不会上传到 Git）
config.example.json    # 配置示例
start.bat              # Windows 启动脚本
.gitignore             # Git 忽略规则
```

## 许可

MIT
