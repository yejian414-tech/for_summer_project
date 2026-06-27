# TORCS 比赛解说中间件

`midware` 是 TORCS 的实时 AI 比赛解说服务。它接收 TORCS human 数据采集器发出的 UDP 遥测，缓存最近一段比赛状态，检测关键比赛事件，然后调用 OpenAI-compatible API（例如 LM Studio）生成解说，并通过 WebSocket 流式推送到网页端和 Electron 桌面字幕悬浮窗。

项目的标准 AI 显示链路是 `midware WebSocket -> overlay-app`。后续新增的 AI 功能如果需要面向用户显示或朗读内容，应复用 `ai_start` / `token` / `ai_done` / `error` 消息协议，不应单独创建新的字幕 UI。完整协议见：

```text
../docs/display-layer-contract.md
```

## 功能概览

当前解说功能由四部分组成：

```text
TORCS UDP :3101
  -> telemetry.py
     解析车辆遥测和排名数据，保存滑动窗口
  -> commentary_engine.py
     检测事件、设置优先级、冷却和去重，生成结构化 payload
  -> context_manager.py
     管理解说员人设、历史上下文和 token 预算
  -> commentary.py + static/index.html / static/index2.html
     FastAPI 服务、REST API、WebSocket 流式输出和网页配置界面
  -> ../overlay-app
     Electron 桌面悬浮字幕 HUD，连接 ws://127.0.0.1:8765/ws
```

已支持的自动解说事件：

| 事件 | 说明 |
| --- | --- |
| `position_change` | 名次变化 |
| `contact` | 损伤值突增，疑似碰撞 |
| `off_track` | `trackPos` 超出赛道边界 |
| `lap_complete` | 完成一圈 |
| `battle` | 与前车距离过近，形成追击 |
| `pace_surge` | 短时间明显加速 |
| `pace_update` | 固定间隔节奏播报 |

自动解说模式：

| 模式 | 说明 |
| --- | --- |
| `off` | 关闭自动解说 |
| `interval` | 固定间隔生成解说 |
| `event` | 只在事件触发时生成解说 |
| `hybrid` | 事件优先，同时保留固定间隔节奏播报 |

## 目录结构

```text
midware/
├── commentary.py          # FastAPI 主服务，REST/WebSocket/调度
├── telemetry.py           # UDP 解析、车辆帧缓存、排名缓存
├── commentary_engine.py   # 事件检测、冷却、去重、payload 构造
├── context_manager.py     # 解说员 prompt、历史上下文、token 裁剪
├── requirements.txt       # Python 依赖
└── static/
    ├── index.html         # 文字解说 Web UI
    └── index2.html        # 语音解说 Web UI
```

桌面悬浮字幕窗位于仓库根目录的 `overlay-app/`：

```text
overlay-app/
├── package.json
├── electron/
│   ├── main.js            # Electron 主进程，创建透明置顶窗口
│   └── preload.js         # 最小 IPC 暴露，仅支持隐藏窗口
├── src/
│   ├── index.html         # 字幕面板 DOM
│   ├── styles.css         # 游戏 HUD 风格
│   ├── renderer.js        # WebSocket 状态机和语音播放
│   ├── settings.html      # 独立设置窗口
│   ├── settings.css
│   └── settings.js
└── TESTING.md             # Overlay 完整测试文档
```

## 一、编译 TORCS

在项目根目录执行。项目推荐安装到仓库内的 `BUILD` 目录，不写入系统目录。

```bash
cd ~/test/torcs-1.3.7

sudo apt-get update
sudo apt-get install -y \
  libglib2.0-dev \
  libgl1-mesa-dev \
  libglu1-mesa-dev \
  freeglut3-dev \
  libplib-dev \
  libopenal-dev \
  libalut-dev \
  libxi-dev \
  libxmu-dev \
  libxrender-dev \
  libxrandr-dev \
  libpng-dev \
  libvorbis-dev
```

编译并安装到 `BUILD`：

```bash
export CFLAGS="-fPIC"
export CPPFLAGS="$CFLAGS"
export CXXFLAGS="$CFLAGS"

./configure --prefix="$(pwd)/BUILD"
make -j"$(nproc)"
make install
make datainstall
```

