"""QQ 通道层：WebSocket 收消息 + OpenAPI 发消息。

入口只有一个 `run_bot(handler)`：
- `handler` 就是现有的 `agent.chat(content, group_openid, member_openid, message_id)`，
  同步函数即可（内部会丢进线程池执行，不阻塞心跳）。
- 除 `handler` 外不感知业务：不 import agent/service/db，避免循环依赖。

模块职责：
- `token.py`     access_token 的缓存与续期
- `gateway.py`   WSS 连接、握手、心跳、Resume、关闭码处置
- `events.py`    事件体 → IncomingMessage（字段归一 + 文本清洗）
- `dispatcher.py` 去重、队列、会话串行、线程池执行、回复发送
- `api.py`       HTTPS 发送（错误码分级 + 重试 + 超长切分）
- `errors.py`    错误码 → 该不该重试
"""
from __future__ import annotations

import config
from qq.api import OpenAPIClient, split_text
from qq.dispatcher import Dispatcher
from qq.errors import (
    AuthError,
    DuplicateMessage,
    FatalError,
    PassiveExpiredError,
    QQError,
    RateLimitError,
    SendRejectedError,
    TransientError,
)
from qq.events import IncomingMessage, build_event, normalize_content
from qq.gateway import FatalGatewayError, GatewayClient
from qq.token import TokenManager

__all__ = [
    "run_bot",
    "IncomingMessage",
    "build_event",
    "normalize_content",
    "split_text",
    "TokenManager",
    "OpenAPIClient",
    "Dispatcher",
    "GatewayClient",
    "FatalGatewayError",
    "QQError",
    "AuthError",
    "RateLimitError",
    "TransientError",
    "PassiveExpiredError",
    "SendRejectedError",
    "DuplicateMessage",
    "FatalError",
]


async def run_bot(handler) -> None:
    """启动通道层主循环，直到收到退出信号（由调用方负责 KeyboardInterrupt）。"""
    if not config.QQ_BOT_APPID or not config.QQ_BOT_SECRET:
        raise SystemExit("缺少 QQ_BOT_APPID / QQ_BOT_SECRET，请在 .env 中填写")

    token = TokenManager(
        config.QQ_BOT_APPID,
        config.QQ_BOT_SECRET,
        config.QQ_TOKEN_URL,
        fallback_url=config.QQ_TOKEN_FALLBACK_URL,
        timeout=config.QQ_HTTP_TIMEOUT,
    )
    sender = OpenAPIClient(
        token,
        config.QQ_API_BASE,
        timeout=config.QQ_HTTP_TIMEOUT,
        retry=config.QQ_HTTP_RETRY,
        app_id=config.QQ_BOT_APPID,
        allow_active=config.QQ_ALLOW_ACTIVE_MSG,
    )
    dispatcher = Dispatcher(
        handler,
        sender,
        workers=config.QQ_WORKERS,
        queue_max=config.QQ_QUEUE_MAX,
        passive_ttl=config.QQ_PASSIVE_TTL_SECONDS,
        dedupe_ttl=config.QQ_DEDUPE_TTL_SECONDS,
        dedupe_capacity=config.QQ_DEDUPE_CAPACITY,
        max_chars=config.QQ_MAX_CONTENT_CHARS,
        max_parts=config.QQ_MAX_PARTS,
        no_reply_marker=config.QQ_NO_REPLY_MARKER,
    )
    gateway = GatewayClient(
        token,
        dispatcher,
        gateway_url=config.QQ_GATEWAY_URL,
        intents=config.QQ_INTENTS,
        shard=config.QQ_SHARD,
        heartbeat_factor=config.QQ_HEARTBEAT_FACTOR,
        http_timeout=config.QQ_HTTP_TIMEOUT,
    )

    dispatcher.start()
    try:
        await gateway.run_forever()
    finally:
        await gateway.close()
        await dispatcher.aclose()
        await sender.aclose()
        await token.aclose()
