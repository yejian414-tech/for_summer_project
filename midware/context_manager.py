"""
Context Manager — SillyTavern 风格上下文窗口控制

SillyTavern 的核心思路：
  1. 把所有消息分层：System Prompt → Persona → 历史对话 → 当前用户消息
  2. 给每层分配 token 预算；超出时从历史对话最旧的一端裁剪
  3. 格式化时按选定的 Chat Template（ChatML / Instruct / Raw）拼装
"""

import json
import re
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Token 估算（无需 tiktoken 依赖）
# 1 token ≈ 4 个 ASCII 字符 / 2 个 CJK 字符 —— 足够做预算控制
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    ascii_chars = len(re.sub(r'[^\x00-\x7F]', '', text))
    cjk_chars   = len(re.findall(r'[一-鿿぀-ヿ]', text))
    other_chars = len(text) - ascii_chars - cjk_chars
    return max(1, ascii_chars // 4 + cjk_chars // 2 + other_chars // 3)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: Literal["system", "user", "assistant"]
    content: str
    pinned: bool = False          # True = 不会被裁剪（对应 ST 的 "Keep in Context"）
    tokens: int = field(init=False)

    def __post_init__(self):
        self.tokens = estimate_tokens(self.content)


@dataclass
class ContextConfig:
    # ---- 窗口大小 ----
    max_context_tokens: int = 4096   # 发送给 AI 的总 token 上限
    max_response_tokens: int = 512   # 留给 AI 回复的 token 数

    # ---- 裁剪策略（对应 ST 的 "Context Trim Strategy"）----
    trim_strategy: Literal["oldest_first", "newest_first"] = "oldest_first"

    # ---- 聊天模板 ----
    chat_template: Literal["chatml", "instruct", "raw"] = "chatml"

    # ---- 解说员人设（对应 ST 的 "Character Card / System Prompt"）----
    commentator_persona: str = (
        "你是一位专业的中文赛车比赛解说员，风格热情、专业、紧凑、有临场感。"
        "根据提供的 TORCS 实时遥测数据和比赛事件，用流畅中文给出简短（1-3句）的精彩解说。"
        "优先描述具体事件，重点关注速度变化、位次争夺、驾驶动作、危险情况。"
        "不要编造遥测数据中没有的信息，避免重复上一条解说。"
    )

    # ---- 数据字段过滤（选择哪些 TORCS 字段进入 prompt）----
    included_fields: list = field(default_factory=lambda: [
        "sim_time", "racePos", "lap", "speedX", "rpm",
        "gear", "throttle", "brake", "steer",
        "damage", "fuel", "lastLapTime", "curLapTime",
        "trackPos", "distFromStart",
    ])

    # ---- 排名摘要是否附加 ----
    include_rankings: bool = True


# ---------------------------------------------------------------------------
# 核心：Context Manager
# ---------------------------------------------------------------------------

class ContextManager:
    """
    管理对话历史、System Prompt 拼装、上下文裁剪。
    设计对应 SillyTavern 的 Context Template + Chat History 模块。
    """

    def __init__(self, config: ContextConfig | None = None):
        self.config  = config or ContextConfig()
        self.history: list[Message] = []   # 解说历史（user=遥测数据, assistant=解说）

    # ------------------------------------------------------------------
    # 添加消息
    # ------------------------------------------------------------------

    def add_user(self, content: str, pinned: bool = False) -> Message:
        msg = Message(role="user", content=content, pinned=pinned)
        self.history.append(msg)
        return msg

    def add_assistant(self, content: str) -> Message:
        msg = Message(role="assistant", content=content)
        self.history.append(msg)
        return msg

    def clear_history(self):
        self.history = [m for m in self.history if m.pinned]

    # ------------------------------------------------------------------
    # 构建发送给 AI 的 messages 列表（已裁剪到 token 预算内）
    # ------------------------------------------------------------------

    def build_messages(self) -> list[dict]:
        """
        返回符合 OpenAI Chat Completions 格式的 messages 列表。
        对应 ST 的 "Build Prompt" 步骤。
        """
        budget = self.config.max_context_tokens - self.config.max_response_tokens

        # 1. System prompt（固定占用，不裁剪）
        system_msg = Message(role="system", content=self.config.commentator_persona)
        budget -= system_msg.tokens

        # 2. 历史消息：先收集 pinned，再从新到旧填充剩余预算
        pinned   = [m for m in self.history if m.pinned]
        unpinned = [m for m in self.history if not m.pinned]

        for m in pinned:
            budget -= m.tokens

        # 按裁剪策略排序 unpinned
        if self.config.trim_strategy == "oldest_first":
            candidates = list(reversed(unpinned))   # 从最新往旧选，直到预算耗尽
        else:
            candidates = list(unpinned)             # 从最旧往新选

        selected_unpinned: list[Message] = []
        for m in candidates:
            if budget - m.tokens < 0:
                break
            budget -= m.tokens
            selected_unpinned.append(m)

        if self.config.trim_strategy == "oldest_first":
            selected_unpinned.reverse()   # 恢复时间顺序

        final_history = sorted(
            pinned + selected_unpinned,
            key=lambda m: self.history.index(m)
        )

        # 3. 组装
        messages = [{"role": "system", "content": system_msg.content}]
        for m in final_history:
            messages.append({"role": m.role, "content": m.content})

        return messages

    # ------------------------------------------------------------------
    # 格式化遥测数据 → user message（对应 ST 的 "World Info / Author's Note"）
    # ------------------------------------------------------------------

    def format_telemetry(
        self,
        telemetry: dict,
        rankings: list[dict] | None = None,
    ) -> str:
        """
        把 TORCS CSV 行转成自然语言描述，塞进 user message。
        只包含 config.included_fields 中指定的字段。
        """
        lines = ["【实时遥测数据】"]
        field_labels = {
            "sim_time":      ("比赛时间",    "s"),
            "racePos":       ("当前名次",    "名"),
            "lap":           ("当前圈数",    "圈"),
            "speedX":        ("纵向车速",    "km/h"),
            "rpm":           ("发动机转速",  "rpm"),
            "gear":          ("挡位",        ""),
            "throttle":      ("油门",        "%"),
            "brake":         ("制动",        "%"),
            "steer":         ("转向",        ""),
            "damage":        ("车辆损伤",    ""),
            "fuel":          ("剩余油量",    "L"),
            "lastLapTime":   ("上圈用时",    "s"),
            "curLapTime":    ("本圈用时",    "s"),
            "trackPos":      ("赛道位置",    ""),
            "distFromStart": ("距起点距离",  "m"),
        }

        for key in self.config.included_fields:
            if key not in telemetry:
                continue
            val = telemetry[key]
            label, unit = field_labels.get(key, (key, ""))
            # 数值格式化
            if key in ("throttle", "brake"):
                val = f"{float(val)*100:.0f}"
            elif isinstance(val, float):
                val = f"{val:.2f}"
            lines.append(f"  {label}: {val}{unit}")

        if self.config.include_rankings and rankings:
            lines.append("\n【全场排名】")
            for r in rankings:
                lines.append(
                    f"  P{r.get('race_pos','?')} {r.get('car_name','?')} "
                    f"— 圈数 {r.get('laps','?')} / 距起点 {r.get('dist_from_start',0):.0f}m"
                )

        lines.append("\n请根据以上数据给出精彩解说：")
        return "\n".join(lines)

    def format_event_prompt(self, payload: dict) -> str:
        """
        把事件检测引擎生成的结构化 payload 转成 user message。
        """
        payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
        return (
            "请根据以下结构化比赛事件生成中文解说。\n"
            "要求：1-3句，紧扣事件，语言有临场感，不要输出列表或 JSON。\n\n"
            f"{payload_text}"
        )

    def format_event_history_entry(self, payload: dict) -> str:
        event_type = payload.get("event_type", "unknown")
        reason = payload.get("event_reason", "")
        event_time = payload.get("event_time", "?")
        state = payload.get("current_state", {})
        return (
            f"事件摘要: {event_type} @ {event_time}s; {reason}; "
            f"P{state.get('race_pos', '?')}, lap {state.get('lap', '?')}, "
            f"speed {state.get('speed_x', '?')} km/h."
        )

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        total = sum(m.tokens for m in self.history)
        system_tokens = estimate_tokens(self.config.commentator_persona)
        return {
            "history_messages": len(self.history),
            "history_tokens": total,
            "system_tokens": system_tokens,
            "budget_remaining": (
                self.config.max_context_tokens
                - self.config.max_response_tokens
                - system_tokens
                - total
            ),
            "max_context_tokens": self.config.max_context_tokens,
            "max_response_tokens": self.config.max_response_tokens,
        }
