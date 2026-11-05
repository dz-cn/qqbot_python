"""QQ 通道层的异常与错误码分级。

设计原则：调用方只需要判断「能不能重试」，不需要知道具体错误码。
- 幂等成功：DuplicateMessage（平台已收过同一条，重发被判重）
- 放弃：PassiveExpiredError（被动回复窗口过期/越权，重试必然失败）
- 放弃：SendRejectedError（内容/权限/参数问题，重试只会重复扣配额）
- 重试：RateLimitError / TransientError（频控、5xx、网络抖动）
- 刷新后重试一次：AuthError（token 失效）
- 退出：FatalError（配置级错误，重连没有意义）

错误码来源：官方「发送单聊消息 / 发送群聊消息」页的错误码表与「API 调用指南」公共错误码。
"""
from __future__ import annotations


class QQError(Exception):
    """所有通道层异常的基类，带平台错误码与链路追踪 ID，便于排障。"""

    def __init__(self, message: str, *, code: int | None = None,
                 trace_id: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.trace_id = trace_id
        self.http_status = http_status

    def __str__(self) -> str:  # 日志里一行看完：错误码 + 文案 + trace_id
        parts = [super().__str__()]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        if self.trace_id:
            parts.append(f"trace_id={self.trace_id}")
        return " | ".join(parts)


class AuthError(QQError):
    """鉴权失败：刷新 token 后可重试一次。"""


class RateLimitError(QQError):
    """频控：退避后重试。"""


class TransientError(QQError):
    """可重试：5xx、网络错误、平台临时故障。"""


class PassiveExpiredError(QQError):
    """被动回复窗口过期 / msg_id 越权 / 事件不支持回复：放弃，不要重试。"""


class SendRejectedError(QQError):
    """永久失败：内容违规、超长、无权限、机器人不在群等。"""


class DuplicateMessage(QQError):
    """40054005 消息被去重：等价于已送达。"""


class FatalError(QQError):
    """配置级错误：intents 无权限、机器人下架/封禁等，重连无意义。"""


# 官方错误码 → 异常类型。表里没有的按 HTTP 状态兜底，再兜底到 SendRejectedError（不重试更安全）。
_DUPLICATE = {40054005}
_AUTH = {11241, 11243, 11252, 11253}
_PASSIVE_EXPIRED = {304103, 40034005, 40034024, 40034026, 40034027, 40034128}
_RATE_LIMIT = {40034100, 304045, 1100100}
# 平台明确提示"请稍后重试"或属于转存/内部异常：可以重试
_TRANSIENT = {304016, 304025, 40034004, 50055001, 50055002, 50055006}
# 内容/权限/参数问题：重试只会重复失败并扣配额
_REJECTED = {
    22006, 304004, 304036, 304061, 304062, 304064, 304080,
    340069,
    40034006, 40034008, 40034009, 40034010, 40034011, 40034029,
    40034101, 40034106, 40034108, 40034109, 40034122, 40034123, 40034124, 40034127,
    40054002, 40054003, 40054004, 40054006, 40054007, 40054010, 40054013, 40054016, 40054018,
    50006, 50035, 50037, 50043, 50045, 50046, 50047, 50048,
}


def error_for(code: int | None, message: str = "", *, trace_id: str | None = None,
              http_status: int | None = None) -> QQError:
    """把平台错误码翻译成「该不该重试」的异常实例。"""
    kwargs = {"code": code, "trace_id": trace_id, "http_status": http_status}
    if code in _DUPLICATE:
        return DuplicateMessage(message or "消息被去重", **kwargs)
    if code in _AUTH:
        return AuthError(message or "鉴权失败", **kwargs)
    if code in _PASSIVE_EXPIRED:
        return PassiveExpiredError(message or "被动回复已失效", **kwargs)
    if code in _RATE_LIMIT:
        return RateLimitError(message or "触发频率限制", **kwargs)
    if code in _TRANSIENT:
        return TransientError(message or "平台临时错误", **kwargs)
    if code in _REJECTED:
        return SendRejectedError(message or "消息被拒绝", **kwargs)
    return SendRejectedError(message or f"未知错误码 {code}", **kwargs)


def error_for_http(status: int, message: str = "", *, trace_id: str | None = None) -> QQError:
    """没有 err_code 时按 HTTP 状态兜底（401/429/5xx 分别对应鉴权、频控、可重试）。"""
    if status == 401:
        return AuthError(message or "HTTP 401 鉴权失败", http_status=status, trace_id=trace_id)
    if status == 429:
        return RateLimitError(message or "HTTP 429 频率限制", http_status=status, trace_id=trace_id)
    if status >= 500:
        return TransientError(message or f"HTTP {status} 服务端错误", http_status=status, trace_id=trace_id)
    return SendRejectedError(message or f"HTTP {status}", http_status=status, trace_id=trace_id)
