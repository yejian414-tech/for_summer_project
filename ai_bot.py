#!/usr/bin/env python3
"""
Feature 4: Granite-powered AI racing bot.

Steps implemented:
  1  parse_scr_state()    — decode TORCS SCR sensor string → Python dict
  2  format_scr_control() — encode control dict → TORCS SCR wire string
  3  ScrClient            — UDP handshake + main receive/send loop
     run_bot()            — connect to TORCS and drive
  4  compute_control()    — strategy-parameterized low-level controller
                            ATTACK / NORMAL / DEFEND / SAVE_FUEL / PIT
"""

from __future__ import annotations

import json
import math
import re
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any

try:
    from telemetry_common import (
        clamp, parse_float, parse_int,
        LatestTaskRunner, chat_completion_text, extract_json_object,
        compact_track_profile, compact_opponent_profile,
        connect_openai_compatible_model, print_connection_banner,
    )
    _TELEMETRY_AVAILABLE = True
except ImportError:
    _TELEMETRY_AVAILABLE = False
    # telemetry_common requires openai; define the three helpers locally
    # so tests can run without any extra dependencies installed.
    def parse_float(value: str, default: float = 0.0) -> float:  # type: ignore[misc]
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def parse_int(value: str, default: int = 0) -> int:  # type: ignore[misc]
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def clamp(value: float, low: float, high: float) -> float:  # type: ignore[misc]
        return max(low, min(high, value))

    def extract_json_object(text: str) -> dict[str, Any] | None:  # type: ignore[misc]
        """Minimal fallback: find first {...} block and parse it."""
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None

    def compact_track_profile(track: list[float]) -> dict[str, Any]:  # type: ignore[misc]
        if not track:
            return {}
        return {"min": round(min(track), 1), "max": round(max(track), 1),
                "fwd": round(track[9], 1) if len(track) > 9 else 0.0}

    def compact_opponent_profile(opponents: list[float]) -> dict[str, Any]:  # type: ignore[misc]
        if not opponents:
            return {}
        close = [o for o in opponents if o < 30.0]
        return {"closest": round(min(opponents), 1), "close_count": len(close)}


# ---------------------------------------------------------------------------
# SCR field metadata
# ---------------------------------------------------------------------------

_FIELD_MAP: dict[str, str] = {
    "angle":         "angle",
    "curLapTime":    "cur_lap_time",
    "damage":        "damage",
    "distFromStart": "dist_from_start",
    "distRaced":     "dist_raced",
    "fuel":          "fuel",
    "gear":          "gear",
    "lastLapTime":   "last_lap_time",
    "opponents":     "opponents",
    "racePos":       "race_pos",
    "rpm":           "rpm",
    "speedX":        "speed_x",
    "speedY":        "speed_y",
    "speedZ":        "speed_z",
    "track":         "track",
    "trackPos":      "track_pos",
    "wheelSpinVel":  "wheel_spin_vel",
    "z":             "z",
    "focus":         "focus",
    "x":             "x",
    "y":             "y",
    "roll":          "roll",
    "pitch":         "pitch",
    "yaw":           "yaw",
    "speedGlobalX":  "speed_global_x",
    "speedGlobalY":  "speed_global_y",
}

_ARRAY_FIELDS: frozenset[str] = frozenset({"opponents", "track", "wheelSpinVel", "focus"})
_INT_FIELDS:   frozenset[str] = frozenset({"gear", "racePos"})

_ARRAY_LENGTHS: dict[str, int] = {
    "opponents": 36, "track": 19, "wheelSpinVel": 4, "focus": 5,
}
_ARRAY_DEFAULTS: dict[str, float] = {
    "opponents": 200.0, "track": -1.0, "wheelSpinVel": 0.0, "focus": -1.0,
}

_REQUIRED_KEYS: frozenset[str] = frozenset({"speedX", "fuel", "gear", "track"})
_SCR_TOKEN = re.compile(r"\((\w+)\s+([^)]*)\)")


# ---------------------------------------------------------------------------
# Step 1: SCR state parser
# ---------------------------------------------------------------------------

def parse_scr_state(message: str) -> dict[str, Any] | None:
    """Decode a TORCS SCR sensor string into a Python dict.

    Returns a dict with snake_case keys, or None if the string is empty,
    unparseable, or is missing required fields.
    """
    if not message:
        return None

    raw: dict[str, str] = {}
    for match in _SCR_TOKEN.finditer(message):
        raw[match.group(1)] = match.group(2).strip()

    if not raw:
        return None
    if not _REQUIRED_KEYS.issubset(raw):
        return None

    state: dict[str, Any] = {}
    for scr_name, py_name in _FIELD_MAP.items():
        raw_value = raw.get(scr_name, "")
        if scr_name in _ARRAY_FIELDS:
            parts    = raw_value.split() if raw_value else []
            expected = _ARRAY_LENGTHS[scr_name]
            fill     = _ARRAY_DEFAULTS[scr_name]
            values   = [parse_float(p, fill) for p in parts]
            if len(values) < expected:
                values.extend([fill] * (expected - len(values)))
            state[py_name] = values[:expected]
        elif scr_name in _INT_FIELDS:
            state[py_name] = parse_int(raw_value, 0)
        else:
            state[py_name] = parse_float(raw_value, 0.0)

    return state


# ---------------------------------------------------------------------------
# Step 2: control serializer
# ---------------------------------------------------------------------------

