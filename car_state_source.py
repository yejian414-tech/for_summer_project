
#!/usr/bin/env python3
"""
Feature 1: car_state data sources.

This file provides data sources for the chatbot side.
The real telemetry conversion and problem detection are now owned by
race_analyzer.py.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Protocol

from race_analyzer import (
    CAR_STATE_KEYS,
    analyze_car_state,
    empty_car_state,
    telemetry_to_car_state,
    validate_car_state,
)
from telemetry_common import TelemetryBuffer


class CarStateSource(Protocol):
    def get_state(self) -> dict[str, Any]: ...


_DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "speed": 210.0,
        "rpm": 8700.0,
        "gear": 5,
        "track_pos": 0.72,
        "damage": 1200.0,
        "fuel": 35.0,
        "lap_time": 102.3,
    },
    {
        "speed": 65.0,
        "rpm": 6200.0,
        "gear": 4,
        "track_pos": 0.15,
        "damage": 0.0,
        "fuel": 58.0,
        "lap_time": 41.2,
    },
    {
        "speed": 180.0,
        "rpm": 9100.0,
        "gear": 4,
        "track_pos": -0.95,
        "damage": 3400.0,
        "fuel": 12.0,
        "lap_time": 88.7,
    },
]


class FakeCarStateSource:
    """Demo data for testing without TORCS."""

    def __init__(self, scenarios: list[dict[str, Any]] | None = None) -> None:
        self._cycle = itertools.cycle(scenarios or _DEMO_SCENARIOS)

    def get_state(self) -> dict[str, Any]:
        raw = dict(next(self._cycle))
        raw["problems"] = analyze_car_state(raw)
        return validate_car_state(raw)


class LiveCarStateSource:
    """Read live TORCS UDP telemetry and return the agreed car_state dict."""

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
        return telemetry_to_car_state(latest)


def wait_for_live_state(source: LiveCarStateSource, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if source.is_ready():
            return True
        time.sleep(0.2)
    return False
