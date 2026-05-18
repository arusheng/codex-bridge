#!/usr/bin/env python3
"""
MiMo API Bridge
- /v1/responses -> Chat Completions translation
- /v1/chat/completions -> passthrough
- / -> Web management UI
"""
import http.server
import urllib.request
import ssl
import json
import sys
import time
import os
import copy

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Cache reasoning_content from responses to inject back in multi-turn
# Key: simple counter, Value: reasoning text
_reasoning_cache = []
_reasoning_cache_max = 20

DEFAULT_CONFIG = {
    "api_url": "https://token-plan-cn.xiaomimimo.com",
    "api_key": "",
    "proxy_url": "http://127.0.0.1:7897",
    "listen_port": 3080,
    "model_mapping": {
        "gpt-5.5": "mimo-v2-omni",
        "gpt-5.4": "mimo-v2.5-pro",
        "GPT-5.5": "mimo-v2-omni",
        "GPT-5.4": "mimo-v2.5-pro",
    },
    "model_list": [
        {"id": "GPT-5.5", "name": "MiMo 全模态 (mimo-v2-omni)"},
        {"id": "GPT-5.4", "name": "MiMo 2.5 Pro (mimo-v2.5-pro)"},
    ],
}

cfg = {}


def load_config():
    global cfg
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config()


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def create_ssl_context():
    return ssl.create_default_context()


def proxy_to_api(path, method, headers, body=None):
    url = cfg["api_url"].rstrip("/") + path
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + cfg["api_key"])
    ct = headers.get("Content-Type") or headers.get("content-type")
    if ct:
        req.add_header("Content-Type", ct)
    proxy_url = cfg.get("proxy_url", "")
    if proxy_url:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url}),
            urllib.request.HTTPSHandler(context=create_ssl_context()),
        )
    else:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=create_ssl_context())
        )
    return opener.open(req, timeout=300)


