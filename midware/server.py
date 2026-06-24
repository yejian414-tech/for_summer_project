"""
TORCS 比赛解说中间件 — 主服务

功能：
  · REST API：AI 配置 / Context 配置 / 手动触发解说
  · WebSocket：实时推送解说流 & TORCS 数据
  · UDP 监听器（后台线程）：接收 TORCS human 模块推送的遥测数据（端口 3101）
  · CSV 文件读取：从历史 CSV 回放生成解说

启动：
    pip install fastapi uvicorn httpx aiofiles
    python server.py
"""

import asyncio
import csv
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from commentary_engine import CommentaryConfig, CommentaryEngine
from context_manager import ContextConfig, ContextManager
from telemetry import TelemetryStore, start_udp_listener

# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# -- AI API 配置（可在 UI 中修改）--
api_config: dict[str, Any] = {
    "provider":  "openai",          # "openai" | "anthropic" | "ollama"
    "base_url":  "https://api.openai.com/v1",
    "api_key":   "",
    "model":     "gpt-4o-mini",
    "temperature": 0.8,
    "stream":    True,
}

# -- Context 配置 --
ctx_cfg   = ContextConfig()
ctx_mgr   = ContextManager(ctx_cfg)

# -- 遥测数据缓存（UDP 线程写入，主线程读） --
telemetry_store = TelemetryStore(window_seconds=30.0)

# -- WebSocket 客户端集合 --
ws_clients: set[WebSocket] = set()

# -- 自动解说配置 --
commentary_engine = CommentaryEngine(
    CommentaryConfig(
        mode="interval",
        baseline_interval=10.0,
        event_cooldown=1.0,
        window_seconds=6.0,
        dedupe_seconds=10.0,
        max_words=45,
    )
)
_auto_task: asyncio.Task | None = None

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="TORCS 比赛解说中间件")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# 广播消息给所有 WebSocket 客户端
# ---------------------------------------------------------------------------

async def broadcast(msg: dict):
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ---------------------------------------------------------------------------
# AI 调用
# ---------------------------------------------------------------------------

async def call_ai(messages: list[dict]) -> str:
    """
    调用 AI API 并返回完整回复文本。
    同时通过 WebSocket 流式推送 token。
    """
    provider = api_config["provider"]
    key      = api_config["api_key"]
    base_url = api_config["base_url"].rstrip("/")
    model    = api_config["model"]
    temp     = api_config["temperature"]
    do_stream = api_config["stream"]

    headers = {"Content-Type": "application/json"}

    # ---- Anthropic ----
    if provider == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        system_content = next((m["content"] for m in messages if m["role"] == "system"), "")
        filtered = [m for m in messages if m["role"] != "system"]
        payload = {
            "model": model,
            "max_tokens": ctx_cfg.max_response_tokens,
            "temperature": temp,
            "system": system_content,
            "messages": filtered,
            "stream": do_stream,
        }
        url = f"{base_url}/messages"

    # ---- OpenAI-compatible / Ollama ----
    else:
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "max_tokens": ctx_cfg.max_response_tokens,
            "temperature": temp,
            "messages": messages,
            "stream": do_stream,
        }
        url = f"{base_url}/chat/completions"

    full_text = ""

    async with httpx.AsyncClient(timeout=60) as client:
        if do_stream:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise RuntimeError(f"API {resp.status_code}: {body.decode()[:300]}")
                async for line in resp.aiter_lines():
                    token = _extract_stream_token(line, provider)
                    if token:
                        full_text += token
                        await broadcast({"type": "token", "text": token})
        else:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"API {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if provider == "anthropic":
                full_text = data["content"][0]["text"]
            else:
                full_text = data["choices"][0]["message"]["content"]
            await broadcast({"type": "token", "text": full_text})

    return full_text


def _extract_stream_token(line: str, provider: str) -> str:
    """从 SSE 数据行提取单个 token 文本。"""
    if not line.startswith("data:"):
        return ""
    chunk = line[5:].strip()
    if chunk in ("[DONE]", ""):
        return ""
    try:
        data = json.loads(chunk)
        if provider == "anthropic":
            if data.get("type") == "content_block_delta":
                return data.get("delta", {}).get("text", "")
        else:
            return data["choices"][0].get("delta", {}).get("content", "") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 解说生成（核心流程）
# ---------------------------------------------------------------------------

