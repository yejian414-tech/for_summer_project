#!/bin/bash
# TORCS 比赛解说 — 一键启动脚本
# 用法：bash start.sh [torcs|midware|all]
# 不带参数默认启动全部

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

start_midware() {
  echo "[midware] 启动解说中间件..."
  cd "$ROOT/midware"
  if [ ! -d ".venv" ]; then
    echo "[midware] 创建虚拟环境..."
    python3 -m venv .venv
  fi
  source .venv/bin/activate
  pip install -q -r requirements.txt
  echo "[midware] 服务运行在 http://localhost:8765"
  python server.py &
  MIDWARE_PID=$!
  echo "[midware] PID=$MIDWARE_PID"
  cd "$ROOT"
}

start_torcs() {
  echo "[torcs] 设置权限..."
  chmod +x "$ROOT/src/linux/torcs"

  echo "[torcs] 配置环境变量..."
  export TORCS_PLAYER_UDP_HOST=127.0.0.1
  export TORCS_PLAYER_UDP_PORT=3101
  export TORCS_PLAYER_LOG_DIR="$ROOT/logs"
  mkdir -p "$ROOT/logs"

  echo "[torcs] 启动游戏..."
  "$ROOT/src/linux/torcs"
}

case "${1:-all}" in
  midware)
    start_midware
    wait
    ;;
  torcs)
    start_torcs
    ;;
  all)
    start_midware
    sleep 2
    echo ""
    echo "========================================"
    echo "  解说页面: http://localhost:8765"
    echo "========================================"
    echo ""
    start_torcs
    ;;
  *)
    echo "用法: bash start.sh [torcs|midware|all]"
    exit 1
    ;;
esac