def format_scr_control(
    *,
    accel:  float = 0.0,
    brake:  float = 0.0,
    gear:   int   = 1,
    steer:  float = 0.0,
    clutch: float = 0.0,
    focus:  int   = 0,
    meta:   int   = 0,
) -> str:
    """Encode a control action into the TORCS SCR wire format.

    All values are clamped to their legal ranges before serialisation.
    """
    accel  = clamp(accel,  0.0,  1.0)
    brake  = clamp(brake,  0.0,  1.0)
    steer  = clamp(steer, -1.0,  1.0)
    clutch = clamp(clutch, 0.0,  1.0)
    focus  = int(clamp(float(focus), -90.0, 90.0))
    gear   = int(gear)
    meta   = 1 if meta else 0
    return (
        f"(accel {accel:.3f})"
        f"(brake {brake:.3f})"
        f"(gear {gear})"
        f"(steer {steer:.3f})"
        f"(clutch {clutch:.3f})"
        f"(focus {focus})"
        f"(meta {meta})"
    )


# ---------------------------------------------------------------------------
# Step 3: SCR UDP client
# ---------------------------------------------------------------------------

# 19 track-sensor angles sent during the SCR handshake.
_INIT_ANGLES: tuple[int, ...] = (
    -90, -75, -60, -45, -30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30, 45, 60, 75, 90
)
_SCR_BUF           = 1000
_HANDSHAKE_RETRIES = 5
_HANDSHAKE_TIMEOUT = 5.0    # seconds per attempt
_STEP_TIMEOUT      = 0.1    # seconds; per-step recv timeout


