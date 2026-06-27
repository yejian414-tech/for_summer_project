const WS_URL = 'ws://127.0.0.1:8765/ws';
const RECONNECT_DELAY_MS = 3000;
const PING_INTERVAL_MS = 15000;

const caption = document.getElementById('caption');

let socket = null;
let reconnectTimer = null;
let pingTimer = null;
let pendingText = '';

function setCaption(text) {
  caption.textContent = text;
}

function clearTimers() {
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

function scheduleReconnect() {
  if (reconnectTimer) {
    return;
  }

  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, RECONNECT_DELAY_MS);
}

function startPing() {
  clearTimers();
  pingTimer = window.setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send('ping');
    }
  }, PING_INTERVAL_MS);
}

function conciseMessage(message) {
  if (!message || typeof message !== 'string') {
    return '';
  }

  const trimmed = message.trim();
  if (!trimmed) {
    return '';
  }

  return trimmed.length > 80 ? `${trimmed.slice(0, 77)}...` : trimmed;
}

function handleMessage(message) {
  switch (message.type) {
    case 'connected':
      setCaption('Waiting for commentary...');
      break;
    case 'ai_start':
      pendingText = '';
      setCaption('Generating captions...');
      break;
    case 'token':
      if (typeof message.text === 'string') {
        pendingText += message.text;
      }
      break;
    case 'ai_done': {
      const finalText = typeof message.content === 'string' && message.content.trim()
        ? message.content.trim()
        : pendingText.trim();
      setCaption(finalText || 'Waiting for commentary...');
      break;
    }
    case 'error': {
      const detail = conciseMessage(message.message);
      setCaption(detail ? `Commentary error: ${detail}` : 'Commentary error');
      break;
    }
    case 'telemetry_update':
    case 'event_detected':
      break;
    default:
      break;
  }
}

function connect() {
  setCaption('Connecting to commentary service...');
  clearTimers();

  socket = new WebSocket(WS_URL);

  socket.addEventListener('open', () => {
    setCaption('Waiting for commentary...');
    startPing();
  });

  socket.addEventListener('message', (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch {
      setCaption('Commentary error');
    }
  });

  socket.addEventListener('error', () => {
    setCaption('Connection lost');
  });

  socket.addEventListener('close', () => {
    clearTimers();
    setCaption('Connection lost');
    scheduleReconnect();
  });
}

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && window.torcsOverlay) {
    window.torcsOverlay.hide();
  }
});

connect();
