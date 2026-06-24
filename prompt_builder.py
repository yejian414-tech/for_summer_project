#!/usr/bin/env python3
"""
Feature 1 (AI Racing Engineer Chatbot): prompt construction.

Turns a car_state dict (see car_state_source.py for the contract) plus a
player question into the chat-completion `messages` list sent to Granite.
"""

from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = (
    "你是一名专业但说话简单直接的 AI 赛车工程师，搭档是一名 TORCS 赛车游戏玩家。"
    "你只能依据下面提供的实时赛车数据和检测到的问题来回答，不要编造没有给出的数值。"
    "回答必须使用简体中文，简洁、具体、可执行，像车队工程师在对讲机里说话一样，"
    "通常 2-4 句话即可，不需要免责声明，也不要重复整段输入数据。"
)


def format_car_state(car_state: dict[str, Any]) -> str:
    problems = car_state.get("problems") or []
    problem_text = "、".join(problems) if problems else "暂无明显异常"
    return (
        f"速度：{car_state.get('speed', 0):.0f} km/h\n"
        f"转速：{car_state.get('rpm', 0):.0f} rpm\n"
        f"挡位：{car_state.get('gear', 0)}\n"
        f"赛道位置：{car_state.get('track_pos', 0):.2f}（0为中心线，绝对值越接近1越靠近赛道边缘）\n"
        f"车辆损伤：{car_state.get('damage', 0):.0f}\n"
        f"剩余油量：{car_state.get('fuel', 0):.1f} L\n"
        f"当前圈速：{car_state.get('lap_time', 0):.1f} s\n"
        f"检测到的问题：{problem_text}"
    )


def build_messages(
    car_state: dict[str, Any],
    user_question: str,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Build the chat-completion messages list for one turn.

    `history` is an optional list of prior {"role": ..., "content": ...}
    turns from this same session. The caller (chat_engineer.py) is
    responsible for trimming it so older laps don't dominate the context
    window.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": f"当前赛车数据：\n{format_car_state(car_state)}\n\n玩家问题：\n{user_question}",
        }
    )
    return messages