class ScrClient:
    """UDP client for the TORCS SCR protocol.

    Usage::

        with ScrClient(host="localhost", port=3001) as client:
            client.connect()          # handshake
            while True:
                state = client.receive_state()
                if state is None:     # race ended / restarted
                    break
                if not state:         # timeout — resend last controls
                    client.send_control(last_ctrl)
                    continue
                last_ctrl = format_scr_control(...)
                client.send_control(last_ctrl)
    """

    def __init__(self, host: str = "localhost", port: int = 3001) -> None:
        self._addr = (host, port)
        self._sock: socket.socket | None = None
        self._done = False

    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        """Send SCR(init …) and wait for ***identified***."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(_HANDSHAKE_TIMEOUT)

        payload = ("SCR(init " + " ".join(str(a) for a in _INIT_ANGLES) + ")").encode()

        for attempt in range(1, _HANDSHAKE_RETRIES + 1):
            self._sock.sendto(payload, self._addr)
            try:
                data, _ = self._sock.recvfrom(_SCR_BUF)
            except socket.timeout:
                print(f"  [scr] handshake attempt {attempt}/{_HANDSHAKE_RETRIES} timed out")
                continue

            if data.rstrip(b"\x00").decode(errors="replace") == "***identified***":
                self._sock.connect(self._addr)          # fix default peer → use send/recv
                self._sock.settimeout(_STEP_TIMEOUT)
                return

        raise ConnectionError(
            f"TORCS did not respond at {self._addr[0]}:{self._addr[1]} "
            f"after {_HANDSHAKE_RETRIES} attempts"
        )

    def receive_state(self) -> dict[str, Any] | None:
        """Receive one simulation step from TORCS.

        Returns:
            Parsed state dict  — normal packet.
            Empty dict {}      — recv timed out; caller should resend last control.
            None               — race ended (***shutdown***) or restarted (***restart***).
        """
        if self._sock is None:
            raise RuntimeError("Not connected — call connect() first")

        try:
            data = self._sock.recv(_SCR_BUF)
        except socket.timeout:
            return {}
        except ConnectionRefusedError:
            # TORCS closed the port (race ended or simulator quit).
            self._done = True
            return None

        text = data.rstrip(b"\x00").decode(errors="replace")

        if text.startswith("***shutdown***"):
            self._done = True
            return None
        if text.startswith("***restart***"):
            return None

        return parse_scr_state(text)

    def send_control(self, ctrl: str) -> None:
        if self._sock is None:
            raise RuntimeError("Not connected")
        self._sock.send(ctrl.encode())

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    @property
    def is_shutdown(self) -> bool:
        return self._done

    def __enter__(self) -> "ScrClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Gear shifting — two implementations
# ---------------------------------------------------------------------------

# --- RPM-based (used by _simple_autopilot legacy stub) ---
_GEAR_UP_RPM   = 7500
_GEAR_DOWN_RPM = 3000
_MAX_GEAR      = 6


def _auto_gear(current: int, rpm: float) -> int:
    if current <= 0:
        return 1
    if rpm > _GEAR_UP_RPM and current < _MAX_GEAR:
        return current + 1
    if rpm < _GEAR_DOWN_RPM and current > 1:
        return current - 1
    return current


# --- Speed-based (used by compute_control, ported from snakeoil.py) ---
# km/h thresholds; gear n shifts up when speed > _UP[n], down when < _DOWN[n]
_UP_SPEED   = (0, 35, 60, 85, 115, 140)   # index = current gear
_DOWN_SPEED = (0,  0, 28, 50,  72,  95)


def _gear_from_speed(gear: int, speed: float) -> int:
    """Speed-based gear selector (more reliable than RPM across car types)."""
    if gear <= 0:
        return 1
    if gear < len(_UP_SPEED) and speed > _UP_SPEED[gear]:
        return gear + 1
    if gear > 1 and speed < _DOWN_SPEED[gear]:
        return gear - 1
    return gear


# ---------------------------------------------------------------------------
# ABS and traction control — ported from snakeoil.py (SCR reference client)
# ---------------------------------------------------------------------------

_WHEEL_RADIUS = 0.33    # metres (approximate for trb1/sc cars)

_ABS_SLIP  = 2.0        # m/s: wheel-lock slip to start reducing brake
_ABS_RANGE = 5.0        # m/s: full ABS modulation range

_TCL_SLIP  = 2.0        # m/s: wheel-spin slip to start reducing throttle
_TCL_RANGE = 10.0       # m/s: full TCL modulation range


def _apply_abs(brake: float, speed_kmh: float, wheel_vels: list[float]) -> float:
    """Reduce brake pressure when wheels are locking up."""
    speed_ms = speed_kmh / 3.6
    if speed_ms < 3.0 or not wheel_vels:
        return brake
    wheel_speed_ms = (sum(wheel_vels) / len(wheel_vels)) * _WHEEL_RADIUS
    slip = speed_ms - wheel_speed_ms
    if slip > _ABS_SLIP:
        brake *= max(0.0, 1.0 - (slip - _ABS_SLIP) / _ABS_RANGE)
    return brake


def _apply_tcl(accel: float, speed_kmh: float, wheel_vels: list[float]) -> float:
    """Reduce throttle when rear wheels are spinning."""
    speed_ms = speed_kmh / 3.6
    if len(wheel_vels) < 4:
        return accel
    rear_ms = (wheel_vels[2] + wheel_vels[3]) / 2.0 * _WHEEL_RADIUS
    slip = rear_ms - speed_ms
    if slip > _TCL_SLIP:
        accel *= max(0.0, 1.0 - (slip - _TCL_SLIP) / _TCL_RANGE)
    return accel


def _simple_autopilot(state: dict[str, Any]) -> str:
    """Rule-based controller — drives forward for Step 3 integration testing."""
    speed = state.get("speed_x", 0.0)
    rpm   = state.get("rpm", 0.0)
    gear  = state.get("gear", 0)
    angle = state.get("angle", 0.0)     # radians, car vs track axis
    tpos  = state.get("track_pos", 0.0) # [-1, 1]; 0 = centre
    track = state.get("track", [])

    gear  = _auto_gear(gear, rpm)
    steer = angle * 10.0 / math.pi - tpos * 0.5   # align + return to centre

    front = track[9] if len(track) > 9 else 100.0  # index 9 = 0° straight ahead

    if abs(tpos) > 1.0:                 # off track — recover
        accel, brake = 0.0, 0.5
        steer = -tpos * 0.8
    elif front < 20.0:                  # obstacle close ahead
        accel, brake = 0.3, 0.3
    elif front < 50.0 or speed > 150.0: # slow for corner / speed limit
        accel, brake = 0.6, 0.0
    else:
        accel, brake = 1.0, 0.0

    return format_scr_control(accel=accel, brake=brake, gear=gear, steer=steer)


# ---------------------------------------------------------------------------
# Step 4: Strategy-parameterised low-level controller
# ---------------------------------------------------------------------------

ATTACK    = "ATTACK"
NORMAL    = "NORMAL"
DEFEND    = "DEFEND"
SAVE_FUEL = "SAVE_FUEL"
PIT       = "PIT"

_ALL_STRATEGIES: frozenset[str] = frozenset({ATTACK, NORMAL, DEFEND, SAVE_FUEL, PIT})


@dataclass(frozen=True)
class _DriveParams:
    max_speed:    float  # km/h absolute ceiling
    accel_limit:  float  # maximum accel command [0, 1]
    brake_gain:   float  # multiplier when speed exceeds target
    steer_gain:   float  # angle * steer_gain → steer component
                         # angle is in radians (~[-0.5, 0.5] during normal driving)
                         # keep near 1.0 — do NOT multiply by 10 or divide by π
    center_gain:  float  # track_pos correction (pull back to centre)
    speed_factor: float  # corner_speed_kmh = sqrt(min_fwd_m * speed_factor)


#                          max_spd  accel  brake_g  steer_g  cntr_g  spd_factor
_PARAMS: dict[str, _DriveParams] = {
    ATTACK:    _DriveParams(300,    1.00,   1.20,    1.1,     0.4,   220),
    NORMAL:    _DriveParams(250,    0.95,   1.00,    1.0,     0.5,   180),
    DEFEND:    _DriveParams(180,    0.80,   0.90,    1.0,     0.6,   130),
    SAVE_FUEL: _DriveParams(150,    0.65,   0.80,    1.0,     0.5,    80),
    PIT:       _DriveParams( 50,    0.30,   1.50,    0.8,     0.8,    10),
}


def compute_control(state: dict[str, Any], strategy: str = NORMAL) -> str:
    """Translate a strategy + live sensor state into a concrete SCR control string.

    Called every simulation step. Granite (Step 6) supplies the strategy;
    the safety layer (Step 5) may override it before calling this function.
    """
    params     = _PARAMS.get(strategy, _PARAMS[NORMAL])

    speed      = state.get("speed_x",      0.0)
    gear       = state.get("gear",           0)
    angle      = state.get("angle",        0.0)
    tpos       = state.get("track_pos",    0.0)
    track      = state.get("track",         [])
    wheel_vels = state.get("wheel_spin_vel", [])

    # --- off-track recovery (before gear shifting so gear=-1 is never clobbered) ---
    if abs(tpos) > 1.0:
        max_lock = clamp(1.5 - abs(speed) * 0.01, 0.3, 1.0)
        if speed > 5.0:
            # Still rolling forward -> brake hard, steer toward centre
            recovery_steer = clamp(-tpos * 0.8, -max_lock, max_lock)
            return format_scr_control(accel=0.0, brake=0.8, gear=max(gear, 1), steer=recovery_steer)
        else:
            # Stopped or reversing -> reverse with steer INVERTED for backward motion
            recovery_steer = clamp(tpos * 0.6, -max_lock, max_lock)
            return format_scr_control(accel=0.6, brake=0.0, gear=-1, steer=recovery_steer)

    # --- gear (speed-based, from snakeoil.py) ---
    gear = _gear_from_speed(gear, speed)

    # --- lookahead steering ---
    # angle > 0 = car faces RIGHT -> will drift right -> tpos falls
    # future_tpos = tpos - angle*L/halfwidth
    _L     = 17.0 + 0.33 * speed / 3.6
    _ftpos = tpos - angle * _L / 5.0
    steer  = clamp(-_ftpos * params.center_gain, -1.0, 1.0)

    # --- corner speed limit: tight forward window (±5°, indices 8–10) ---
    # Using ±20° caused unnecessary braking when corner walls read short.
    fwd          = [track[i] for i in range(8, 11)] if len(track) >= 11 else []
    min_fwd      = min(fwd) if fwd else 100.0
    corner_limit = math.sqrt(max(min_fwd, 1.0) * params.speed_factor)
    target_speed = min(params.max_speed, corner_limit)

    # --- accel / brake ---
    if speed < target_speed:
        accel = params.accel_limit
        brake = 0.0
    else:
        excess = (speed - target_speed) / max(target_speed, 1.0)
        accel  = 0.0
        brake  = clamp(excess * params.brake_gain, 0.0, 1.0)

    # ABS: prevent wheel lock-up under braking (snakeoil.py)
    brake = _apply_abs(brake, speed, wheel_vels)
    # TCL: prevent rear-wheel spin on acceleration (snakeoil.py)
    accel = _apply_tcl(accel, speed, wheel_vels)

    # PIT: once we've slowed to a crawl, ask TORCS for the pit stop
    meta = 1 if (strategy == PIT and speed < 10.0) else 0

    return format_scr_control(accel=accel, brake=brake, gear=gear, steer=steer, meta=meta)


# ---------------------------------------------------------------------------
# Step 5: Safety layer
# ---------------------------------------------------------------------------

# Thresholds — centralised here so they're easy to tune without touching logic.
_FUEL_PIT      = 5.0    # litres: force PIT regardless of Granite's choice
_FUEL_CAUTION  = 15.0   # litres: downgrade ATTACK → NORMAL (running low)
_DMG_NO_ATTACK = 8000   # damage points: disallow ATTACK (car degraded)
_DMG_DEFEND    = 9500   # damage points: force DEFEND even if Granite says NORMAL


def safety_filter(strategy: str | None, state: dict[str, Any]) -> str:
    """Map a Granite-supplied strategy to a safe strategy using hard rules.

    Pure function — no I/O, no side effects.  Rules are checked in
    descending priority; the first match wins and short-circuits the rest.

    Args:
        strategy: Raw strategy name from Granite, or None on timeout/error.
        state:    Latest parsed SCR sensor dict from parse_scr_state().

    Returns:
        A strategy string guaranteed to be in _ALL_STRATEGIES.
    """
    fuel   = state.get("fuel",   50.0)
    damage = state.get("damage",  0.0)

    # Priority 1 — unknown / timed-out strategy → safe default
    if strategy not in _ALL_STRATEGIES:
        return NORMAL

    # Priority 2 — almost out of fuel → pit now, no argument
    if fuel < _FUEL_PIT:
        return PIT

    # Priority 3 — car is critically damaged → protect what's left
    if damage >= _DMG_DEFEND:
        return DEFEND

    # Priority 4 — car is damaged but still drivable → no attacking
    if damage >= _DMG_NO_ATTACK and strategy == ATTACK:
        return NORMAL

    # Priority 5 — fuel running low → conserve, don't attack
    if fuel < _FUEL_CAUTION and strategy == ATTACK:
        return NORMAL

    return strategy


# ---------------------------------------------------------------------------
# Step 6: Granite strategy caller
# ---------------------------------------------------------------------------

_STRATEGY_INTERVAL = 5.0   # seconds between Granite requests
_GRANITE_TIMEOUT   = 4.0   # seconds to wait for a single LLM response
_GRANITE_MAX_TOK   = 80    # keep responses short and fast

_SYSTEM_PROMPT = """\
You are a race strategist for a TORCS simulation. \
Given live sensor data, choose one driving strategy and explain in one sentence why.

