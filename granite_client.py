#!/usr/bin/env python3
"""
Feature 1 (AI Racing Engineer Chatbot): Granite / LM Studio connection.

Thin wrapper around the shared model-connection helpers in
telemetry_common.py so Feature 1 follows the same
"TORCS data -> Python middleware -> Granite endpoint -> text" pattern
already used by Feature 2 (telemetry_analyzer.py) and Feature 3
(race_commentator.py).
"""

from __future__ import annotations

import os

from telemetry_common import (
    DEFAULT_MODEL_BASE_URL,
    DEFAULT_MODEL_NAME,
    ModelConnection,
    chat_completion_text,
    connect_openai_compatible_model,
    print_connection_banner,
)


MODEL_BASE_URL = os.getenv("TORCS_ENGINEER_BASE_URL", DEFAULT_MODEL_BASE_URL)
MODEL_NAME = os.getenv("TORCS_ENGINEER_MODEL", DEFAULT_MODEL_NAME)
TEMPERATURE = float(os.getenv("TORCS_ENGINEER_TEMPERATURE", "0.4"))
MAX_TOKENS = int(os.getenv("TORCS_ENGINEER_MAX_TOKENS", "260"))
REQUEST_TIMEOUT = float(os.getenv("TORCS_ENGINEER_TIMEOUT", "20.0"))


def connect() -> ModelConnection:
    return connect_openai_compatible_model(base_url=MODEL_BASE_URL, requested_model=MODEL_NAME)


def ask_engineer(connection: ModelConnection, messages: list[dict[str, str]]) -> str:
    return chat_completion_text(
        connection,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout=REQUEST_TIMEOUT,
    )


def print_banner(connection: ModelConnection) -> None:
    print_connection_banner(connection, "TORCS AI Racing Engineer Chatbot")
