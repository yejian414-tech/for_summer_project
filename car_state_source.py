#!/usr/bin/env python3
"""
Feature 1 (AI Racing Engineer Chatbot): car_state data contract + sources.

This module defines the JSON contract that the data-collection teammate
("A" 同学, 负责"赛车数据采集与状态分析功能") hands off to the chatbot side
("B" 同学, 负责"AI 赛车工程师问答功能"), and ships two implementations so
B's part can be built and tested independently before that handoff lands:

    FakeCarStateSource  - rotates through a few hand-written demo scenarios.
    LiveCarStateSource  - a temporary bridge that reads the same TORCS UDP
                           telemetry feed already used by Feature 2/3 and
                           derives the same car_state shape with simple rule
                           checks, so a live demo works even before A hands
                           off the real race_analyzer.py.

Agreed car_state contract (matches the team's 分工文档):

{
  "speed": float,        # km/h
  "rpm": float,
  "gear": int,
  "track_pos": float,    # -1..1, 0 = track center line
  "damage": float,
  "fuel": float,         # liters remaining
  "lap_time": float,     # seconds, current lap time
  "problems": [str, ...] # human-readable Chinese problem strings
}

Once A delivers a real race_analyzer.py that produces this same dict shape,
swap LiveCarStateSource for that module without touching prompt_builder.py,
granite_client.py, or chat_engineer.py.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Protocol

from telemetry_common import TelemetryBuffer


CAR_STATE_KEYS = ("speed", "rpm", "gear", "track_pos", "damage", "fuel", "lap_time", "problems")


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
    """Fill in any missing keys so prompt_builder never KeyErrors on a
    partial dict (e.g. while A's module is still being wired up)."""
    merged = empty_car_state()
    merged.update({key: state[key] for key in CAR_STATE_KEYS if key in state})
    return merged


class CarStateSource(Protocol):
    def get_state(self) -> dict[str, Any]: ...


def analyze_car_state(state: dict[str, Any]) -> list[str]:
    """Minimal rule checks, mirrors the spec the team agreed on.

    This is A's long-term responsibility (race_analyzer.py). The copy here
    exists only so Feature 1 has real "problems" to show before that
    module is handed off.
    """
    problems: list[str] = []
    if abs(state.get("track_pos", 0.0)) > 0.8:
        problems.append("车辆快要偏离赛道")
    if state.get("rpm", 0.0) > 8500:
        problems.append("转速过高，建议升挡")
    if state.get("speed", 0.0) < 80 and state.get("gear", 0) > 3:
        problems.append("当前挡位过高，出弯可能加速慢")
    if state.get("damage", 0.0) > 3000:
        problems.append("车辆损伤较严重，需要小心驾驶")
    return problems


_DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "speed": 210.0, "rpm": 8700.0, "gear": 5, "track_pos": 0.72,
        "damage": 1200.0, "fuel": 35.0, "lap_time": 102.3,
    },
    {
        "speed": 65.0, "rpm": 6200.0, "gear": 4, "track_pos": 0.15,
        "damage": 0.0, "fuel": 58.0, "lap_time": 41.2,
    },
    {
        "speed": 180.0, "rpm": 9100.0, "gear": 4, "track_pos": -0.95,
        "damage": 3400.0, "fuel": 12.0, "lap_time": 88.7,
    },
]


class FakeCarStateSource:
    """Hand-written demo data so B can build/test before A's feed is ready."""

    def __init__(self, scenarios: list[dict[str, Any]] | None = None) -> None:
        self._cycle = itertools.cycle(scenarios or _DEMO_SCENARIOS)

    def get_state(self) -> dict[str, Any]:
        raw = dict(next(self._cycle))
        raw["problems"] = analyze_car_state(raw)
        return validate_car_state(raw)


class LiveCarStateSource:
    """Bridges the existing TORCS UDP telemetry feed into the car_state
    contract above.

    Reuses the same UDP feed (default port 3101, see TORCS_PLAYER_UDP_PORT
    in the human driver telemetry export) as Feature 2/3, so it works out
    of the box once that export is enabled. Treat this as a stand-in for
    A's real race_analyzer.py output.
    """

    def __init__(self, udp_port: int = 3101, retention_seconds: float = 30.0) -> None:
        self._buffer = TelemetryBuffer(udp_port=udp_port, retention_seconds=retention_seconds)
        self._buffer.start_background()

    def is_ready(self) -> bool:
        return len(self._buffer.snapshot()) > 0

    def get_state(self) -> dict[str, Any]:
        frames = self._buffer.snapshot()
        if not frames:
            return empty_car_state()
        latest = frames[-1]
        raw = {
            "speed": latest["speed_x"],
            "rpm": latest["rpm"],
            "gear": latest["gear"],
            "track_pos": latest["track_pos"],
            "damage": latest["damage"],
            "fuel": latest["fuel"],
            "lap_time": latest["cur_lap_time"],
        }
        raw["problems"] = analyze_car_state(raw)
        return validate_car_state(raw)


def wait_for_live_state(source: LiveCarStateSource, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if source.is_ready():
            return True
        time.sleep(0.2)
    return False