Respond with JSON only — no markdown, no extra text:
{"strategy": "<one of ATTACK|NORMAL|DEFEND|SAVE_FUEL|PIT>", "reason": "<one sentence>"}

Strategy guide:
- ATTACK:    push hard, high risk, use when fuel ok and no damage and clear track
- NORMAL:    balanced pace, default choice
- DEFEND:    cautious, use when damaged or opponent close behind
- SAVE_FUEL: economical, use when fuel < 20 L and many laps remain
- PIT:       slow down for pit stop, use when fuel < 5 L or damage critical"""


def _build_strategy_prompt(state: dict[str, Any]) -> str:
    """Summarise the SCR state into a compact JSON payload for the prompt."""
    track  = state.get("track", [])
    opps   = state.get("opponents", [])

    track_summary = compact_track_profile(track)   if track else {}
    opp_summary   = compact_opponent_profile(opps) if opps  else {}

    payload = {
        "speed_kmh":   round(state.get("speed_x",      0.0), 1),
        "fuel_L":      round(state.get("fuel",         50.0), 1),
        "damage":      round(state.get("damage",        0.0), 0),
        "track_pos":   round(state.get("track_pos",    0.0), 3),
        "gear":              state.get("gear",            1),
        "race_pos":          state.get("race_pos",        1),
        "dist_raced_m":round(state.get("dist_raced",   0.0), 0),
        "track":       track_summary,
        "opponents":   opp_summary,
    }
    import json as _json
    return _SYSTEM_PROMPT + "\n\nLive data:\n" + _json.dumps(payload, ensure_ascii=True)


def _parse_strategy_response(text: str) -> tuple[str, str]:
    """Extract (strategy, reason) from Granite's JSON reply.

    Returns (NORMAL, reason) if the strategy field is missing or invalid.
    """
    parsed = extract_json_object(text)
    if not parsed:
        return NORMAL, "parse error"
    raw_strategy = str(parsed.get("strategy", "")).strip().upper()
    reason       = str(parsed.get("reason", "")).strip()
    strategy = raw_strategy if raw_strategy in _ALL_STRATEGIES else NORMAL
    return strategy, reason


class GraniteStrategist:
    """Async Granite strategy caller.

    Submits a new strategy request to Granite every ``interval`` seconds
    without blocking the main control loop.  The most recent completed
    result is cached and returned on each ``tick()`` call.

    Usage::

        g = GraniteStrategist(connection)
        # inside main loop:
        raw_strategy, reason = g.tick(state)
        safe_strategy = safety_filter(raw_strategy, state)
        ctrl = compute_control(state, safe_strategy)
    """

    def __init__(self, connection: Any, interval: float = _STRATEGY_INTERVAL) -> None:
        self._connection = connection
        self._interval   = interval
        self._runner     = LatestTaskRunner(self._call_granite, "granite-strategist")
        self._last_strategy: str = NORMAL
        self._last_reason:   str = "startup"
        self._last_submit:   float = -interval   # trigger immediately on first tick

    # ------------------------------------------------------------------ #

    def tick(self, state: dict[str, Any]) -> tuple[str, str]:
        """Call once per main-loop iteration.

        Submits a new Granite request if the interval has elapsed, then
        returns the most recent completed (strategy, reason) pair.
        """
        now = time.monotonic()
        if now - self._last_submit >= self._interval:
            self._runner.submit({"state": state}, priority=0)
            self._last_submit = now

        result = self._runner.pop_completed()
        if result is not None:
            if result.error:
                print(f"[Granite] error: {result.error}")
            else:
                strategy, reason = result.output
                self._last_strategy = strategy
                self._last_reason   = reason
                print(f"[Granite] {strategy}  — {reason}")

        return self._last_strategy, self._last_reason

    def last_strategy(self) -> str:
        return self._last_strategy

    # ------------------------------------------------------------------ #

    def _call_granite(self, task: dict[str, Any]) -> tuple[str, str]:
        """Worker: runs in background thread, calls LLM, returns (strategy, reason)."""
        state  = task["state"]
        prompt = _build_strategy_prompt(state)
        text   = chat_completion_text(
            self._connection,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=_GRANITE_MAX_TOK,
            timeout=_GRANITE_TIMEOUT,
        )
        return _parse_strategy_response(text)


# ---------------------------------------------------------------------------
# Main drive loop
# ---------------------------------------------------------------------------

def run_bot(
    host:       str   = "localhost",
    port:       int   = 3001,
    strategy:   str   = NORMAL,
    *,
    use_granite: bool = False,
    verbose:    bool  = True,
) -> None:
    """Connect to TORCS and drive.

    With ``use_granite=True`` (Step 7), a GraniteStrategist is created and
    queried every few seconds to update the driving strategy dynamically.
    Without it, the fixed ``strategy`` argument is used throughout.
    """
    if strategy not in _ALL_STRATEGIES:
        print(f"Unknown strategy '{strategy}', falling back to NORMAL.")
        strategy = NORMAL

    # --- Step 7: optionally connect Granite ---
    strategist: GraniteStrategist | None = None
    if use_granite:
        if not _TELEMETRY_AVAILABLE:
            print("[warn] telemetry_common not available — falling back to fixed strategy.")
        else:
            try:
                _conn = connect_openai_compatible_model()
                print_connection_banner(_conn, "AI Bot — Granite Strategist")
                strategist = GraniteStrategist(_conn)
            except Exception as e:
                print(f"[warn] Could not connect to Granite ({e}) — using fixed strategy.")

    print(f"Connecting to TORCS at {host}:{port}  strategy={strategy}  granite={strategist is not None}…")

    with ScrClient(host, port) as client:
        client.connect()
        print("Identified! Entering drive loop. Press Ctrl-C to stop.\n")

        step             = 0
        last_ctrl        = format_scr_control()   # idle
        current_strategy = strategy               # updated by Granite each tick

        try:
            while True:
                state = client.receive_state()

                if state is None:
                    print("Race ended — exiting loop.")
                    break

                if not state:
                    # Recv timed out — TORCS reuses last control; we echo ours.
                    client.send_control(last_ctrl)
                    continue

                # --- Step 7: Granite strategy update (non-blocking) ---
                if strategist is not None:
                    raw_strategy, _reason = strategist.tick(state)
                    current_strategy = safety_filter(raw_strategy, state)
                else:
                    current_strategy = safety_filter(strategy, state)

                last_ctrl = compute_control(state, current_strategy)
                client.send_control(last_ctrl)
                step += 1

                if verbose and step % 100 == 0:
                    speed = state.get("speed_x", 0.0)
                    gear  = state.get("gear",    0)
                    fuel  = state.get("fuel",    0.0)
                    tpos  = state.get("track_pos", 0.0)
                    print(
                        f"  step={step:6d}  {speed:6.1f} km/h  "
                        f"gear={gear}  fuel={fuel:.1f} L  tpos={tpos:+.2f}  "
                        f"strategy={current_strategy}"
                    )

        except KeyboardInterrupt:
            print(f"\nStopped after {step} steps.")

    if client.is_shutdown:
        print("Server sent ***shutdown***.")


# ---------------------------------------------------------------------------
# Entry points
#   python3 ai_bot.py                              → run unit tests
#   python3 ai_bot.py --bot                        → localhost:3001, NORMAL
#   python3 ai_bot.py --bot HOST PORT              → custom address
#   python3 ai_bot.py --bot HOST PORT STRATEGY     → e.g. ATTACK
# ---------------------------------------------------------------------------

def _run_tests() -> None:
    opponents = " ".join(["200.0"] * 36)
    track     = " ".join(["150.0"] * 9 + ["180.0"] + ["150.0"] * 9)
    wheels    = "12.5 12.5 13.0 13.0"
    focus_    = "-1.0 -1.0 -1.0 -1.0 -1.0"

    sample = (
        f"(angle 0.015)(curLapTime 42.3)(damage 0)(distFromStart 312.7)"
        f"(distRaced 312.7)(fuel 38.5)(gear 4)(lastLapTime 91.2)"
        f"(opponents {opponents})(racePos 2)(rpm 7800)"
        f"(speedX 148.3)(speedY -0.4)(speedZ 0.0)"
        f"(track {track})(trackPos 0.12)(wheelSpinVel {wheels})"
        f"(z 0.33)(focus {focus_})(x 241.0)(y 88.0)"
        f"(roll 0.0)(pitch 0.01)(yaw 1.57)"
        f"(speedGlobalX 120.1)(speedGlobalY 88.3)"
    )

    # ---- parse_scr_state ------------------------------------------------
    state = parse_scr_state(sample)
    assert state is not None,                       "FAIL: returned None for valid packet"
    assert state["gear"] == 4,                      f"FAIL: gear={state['gear']}"
    assert state["race_pos"] == 2,                  f"FAIL: race_pos={state['race_pos']}"
    assert abs(state["speed_x"] - 148.3) < 1e-6,   f"FAIL: speed_x={state['speed_x']}"
    assert abs(state["fuel"] - 38.5) < 1e-6,        f"FAIL: fuel={state['fuel']}"
    assert len(state["opponents"]) == 36,           f"FAIL: opponents length={len(state['opponents'])}"
    assert len(state["track"]) == 19,               f"FAIL: track length={len(state['track'])}"
    assert len(state["wheel_spin_vel"]) == 4,       f"FAIL: wheel_spin_vel length={len(state['wheel_spin_vel'])}"
    assert len(state["focus"]) == 5,                f"FAIL: focus length={len(state['focus'])}"
    assert state["opponents"][0] == 200.0,          f"FAIL: opponents[0]={state['opponents'][0]}"
    assert state["focus"][0] == -1.0,               f"FAIL: focus[0]={state['focus'][0]}"
    print("parse_scr_state  ... OK")

    assert parse_scr_state("") is None,             "FAIL: empty string should return None"
    assert parse_scr_state("(angle 0.1)") is None,  "FAIL: incomplete packet should return None"

    short_opp = " ".join(["50.0"] * 10)
    partial = (
        f"(angle 0)(curLapTime 0)(damage 0)(distFromStart 0)(distRaced 0)"
        f"(fuel 30)(gear 1)(lastLapTime 0)(opponents {short_opp})"
        f"(racePos 1)(rpm 0)(speedX 0)(speedY 0)(speedZ 0)"
        f"(track {track})(trackPos 0)(wheelSpinVel {wheels})(z 0)"
    )
    ps = parse_scr_state(partial)
    assert ps is not None,               "FAIL: partial packet returned None"
    assert len(ps["opponents"]) == 36,   "FAIL: short opponents not padded to 36"
    assert ps["opponents"][35] == 200.0, "FAIL: padding value wrong"
    print("parse_scr_state  (edge cases) ... OK")

    # ---- format_scr_control --------------------------------------------
    ctrl = format_scr_control(accel=0.8, brake=0.0, gear=3, steer=-0.12)
    assert "(accel 0.800)" in ctrl
    assert "(brake 0.000)" in ctrl
    assert "(gear 3)"      in ctrl
    assert "(steer -0.120)" in ctrl
    assert "(clutch 0.000)" in ctrl
    assert "(focus 0)"     in ctrl
    assert "(meta 0)"      in ctrl
    print(f"format_scr_control ... OK  →  {ctrl}")

    over = format_scr_control(accel=2.0, brake=-1.0, steer=5.0, focus=200)
    assert "(accel 1.000)" in over
    assert "(brake 0.000)" in over
    assert "(steer 1.000)" in over
    assert "(focus 90)"    in over
    print("format_scr_control (clamping) ... OK")

    # ---- _simple_autopilot --------------------------------------------
    track_vals = [150.0] * 9 + [180.0] + [150.0] * 9
    fake = {
        "speed_x": 80.0, "rpm": 5000.0, "gear": 3,
        "angle": 0.1, "track_pos": 0.2, "track": track_vals,
    }
    ap = _simple_autopilot(fake)
    assert "(accel 1.000)" in ap, f"FAIL: expected full throttle on clear track: {ap}"
    assert "(gear 3)"      in ap, f"FAIL: gear should stay 3 at 5000 rpm: {ap}"
    print(f"_simple_autopilot  ... OK  →  {ap}")

    # ---- ScrClient API (no TORCS) — just instantiation + close ----------
    c = ScrClient("localhost", 3001)
    assert c._addr == ("localhost", 3001)
    assert not c.is_shutdown
    c.close()   # no-op when never connected
    print("ScrClient          ... OK  (instantiation + close without connect)")

    # ---- compute_control ------------------------------------------------
    track_vals = [150.0] * 9 + [180.0] + [150.0] * 9   # clear straight
    cs = {"speed_x": 80.0, "rpm": 5000.0, "gear": 3,
          "angle": 0.0, "track_pos": 0.0, "track": track_vals}

    # clear straight at 80 km/h — each strategy should accelerate
    # (corner_limit @ min_fwd=150: ATTACK=190, NORMAL=140, SAVE_FUEL=110, PIT=39)
    cc_attack = compute_control(cs, ATTACK)
    assert "(accel 1.000)" in cc_attack,  f"FAIL ATTACK accel: {cc_attack}"
    assert "(brake 0.000)" in cc_attack,  f"FAIL ATTACK brake: {cc_attack}"
    print(f"compute_control ATTACK    ... OK  →  {cc_attack}")

    cc_normal = compute_control(cs, NORMAL)
    assert "(accel 0.950)" in cc_normal,  f"FAIL NORMAL accel: {cc_normal}"
    print(f"compute_control NORMAL    ... OK  →  {cc_normal}")

    cc_save = compute_control(cs, SAVE_FUEL)
    assert "(accel 0.650)" in cc_save,    f"FAIL SAVE_FUEL accel: {cc_save}"
    print(f"compute_control SAVE_FUEL ... OK  →  {cc_save}")

    # NORMAL over target speed → should brake
    cs_fast = {**cs, "speed_x": 250.0}
    cc_over = compute_control(cs_fast, NORMAL)
    assert "(accel 0.000)" in cc_over, f"FAIL: over target should not accelerate: {cc_over}"
    assert "(brake 0.000)" not in cc_over, f"FAIL: over target should brake: {cc_over}"
    print(f"compute_control NORMAL over-speed ... OK  →  {cc_over}")

    # off-track recovery: tpos > 1 → always brake regardless of strategy
    # off-track + still moving → brake, steer capped by speed
    # speed=80: max_lock = clamp(1.5-0.8, 0.3, 1.0) = 0.7
    # recovery_steer = clamp(-1.5*0.8, -0.7, 0.7) = -0.7
    cs_offt = {**cs, "track_pos": 1.5}   # speed_x=80 in cs → still rolling
    cc_offt = compute_control(cs_offt, ATTACK)
    assert "(accel 0.000)" in cc_offt, f"FAIL off-track accel: {cc_offt}"
    assert "(brake 0.800)" in cc_offt, f"FAIL off-track brake: {cc_offt}"
    assert "(steer -0.700)" in cc_offt, f"FAIL off-track steer: {cc_offt}"
    print(f"compute_control off-track (moving)  ... OK  →  {cc_offt}")

    # off-track + stopped → reverse gear, steer INVERTED (tpos=1.5 → steer=+0.9)
    # speed=0: max_lock=clamp(1.5,0.3,1.0)=1.0; recovery_steer=clamp(1.5*0.6,-1,1)=0.9
    cs_stuck = {**cs, "track_pos": 1.5, "speed_x": 0.0}
    cc_stuck = compute_control(cs_stuck, ATTACK)
    assert "(gear -1)"     in cc_stuck, f"FAIL stuck gear: {cc_stuck}"
    assert "(accel 0.600)" in cc_stuck, f"FAIL stuck accel: {cc_stuck}"
    assert "(steer 0.900)" in cc_stuck, f"FAIL stuck steer (should be inverted): {cc_stuck}"
    print(f"compute_control off-track (stuck)   ... OK  →  {cc_stuck}")

    # PIT + speed < 10 → meta=1
    cs_pit = {**cs, "speed_x": 5.0, "rpm": 800.0, "gear": 1}
    cc_pit = compute_control(cs_pit, PIT)
    assert "(meta 1)" in cc_pit, f"FAIL PIT meta: {cc_pit}"
    print(f"compute_control PIT       ... OK  →  {cc_pit}")

    # ---- safety_filter ------------------------------------------------------
    base = {"fuel": 50.0, "damage": 0.0}

    # valid strategy + healthy car → pass through unchanged
    assert safety_filter(ATTACK,    base) == ATTACK,    "FAIL: healthy ATTACK should pass"
    assert safety_filter(NORMAL,    base) == NORMAL,    "FAIL: healthy NORMAL should pass"
    assert safety_filter(SAVE_FUEL, base) == SAVE_FUEL, "FAIL: healthy SAVE_FUEL should pass"
    print("safety_filter pass-through   ... OK")

    # unknown / None → NORMAL
    assert safety_filter(None,        base) == NORMAL, "FAIL: None → NORMAL"
    assert safety_filter("TURBO",     base) == NORMAL, "FAIL: unknown → NORMAL"
    assert safety_filter("",          base) == NORMAL, "FAIL: empty → NORMAL"
    print("safety_filter unknown/None   ... OK")

    # fuel < 5 → PIT (beats any strategy including ATTACK)
    low_fuel = {**base, "fuel": 3.0}
    assert safety_filter(ATTACK, low_fuel) == PIT, "FAIL: low fuel + ATTACK → PIT"
    assert safety_filter(NORMAL, low_fuel) == PIT, "FAIL: low fuel + NORMAL → PIT"
    print("safety_filter low fuel → PIT ... OK")

    # damage >= 9500 → DEFEND
    critical_dmg = {**base, "damage": 9600.0}
    assert safety_filter(ATTACK, critical_dmg) == DEFEND, "FAIL: critical damage → DEFEND"
    assert safety_filter(NORMAL, critical_dmg) == DEFEND, "FAIL: critical damage → DEFEND"
    print("safety_filter critical damage → DEFEND ... OK")

    # 8000 <= damage < 9500 → ATTACK blocked, others pass
    high_dmg = {**base, "damage": 8500.0}
    assert safety_filter(ATTACK, high_dmg) == NORMAL,  "FAIL: high damage + ATTACK → NORMAL"
    assert safety_filter(NORMAL, high_dmg) == NORMAL,  "FAIL: high damage + NORMAL passes"
    assert safety_filter(DEFEND, high_dmg) == DEFEND,  "FAIL: high damage + DEFEND should pass"
    print("safety_filter high damage     ... OK")

    # fuel < 15 → ATTACK blocked
    caution_fuel = {**base, "fuel": 12.0}
    assert safety_filter(ATTACK, caution_fuel) == NORMAL, "FAIL: caution fuel + ATTACK → NORMAL"
    assert safety_filter(NORMAL, caution_fuel) == NORMAL, "FAIL: caution fuel + NORMAL passes"
    print("safety_filter caution fuel    ... OK")

    # ---- Step 6: _parse_strategy_response ----------------------------------
    # valid JSON with known strategy
    s, r = _parse_strategy_response('{"strategy": "ATTACK", "reason": "clear track ahead"}')
    assert s == ATTACK, f"FAIL parse valid: {s}"
    assert r == "clear track ahead", f"FAIL reason: {r}"
    print(f"_parse_strategy_response valid   ... OK  ({s} / {r!r})")

    # strategy field in wrong case → should normalise
    s, r = _parse_strategy_response('{"strategy": "defend", "reason": "opponent close"}')
    assert s == DEFEND, f"FAIL parse lower-case: {s}"
    print(f"_parse_strategy_response lower   ... OK  ({s})")

    # unknown strategy name → NORMAL
    s, r = _parse_strategy_response('{"strategy": "TURBO", "reason": "go fast"}')
    assert s == NORMAL, f"FAIL parse unknown: {s}"
    print(f"_parse_strategy_response unknown → NORMAL ... OK")

    # garbage text → NORMAL
    s, r = _parse_strategy_response("Sorry, I cannot help with that.")
    assert s == NORMAL, f"FAIL parse garbage: {s}"
    print(f"_parse_strategy_response garbage → NORMAL ... OK")

    # missing reason field → empty string, strategy still valid
    s, r = _parse_strategy_response('{"strategy": "SAVE_FUEL"}')
    assert s == SAVE_FUEL, f"FAIL parse no-reason: {s}"
    assert r == "",         f"FAIL reason should be empty: {r!r}"
    print(f"_parse_strategy_response no-reason ... OK  ({s})")

    # ---- Step 6: _build_strategy_prompt ------------------------------------
    sample_state = {
        "speed_x": 120.0, "fuel": 18.0, "damage": 500.0,
        "track_pos": 0.1, "gear": 4, "race_pos": 3,
        "dist_raced": 1200.0,
        "track":     [200.0] * 19,
        "opponents": [200.0] * 36,
    }
    prompt = _build_strategy_prompt(sample_state)
    assert "ATTACK" in prompt,     "FAIL: prompt missing strategy guide"
    assert "120.0"  in prompt,     "FAIL: prompt missing speed"
    assert "18.0"   in prompt,     "FAIL: prompt missing fuel"
    assert "strategy" in prompt,   "FAIL: prompt missing JSON schema hint"
    print("_build_strategy_prompt          ... OK  (prompt contains speed/fuel/strategy)")

    print("\nAll tests passed.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--bot":
        _host, _port, _strategy = "localhost", 3001, NORMAL
        _granite = False
        positional: list[str] = []
        i = 1
        while i < len(args):
            if args[i] == "--strategy" and i + 1 < len(args):
                _strategy = args[i + 1].upper()
                i += 2
            elif args[i] == "--granite":
                _granite = True
                i += 1
            else:
                positional.append(args[i])
                i += 1
        if len(positional) > 0:
            _host = positional[0]
        if len(positional) > 1 and positional[1].isdigit():
            _port = int(positional[1])
        elif len(positional) > 1:
            _strategy = positional[1].upper()
        run_bot(_host, _port, _strategy, use_granite=_granite)
    else:
        _run_tests()
