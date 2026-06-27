# Legacy TORCS Data Middleware

此目录是旧版独立数据中间层，当前 `midware` 解说服务不依赖这里的代码。

当前主解说链路使用：

```text
midware/telemetry.py
midware/commentary_engine.py
midware/context_manager.py
midware/commentary.py
```

## 当前状态

`middleware/` 可以单独启动为一个只负责数据缓存和查询的 FastAPI 服务：

```bash
cd middleware
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

它提供：

```text
/state
/history
/rankings
/rankings/history
/health
```

但当前项目的解说 UI、自动解说、LM Studio 调用、TORCS UDP 接入和 WebSocket 推送均已合并到 `midware/` 中。桌面字幕悬浮窗位于 `overlay-app/`，它连接 `midware/commentary.py` 暴露的 `ws://127.0.0.1:8765/ws`。

## 建议

除非需要复用旧版 REST 数据服务，否则新开发请使用 `midware/`。

后续确认没有兼容需求后，可以删除此目录。
