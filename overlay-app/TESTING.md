# TORCS AI Overlay Testing Guide

This document explains how to test the `TORCS AI Overlay` Electron app.

The overlay is an English-only floating caption HUD. It connects to:

```text
ws://127.0.0.1:8765/ws
```

## 1. Test Goals

Use this guide to verify:

- The Electron app installs and starts.
- The window is frameless, transparent, always on top, and positioned near the bottom center.
- The UI shows only caption/status text, with no toolbar or buttons.
- The WebSocket state flow works correctly.
- The app reconnects when the commentary service is unavailable.
- The `Esc` key hides the overlay.

## 2. Expected Project Files

From the repository root:

```bash
cd /home/ubu/test/torcs-1.3.7
find overlay-app -maxdepth 3 -type f | sort
```

Expected important files:

```text
overlay-app/package.json
overlay-app/electron/main.js
overlay-app/electron/preload.js
overlay-app/src/index.html
overlay-app/src/styles.css
overlay-app/src/renderer.js
overlay-app/TESTING.md
```

`node_modules/` and `package-lock.json` may also exist after `npm install`.

## 3. Environment Check

The app needs Node.js and npm.

Run:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
which node
which npm
node --version
npm --version
```

### Correct WSL Setup

If you are running inside WSL, `which node` and `which npm` should point to Linux paths, for example:

```text
/home/ubu/.nvm/versions/node/v20.x.x/bin/node
/home/ubu/.nvm/versions/node/v20.x.x/bin/npm
```

or:

```text
/usr/bin/node
/usr/bin/npm
```

### Incorrect WSL Setup

If they point to Windows paths, for example:

```text
/mnt/c/...
/mnt/d/...
```

then WSL is calling Windows Node/npm. This can break Electron installation with errors involving:

```text
UNC paths are not supported
Cannot find module 'C:\Windows\install.js'
```

Fix this by installing Linux Node inside WSL. One common option is `nvm`:

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install --lts
nvm use --lts
```

Then check again:

```bash
which node
which npm
```

## 4. Clean Install Test

From the overlay app directory:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
npm install
```

Expected result:

- Install completes without `npm error`.
- `node_modules/` is created.
- `package-lock.json` is created or updated.

Warnings such as this are not fatal:

```text
npm warn deprecated boolean@3.2.0
```

If a previous install failed because Windows npm was used from WSL, clean the install output and retry after fixing Node:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
rm -rf node_modules package-lock.json
npm install
```

If `rm -rf node_modules` fails with permission errors, close Windows Explorer, VS Code, and any terminal that may be using files under `node_modules`, then retry.

## 5. Static Syntax Check

Run:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
node --check electron/main.js
node --check electron/preload.js
node --check src/renderer.js
```

Expected result:

- No output.
- Exit code is `0`.

If there is a syntax error, Node will print the file and line number.

## 6. Start Without Backend

Make sure the Python commentary backend is not running.

Then start the overlay:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
npm start
```

Expected result:

- A floating overlay window appears.
- It is around `900px` wide and `160px` tall.
- It is near the bottom center of the primary display.
- It has no title bar, toolbar, close button, or visible browser chrome.
- It shows:

```text
Connection lost
```

This is expected because the WebSocket service is unavailable.

The app should keep trying to reconnect every 3 seconds.

## 7. Start With Real Backend

Start the existing Python commentary backend that exposes:

```text
ws://127.0.0.1:8765/ws
```

Then start the overlay:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
npm start
```

Expected initial result:

```text
Waiting for commentary...
```

When the backend sends commentary events, the expected UI states are:

| Backend message | Expected caption |
| --- | --- |
| `{ "type": "connected", "stats": ... }` | `Waiting for commentary...` |
| `{ "type": "ai_start" }` | `Generating captions...` |
| `{ "type": "token", "text": "..." }` | No immediate display change; text is buffered |
| `{ "type": "ai_done", "content": "..." }` | Final English commentary text |
| `{ "type": "error", "message": "..." }` | `Commentary error: ...` |
| `{ "type": "telemetry_update", ... }` | Ignored |
| `{ "type": "event_detected", ... }` | Ignored |

## 8. Manual WebSocket Mock Test

Use this when the real backend is not ready or when you want predictable test messages.

### 8.1 Install Python Test Dependency

From anywhere:

```bash
python3 -m pip install websockets
```

If you use a virtual environment, activate it first.

### 8.2 Create A Temporary Mock Server

Create this file outside `overlay-app`, for example:

```bash
cd /home/ubu/test/torcs-1.3.7
nano test_overlay_ws.py
```

Paste:

```python
import asyncio
import json
import websockets


async def log_ping(websocket):
    async for message in websocket:
        if message == "ping":
            print("Received ping from overlay")


async def handler(websocket):
    await websocket.send(json.dumps({
        "type": "connected",
        "stats": {"source": "mock"}
    }))

    ping_task = asyncio.create_task(log_ping(websocket))

    await asyncio.sleep(1)
    await websocket.send(json.dumps({"type": "ai_start"}))

    await asyncio.sleep(1)
    await websocket.send(json.dumps({
        "type": "token",
        "text": "Brake late into turn one, "
    }))
    await websocket.send(json.dumps({
        "type": "token",
        "text": "then ease back onto the throttle."
    }))

    await asyncio.sleep(1)
    await websocket.send(json.dumps({
        "type": "ai_done",
        "content": "Brake late into turn one, then ease back onto the throttle."
    }))

    await asyncio.sleep(3)
    await websocket.send(json.dumps({
        "type": "error",
        "message": "Mock commentary fault"
    }))

    await ping_task


