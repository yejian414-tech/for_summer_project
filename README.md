# TORCS 1.3.7



本项目仅面向 Ubuntu 部署。

## 安装依赖

```bash
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

## 编译与安装

在项目根目录执行：

```bash
export CFLAGS="-fPIC"
export CPPFLAGS="$CFLAGS"
export CXXFLAGS="$CFLAGS"

./configure --prefix="$(pwd)/BUILD"
make -j"$(nproc)"
make install
make datainstall
```

程序会安装到项目目录下的 `BUILD` 文件夹，不会写入系统安装目录。

## 运行

```bash
./BUILD/bin/torcs
```

TORCS 的用户配置和运行数据默认保存在 `~/.torcs`。

## 原生 SCR Server

项目保留了 SCR patch 提供的 `scr_server` 驾驶员模块，源码位于 `src/drivers/scr_server`。它面向外部自动驾驶客户端：TORCS 负责仿真和传感器计算，客户端通过 UDP 接收车辆状态、计算动作，再把油门、制动、转向等控制量发回 TORCS。

SCR Server 是实时闭环控制接口，本身不会生成 CSV。它和下文的 human 数据采集器彼此独立：

| 接口 | 用途 | 默认端口/输出 |
| --- | --- | --- |
| `scr_server` | 外部程序通过传感器状态控制 SCR 车辆 | UDP 3001–3010 |
| human 数据采集器 | 记录真人玩家状态，也可单向推送每行记录 | CSV；UDP 3101 |

### 启动

构建和安装项目时，`scr_server.so` 及其 10 个车辆配置会一并安装。启动时必须用 `-ver` 指定 SCR 协议版本；推荐使用量程为 200 m 的 `2013`：

```bash
./BUILD/bin/torcs -ver 2013
```

随后在图形界面进入 **Race → Quick Race**，选择 `scr_server 1` 作为驾驶员并开始比赛。仓库自带的 Quick Race 配置已经默认选择 `scr_server 1`。也可以直接指定该配置：

```bash
./BUILD/bin/torcs \
  -ver 2013 \
  -r "$(pwd)/BUILD/share/games/torcs/config/raceman/quickrace.xml"
```

进入比赛后 TORCS 会等待客户端握手，因此应同时启动外部 SCR 客户端。`scr_server 1` 到 `scr_server 10` 依次监听 UDP 端口 `3001` 到 `3010`，支持最多 10 个独立客户端。

SCR Server 还支持以下启动参数：

- `-t <微秒>`：等待客户端每步回传控制指令的超时时间，默认 `10000`，即 10 ms；超时后沿用上一条控制指令。
- `-noisy`：给赛道、对手和 focus 距离传感器加入噪声。
- `-nodamage`：禁用真实损伤，并在状态中返回模拟损伤值。
- `-nofuel`：禁用燃油消耗。
- `-nolaptime`：禁用圈速限制。
- `-ver 2009`：距离传感器量程为 100 m；`-ver 2010` 至 `-ver 2013` 的量程为 200 m。

### UDP 协议

协议使用由括号包围的空格分隔字段，不是 JSON。客户端首先向对应端口发送以 `SCR` 开头的识别消息：

```text
SCR
```

也可以在握手时自定义 19 路赛道传感器的角度：

```text
SCR(init -90 -80 -70 -60 -50 -40 -30 -20 -10 0 10 20 30 40 50 60 70 80 90)
```

未提供 `init` 时使用上面的默认角度。识别成功后服务端回复：

```text
***identified***
```

之后每个仿真步按以下顺序循环：

1. 服务端发送一条车辆状态消息。
2. 客户端在超时前发送一条控制消息。
3. TORCS 应用控制量并进入下一仿真步。

状态消息示意：

```text
(angle 0.01)(curLapTime 3.2)(damage 0)(distFromStart 125.4)...
```

状态字段包括 `angle`、`curLapTime`、`damage`、`distFromStart`、`distRaced`、`fuel`、`gear`、`lastLapTime`、`opponents[36]`、`racePos`、`rpm`、`speedX/Y/Z`、`track[19]`、`trackPos`、`wheelSpinVel[4]`、`z` 和 `focus[5]`。这些字段的单位和含义与下文主 CSV 中的同名字段一致。此外，SCR Server 还发送：

| 字段 | 单位 | 含义 |
| --- | --- | --- |
| `x`, `y` | m | 车辆世界坐标 |
| `roll`, `pitch`, `yaw` | rad | 车辆在世界坐标系中的横滚角、俯仰角和航向角 |
| `speedGlobalX`, `speedGlobalY` | m/s | 世界坐标系 X、Y 方向的速度 |

`focus[5]` 的中心方向由客户端控制消息中的 `focus` 指定，五路角度为 `focus-2°` 到 `focus+2°`。focus 每次有效读取后有 1 秒冷却时间，冷却期间返回 `-1`。

客户端控制消息包含以下字段：

```text
(accel 1)(brake 0)(gear 1)(steer 0)(clutch 0)(focus 0)(meta 0)
```

| 字段 | 范围 | 含义 |
| --- | --- | --- |
| `accel` | `[0, 1]` | 油门 |
| `brake` | `[0, 1]` | 制动 |
| `gear` | `-1, 0, 1...` | 倒挡、空挡或前进挡 |
| `steer` | `[-1, 1]` | 转向 |
| `clutch` | `[0, 1]` | 离合器 |
| `focus` | `[-90, 90]` 度 | focus 中心方向；超出范围表示不请求 focus 数据 |
| `meta` | `0` 或 `1` | `1` 请求重新开始比赛，通常使用 `0` |

## 数据采集

数据采集集成在 `human` 驾驶员模块中，不需要另外启动采集程序。每场比赛开始时，程序会为每个真人玩家创建一组日志；驾驶过程中按仿真时间定期采样，比赛结束或重新开始时关闭日志。

默认采样率为 20 Hz。可以在启动 TORCS 前通过环境变量调整采集行为：

```bash
# 输出目录必须已经存在；不设置时使用 TORCS 进程的当前工作目录
mkdir -p "$(pwd)/logs"
export TORCS_PLAYER_LOG_DIR="$(pwd)/logs"