async def generate_commentary(
    telemetry: dict | None = None,
    rankings: list | None = None,
    manual_prompt: str | None = None,
    event_payload: dict | None = None,
    history_mode: str = "full",
) -> str:
    """
    构建上下文 → 调用 AI → 存入历史 → 广播。
    """
    if not api_config["api_key"] and api_config["provider"] != "ollama":
        raise ValueError("API Key 未设置")

    # 1. 构造 user message
    if event_payload:
        user_content = ctx_mgr.format_event_prompt(event_payload)
        history_content = ctx_mgr.format_event_history_entry(event_payload)
    elif manual_prompt:
        user_content = manual_prompt
        history_content = user_content
    elif telemetry:
        user_content = ctx_mgr.format_telemetry(telemetry, rankings)
        history_content = user_content
    else:
        raise ValueError("没有遥测数据或手动 prompt")

    # 2. 加入历史
    if history_mode != "assistant_only":
        ctx_mgr.add_user(history_content)

    # 3. 广播 user 消息（用于 UI 显示）
    await broadcast({
        "type": "user_msg",
        "content": user_content,
        "stats": ctx_mgr.stats(),
    })

    # 4. 构建发送给 AI 的消息列表（已裁剪）
    messages = ctx_mgr.build_messages()

    # 5. 调用 AI
    await broadcast({"type": "ai_start"})
    try:
        reply = await call_ai(messages)
    except Exception as e:
        await broadcast({"type": "error", "message": str(e)})
        raise

    # 6. 把 AI 回复存入历史
    ctx_mgr.add_assistant(reply)

    # 7. 广播完成信号
    await broadcast({
        "type": "ai_done",
        "content": reply,
        "stats": ctx_mgr.stats(),
    })

    return reply


# ---------------------------------------------------------------------------
# 自动解说定时任务
# ---------------------------------------------------------------------------

async def _auto_commentary_loop():
    while True:
        cfg = commentary_engine.config
        if cfg.mode == "off":
            await asyncio.sleep(1)
            continue

        await asyncio.sleep(0.5 if cfg.mode in ("event", "hybrid") else max(1.0, cfg.baseline_interval))

        t, r = telemetry_store.latest()
        if t is None:
            continue

        try:
            frames = telemetry_store.recent_frames(cfg.window_seconds)
            decision = commentary_engine.next_decision(frames, r)
            if decision is None:
                continue

            await broadcast({"type": "event_detected", "event": decision.event, "payload": decision.payload})
            reply = await generate_commentary(
                t,
                r,
                event_payload=decision.payload,
                history_mode="summary",
            )
            if not commentary_engine.should_emit_text(reply, float(decision.payload.get("event_time", 0.0))):
                log.info("重复解说已被去重记录标记")
        except Exception as e:
            log.warning(f"自动解说失败: {e}")


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>请将 index.html 放入 static/ 目录</h1>")


@app.get("/api/config")
async def get_config():
    return {
        "api": {**api_config, "api_key": "***" if api_config["api_key"] else ""},
        "context": {
            "max_context_tokens":  ctx_cfg.max_context_tokens,
            "max_response_tokens": ctx_cfg.max_response_tokens,
            "trim_strategy":       ctx_cfg.trim_strategy,
            "chat_template":       ctx_cfg.chat_template,
            "commentator_persona": ctx_cfg.commentator_persona,
            "included_fields":     ctx_cfg.included_fields,
            "include_rankings":    ctx_cfg.include_rankings,
        },
        "auto_interval": commentary_engine.config.baseline_interval if commentary_engine.config.mode != "off" else 0,
        "commentary": {
            "mode": commentary_engine.config.mode,
            "baseline_interval": commentary_engine.config.baseline_interval,
            "event_cooldown": commentary_engine.config.event_cooldown,
            "window_seconds": commentary_engine.config.window_seconds,
            "dedupe_seconds": commentary_engine.config.dedupe_seconds,
            "max_words": commentary_engine.config.max_words,
        },
    }


@app.post("/api/config/api")
async def update_api_config(body: dict):
    """更新 AI API 配置（POST JSON）。"""
    for k in ("provider","base_url","api_key","model","temperature","stream"):
        if k in body:
            api_config[k] = body[k]
    return {"ok": True}


@app.post("/api/config/context")
async def update_context_config(body: dict):
    """更新上下文配置。"""
    global ctx_cfg, ctx_mgr
    for k, v in body.items():
        if hasattr(ctx_cfg, k):
            setattr(ctx_cfg, k, v)
    ctx_mgr.config = ctx_cfg
    return {"ok": True, "stats": ctx_mgr.stats()}


@app.post("/api/config/auto_interval")
async def update_auto_interval(body: dict):
    interval = float(body.get("interval", 0))
    commentary_engine.config.baseline_interval = interval
    commentary_engine.config.mode = "interval" if interval > 0 else "off"
    return {"ok": True, "interval": interval, "mode": commentary_engine.config.mode}