async def main():
    async with websockets.serve(handler, "127.0.0.1", 8765):
        print("Mock WebSocket server running at ws://127.0.0.1:8765/ws")
        await asyncio.Future()


asyncio.run(main())
```

### 8.3 Run The Mock Server

In terminal 1:

```bash
cd /home/ubu/test/torcs-1.3.7
python3 test_overlay_ws.py
```

Expected terminal output:

```text
Mock WebSocket server running at ws://127.0.0.1:8765/ws
```

### 8.4 Run The Overlay

In terminal 2:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
npm start
```

Expected overlay sequence:

```text
Waiting for commentary...
Generating captions...
Brake late into turn one, then ease back onto the throttle.
Commentary error: Mock commentary fault
```

The mock server should also print:

```text
Received ping from overlay
```

This confirms the 15-second ping behavior.

## 9. Reconnect Test

Use either the real backend or the mock backend.

1. Start the backend.
2. Start the overlay.
3. Confirm the overlay shows:

```text
Waiting for commentary...
```

4. Stop the backend with `Ctrl+C`.
5. Confirm the overlay changes to:

```text
Connection lost
```

6. Start the backend again on the same port.
7. Wait up to 3 seconds.
8. Confirm the overlay returns to:

```text
Waiting for commentary...
```

## 10. Window Behavior Test

Start the overlay:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
npm start
```

Verify:

- The window has no title bar.
- The window has no browser controls.
- The UI contains no close button.
- The UI contains no toolbar.
- The panel can be dragged by dragging the caption area.
- The overlay stays above normal application windows.
- Pressing `Esc` hides the overlay.

Note: after pressing `Esc`, the current version has no tray icon or global shortcut to bring the window back. Restart with `npm start` if needed.

## 11. Visual Design Test

Verify the overlay visually:

- One dark rounded panel only.
- Background color appears close to `rgba(34, 34, 34, 0.92)`.
- Corners are strongly rounded, around `24px`.
- Shadow is strong but soft.
- Text is centered.
- Text is English only.
- Caption font size is around `28px`.
- Caption font weight is bold.
- Long English captions wrap inside the panel.
- There is no Chinese UI text.
- There are no controls such as `Translate`, `Show original`, `Font size`, or `Expand subtitles`.

## 12. Long Caption Test

Use the mock server or real backend to send a long `ai_done` message:

```json
{
  "type": "ai_done",
  "content": "Hold the outside line through the fast right-hander, keep the steering calm, and prepare to brake hard once the car is fully straight."
}
```

Expected result:

- The sentence wraps cleanly.
- Text remains centered.
- Text does not overflow out of the panel.
- No scrollbar appears.

## 13. Security Check

Inspect `electron/main.js`:

```bash
cd /home/ubu/test/torcs-1.3.7/overlay-app
grep -n "contextIsolation\\|nodeIntegration\\|preload" electron/main.js
```

Expected result:

```text
contextIsolation: true
nodeIntegration: false
preload: ...
```

Inspect `electron/preload.js`:

```bash
cat electron/preload.js
```

Expected behavior:

- Only a minimal `hide` API is exposed.
- No broad Node.js APIs are exposed to the renderer.

Inspect `src/renderer.js`:

```bash
grep -n "textContent\\|innerHTML" src/renderer.js
```

Expected result:

- `textContent` is used for display.
- `innerHTML` is not used.

## 14. Troubleshooting

### npm install fails with UNC path errors

Symptom:

```text
UNC paths are not supported
Cannot find module 'C:\Windows\install.js'
```

Cause:

WSL is using Windows Node/npm.

Fix:

Install Linux Node/npm inside WSL, then reinstall dependencies:

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install --lts
nvm use --lts

cd /home/ubu/test/torcs-1.3.7/overlay-app
rm -rf node_modules package-lock.json
npm install
```

### Overlay shows Connection lost

Cause:

The backend is not running or is not listening on:

```text
ws://127.0.0.1:8765/ws
```

Fix:

Start the backend, then wait up to 3 seconds for reconnect.

### Port 8765 is already in use

Run:

```bash
ss -ltnp | grep 8765
```

Stop the process using the port, or change the backend port and update `WS_URL` in:

```text
overlay-app/src/renderer.js
```

### Electron window does not appear in WSL

Possible causes:

- WSL GUI support is unavailable.
- Display forwarding is not configured.
- Electron was installed for the wrong platform.

Recommended approach:

- On Windows 11 with WSLg, use Linux Node/npm inside WSL and run `npm start`.
- Otherwise, run the app from native Windows Node in a Windows filesystem path, not from `\\wsl.localhost\...`.

### Esc hides the window and it cannot be restored

This is expected for the current version. There is no system tray and no global shortcut yet.

Restart the app:

```bash
npm start
```

## 15. Final Acceptance Checklist

Mark the overlay as passing if all items are true:

- `npm install` succeeds.
- `npm start` opens the overlay.
- The window is frameless and transparent.
- The window is always on top.
- The window appears near the bottom center.
- The caption panel is draggable.
- The UI has no toolbar, no close button, and no browser chrome.
- Initial connected state shows `Waiting for commentary...`.
- Missing backend state shows `Connection lost`.
- `ai_start` shows `Generating captions...`.
- `token` messages are buffered.
- `ai_done` shows final English commentary.
- `error` shows `Commentary error` with a concise message when available.
- `telemetry_update` and `event_detected` do not change the caption.
- Long English captions wrap cleanly.
- Pressing `Esc` hides the overlay.
- No Chinese text appears in the overlay UI.