# 采样率，单位 Hz
export TORCS_PLAYER_LOG_HZ=20

# 每条主日志记录还会以 UDP 数据报发送到这里
export TORCS_PLAYER_UDP_HOST=127.0.0.1
export TORCS_PLAYER_UDP_PORT=3101

./BUILD/bin/torcs
```

采样受仿真步长限制，因此实际相邻记录的时间应以 `sim_time` 为准。UDP 内容与主 CSV 的数据行相同，但不发送表头；没有接收端也不影响 CSV 写入。

### CSV 文件

每个真人玩家每场比赛生成两个文件，`<player>` 是从 1 开始的玩家编号，`<timestamp>` 是开始采集时的 Unix 时间戳：

- `player-<player>-<timestamp>.csv`：当前玩家的控制量、车辆状态和传感器数据，每个采样时刻一行。
- `rankings-player-<player>-<timestamp>.csv`：同一采样时刻所有参赛车辆的位置和排名，每辆车一行。多人本地驾驶时，每个玩家各自生成一份排名文件。

未设置 `TORCS_PLAYER_LOG_DIR` 时，文件写入进程当前工作目录。使用本仓库的 `./BUILD/bin/torcs` 启动脚本时，该目录通常是 `BUILD/share/games/torcs`；建议显式设置输出目录，以免依赖启动方式。

### 主 CSV 字段

| 字段 | 单位/范围 | 含义 |
| --- | --- | --- |
| `seq` | 从 0 开始 | 当前日志内的采样序号 |
| `sim_time` | s | 本场比赛的仿真时间 |
| `player` | 从 1 开始 | 真人玩家编号 |
| `lap` | 圈 | 当前圈计数，起跑阶段通常为 0 |
| `x`, `y` | m | 车辆在赛道世界坐标系中的平面位置 |
| `yaw` | rad | 车辆在世界坐标系中的航向角 |
| `accel_x`, `accel_y` | m/s² | 车辆纵向、横向加速度 |
| `steer` | `[-1, 1]` | 转向指令；负值和正值分别代表两个转向方向 |
| `throttle` | `[0, 1]` | 油门指令 |
| `brake` | `[0, 1]` | 制动指令 |
| `clutch` | `[0, 1]` | 离合器指令 |
| `angle` | rad，`[-π, π]` | 赛道中心线切线方向与车辆航向之间的夹角 |
| `curLapTime` | s | 当前圈已经用时 |
| `damage` | 无量纲 | TORCS 累积车辆损伤值，越大表示损伤越严重 |
| `distFromStart` | m | 沿赛道中心线到起终点的距离；每圈会回绕 |
| `distRaced` | m | 从本次采集开始累计行驶的有符号赛道距离；跨越起终点时已做回绕修正 |
| `fuel` | L | 剩余燃油量 |
| `gear` | 整数 | 挡位；`-1` 为倒挡、`0` 为空挡、正数为前进挡 |
| `lastLapTime` | s | 上一圈用时；尚未完成一圈时通常为 0 |
| `racePos` | 从 1 开始 | 当前比赛名次 |
| `rpm` | rpm | SCR 风格的发动机转速值，代码由 TORCS 内部发动机角速度乘以 10 得到 |
| `speedX`, `speedY`, `speedZ` | km/h | 车辆自身坐标系中的纵向、横向、垂向速度 |
| `trackPos` | 通常 `[-1, 1]` | 相对赛道中心的位置：`0` 为中心线，绝对值 `1` 为左右边界；超出该范围表示驶出赛道 |
| `z` | m | 车身世界高度减去当前位置赛道表面高度 |
| `opponent_0` … `opponent_35` | m，最大 200 | 环绕车辆一周的 36 路对手距离传感器，每路覆盖 10°；`opponent_18` 朝正前方，索引依次覆盖相对方向 `-180°` 到 `170°`。没有对手时为 200 |
| `track_0` … `track_18` | m，最大 200 | 19 路赛道边界距离，方向相对车辆轴线从 `-90°` 到 `90°`、间隔 10°；`track_9` 朝正前方。车辆驶出赛道时全部为 `-1`，量程内未碰到边界时为 200 |
| `wheelSpinVel_0` … `wheelSpinVel_3` | rad/s | 四个车轮的角速度；顺序是右前、左前、右后、左后 |
| `focus_0` … `focus_4` | m，最大 200 | 车辆前向附近 `-2°`、`-1°`、`0°`、`1°`、`2°` 的赛道边界距离；车辆驶出赛道时为 `-1`，量程内未碰到边界时为 200 |

说明：传感器角度遵循 SCR/TORCS 的坐标和正负方向约定。如果下游任务需要明确区分“左/右”，建议用一帧已知姿态数据验证所用坐标系，不要只根据索引名猜测。

### 排名 CSV 字段

| 字段 | 单位/范围 | 含义 |
| --- | --- | --- |
| `sim_time` | s | 采样时的比赛仿真时间；同一时刻的所有车辆具有相同值 |
| `car_index` | 从 0 开始 | 车辆在当前 `tSituation` 车辆数组中的索引 |
| `car_name` | 字符串 | 车辆或车手名称，按标准 CSV 规则使用双引号转义 |
| `race_pos` | 从 1 开始 | 当前比赛名次 |
| `laps` | 圈 | 当前圈计数 |
| `dist_from_start` | m | 该车沿赛道中心线到起终点的距离 |

## 常用启动参数

- `-s`：禁用多重纹理，适用于部分旧显卡或图形兼容问题。
- `-m`：显示 X 鼠标光标。
- `-r <配置文件>`：使用指定的比赛配置文件启动，适用于测试或 AI 训练。
- `-d`：在 GDB 下运行并在退出时打印调用栈，建议配合调试构建使用。
- `-g`：在 Valgrind 下运行，建议配合调试构建使用。

查看游戏内帮助请按 `F1`。

## 许可证说明

项目中的部分车辆素材并非 GPL 自由内容，具体许可信息请查看对应目录内的 `readme.txt`，主要包括：

- `data/cars/models/pw-*`
- `data/cars/models/kc-*`

## Granite AI Features

This project now includes three Granite-powered middleware scripts:

- `chat_engineer.py` / `chat_engineer_gui.py` (+ `car_state_source.py`, `prompt_builder.py`, `granite_client.py`) for Feature 1: AI racing engineer chatbot (CLI and desktop GUI entrypoints)
- `telemetry_analyzer.py` for Feature 2: telemetry analysis and driving guidance
- `race_commentator.py` for Feature 3: procedural race commentary

All three features follow the same architecture:

```text
TORCS UDP CSV -> Python middleware -> Granite endpoint -> text output
```

The middleware keeps TORCS-specific parsing local and sends a structured payload to an external Granite model through an OpenAI-compatible API.

### Shared Setup

Before launching TORCS, enable the built-in human driver telemetry export:

```bash
mkdir -p "$(pwd)/logs"
export TORCS_PLAYER_LOG_DIR="$(pwd)/logs"
export TORCS_PLAYER_LOG_HZ=20
export TORCS_PLAYER_UDP_HOST=127.0.0.1
export TORCS_PLAYER_UDP_PORT=3101
```

Then start your Granite-compatible model server and point the middleware at it:

```bash
export TORCS_AI_BASE_URL="http://<model-host>:<port>/v1"
export TORCS_AI_MODEL="your-lm-studio-model-id"
```

If you are using LM Studio on the same computer, the default endpoint is already:

```bash
http://127.0.0.1:1234/v1
```

So on most machines you only need to:

1. Open LM Studio
2. Load a Granite model
3. Start the local server from the Developer tab

The middleware will then auto-detect the visible local model list from `GET /v1/models` and prefer a model whose identifier contains `granite`. You can still override the endpoint or exact model ID with environment variables if needed.

Quick smoke test for the local connection:

```bash
python3 lmstudio_smoke_test.py
```

You can still override the endpoint per feature with the feature-specific environment variables shown below.

### Feature 1: AI Racing Engineer Chatbot

This is Module 1 from the project brief, split between two teammates per the team's 分工文档:

- "A 同学" owns **赛车数据采集与状态分析功能**: reading TORCS data and turning it into a `car_state` dict with a `problems` list.
- "B 同学" owns **AI 赛车工程师问答功能**: turning that `car_state` plus a player question into a Granite-generated answer. The files below (`car_state_source.py`, `prompt_builder.py`, `granite_client.py`, `chat_engineer.py`) are B's deliverable.

Agreed `car_state` contract between A and B:

```json
{
  "speed": 210.0,
  "rpm": 8700.0,
  "gear": 5,
  "track_pos": 0.72,
  "damage": 1200.0,
  "fuel": 35.0,
  "lap_time": 102.3,
  "problems": ["车辆快要偏离赛道", "转速过高，建议升挡"]
}
```

Run (CLI version, runs in a terminal):

```bash
python3 chat_engineer.py
```

Run (desktop GUI version, recommended for demos -- a floating chat window with a live status panel instead of a terminal):

```bash
python3 chat_engineer_gui.py
```

`chat_engineer_gui.py` is a Tkinter window with three parts: a live status panel (speed/rpm/gear/track position/damage/fuel/lap time + detected problems, refreshed every `TORCS_ENGINEER_REFRESH_MS` ms), a scrollable chat log, and an input box (Enter or the 发送 button to send). It reuses `car_state_source.py`, `prompt_builder.py`, and `granite_client.py` unchanged -- only the input/output layer differs from the CLI version, and Granite calls run on a background thread so the window never freezes while waiting for a reply. Keep the CLI version around as a quick debug entrypoint (no GUI dependencies, easier to read raw errors).

Optional environment variables (shared by both entrypoints, plus one GUI-only var):

```bash
export TORCS_ENGINEER_BASE_URL="http://<model-host>:<port>/v1"
export TORCS_ENGINEER_MODEL="granite"
export TORCS_ENGINEER_USE_FAKE_DATA=true   # force demo car_state data
export TORCS_ENGINEER_UDP_PORT=3101        # live telemetry UDP port
export TORCS_ENGINEER_HISTORY_TURNS=3      # how many past Q&A turns to keep as context
export TORCS_ENGINEER_REFRESH_MS=500       # GUI only: status panel refresh interval (ms)
```

What the chatbot does:

1. Tries to read live `car_state` data from the same TORCS UDP telemetry feed (port `3101`) used by Feature 2/3, via `LiveCarStateSource` in `car_state_source.py`. This is a temporary bridge standing in for A's `race_analyzer.py` output, so B can build and demo the chatbot before that handoff lands.
2. Falls back to `FakeCarStateSource` (a few hand-written demo scenarios) if no live telemetry shows up within 5 seconds, or if `TORCS_ENGINEER_USE_FAKE_DATA=true` is set.
3. Prints the current car state and prompts for a player question on the command line.
4. `prompt_builder.py` formats the car state and question into a Chinese system+user prompt for Granite.
5. `granite_client.py` sends the prompt to the Granite-compatible endpoint and returns the answer.
6. Keeps a short rolling history of the last few Q&A turns so follow-up questions ("那我下一圈呢？") have context.

Student reproduction steps:

1. Optionally start TORCS with the human driver telemetry export enabled (see Shared Setup above), or just set `TORCS_ENGINEER_USE_FAKE_DATA=true` to test without TORCS running at all.
2. Start the Granite-compatible model endpoint (e.g. LM Studio with a Granite model loaded).
3. Run `python3 chat_engineer.py`.
4. Ask questions like "我的轮胎状态怎么样？" / "现在该不该进站？" / "为什么我刚才过弯慢？".
5. Type `exit` to quit.

Swapping in A's real data: once A delivers a module that produces a dict mat