def translate_responses_to_chat(req_body):
    messages = []
    system = req_body.get("system") or req_body.get("instructions")
    if system:
        messages.append({"role": "system", "content": system})

    inp = req_body.get("input", "")
    if isinstance(inp, list):
        for i, item in enumerate(inp):
            if isinstance(item, dict):
                role = item.get("role", item.get("type", ""))
                if role == "assistant":
                    c = item.get("content", "")
                    if isinstance(c, list):
                        types = [p.get("type", "") for p in c if isinstance(p, dict)]
                        has_reasoning = "reasoning" in types
                        log(f"  [{i}] assistant: types={types} has_reasoning={has_reasoning}")
                    else:
                        log(f"  [{i}] assistant: content_type={type(c).__name__} len={len(str(c))}")
                elif item.get("type") == "function_call":
                    log(f"  [{i}] function_call: {item.get('name','')}")
                elif item.get("type") == "function_call_output":
                    log(f"  [{i}] function_call_output: len={len(str(item.get('output','')))}")
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                item_type = item.get("type", "")

                # function_call_output = tool result from Codex
                if item_type == "function_call_output":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": item.get("call_id", ""),
                        "content": item.get("output", ""),
                    })
                    continue

                # function_call = tool call from assistant
                if item_type == "function_call":
                    msg = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": item.get("call_id", ""),
                            "type": "function",
                            "function": {
                                "name": item.get("name", ""),
                                "arguments": item.get("arguments", "{}"),
                            }
                        }]
                    }
                    # Inject cached reasoning_content (MiMo requires it for tool calls)
                    if _reasoning_cache:
                        msg["reasoning_content"] = _reasoning_cache.pop(0)
                        log(f"Injected cached reasoning_content ({len(msg['reasoning_content'])} chars)")
                    messages.append(msg)
                    continue

                # Regular message (user/assistant)
                role = item.get("role", "user")
                content = ""
                reasoning_content = ""
                tool_calls = item.get("tool_calls")
                if "content" in item:
                    c = item["content"]
                    if isinstance(c, str):
                        content = c
                    elif isinstance(c, list):
                        parts = []
                        for p in c:
                            if isinstance(p, dict):
                                if p.get("type") in ("input_text", "text"):
                                    parts.append(p.get("text", ""))
                                elif p.get("type") == "reasoning":
                                    reasoning_content = p.get("reasoning", "")
                                elif p.get("type") == "output_text":
                                    parts.append(p.get("text", ""))
                            elif isinstance(p, str):
                                parts.append(p)
                        content = "\n".join(parts)
                elif "text" in item:
                    content = item["text"]
                msg = {"role": role, "content": content}
                if reasoning_content:
                    msg["reasoning_content"] = reasoning_content
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                messages.append(msg)

    model = req_body.get("model", "")
    mapping = cfg.get("model_mapping", {})
    model = mapping.get(model, mapping.get(model.upper(), mapping.get(model.lower(), model)))

    chat_req = {"model": model, "messages": messages}

    # Convert tools from Responses API format to Chat Completions format
    if "tools" in req_body:
        converted_tools = []
        for t in req_body["tools"]:
            tname = t.get("name", "") or t.get("function", {}).get("name", "")
            if not tname:
                continue
            if "function" in t:
                converted_tools.append(t)
            elif "name" in t:
                converted_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    }
                })
            else:
                converted_tools.append(t)
        if converted_tools:
            chat_req["tools"] = converted_tools
        # MiMo requires webSearchEnabled when tools are present
        chat_req["webSearchEnabled"] = True
        log(f"Tools: {len(converted_tools)} converted")

    if "max_output_tokens" in req_body:
        chat_req["max_tokens"] = req_body["max_output_tokens"]
    elif "max_tokens" in req_body:
        chat_req["max_tokens"] = req_body["max_tokens"]
    chat_req["stream"] = req_body.get("stream", False)
    log(f"Outgoing: {json.dumps({k:v for k,v in chat_req.items() if k != 'messages'}, ensure_ascii=False)[:500]}")
    return chat_req


