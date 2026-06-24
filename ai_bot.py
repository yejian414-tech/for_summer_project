#!/usr/bin/env python3
"""
Feature 4: Granite-powered AI racing bot.

Current file: Step 1 + Step 2 only.
  parse_scr_state()   — decode TORCS SCR sensor string → Python dict
  format_scr_control() — encode control dict → TORCS SCR wire string
"""

from __future__ import annotations

import re
from typing import Any

try:
    from telemetry_common import clamp, parse_float, parse_int
except ImportError:
    # telemetry_common requires openai; define the three helpers locally
    # so Step 1/2 tests can run without any extra dependencies installed.
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


# ---------------------------------------------------------------------------
# SCR field metadata
# ---------------------------------------------------------------------------

# Maps SCR camelCase wire names to snake_case Python names.
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

# Fields whose wire value is a space-separated list of floats.
_ARRAY_FIELDS: frozenset[str] = frozenset({"opponents", "track", "wheelSpinVel", "focus"})

# Fields that must be parsed as int rather than float.
_INT_FIELDS: frozenset[str] = frozenset({"gear", "racePos"})

# Expected list length for each array field.
_ARRAY_LENGTHS: dict[str, int] = {
    "opponents":    36,
    "track":        19,
    "wheelSpinVel": 4,
    "focus":        5,
}

# Default fill value when an array element is missing.
_ARRAY_DEFAULTS: dict[str, float] = {
    "opponents": 200.0,   # no car detected
    "track":      -1.0,   # out-of-range / off track
    "wheelSpinVel": 0.0,
    "focus":       -1.0,  # out-of-range
}

# A valid state packet must contain at least these wire keys.
_REQUIRED_KEYS: frozenset[str] = frozenset({"speedX", "fuel", "gear", "track"})

# Compiled regex: matches one (key value...) token.
_SCR_TOKEN = re.compile(r"\((\w+)\s+([^)]*)\)")


# ---------------------------------------------------------------------------
# Step 1: SCR state parser
# ---------------------------------------------------------------------------

