
#!/usr/bin/env python3
"""
A module: convert raw TORCS telemetry into the agreed car_state contract.

Input:
    raw telemetry frame from TORCS / telemetry_common.py

Output:
    {
        "speed": float,
        "rpm": float,
        "gear": int,
        "track_pos": float,
        "damage": float,
        "fuel": float,
        "lap_time": float,
        "problems": [str, ...]
    }
"""

from __future__ import annotations

from typing import Any


CAR_STATE_KEYS = (
    "speed",
    "rpm",
    "gear",
    "track_pos",
    "damage",
    "fuel",
    "lap_time",
    "problems",
)


def empty_car_state() -> dict[str, Any]:
    return {
        "speed": 0.0,
        "rpm": 0.0,
        "gear": 0,
        "track_pos": 0.0,
        "damage": 0.0,
        "fuel": 0.0,
        "lap_time": 0.0,
        "problems": [],
    }


def validate_car_state(state: dict[str, Any]) -> dict[str, Any]:
    merged = empty_car_state()
    for key in CAR_STATE_KEYS:
        if key in state:
            merged[key] = state[key]
    return merged


def _read_number(raw: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key not in raw:
            continue
        try:
            return float(raw[key])
        except (TypeError, ValueError):
            continue
    return default


def _read_int(raw: dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        if key not in raw:
            continue
        try:
            return int(float(raw[key]))
        except (TypeError, ValueError):
            continue
    return default


def telemetry_to_car_state(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Convert raw TORCS telemetry field names into the team's car_state format.

    Supports both styles:
    - raw UDP/CSV names: speedX, trackPos, curLapTime
    - parsed Python names: speed_x, track_pos, cur_lap_time
    """
    state = {
        "speed": _read_number(raw, "speed", "speed_x", "speedX"),
        "rpm": _read_number(raw, "rpm"),
        "gear": _read_int(raw, "gear"),
        "track_pos": _read_number(raw, "track_pos", "trackPos"),
        "damage": _read_number(raw, "damage"),
        "fuel": _read_number(raw, "fuel"),
        "lap_time": _read_number(raw, "lap_time", "cur_lap_time", "curLapTime"),
    }
    state["problems"] = analyze_car_state(state)
    return validate_car_state(state)


def analyze_car_state(state: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    speed = float(state.get("speed", 0.0))
    rpm = float(state.get("rpm", 0.0))
    gear = int(state.get("gear", 0))
    track_pos = float(state.get("track_pos", 0.0))
    damage = float(state.get("damage", 0.0))
    fuel = float(state.get("fuel", 0.0))

    if abs(track_pos) > 1.0:
        problems.append("车辆已经驶出赛道边界，需要立刻回到赛道内")
    elif abs(track_pos) > 0.8:
        problems.append("车辆快要偏离赛道，建议减小转向并回到中心线附近")

    if rpm > 8500:
        problems.append("转速过高，建议尽快升挡")
    elif rpm < 2500 and gear > 2:
        problems.append("转速偏低，当前挡位可能过高")

    if speed < 80 and gear > 3:
        problems.append("低速时挡位过高，出弯加速可能会变慢")

    if damage > 3000:
        problems.append("车辆损伤较严重，需要减少碰撞风险")
    elif damage > 1500:
        problems.append("车辆已有明显损伤，驾驶需要更保守")

    if 0 < fuel < 8:
        problems.append("剩余油量偏低，需要考虑进站或节省燃油")

    if not problems:
        problems.append("暂无明显异常")

    return problems