def make_sse(event_type, data):
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def translate_chat_to_responses_stream(chat_chunks, model, resp_id):
    content_index = 0
    started = False
    full_text = ""
    full_reasoning = ""
    tool_calls = {}  # index -> {id, name, arguments}

    yield make_sse("response.created", {"type": "response.created", "response": {
        "id": resp_id, "object": "response", "created_at": int(time.time()),
        "model": model, "output": [], "status": "in_progress"}})
    yield make_sse("response.in_progress", {"type": "response.in_progress", "response": {
        "id": resp_id, "object": "response", "created_at": int(time.time()),
        "model": model, "output": [], "status": "in_progress"}})

    for line in chat_chunks:
        line = line.decode("utf-8", errors="replace").strip()
        if not line or line == "data: [DONE]":
            continue
        if line.startswith("data: "):
            try:
                chunk = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                text = delta.get("content", "")
                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    full_reasoning += reasoning

                # Handle tool calls in streaming
                delta_tool_calls = delta.get("tool_calls") or []
                for tc in delta_tool_calls:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls:
                        tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    if "id" in tc and tc["id"]:
                        tool_calls[idx]["id"] = tc["id"]
                    if "function" in tc:
                        fn = tc["function"]
                        if "name" in fn and fn["name"]:
                            tool_calls[idx]["name"] = fn["name"]
                        if "arguments" in fn:
                            tool_calls[idx]["arguments"] += fn["arguments"]

                if not started and (text or reasoning or delta_tool_calls):
                    started = True
                    yield make_sse("response.output_item.added", {"type": "response.output_item.added",
                        "output_index": 0, "item": {"type": "message", "id": f"{resp_id}_msg_0",
                        "role": "assistant", "status": "in_progress", "content": []}})

                if text:
                    full_text += text
                    yield make_sse("response.content_part.added", {"type": "response.content_part.added",
                        "output_index": 0, "content_index": content_index, "part": {"type": "output_text", "text": ""}})
                    yield make_sse("response.output_text.delta", {"type": "response.output_text.delta",
                        "output_index": 0, "content_index": content_index, "delta": text})
                    yield make_sse("response.content_part.done", {"type": "response.content_part.done",
                        "output_index": 0, "content_index": content_index, "part": {"type": "output_text", "text": text}})
                    content_index += 1

    # Build output items
    output_items = []

    # Text message (if any)
    if full_text or not tool_calls:
        content_parts = [{"type": "output_text", "text": full_text}]
        if full_reasoning:
            content_parts.append({"type": "reasoning", "reasoning": full_reasoning})
        output_items.append({"type": "message", "id": f"{resp_id}_msg_0", "role": "assistant",
            "status": "completed", "content": content_parts})
        yield make_sse("response.output_item.done", {"type": "response.output_item.done",
            "output_index": len(output_items) - 1,
            "item": output_items[-1]})

    # Tool call items
    for idx in sorted(tool_calls.keys()):
        tc = tool_calls[idx]
        item = {
            "type": "function_call",
            "id": f"{resp_id}_fc_{idx}",
            "call_id": tc["id"] or f"call_{idx}",
            "name": tc["name"],
            "arguments": tc["arguments"],
        }
        output_items.append(item)
        yield make_sse("response.output_item.added", {"type": "response.output_item.added",
            "output_index": len(output_items) - 1, "item": item})
        yield make_sse("response.output_item.done", {"type": "response.output_item.done",
            "output_index": len(output_items) - 1, "item": item})

    # Cache reasoning for multi-turn tool call scenarios
    if full_reasoning:
        _reasoning_cache.append(full_reasoning)
        if len(_reasoning_cache) > _reasoning_cache_max:
            _reasoning_cache.pop(0)
        log(f"Cached reasoning_content ({len(full_reasoning)} chars)")

    yield make_sse("response.completed", {"type": "response.completed", "response": {
        "id": resp_id, "object": "response", "created_at": int(time.time()), "model": model,
        "output": output_items, "status": "completed",
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}})