@app.get("/api/commentary/config")
async def get_commentary_config():
    return {
        "mode": commentary_engine.config.mode,
        "baseline_interval": commentary_engine.config.baseline_interval,
        "event_cooldown": commentary_engine.config.event_cooldown,
        "window_seconds": commentary_engine.config.window_seconds,
        "dedupe_seconds": commentary_engine.config.dedupe_seconds,
        "max_words": commentary_engine.config.max_words,
    }


@app.post("/api/commentary/config")
async def update_commentary_config(body: dict):
    commentary_engine.update_config(body)
    return {"ok": True, "config": await get_commentary_config()}


@app.post("/api/commentary/manual")
async def manual_commentary(body: dict):
    """手动触发一次解说（可附带自定义 prompt）。"""
    t, r = telemetry_store.latest()

    prompt = body.get("prompt") or None
    asyncio.create_task(generate_commentary(t, r, manual_prompt=prompt))
    return {"ok": True, "queued": True}


@app.post("/api/commentary/clear")
async def clear_history():
    ctx_mgr.clear_history()
    return {"ok": True, "stats": ctx_mgr.stats()}


@app.get("/api/telemetry")
async def get_telemetry():
    telemetry, rankings = telemetry_store.latest()
    return {"telemetry": telemetry, "rankings": rankings}


@app.get("/api/telemetry/history")
async def get_telemetry_history(seconds: float | None = None):
    return {"frames": telemetry_store.recent_frames(seconds), "rankings": telemetry_store.recent_rankings(seconds)}


@app.post("/api/telemetry/push")
async def push_telemetry(body: dict):
    """手动 POST 遥测数据（测试用）。"""
    telemetry = body.get("telemetry", {})
    rankings = body.get("rankings", [])
    telemetry_store.push(telemetry, rankings)
    await broadcast({"type": "telemetry_update", "telemetry": telemetry, "rankings": rankings})
    return {"ok": True}


@app.get("/api/events/recent")
async def get_recent_events():
    return {"events": commentary_engine.recent_events}


@app.post("/api/csv/load")
async def load_csv(body: dict):
    """
    从 CSV 文件路径读取数据并触发解说。
    body: { "path": "/path/to/player-1-*.csv", "rankings_path": "..." }
    """
    csv_path = Path(body.get("path", ""))
    rank_path = body.get("rankings_path")

    if not csv_path.exists():
        return JSONResponse({"error": f"文件不存在: {csv_path}"}, status_code=404)

    # 读取最后一行（最新时刻）
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: _try_float(v) for k, v in row.items()})

    if not rows:
        return JSONResponse({"error": "CSV 为空"}, status_code=400)

    t = rows[-1]

    # 排名文件
    r = []
    if rank_path:
        rp = Path(rank_path)
        if rp.exists():
            with open(rp, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 取最新 sim_time 的行
                    r.append({k: _try_float(v) for k, v in row.items()})
            # 过滤最新时刻
            if r:
                latest_t = max(float(x.get("sim_time",0)) for x in r)
                r = [x for x in r if abs(float(x.get("sim_time",0)) - latest_t) < 0.01]

    telemetry_store.push(t, r or None)

    asyncio.create_task(generate_commentary(t, r or None))
    return {"ok": True, "rows_loaded": len(rows), "latest_sim_time": t.get("sim_time")}


def _try_float(v: str):
    try:
        return float(v)
    except (ValueError, TypeError):
        return v


@app.get("/api/stats")
async def get_stats():
    return ctx_mgr.stats()


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    log.info(f"WebSocket 已连接，当前客户端数: {len(ws_clients)}")
    try:
        # 发送初始状态
        await ws.send_json({
            "type": "connected",
            "stats": ctx_mgr.stats(),
            "has_telemetry": telemetry_store.has_telemetry(),
        })
        while True:
            # 保持连接，接收 ping
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_clients.discard(ws)
        log.info(f"WebSocket 断开，剩余客户端: {len(ws_clients)}")


# ---------------------------------------------------------------------------
# 启动事件
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    # 启动 UDP 监听线程
    start_udp_listener(
        telemetry_store,
        port=3101,
        on_error=lambda exc: log.error(f"UDP 监听器错误: {exc}"),
    )
    log.info("UDP 监听器启动 0.0.0.0:3101")

    # 启动自动解说循环
    global _auto_task
    _auto_task = asyncio.create_task(_auto_commentary_loop())
    log.info("服务启动完成 → http://localhost:8765")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=False)
