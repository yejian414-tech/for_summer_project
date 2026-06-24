#!/usr/bin/env python3
"""
Feature 1: AI Racing Engineer Chatbot (CLI).

TORCS telemetry -> car_state contract -> prompt_builder -> Granite -> answer

This is "B 同学" 's deliverable for Module 1 per the team's 分工文档: AI
赛车工程师问答功能. It consumes the car_state contract defined in
car_state_source.py and does not depend on how "A 同学" eventually reads
and analyzes TORCS data -- only on that dict shape.

Run:
    python3 chat_engineer.py

Env vars:
    TORCS_ENGINEER_BASE_URL          - Granite/LM Studio endpoint (see granite_client.py)
    TORCS_ENGINEER_MODEL             - model id override
    TORCS_ENGINEER_USE_FAKE_DATA     - "true" to force demo data instead of live telemetry
    TORCS_ENGINEER_UDP_PORT          - live telemetry UDP port (default 3101)
    TORCS_ENGINEER_HISTORY_TURNS     - how many past Q&A turns to keep as context (default 3)
"""

from __future__ import annotations

import os
from typing import Any

import granite_client
import prompt_builder
from car_state_source import (
    CarStateSource,
    FakeCarStateSource,
    LiveCarStateSource,
    wait_for_live_state,
)
from telemetry_common import env_flag


USE_FAKE_DATA = env_flag("TORCS_ENGINEER_USE_FAKE_DATA", False)
UDP_PORT = int(os.getenv("TORCS_ENGINEER_UDP_PORT", "3101"))
HISTORY_TURNS = int(os.getenv("TORCS_ENGINEER_HISTORY_TURNS", "3"))


def choose_car_state_source() -> CarStateSource:
    if USE_FAKE_DATA:
        print("[ChatEngineer] TORCS_ENGINEER_USE_FAKE_DATA=true -> using demo car_state data.")
        return FakeCarStateSource()

    live = LiveCarStateSource(udp_port=UDP_PORT)
    print(f"[ChatEngineer] Waiting up to 5s for live telemetry on UDP:{UDP_PORT} ...")
    if wait_for_live_state(live, timeout=5.0):
        print("[ChatEngineer] Live telemetry detected, using real car_state data.")
        return live

    print(
        "[ChatEngineer] No live telemetry yet. Falling back to demo data. "
        "Start TORCS with the human driver telemetry export enabled (see README), "
        "or set TORCS_ENGINEER_USE_FAKE_DATA=true to silence this message."
    )
    return FakeCarStateSource()


def print_car_state(car_state: dict[str, Any]) -> None:
    print("\n" + prompt_builder.format_car_state(car_state))


def trim_history(history: list[dict[str, str]], turns: int) -> list[dict[str, str]]:
    max_messages = turns * 2
    if len(history) <= max_messages:
        return history
    return history[-max_messages:]


def main() -> None:
    connection = granite_client.connect()
    granite_client.print_banner(connection)

    car_state_source = choose_car_state_source()
    history: list[dict[str, str]] = []

    print("\n输入你的问题（例如：我的轮胎状态怎么样？/ 现在该不该进站？），输入 exit 退出。\n")

    while True:
        try:
            car_state = car_state_source.get_state()
            print_car_state(car_state)
            user_question = input("\n玩家：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见。")
            break

        if not user_question:
            continue
        if user_question.lower() in {"exit", "quit", "q"}:
            print("再见。")
            break

        messages = prompt_builder.build_messages(car_state, user_question, history=history)
        try:
            answer = granite_client.ask_engineer(connection, messages)
        except Exception as exc:
            print(f"[ChatEngineer] Granite 请求失败：{exc}")
            continue

        print(f"AI工程师：{answer}")
        history.append({"role": "user", "content": user_question})
        history.append({"role": "assistant", "content": answer})
        history[:] = trim_history(history, HISTORY_TURNS)


if __name__ == "__main__":
    main()