编译完成后，TORCS 启动入口是：

```bash
./BUILD/bin/torcs
```

## 二、准备 LM Studio API

在 LM Studio 中：

1. 加载模型，例如 `granite-4.1-8b`。
2. 进入 Developer / Local Server。
3. 启动本地 server。
4. 用 `curl` 确认模型列表。

如果 LM Studio 在 Windows 主机上，WSL 中通常需要使用 Windows 主机 IP。例如：

```bash
curl http://172.24.160.1:1234/v1/models
```

### 在 WSL 中查找 LM Studio 地址

如果 LM Studio 运行在 Windows，`127.0.0.1` 在 WSL 里不一定能访问到 Windows 侧服务。可以用下面几种方式找到 Windows 主机 IP。

方式 1：查看默认网关，通常就是 Windows 主机地址：

```bash
ip route | awk '/default/ {print $3}'
```

假设输出是：

```text
172.24.160.1
```

那么 LM Studio Base URL 通常就是：

```text
http://172.24.160.1:1234/v1
```

方式 2：从 WSL 的 DNS 配置里查看 nameserver：

```bash
cat /etc/resolv.conf | grep nameserver
```

如果输出：

```text
nameserver 172.24.160.1
```

同样可以尝试：

```bash
curl http://172.24.160.1:1234/v1/models
```

方式 3：直接试几个候选地址：

```bash
curl http://127.0.0.1:1234/v1/models
curl http://localhost:1234/v1/models
curl http://$(ip route | awk '/default/ {print $3}'):1234/v1/models
```

哪个能返回模型列表，网页里的 `Base URL` 就填哪个地址加 `/v1`。

返回示例：

```json
{
  "data": [
    {
      "id": "granite-4.1-8b",
      "object": "model",
      "owned_by": "organization_owner"
    }
  ],
  "object": "list"
}
```

此时网页端 AI 配置填写：

```text
提供商: OpenAI / 兼容接口
Base URL: http://172.24.160.1:1234/v1
模型: granite-4.1-8b
API Key: lm-studio
```

说明：LM Studio 通常不校验 API Key，但当前 `commentary.py` 对 OpenAI-compatible provider 要求 API Key 非空，所以填 `lm-studio` 即可。

## 三、部署并启动 midware

第一次运行需要创建虚拟环境并安装依赖：

```bash
cd ~/test/torcs-1.3.7/midware
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

以后启动：

```bash
cd ~/test/torcs-1.3.7/midware
source .venv/bin/activate
python commentary.py
```

默认加载文字解说界面 `static/index.html`。如果要查看语音解说界面 `static/index2.html`，使用：

```bash
python commentary.py --ui voice
```

服务启动后访问：

```text
http://localhost:8765
```

WebSocket 解说流地址：

```text
ws://127.0.0.1:8765/ws
```

`overlay-app` 默认连接这个地址。

## 四、启动 Electron 字幕悬浮窗

`overlay-app` 是独立 Electron 应用，显示一个透明、无边框、始终置顶的英文状态/字幕 HUD。它不启动 Python 后端；后端仍然由 `midware/commentary.py` 提供。主字幕窗口右上角有一个小设置按钮，也可以通过应用菜单打开独立设置窗口。

第一次运行安装依赖：

```bash
cd ~/test/torcs-1.3.7/overlay-app
npm install
```

启动：

```bash
cd ~/test/torcs-1.3.7/overlay-app
npm start
```

状态含义：

| Overlay 文本 | 含义 |
| --- | --- |
| `Connecting to commentary service...` | 正在连接 `midware` WebSocket |
| `Waiting for commentary...` | 已连接，等待解说 |
| `Generating captions...` | 后端已开始生成解说 |
| `Connection lost` | 后端未启动或连接断开，overlay 会每 3 秒重连 |
| `Commentary error: ...` | 后端返回解说错误 |

注意：

- Overlay UI 文案是英文；最终字幕内容来自后端 `ai_done.content`。
- 要保持最终字幕为英文，请在网页配置、prompt 或模型输出侧要求英文解说。
- 设置窗口可以配置 overlay 连接、语音解说、模型 API、解说员人设、自动解说、CSV 读取和演示数据注入。
- 配音默认关闭；开启后 overlay 在收到 `ai_done` 最终解说时朗读，`ai_start` 会停止上一句语音。如果 Electron 没有可用浏览器 voice，会自动回退到系统 `speech-dispatcher` 的 `spd-say`。
- 可以通过应用菜单隐藏或恢复 overlay，并打开设置窗口。
- WSL 中请使用 Linux 版 Node/npm。若 `which npm` 指向 `/mnt/c/...` 或 `/mnt/d/...`，Electron 安装可能因为 Windows UNC 路径失败。

## 五、运行前检查

Python 语法检查：

```bash
cd ~/test/torcs-1.3.7
python3 -m py_compile \
  midware/commentary.py \
  midware/context_manager.py \
  midware/telemetry.py \
  midware/commentary_engine.py
