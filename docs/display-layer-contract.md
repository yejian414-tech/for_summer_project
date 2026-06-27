# TORCS AI Display Layer Contract

This document defines the standard display path for user-facing AI output in this project.

All new AI features should use this path:

```text
AI feature
  -> midware display broadcast
  -> WebSocket ws://127.0.0.1:8765/ws
  -> overlay-app
```

The goal is to keep captions, voice, connection settings, and race-HUD presentation consistent across the project.

## Scope

This contract applies to AI-generated output that a driver, presenter, or viewer should see or hear during a TORCS session, including:

- Live race commentary.
- Driving advice.
- Race engineer prompts.
- Incident analysis.
- Strategy alerts.
- Demo or classroom explanation text.

It does not apply to developer-only logs, debug traces, unit-test output, or backend health checks.

## Display Ownership

`overlay-app` owns:

- Caption display.
- Voice playback.
- Floating window behavior.
- Overlay connection settings.
- Model/API settings UI that talks to `midware`.
- User-facing HUD presentation.

AI feature code owns:

- Collecting or analyzing data.
- Building prompts or structured payloads.
- Calling the model, directly or through shared midware helpers.
- Sending display messages through the standard WebSocket broadcast.

AI feature code should not create separate caption windows, browser toolbars, Tkinter popups, terminal-only presentation paths, or feature-specific display overlays for user-facing output.

## WebSocket Endpoint

The standard endpoint is:

```text
ws://127.0.0.1:8765/ws
```

`midware/commentary.py` currently exposes this endpoint and keeps track of connected clients.

## Required Message Types

### Connected

Sent by the backend when a client connects.

```json
{
  "type": "connected",
  "stats": {},
  "has_telemetry": true
}
```

Overlay behavior:

- Shows `Waiting for commentary...`.
- Does not speak.

### AI Start

Sent when an AI response begins.

```json
{
  "type": "ai_start"
}
```

Overlay behavior:

- Clears pending streamed text.
- Shows `Generating captions...`.
- Stops any currently playing voice.

### Token

Sent for streamed model output.

```json
{
  "type": "token",
  "text": "Brake late into "
}
```

Overlay behavior:

- Buffers the token text.
- Does not update the visible caption yet.
- Does not speak.

### AI Done

Sent when the AI response is complete.

```json
{
  "type": "ai_done",
  "content": "Brake late into turn one, then ease back onto the throttle.",
  "stats": {}
}
```

Overlay behavior:

- Displays `content` if present.
- Falls back to buffered `token` text if `content` is empty.
- Speaks the final text if voice is enabled.

### Error

Sent when a user-facing AI action fails.

```json
{
  "type": "error",
  "message": "API 500: model unavailable"
}
```

Overlay behavior:

- Shows `Commentary error` plus a concise message.
- Does not speak.

## Existing Non-Display Messages

These messages may continue to be broadcast for dashboards, logs, or future UI, but the current overlay ignores them:

```json
{ "type": "telemetry_update" }
{ "type": "event_detected" }
{ "type": "user_msg" }
{ "type": "pong" }
```

Do not rely on these messages to show captions in `overlay-app`.

## Language Policy

The overlay caption HUD is English-first.

For content that should appear in the overlay, prefer final English text in:

```json
{
  "type": "ai_done",
  "content": "Final English caption."
}
```

If a feature needs bilingual or structured output later, add explicit fields while preserving `content` as the display-safe English caption:

```json
{
  "type": "ai_done",
  "content": "Final English caption.",
  "content_zh": "中文解说。",
  "source": "commentary"
}
```

The overlay currently displays only `content`.

## Recommended Optional Fields

Future AI features may include these optional fields. The current overlay safely ignores unknown fields.

```json
{
  "type": "ai_done",
  "source": "commentary",
  "priority": 2,
  "content": "Final English caption.",
  "stats": {}
}
```

Suggested meaning:

- `source`: feature identifier, such as `commentary`, `engineer`, `strategy`, or `incident_analysis`.
- `priority`: display priority, where higher values may later interrupt lower-priority messages.
- `stats`: token/context metadata for diagnostics.

Do not require these optional fields until the overlay implements arbitration between multiple AI features.

## Implementation Pattern

In `midware/commentary.py`, the existing path already follows this contract:

```python
await broadcast({"type": "ai_start"})
await broadcast({"type": "token", "text": token})
await broadcast({"type": "ai_done", "content": reply, "stats": ctx_mgr.stats()})
await broadcast({"type": "error", "message": str(e)})
```

New AI features should use the same message types. If a feature runs outside `midware`, route its display output back through `midware` instead of opening a separate UI.

## Testing Expectations

Any new feature using the display layer should verify:

- `ai_start` shows `Generating captions...`.
- streamed `token` messages do not create partial visible captions.
- `ai_done.content` appears in the overlay.
- voice playback occurs only on final `ai_done` text when enabled.
- `error.message` appears as a concise error state.
- telemetry and event messages do not disturb the current caption.

Use `overlay-app/TESTING.md` for the full overlay test flow.