def translate_chat_to_responses_nonstream(chat_data, model, resp_id):
    text = ""
    reasoning = ""
    tool_calls_out = []
    choices = chat_data.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        text = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""
        for tc in (msg.get("tool_calls") or []):
            tool_calls_out.append({
                "type": "function_call",
                "id": f"{resp_id}_fc_{len(tool_calls_out)}",
                "call_id": tc.get("id", ""),
                "name": tc.get("function", {}).get("name", ""),
                "arguments": tc.get("function", {}).get("arguments", "{}"),
            })

    output_items = []
    if text or reasoning or not tool_calls_out:
        content_parts = [{"type": "output_text", "text": text}]
        if reasoning:
            content_parts.append({"type": "reasoning", "reasoning": reasoning})
        output_items.append({"type": "message", "id": f"{resp_id}_msg_0", "role": "assistant",
            "status": "completed", "content": content_parts})
    output_items.extend(tool_calls_out)

    # Cache reasoning for multi-turn tool call scenarios
    if reasoning:
        _reasoning_cache.append(reasoning)
        if len(_reasoning_cache) > _reasoning_cache_max:
            _reasoning_cache.pop(0)
        log(f"Cached reasoning_content ({len(reasoning)} chars)")

    return {"id": resp_id, "object": "response", "created_at": int(time.time()), "model": model,
        "output": output_items, "status": "completed",
        "usage": chat_data.get("usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})}


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MiMo Bridge - 管理面板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e1e1e1;min-height:100vh}
.container{max-width:720px;margin:0 auto;padding:24px}
h1{font-size:1.4rem;margin-bottom:8px;color:#fff}
.subtitle{color:#888;font-size:.85rem;margin-bottom:24px}
.card{background:#1a1d27;border:1px solid #2a2d3a;border-radius:10px;padding:20px;margin-bottom:16px}
.card-title{font-size:.9rem;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:14px}
label{display:block;font-size:.85rem;color:#aaa;margin-bottom:5px}
input,textarea,select{width:100%;background:#12141c;border:1px solid #2a2d3a;border-radius:6px;color:#e1e1e1;padding:9px 12px;font-size:.9rem;outline:none;transition:border .2s}
input:focus,textarea:focus,select:focus{border-color:#646cff}
select{cursor:pointer;appearance:auto}
select option{background:#1a1d27;color:#e1e1e1}
textarea{resize:vertical;min-height:60px;font-family:monospace;font-size:.82rem}
.row{display:flex;gap:12px;margin-bottom:12px}
.row>*{flex:1}
.row .no-grow{flex:none;min-width:auto}
.btn{display:inline-block;padding:9px 18px;border:none;border-radius:6px;font-size:.85rem;cursor:pointer;transition:all .2s;font-weight:600}
.btn-primary{background:#646cff;color:#fff}.btn-primary:hover{background:#535bf2}
.btn-success{background:#2ea043;color:#fff}.btn-success:hover{background:#238636}
.btn-danger{background:#da3633;color:#fff}.btn-danger:hover{background:#b62324}
.btn-ghost{background:transparent;color:#888;border:1px solid #2a2d3a}.btn-ghost:hover{color:#fff;border-color:#646cff}
.btn-sm{padding:5px 12px;font-size:.78rem}
.mapping-item{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.mapping-item input{flex:1}
.mapping-item .arrow{color:#646cff;font-weight:bold;flex:none}
.model-item{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.model-item input{flex:1}
.status{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.78rem;font-weight:600}
.status-ok{background:#2ea043;color:#fff}
.status-err{background:#da3633;color:#fff}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:.85rem;color:#fff;z-index:999;animation:fadeIn .3s}
@keyframes fadeIn{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:translateY(0)}}
hr{border:none;border-top:1px solid #2a2d3a;margin:14px 0}
.mt{margin-top:12px}.mb{margin-bottom:12px}
</style>
</head>
<body>
<div class="container">
  <h1>MiMo Bridge</h1>
  <p class="subtitle">API 中转管理面板 &nbsp;|&nbsp; <span id="statusBadge" class="status status-ok">运行中</span></p>

  <div class="card">
    <div class="card-title">API 连接</div>
    <div class="row">
      <div><label>API 地址</label><input id="api_url" placeholder="https://api.example.com/v1"></div>
    </div>
    <div class="row">
      <div><label>API Key</label><input id="api_key" type="password" placeholder="sk-xxxx"></div>
    </div>
    <div class="row">
      <div><label>代理地址 (留空直连)</label><input id="proxy_url" placeholder="http://127.0.0.1:7897"></div>
    </div>
    <button class="btn btn-ghost btn-sm mt" onclick="toggleKey()">显示/隐藏 Key</button>
    <button class="btn btn-primary btn-sm mt" onclick="testConnection()">测试连接</button>
    <button class="btn btn-primary btn-sm mt" onclick="fetchModels()" style="float:right">拉取上游模型</button>
    <div style="clear:both"></div>
  </div>

  <div class="card">
    <div class="card-title">模型映射 <span style="font-weight:normal;text-transform:none;color:#646cff">( 客户端名称 → 实际模型 )</span></div>
    <div id="mappingList"></div>
    <button class="btn btn-ghost btn-sm mt" onclick="addMapping()">+ 添加映射</button>
  </div>

  <div class="card">
    <div class="card-title">客户端模型列表 <span style="font-weight:normal;text-transform:none;color:#646cff">( 自动跟随映射，客户端可见 )</span></div>
    <div id="modelList"></div>
  </div>

  <div style="text-align:right;margin-top:8px">
    <button class="btn btn-success" onclick="saveConfig()">保存配置</button>
  </div>
</div>

<script>
const CODEX_MODELS = [
  "GPT-5.5","GPT-5.4","GPT-5.3","GPT-5.2","GPT-5.1","GPT-5","GPT-5-mini","GPT-5-nano",
  "GPT-4.5","GPT-4o","GPT-4o-mini","GPT-4-turbo","GPT-4","GPT-4-mini",
  "o4-mini","o3","o3-mini","o3-pro","o1","o1-mini","o1-pro",
  "Claude-4-Opus","Claude-4-Sonnet","Claude-3.7-Sonnet","Claude-3.5-Sonnet",
  "Gemini-2.5-Pro","Gemini-2.5-Flash","Gemini-2.0-Pro","Gemini-2.0-Flash",
  "DeepSeek-R1","DeepSeek-V3","DeepSeek-V4",
  "MiMo-v2-Omni","MiMo-v2.5-Pro"
];
let config = {};
let upstreamModels = [];

async function load() {
  const r = await fetch('/api/config');
  config = await r.json();
  document.getElementById('api_url').value = config.api_url || '';
  document.getElementById('api_key').value = config.api_key || '';
  document.getElementById('proxy_url').value = config.proxy_url || '';
  renderMappings();
  renderModels();
}

function renderMappings() {
  const el = document.getElementById('mappingList');
  const m = config.model_mapping || {};
  if (Object.keys(m).length === 0) {
    el.innerHTML = '<p style="color:#666;font-size:.82rem">暂无映射</p>';
  } else {
    el.innerHTML = '';
    for (const [k, v] of Object.entries(m)) addMapping(k, v);
  }
  renderModels();
}

function renderModels() {
  const el = document.getElementById('modelList');
  const m = config.model_mapping || {};
  const keys = Object.keys(m);
  if (keys.length === 0) {
    el.innerHTML = '<p style="color:#666;font-size:.82rem">暂无映射，请先添加</p>';
    return;
  }
  el.innerHTML = keys.map(k =>
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="color:#646cff;font-weight:600">${esc(k)}</span>
      <span style="color:#666">→</span>
      <span style="color:#aaa">${esc(m[k])}</span>
    </div>`
  ).join('');
}

function addMapping(presetKey, presetVal) {
  const el = document.getElementById('mappingList');
  const p = el.querySelector('p'); if (p) p.remove();
  const leftOpts = CODEX_MODELS.map(m => `<option value="${m}"${m===presetKey?' selected':''}>${m}</option>`).join('');
  let rightHtml;
  if (upstreamModels.length > 0) {
    const rightOpts = upstreamModels.map(m => `<option value="${esc(m.id)}"${m.id===presetVal?' selected':''}>${esc(m.id)}</option>`).join('');
    rightHtml = `<select data-role="map-val">${rightOpts}<option value="">自定义...</option></select>`;
  } else {
    rightHtml = `<input data-role="map-val" value="${esc(presetVal||'')}" placeholder="实际模型 (先拉取模型列表)">`;
  }
  const d = document.createElement('div');
  d.className = 'mapping-item';
  d.innerHTML = `<select data-role="map-key">${leftOpts}<option value="">自定义...</option></select>
    <span class="arrow">→</span>
    ${rightHtml}
    <button class="btn btn-danger btn-sm" onclick="this.parentElement.remove()">×</button>`;
  const sel = d.querySelector('select');
  const inp = d.querySelector('[data-role=map-val]');
  sel.addEventListener('change', () => {
    if (!sel.value) {
      sel.style.display='none';
      const custom = document.createElement('input');
      custom.setAttribute('data-role','map-key');
      custom.placeholder='输入自定义模型名';
      d.insertBefore(custom, d.querySelector('.arrow'));
      custom.focus();
    }
  });
  el.appendChild(d);
}

function addModel() {
  const el = document.getElementById('modelList');
  const p = el.querySelector('p'); if (p) p.remove();
  const d = document.createElement('div');
  d.className = 'model-item';
  if (upstreamModels.length > 0) {
    const opts = upstreamModels.map(m => `<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('');
    d.innerHTML = `<select data-role="model-id" onchange="this.nextElementSibling.value=this.options[this.selectedIndex].text">${opts}</select>
      <input data-role="model-name" placeholder="显示名称">
      <button class="btn btn-danger btn-sm" onclick="this.parentElement.remove()">×</button>`;
  } else {
    d.innerHTML = `<input data-role="model-id" placeholder="模型ID (先拉取模型列表)">
      <input data-role="model-name" placeholder="显示名称">
      <button class="btn btn-danger btn-sm" onclick="this.parentElement.remove()">×</button>`;
  }
  el.appendChild(d);
}

function gatherConfig() {
  config.api_url = document.getElementById('api_url').value.trim();
  config.api_key = document.getElementById('api_key').value.trim();
  config.proxy_url = document.getElementById('proxy_url').value.trim();
  config.model_mapping = {};
  document.querySelectorAll('.mapping-item').forEach(row => {
    const keyEl = row.querySelector('[data-role=map-key]');
    const valEl = row.querySelector('[data-role=map-val]');
    const k = keyEl ? keyEl.value.trim() : '';
    const v = valEl ? valEl.value.trim() : '';
    if (k && v) config.model_mapping[k] = v;
  });
  // model_list auto-generated from mapping
  config.model_list = Object.keys(config.model_mapping).map(k => ({id: k, name: k}));
  return config;
}

async function saveConfig() {
  gatherConfig();
  const r = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(config)});
  const d = await r.json();
  toast(d.success ? '已保存' : '保存失败: ' + d.error, d.success);
}

async function testConnection() {
  gatherConfig();
  const r = await fetch('/api/test', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    api_url: config.api_url, api_key: config.api_key, proxy_url: config.proxy_url
  })});
  const d = await r.json();
  toast(d.success ? '连接成功! ' + (d.model||'') : '连接失败: ' + d.error, d.success);
}

async function fetchModels() {
  gatherConfig();
  const r = await fetch('/api/fetch-models', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    api_url: config.api_url, api_key: config.api_key, proxy_url: config.proxy_url
  })});
  const d = await r.json();
  if (d.success && d.models) {
    upstreamModels = d.models;
    renderMappings();
    renderModels();
    toast('已拉取 ' + d.models.length + ' 个模型，下拉框已更新', true);
  } else {
    toast('拉取失败: ' + (d.error || '未知错误'), false);
  }
}

function toggleKey() {
  const el = document.getElementById('api_key');
  el.type = el.type === 'password' ? 'text' : 'password';
}

function toast(msg, ok) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.style.background = ok ? '#2ea043' : '#da3633';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

function esc(s) { return String(s).replace(/"/g, '&quot;').replace(/</g, '&lt;'); }

load();
</script>
</body>
</html>"""


class BridgeHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            data = ADMIN_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/config":
            data = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/v1/models":
            mapping = cfg.get("model_mapping", {})
            client_names = list(dict.fromkeys(mapping.keys()))  # dedupe, preserve order
            models = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "mimo"} for m in client_names]
            data = json.dumps({"object": "list", "data": models}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._proxy_passthrough(None)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/api/config":
            try:
                new_cfg = json.loads(raw.decode("utf-8"))
                cfg.update(new_cfg)
                save_config()
                self._json_response({"success": True})
            except Exception as e:
                self._json_response({"success": False, "error": str(e)})

        elif self.path == "/api/test":
            try:
                body = json.loads(raw.decode("utf-8"))
                url = body.get("api_url", "").rstrip("/") + "/v1/chat/completions"
                key = body.get("api_key", "")
                proxy = body.get("proxy_url", "")
                test_body = json.dumps({"model": "mimo-v2-omni", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}).encode()
                req = urllib.request.Request(url, data=test_body, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", "Bearer " + key)
                if proxy:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"https": proxy, "http": proxy}),
                        urllib.request.HTTPSHandler(context=create_ssl_context()))
                else:
                    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=create_ssl_context()))
                resp = opener.open(req, timeout=15)
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
                model_used = result.get("model", "unknown")
                self._json_response({"success": True, "model": model_used})
            except Exception as e:
                self._json_response({"success": False, "error": str(e)})

        elif self.path == "/api/fetch-models":
            try:
                body = json.loads(raw.decode("utf-8"))
                url = body.get("api_url", "").rstrip("/") + "/v1/models"
                key = body.get("api_key", "")
                proxy = body.get("proxy_url", "")
                req = urllib.request.Request(url, method="GET")
                req.add_header("Authorization", "Bearer " + key)
                if proxy:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"https": proxy, "http": proxy}),
                        urllib.request.HTTPSHandler(context=create_ssl_context()))
                else:
                    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=create_ssl_context()))
                resp = opener.open(req, timeout=15)
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
                models = result.get("data", [])
                self._json_response({"success": True, "models": models})
            except Exception as e:
                self._json_response({"success": False, "error": str(e)})

        elif self.path == "/v1/responses":
            self._handle_responses(raw)
        elif self.path == "/v1/chat/completions":
            self._proxy_passthrough(raw)
        else:
            self._proxy_passthrough(raw)

    def _handle_responses(self, body):
        try:
            req_body = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        mapping = cfg.get("model_mapping", {})
        model = req_body.get("model", "")
        display_model = model
        model = mapping.get(model, mapping.get(model.upper(), mapping.get(model.lower(), model)))

        chat_req = translate_responses_to_chat(req_body)
        chat_req["model"] = model
        chat_body = json.dumps(chat_req).encode("utf-8")
        resp_id = f"resp_{int(time.time()*1000)}"
        stream = chat_req.get("stream", False)

        log(f"Responses: {display_model} -> {model} stream={stream}")

        try:
            resp = proxy_to_api("/v1/chat/completions", "POST", {"Content-Type": "application/json"}, chat_body)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            log(f"Upstream error: {e.code}")
            self._json_response({"error": {"message": err, "type": "upstream_error", "code": str(e.code)}}, e.code)
            return
        except Exception as e:
            log(f"Error: {e}")
            self._json_response({"error": {"message": str(e), "type": "bridge_error"}}, 502)
            return

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for sse in translate_chat_to_responses_stream(resp, display_model, resp_id):
                    self.wfile.write(sse.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            full = resp.read().decode("utf-8", errors="replace")
            try:
                chat_data = json.loads(full)
            except json.JSONDecodeError:
                chat_data = {"choices": []}
            result = translate_chat_to_responses_nonstream(chat_data, display_model, resp_id)
            data = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def _proxy_passthrough(self, body):
        method = "POST" if body else "GET"
        try:
            resp = proxy_to_api(self.path, method, dict(self.headers), body)
            data = resp.read()
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._json_response({"error": {"message": str(e), "type": "bridge_error"}}, 502)

    def _json_response(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, f, *a):
        pass


def log(msg):
    sys.stdout.write(f"[BRIDGE] {msg}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    load_config()
    port = cfg.get("listen_port", 3080)
    server = http.server.HTTPServer(("127.0.0.1", port), BridgeHandler)
    log(f"MiMo Bridge running on http://127.0.0.1:{port}")
    log(f"  Management UI: http://127.0.0.1:{port}/")
    log(f"  API URL: {cfg['api_url']}")
    log(f"  Proxy:   {cfg.get('proxy_url', 'none')}")
    log(f"  Models:  {', '.join(cfg.get('model_mapping', {}).keys())}")
    server.serve_forever()