```

没有输出表示检查通过。

检查 LM Studio：

```bash
curl http://172.24.160.1:1234/v1/models
```

检查 midware：

```bash
curl http://localhost:8765/api/config
```

检查 overlay 文件和 JavaScript 语法：

```bash
cd ~/test/torcs-1.3.7/overlay-app
node --check electron/main.js
node --check electron/preload.js
node --check src/renderer.js
```

完整 overlay 测试文档：

```text
overlay-app/TESTING.md
```

## 六、最小闭环测试（不启动 TORCS）

这是推荐的第一步，用来确认网页、midware、WebSocket、overlay 和 LM Studio 已经连通。

1. 打开 `http://localhost:8765`。
2. 左侧 `AI API 配置` 填好 LM Studio 地址、模型和 API Key。
3. 点击 `保存 API 配置`。
4. 左侧 `数据源` 点击 `注入演示数据`。
5. 确认顶部遥测条显示名次、圈数、速度、油门等信息。
6. 启动 overlay：`cd ~/test/torcs-1.3.7/overlay-app && npm start`。
7. 确认 overlay 显示 `Waiting for commentary...`。
8. 底部输入框留空。
9. 点击 `解说`。

正常现象：

- 右侧出现一条遥测数据消息。
- 随后出现解说员气泡。
- 文本会在网页中流式输出比赛解说。
- Overlay 先显示 `Generating captions...`，完成后显示最终解说内容。

如果报错，优先检查：

- LM Studio server 是否启动。
- `Base URL` 是否为可 curl 通的地址。
- 模型 id 是否和 `/v1/models` 返回的 `id` 完全一致。
- API Key 是否非空。
- Overlay 是否连接到 `ws://127.0.0.1:8765/ws`。

## 七、自动解说测试

网页左侧 `自动解说` 推荐配置：

```text
模式: 事件 + 间隔
解说间隔: 10
事件窗口: 6
事件冷却: 1
```

点击 `应用`。

模式说明：

- `固定间隔`：适合先测试稳定性，每隔 N 秒尝试播报。
- `事件驱动`：只在名次变化、碰撞、出界等事件出现时播报。
- `事件 + 间隔`：推荐比赛演示使用，事件优先，没事件时也有节奏播报。

## 八、连接真实 TORCS 遥测

`midware` 默认监听 UDP `3101`。完整演示建议开三个终端：`midware`、`overlay-app`、`TORCS`。启动 TORCS 前，在 TORCS 终端设置：

```bash
cd ~/test/torcs-1.3.7

export TORCS_PLAYER_UDP_HOST=127.0.0.1
export TORCS_PLAYER_UDP_PORT=3101
export TORCS_PLAYER_LOG_DIR="$(pwd)/logs"
mkdir -p logs

./BUILD/bin/torcs
```

含义：

```text
TORCS_PLAYER_UDP_HOST=127.0.0.1
```

表示把 TORCS 遥测发到本机。

```text
TORCS_PLAYER_UDP_PORT=3101
```

表示发到本机的 `3101` 端口。`midware/commentary.py` 正在监听这个端口。

完整链路：

```text
TORCS
  -> UDP 127.0.0.1:3101
  -> midware telemetry.py
  -> commentary_engine.py
  -> LM Studio
  -> Web UI
  -> WebSocket ws://127.0.0.1:8765/ws
  -> Electron overlay-app
```

推荐运行顺序：

