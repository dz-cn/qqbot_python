"""当前请求的会话身份，用 contextvar 在「主流程 → 工具」之间隐式传递。

工具由模型触发，它拿不到也填不了 openid/scope（属于敏感标识），
但写卡片必须知道「在给谁写」，所以在 chat() 入口设置、工具执行时读取。
"""
import contextvars
from typing import NamedTuple


class SessionContext(NamedTuple):
    """一次请求的上下文快照：scope/openid 定位会话，message_id 供溯源。"""
    scope: str
    openid: str
    message_id: str


# 默认值兜底：万一某条链路没设置，也不会直接崩，退化成全局会话
_current: contextvars.ContextVar[SessionContext] = contextvars.ContextVar(
    "current_session", default=SessionContext("global", "global", "global")
)


def set_current_session(scope: str, openid: str, message_id: str) -> None:
    """在 chat() 里、调用 agent 之前设置本次请求的会话身份。"""
    _current.set(SessionContext(scope, openid, message_id))


def get_context() -> SessionContext:
    """需要一次拿全上下文时用这个，避免分别调多个 getter。"""
    return _current.get()