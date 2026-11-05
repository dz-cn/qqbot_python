"""群聊发言人别名：让上下文里能看出「谁在说话」。

平台事件只给 openid（不保证有昵称），openid 又长又不该整段进 prompt、更不该被模型写进记忆卡片。
这里用 openid 的短哈希生成稳定别名：
- 同一个人跨轮次、跨进程重启都是同一个别名（记忆才能对上号）；
- 不同人 4 位十六进制碰撞概率 1/65536，群聊规模下可忽略；
- 不泄露原始 openid，也不依赖消息出现顺序（顺序会随滑动窗口变化）。
"""
from __future__ import annotations

import hashlib

_ALIAS_PREFIX = "群友"


def speaker_alias(openid: str | None) -> str:
    """openid → `群友xxxx`；空 openid 退化为不带编号的 `群友`。"""
    if not openid:
        return _ALIAS_PREFIX
    digest = hashlib.sha256(openid.encode("utf-8")).hexdigest()[:4]
    return f"{_ALIAS_PREFIX}{digest}"


def label_message(content: str, openid: str | None) -> str:
    """给一条用户消息加上 `[群友xxxx]` 前缀；正文原样保留。"""
    alias = speaker_alias(openid)
    return f"[{alias}] {content}"