1. 启动 LM Studio Local Server。
2. 启动 `midware`：`cd ~/test/torcs-1.3.7/midware && source .venv/bin/activate && python commentary.py`。
3. 打开 `http://localhost:8765` 保存 AI 配置，并应用自动解说配置。
4. 启动 overlay：`cd ~/test/torcs-1.3.7/overlay-app && npm start`。
5. 使用上面的环境变量启动 `./BUILD/bin/torcs`。
6. 在 TORCS 中进入比赛并驾驶。

## 九、API 测试

保存 AI 配置：

```bash
curl -X POST http://localhost:8765/api/config/api \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "openai",
    "base_url": "http://172.24.160.1:1234/v1",
    "api_key": "lm-studio",
    "model": "granite-4.1-8b",
    "temperature": 0.7,
    "stream": true
  }'
```

注入演示遥测：

```bash
curl -X POST http://localhost:8765/api/telemetry/push \
  -H 'Content-Type: application/json' \
  -d '{
    "telemetry": {
      "seq": 1,
      "sim_time": 12.5,
      "lap": 1,
      "racePos": 2,
      "speedX": 175.2,
      "rpm": 6800,
      "gear": 4,
      "throttle": 0.9,
      "brake": 0.0,
      "steer": -0.1,
      "damage": 0,
      "fuel": 88.5,
      "trackPos": 0.1,
      "distFromStart": 1200
    },
    "rankings": [
      {
        "sim_time": 12.5,
        "car_index": 1,
        "car_name": "player 1",
        "race_pos": 2,
        "laps": 1,
        "dist_from_start": 1200
      }
    ]
  }'
```

手动触发解说：

```bash
curl -X POST http://localhost:8765/api/commentary/manual \
  -H 'Content-Type: application/json' \
  -d '{"prompt": null}'
```

读取最新遥测：

```bash
curl http://localhost:8765/api/telemetry
```

读取最近事件：

```bash
curl http://localhost:8765/api/events/recent
```

更新自动解说配置：

```bash
curl -X POST http://localhost:8765/api/commentary/config \
  -H 'Content-Type: application/json' \
  -d '{
    "mode": "hybrid",
    "baseline_interval": 10,
    "window_seconds": 6,
    "event_cooldown": 1,
    "dedupe_seconds": 10,
    "max_words": 45
  }'
```

## 十、常见问题

### 1. 网页报 `API Key 未设置`

OpenAI-compatible provider 下 API Key 不能为空。LM Studio 可以填任意非空字符串：

```text
lm-studio
```

### 2. `curl http://127.0.0.1:1234/v1/models` 不通

如果在 WSL 中访问 Windows 上的 LM Studio，`127.0.0.1` 可能指向 WSL 自己。请使用 Windows 主机 IP，例如：

```text
http://172.24.160.1:1234/v1
```

以实际 `curl /v1/models` 能通的地址为准。

### 3. 网页没有遥测变化

确认 TORCS 启动前设置了：

```bash
export TORCS_PLAYER_UDP_HOST=127.0.0.1
export TORCS_PLAYER_UDP_PORT=3101
```

并确认 `midware` 服务正在运行。

### 4. Overlay 显示 `Connection lost`

确认 `midware/commentary.py` 正在运行，并且端口是 `8765`：

```bash
curl http://localhost:8765/api/config
```

如果网页能打开但 overlay 仍断开，确认 `overlay-app/src/renderer.js` 中的地址是：

```text
ws://127.0.0.1:8765/ws
```

### 5. Overlay 安装时报 UNC 路径错误

在 WSL 中不要使用 Windows 版 Node/npm。检查：

```bash
which node
which npm
```

如果输出指向 `/mnt/c/...` 或 `/mnt/d/...`，安装 Linux 版 Node，例如使用 `nvm`，然后重新执行 `npm install`。

### 6. 自动解说没有触发

先用 `固定间隔` 模式验证基础链路，再切到 `事件驱动` 或 `事件 + 间隔`。

如果只有一帧静态演示数据，事件驱动可能不会频繁触发。真实驾驶时，名次变化、损伤、出界、追近等事件会更容易触发。

### 7. TORCS 编译失败

先确认系统依赖已安装完整。如果仍失败，保留 `make` 输出最后 30-50 行进行排查。