def parse_scr_state(message: str) -> dict[str, Any] | None:
    """Decode a TORCS SCR sensor string into a Python dict.

    TORCS sends one string per simulation step, e.g.:
        (angle 0.01)(curLapTime 3.2)(damage 0)(fuel 40.0)
        (opponents 200 200 ... 200)(track 6.3 11.4 ... 200)(gear 3)...

    Returns a dict with snake_case keys, or None if the string is empty,
    unparseable, or is missing required fields.
    """
    if not message:
        return None

    # ---- tokenise --------------------------------------------------------
    raw: dict[str, str] = {}
    for match in _SCR_TOKEN.finditer(message):
        raw[match.group(1)] = match.group(2).strip()

    if not raw:
        return None

    # ---- require minimum set of keys ------------------------------------
    if not _REQUIRED_KEYS.issubset(raw):
        return None

    # ---- build typed dict -----------------------------------------------
    state: dict[str, Any] = {}

    for scr_name, py_name in _FIELD_MAP.items():
        raw_value = raw.get(scr_name, "")

        if scr_name in _ARRAY_FIELDS:
            parts = raw_value.split() if raw_value else []
            expected = _ARRAY_LENGTHS[scr_name]
            fill = _ARRAY_DEFAULTS[scr_name]
            values = [parse_float(p, fill) for p in parts]
            # pad or trim to the fixed expected length
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
    accel: float = 0.0,
    brake: float = 0.0,
    gear: int = 1,
    steer: float = 0.0,
    clutch: float = 0.0,
    focus: int = 0,
    meta: int = 0,
) -> str:
    """Encode a control action into the TORCS SCR wire format.

    All values are clamped to their legal ranges before serialisation.

    Returns a string like:
        (accel 0.800)(brake 0.000)(gear 3)(steer -0.120)(clutch 0.000)(focus 0)(meta 0)
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
# Quick self-test — run: python3 ai_bot.py
# ---------------------------------------------------------------------------

def _run_tests() -> None:
    # --- build a realistic fake SCR packet --------------------------------
    opponents = " ".join(["200.0"] * 36)
    track     = " ".join(["150.0"] * 9 + ["180.0"] + ["150.0"] * 9)  # 19 values
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

    # ---- test parse_scr_state -------------------------------------------
    state = parse_scr_state(sample)
    assert state is not None,                  "FAIL: returned None for valid packet"
    assert state["gear"] == 4,                 f"FAIL: gear={state['gear']}"
    assert state["race_pos"] == 2,             f"FAIL: race_pos={state['race_pos']}"
    assert abs(state["speed_x"] - 148.3) < 1e-6, f"FAIL: speed_x={state['speed_x']}"
    assert abs(state["fuel"] - 38.5) < 1e-6,  f"FAIL: fuel={state['fuel']}"
    assert len(state["opponents"]) == 36,      f"FAIL: opponents length={len(state['opponents'])}"
    assert len(state["track"]) == 19,          f"FAIL: track length={len(state['track'])}"
    assert len(state["wheel_spin_vel"]) == 4,  f"FAIL: wheel_spin_vel length={len(state['wheel_spin_vel'])}"
    assert len(state["focus"]) == 5,           f"FAIL: focus length={len(state['focus'])}"
    assert state["opponents"][0] == 200.0,     f"FAIL: opponents[0]={state['opponents'][0]}"
    assert state["focus"][0] == -1.0,          f"FAIL: focus[0]={state['focus'][0]}"
    print("parse_scr_state  ... OK")

    # edge: empty string
    assert parse_scr_state("") is None,        "FAIL: empty string should return None"
    # edge: missing required fields
    assert parse_scr_state("(angle 0.1)") is None, "FAIL: incomplete packet should return None"
    # edge: short opponents list gets padded
    short_opp = " ".join(["50.0"] * 10)
    partial = (
        f"(angle 0)(curLapTime 0)(damage 0)(distFromStart 0)(distRaced 0)"
        f"(fuel 30)(gear 1)(lastLapTime 0)(opponents {short_opp})"
        f"(racePos 1)(rpm 0)(speedX 0)(speedY 0)(speedZ 0)"
        f"(track {track})(trackPos 0)(wheelSpinVel {wheels})(z 0)"
    )
    padded_state = parse_scr_state(partial)
    assert padded_state is not None,              "FAIL: partial packet returned None"
    assert len(padded_state["opponents"]) == 36,  "FAIL: short opponents not padded to 36"
    assert padded_state["opponents"][35] == 200.0, "FAIL: padding value wrong"
    print("parse_scr_state  (edge cases) ... OK")

    # ---- test format_scr_control ----------------------------------------
    ctrl = format_scr_control(accel=0.8, brake=0.0, gear=3, steer=-0.12)
    assert "(accel 0.800)" in ctrl,   f"FAIL: accel missing in '{ctrl}'"
    assert "(brake 0.000)" in ctrl,   f"FAIL: brake missing in '{ctrl}'"
    assert "(gear 3)" in ctrl,        f"FAIL: gear missing in '{ctrl}'"
    assert "(steer -0.120)" in ctrl,  f"FAIL: steer missing in '{ctrl}'"
    assert "(clutch 0.000)" in ctrl,  f"FAIL: clutch missing in '{ctrl}'"
    assert "(focus 0)" in ctrl,       f"FAIL: focus missing in '{ctrl}'"
    assert "(meta 0)" in ctrl,        f"FAIL: meta missing in '{ctrl}'"
    print(f"format_scr_control ... OK  →  {ctrl}")

    # edge: clamping
    over = format_scr_control(accel=2.0, brake=-1.0, steer=5.0, focus=200)
    assert "(accel 1.000)" in over,  f"FAIL: accel not clamped in '{over}'"
    assert "(brake 0.000)" in over,  f"FAIL: brake not clamped in '{over}'"
    assert "(steer 1.000)" in over,  f"FAIL: steer not clamped in '{over}'"
    assert "(focus 90)" in over,     f"FAIL: focus not clamped in '{over}'"
    print("format_scr_control (clamping) ... OK")

    print("\nAll tests passed.")


if __name__ == "__main__":
    _run_tests()
