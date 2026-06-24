"""
Telemetry ingestion and sliding-window storage for TORCS commentary.
"""

from __future__ import annotations

import csv
import io
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


MAIN_CSV_FIELDS = [
    "seq", "sim_time", "player", "lap", "x", "y", "yaw",
    "accel_x", "accel_y", "steer", "throttle", "brake", "clutch",
    "angle", "curLapTime", "damage", "distFromStart", "distRaced",
    "fuel", "gear", "lastLapTime", "racePos", "rpm",
    "speedX", "speedY", "speedZ", "trackPos", "z",
    *[f"opponent_{i}" for i in range(36)],
    *[f"track_{i}" for i in range(19)],
    *[f"wheelSpinVel_{i}" for i in range(4)],
    *[f"focus_{i}" for i in range(5)],
]

RANK_CSV_FIELDS = ["sim_time", "car_index", "car_name", "race_pos", "laps", "dist_from_start"]


@dataclass
class TelemetryPacket:
    telemetry: dict[str, Any] | None = None
    rankings: list[dict[str, Any]] = field(default_factory=list)


def try_number(value: str) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def parse_car_row(row: list[str]) -> dict[str, Any] | None:
    if len(row) < 4:
        return None
    frame: dict[str, Any] = {}
    for i, field_name in enumerate(MAIN_CSV_FIELDS):
        if i < len(row):
            frame[field_name] = try_number(row[i])
    return frame if frame else None


def parse_ranking_row(row: list[str]) -> dict[str, Any] | None:
    if len(row) < 7 or row[0] != "R":
        return None
    try:
        return {
            "sim_time": float(row[1]),
            "car_index": int(float(row[2])),
            "car_name": row[3],
            "race_pos": int(float(row[4])),
            "laps": int(float(row[5])),
            "dist_from_start": float(row[6]),
        }
    except (TypeError, ValueError):
        return None


def parse_udp_packet(data: bytes) -> TelemetryPacket:
    text = data.decode("utf-8", errors="replace").strip()
    packet = TelemetryPacket()
    if not text:
        return packet

    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        if row[0] == "R":
            ranking = parse_ranking_row(row)
            if ranking:
                packet.rankings.append(ranking)
            continue
        if packet.telemetry is None:
            packet.telemetry = parse_car_row(row)
    return packet


def telemetry_time(frame: dict[str, Any]) -> float:
    try:
        return float(frame.get("sim_time", 0.0))
    except (TypeError, ValueError):
        return 0.0


class TelemetryStore:
    def __init__(self, window_seconds: float = 30.0) -> None:
        self.window_seconds = window_seconds
        self._frames: deque[dict[str, Any]] = deque()
        self._ranking_snapshots: deque[dict[str, Any]] = deque()
        self._latest_frame: dict[str, Any] | None = None
        self._latest_rankings: list[dict[str, Any]] | None = None
        self._lock = threading.Lock()

    def push(self, telemetry: dict[str, Any] | None, rankings: list[dict[str, Any]] | None = None) -> None:
        if telemetry is None and not rankings:
            return
        with self._lock:
            latest_time = telemetry_time(telemetry) if telemetry else self._latest_sim_time_locked()
            if telemetry:
                self._latest_frame = dict(telemetry)
                self._frames.append(dict(telemetry))
            if rankings:
                self._latest_rankings = [dict(item) for item in rankings]
                self._ranking_snapshots.append(
                    {"sim_time": latest_time, "rankings": [dict(item) for item in rankings]}
                )
            self._evict_locked(latest_time)

    def latest(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        with self._lock:
            frame = dict(self._latest_frame) if self._latest_frame else None
            rankings = [dict(item) for item in self._latest_rankings] if self._latest_rankings else None
            return frame, rankings

    def recent_frames(self, seconds: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if not self._frames:
                return []
            window = self.window_seconds if seconds is None else seconds
            cutoff = telemetry_time(self._frames[-1]) - window
            return [dict(frame) for frame in self._frames if telemetry_time(frame) >= cutoff]

    def recent_rankings(self, seconds: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if not self._ranking_snapshots:
                return []
            window = self.window_seconds if seconds is None else seconds
            cutoff = float(self._ranking_snapshots[-1]["sim_time"]) - window
            return [dict(snapshot) for snapshot in self._ranking_snapshots if snapshot["sim_time"] >= cutoff]

    def has_telemetry(self) -> bool:
        with self._lock:
            return self._latest_frame is not None

    def _latest_sim_time_locked(self) -> float:
        if self._latest_frame:
            return telemetry_time(self._latest_frame)
        return 0.0

    def _evict_locked(self, latest_time: float) -> None:
        cutoff = latest_time - self.window_seconds
        while self._frames and telemetry_time(self._frames[0]) < cutoff:
            self._frames.popleft()
        while self._ranking_snapshots and float(self._ranking_snapshots[0]["sim_time"]) < cutoff:
            self._ranking_snapshots.popleft()


def start_udp_listener(
    store: TelemetryStore,
    *,
    host: str = "0.0.0.0",
    port: int = 3101,
    on_error: Any | None = None,
) -> threading.Thread:
    def listen() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, port))
        sock.settimeout(1.0)
        while True:
            try:
                data, _ = sock.recvfrom(8192)
                packet = parse_udp_packet(data)
                store.push(packet.telemetry, packet.rankings or None)
            except socket.timeout:
                continue
            except Exception as exc:
                if on_error:
                    on_error(exc)
                time.sleep(0.5)

    thread = threading.Thread(target=listen, daemon=True, name="torcs-udp-listener")
    thread.start()
    return thread
